#!/usr/bin/env python3
"""Panel HTTPS para registrar cuentas de votabot mediante Google OAuth."""

import base64
import hashlib
import os
import secrets
import threading
import time
from functools import wraps
from urllib.parse import urlencode

import requests
from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from votabot import ACCOUNTS, METRICS, asegurar_personas, exchange_for_im, load_config, load_json, save_json, slack

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_SCOPE = "openid email profile"
LOGIN_PORT = int(os.environ.get("LOGIN_PORT", "8081"))
MAX_LOGIN_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 15 * 60

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config.update(
    SECRET_KEY=os.environ.get("VOTABOT_SESSION_SECRET", ""),
    SESSION_COOKIE_NAME="votabot_admin",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

attempt_lock = threading.Lock()
login_attempts = {}
accounts_lock = threading.Lock()


def required_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Falta la variable requerida {name}")
    return value


def redirect_uri():
    return f"https://{required_env('VOTABOT_DOMAIN')}/auth/google/callback"


def client_ip():
    return request.remote_addr or "unknown"


def login_allowed(ip):
    now = time.time()
    with attempt_lock:
        timestamps = [value for value in login_attempts.get(ip, []) if now - value < LOGIN_WINDOW_SECONDS]
        login_attempts[ip] = timestamps
        return len(timestamps) < MAX_LOGIN_ATTEMPTS


def register_login_failure(ip):
    with attempt_lock:
        login_attempts.setdefault(ip, []).append(time.time())


def require_admin(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def code_challenge(verifier):
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def upsert_account(access_token):
    im, profile = exchange_for_im(access_token)
    email = profile.get("email", "")
    if not email:
        raise RuntimeError("Google no devolvio el correo de la cuenta")
    with accounts_lock:
        accounts = asegurar_personas(load_json(ACCOUNTS, []))
        existing = next((account for account in accounts if account.get("email") == email), None)
        accounts = [account for account in accounts if account.get("email") != email]
        accounts.append({
            "email": email,
            "name": profile.get("name", ""),
            "im": im,
            "fp": hashlib.md5(im.encode()).hexdigest(),
            "activo": existing.get("activo", True) if existing else False,
        })
        accounts = asegurar_personas(accounts)
        save_json(ACCOUNTS, accounts)
    return email


@app.get("/health")
def health():
    return {"ok": True}


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("admin"):
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        ip = client_ip()
        if not login_allowed(ip):
            abort(429, "Demasiados intentos. Espera 15 minutos.")
        password = request.form.get("password", "")
        if not secrets.compare_digest(password, required_env("VOTABOT_ADMIN_PASSWORD")):
            register_login_failure(ip)
            flash("Contraseña incorrecta.", "error")
            return render_template("login.html"), 401
        session.clear()
        session["admin"] = True
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
@require_admin
def dashboard():
    accounts = asegurar_personas(load_json(ACCOUNTS, []))
    return render_template("dashboard.html", accounts=accounts)


@app.post("/accounts/activate")
@require_admin
def activate_account():
    email = request.form.get("email", "")
    if not email:
        abort(400, "Falta la cuenta.")
    with accounts_lock:
        accounts = asegurar_personas(load_json(ACCOUNTS, []))
        account = next((item for item in accounts if item.get("email") == email), None)
        if not account:
            abort(404, "Cuenta no encontrada.")
        account["activo"] = True
        save_json(ACCOUNTS, accounts)
    slack("✅ Nueva cuenta activada", f"Ya estamos trabajando con la nueva cuenta: *{email}*.", color="#00a859")
    flash(f"{email} quedó activa. Votabot la incorpora en el siguiente ciclo.", "success")
    return redirect(url_for("dashboard"))


@app.get("/api/dashboard")
@require_admin
def dashboard_data():
    accounts = load_json(ACCOUNTS, [])
    active_accounts = sum(1 for account in accounts if account.get("activo", True))
    cfg = load_config()
    try:
        response = requests.get("http://votabot:8080/", timeout=3)
        response.raise_for_status()
        bot = response.json()
    except Exception:
        metrics = load_json(METRICS, {})
        bot = {
            "pausado": False,
            "solicitudes": metrics.get("solicitudes", 0),
            "votos": metrics.get("votos", 0),
            "ok": metrics.get("ok", 0),
            "errores": metrics.get("err", 0),
            "conflictos_409": metrics.get("conflictos", 0),
            "detalle": metrics.get("mesas", {}),
        }
    return {
        "accounts": {"total": len(accounts), "active": active_accounts},
        "survey": {"slugs": cfg.get("slugs", []), "targets": cfg.get("targets", {})},
        "metrics": {
            "paused": bot.get("pausado", False),
            "requests": bot.get("solicitudes", 0),
            "votes": bot.get("votos", 0),
            "accepted": bot.get("ok", 0),
            "rejected": bot.get("errores", 0) + bot.get("conflictos_409", 0),
            "conflicts": bot.get("conflictos_409", 0),
            "details": bot.get("detalle", {}),
        },
    }


@app.get("/accounts/google")
@require_admin
def start_google_login():
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    session["oauth_state"] = state
    session["oauth_verifier"] = verifier
    params = {
        "client_id": required_env("GOOGLE_OAUTH_CLIENT_ID"),
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": GOOGLE_SCOPE,
        "state": state,
        "code_challenge": code_challenge(verifier),
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    return redirect(f"{GOOGLE_AUTH_URL}?{urlencode(params)}")


@app.get("/auth/google/callback")
@require_admin
def google_callback():
    expected_state = session.pop("oauth_state", "")
    verifier = session.pop("oauth_verifier", "")
    received_state = request.args.get("state", "")
    if not expected_state or not secrets.compare_digest(expected_state, received_state):
        abort(400, "Estado OAuth invalido.")
    if request.args.get("error"):
        flash(f"Google canceló el registro: {request.args['error']}", "error")
        return redirect(url_for("dashboard"))
    code = request.args.get("code", "")
    if not code or not verifier:
        abort(400, "Google no devolvio un codigo valido.")
    token_response = requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "code": code,
            "client_id": required_env("GOOGLE_OAUTH_CLIENT_ID"),
            "client_secret": required_env("GOOGLE_OAUTH_CLIENT_SECRET"),
            "redirect_uri": redirect_uri(),
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        },
        timeout=20,
    )
    if not token_response.ok:
        abort(502, "Google rechazo el intercambio de codigo.")
    access_token = token_response.json().get("access_token", "")
    if not access_token:
        abort(502, "Google no devolvio access_token.")
    try:
        email = upsert_account(access_token)
    except Exception as error:
        app.logger.exception("No se pudo registrar cuenta")
        flash(f"No se pudo registrar la cuenta: {error}", "error")
        return redirect(url_for("dashboard"))
    flash(f"Cuenta registrada: {email}", "success")
    return redirect(url_for("dashboard"))


def validate_startup():
    for name in (
        "VOTABOT_DOMAIN",
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "VOTABOT_ADMIN_PASSWORD",
        "VOTABOT_SESSION_SECRET",
    ):
        required_env(name)


if __name__ == "__main__":
    validate_startup()
    app.run(host="0.0.0.0", port=LOGIN_PORT, debug=False)
