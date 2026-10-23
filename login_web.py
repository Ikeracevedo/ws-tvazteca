#!/usr/bin/env python3
"""Panel HTTPS para registrar cuentas de votabot mediante Google OAuth."""

import base64
import csv
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from functools import wraps
from urllib.parse import urlencode

import requests
from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from votabot import ACCOUNTS, ACTIVITY, LOG, METRICS, asegurar_personas, exchange_for_im, load_config, load_json, save_config, save_json, slack

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


def require_ceo(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("ceo"):
            return redirect(url_for("ceo_login"))
        return view(*args, **kwargs)

    return wrapped


def account_token(account):
    account_id = account.get("im", "").encode()
    secret_key = app.config["SECRET_KEY"].encode()
    return hmac.new(secret_key, account_id, hashlib.sha256).hexdigest()


def dashboard_accounts(accounts):
    return [
        {
            "alias": f"Cuenta {position:02d}",
            "activo": account.get("activo", True),
            "token": account_token(account),
        }
        for position, account in enumerate(accounts, start=1)
    ]


def bot_snapshot():
    try:
        response = requests.get("http://votabot:8080/", timeout=3)
        response.raise_for_status()
        return response.json()
    except Exception:
        metrics = load_json(METRICS, {})
        return {
            "pausado": load_config().get("pausado", True),
            "mesas": {},
            "solicitudes": metrics.get("solicitudes", 0),
            "votos": metrics.get("votos", 0),
            "ok": metrics.get("ok", 0),
            "errores": metrics.get("err", 0),
            "conflictos_409": metrics.get("conflictos", 0),
            "detalle": metrics.get("mesas", {}),
        }


def recent_vote_logs(accounts, limit=100):
    aliases = {account.get("email"): f"Cuenta {position:02d}" for position, account in enumerate(accounts, start=1)}
    if ACTIVITY.exists():
        try:
            with ACTIVITY.open("r", encoding="utf-8") as file:
                rows = [json.loads(line) for line in file if line.strip()][-limit:]
            return [
                {
                    "fecha": row.get("fecha", ""), "mesa": row.get("mesa", ""),
                    "destino": row.get("target", ""), "votos": row.get("votos", 0),
                    "cuenta": aliases.get(row.get("cuenta"), "Cuenta protegida"),
                    "ronda": row.get("ronda", ""), "resultado": row.get("resultado", ""),
                }
                for row in reversed(rows)
            ]
        except Exception:
            return []
    if not LOG.exists():
        return []
    try:
        with LOG.open("r", encoding="utf-8", newline="") as file:
            rows = list(csv.DictReader(file))[-limit:]
    except Exception:
        return []
    return [
        {
            "fecha": row.get("fecha", ""),
            "mesa": row.get("mesa", row.get("evento", "")),
            "destino": "",
            "votos": 0,
            "cuenta": aliases.get(row.get("cuenta"), "Cuenta protegida"),
            "ronda": row.get("ronda", ""),
            "resultado": row.get("resultado", ""),
        }
        for row in reversed(rows)
    ]


def calculator(cfg, accounts, bot):
    active_accounts = sum(1 for account in accounts if account.get("activo", True))
    average_delay = max(0.1, (float(cfg["delay_min"]) + float(cfg["delay_max"])) / 2)
    votes_per_request = max([int(value) for value in cfg.get("votos", {}).values()] or [10])
    tables = max(1, len(bot.get("mesas", {})))
    requests_per_minute = active_accounts * 60 / average_delay
    return {
        "delay_average": round(average_delay, 2),
        "votes_per_request": votes_per_request,
        "requests_per_minute": round(requests_per_minute, 1),
        "votes_per_minute": round(requests_per_minute * votes_per_request / tables, 1),
        "votes_per_hour": round(requests_per_minute * votes_per_request * 60 / tables),
        "active_accounts": active_accounts,
    }


def ceo_snapshot():
    accounts = asegurar_personas(load_json(ACCOUNTS, []))
    cfg = load_config()
    bot = bot_snapshot()
    public_config = dict(cfg)
    public_config.pop("slack_webhook", None)
    logs = recent_vote_logs(accounts)
    targets = {mesa: detail.get("target", "") for mesa, detail in bot.get("detalle", {}).items()}
    for event in logs:
        event["destino"] = event["destino"] or targets.get(event["mesa"], "No disponible en histórico")
    return {
        "accounts": [
            {"alias": f"Cuenta {position:02d}", "name": account.get("name", ""), "email": account.get("email", ""), "active": account.get("activo", True)}
            for position, account in enumerate(accounts, start=1)
        ],
        "config": public_config,
        "bot": bot,
        "calculator": calculator(cfg, accounts, bot),
        "logs": logs,
    }


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
    return None


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


@app.route("/ceo/login", methods=["GET", "POST"])
def ceo_login():
    if session.get("ceo"):
        return redirect(url_for("ceo_dashboard"))
    if request.method == "POST":
        password = request.form.get("password", "")
        ceo_password = os.environ.get("VOTABOT_CEO_PASSWORD", "")
        if not ceo_password:
            abort(503, "Falta configurar VOTABOT_CEO_PASSWORD.")
        if not secrets.compare_digest(password, ceo_password):
            flash("Contraseña CEO incorrecta.", "error")
            return render_template("login.html", ceo=True), 401
        session.clear()
        session["ceo"] = True
        return redirect(url_for("ceo_dashboard"))
    return render_template("login.html", ceo=True)


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/ceo")
@require_ceo
def ceo_dashboard():
    return render_template("ceo.html")


@app.get("/api/ceo")
@require_ceo
def ceo_data():
    return ceo_snapshot()


@app.post("/ceo/control")
@require_ceo
def ceo_control():
    action = request.form.get("action", "")
    cfg = load_config()
    if action in {"pause", "resume"}:
        cfg["pausado"] = action == "pause"
        save_config(cfg)
        status = "pausada" if cfg["pausado"] else "reanudada"
        title = "⏸️ CEO detuvo bot" if cfg["pausado"] else "▶️ CEO activó bot"
        slack(title, f"La votación se {status} desde el panel CEO.", color="#ffd100")
        flash(f"Votación {status}.", "success")
        return redirect(url_for("ceo_dashboard"))

    if action != "configure":
        abort(400, "Acción no válida.")
    slug = request.form.get("slug", "").strip()
    target = request.form.get("target", "").strip()
    if not slug or not target:
        abort(400, "Selecciona encuesta y participante.")
    bot = bot_snapshot()
    participants = [
        name for mesa, info in bot.get("mesas", {}).items()
        if mesa.startswith(f"{slug}/")
        for name in info.get("participantes", [])
    ]
    if target not in participants:
        abort(400, "El participante no pertenece a la encuesta activa.")
    try:
        votes = max(1, min(10, int(request.form.get("votes", "10"))))
        workers = max(1, min(20, int(request.form.get("workers", cfg["workers"]))))
        delay_min = max(0.5, float(request.form.get("delay_min", cfg["delay_min"])))
        delay_max = max(delay_min, float(request.form.get("delay_max", cfg["delay_max"])))
    except ValueError:
        abort(400, "Configuración numérica inválida.")
    cfg.update({
        "slugs": [slug],
        "targets": {slug: target},
        "votos": {slug: votes},
        "workers": workers,
        "delay_min": delay_min,
        "delay_max": delay_max,
    })
    save_config(cfg)
    slack("🎯 CEO actualizó campaña", f"Encuesta: *{slug}*\nParticipante: *{target}*", color="#193e70",
          campos=[("Votos por solicitud", str(votes)), ("Workers", str(workers)), ("Frecuencia", f"{delay_min}-{delay_max}s")])
    flash("Campaña actualizada. Votabot aplica el cambio en el siguiente ciclo.", "success")
    return redirect(url_for("ceo_dashboard"))


@app.get("/")
@require_admin
def dashboard():
    accounts = asegurar_personas(load_json(ACCOUNTS, []))
    return render_template("dashboard.html", accounts=dashboard_accounts(accounts))


@app.post("/accounts/activate")
@require_admin
def activate_account():
    token = request.form.get("account_token", "")
    if not token:
        abort(400, "Falta la cuenta.")
    with accounts_lock:
        accounts = asegurar_personas(load_json(ACCOUNTS, []))
        account = next((item for item in accounts if hmac.compare_digest(account_token(item), token)), None)
        if not account:
            abort(404, "Cuenta no encontrada.")
        account["activo"] = True
        save_json(ACCOUNTS, accounts)
        alias = f"Cuenta {accounts.index(account) + 1:02d}"
    slack("✅ Nueva cuenta activada", f"Ya estamos trabajando con la nueva cuenta: *{alias}*.", color="#00a859")
    flash(f"{alias} quedó activa. Votabot la incorpora en el siguiente ciclo.", "success")
    return redirect(url_for("dashboard"))


@app.get("/api/dashboard")
@require_admin
def dashboard_data():
    accounts = load_json(ACCOUNTS, [])
    active_accounts = sum(1 for account in accounts if account.get("activo", True))
    cfg = load_config()
    bot = bot_snapshot()
    return {
        "accounts": {"total": len(accounts), "active": active_accounts},
        "survey": {"slugs": cfg.get("slugs", []), "mesas": bot.get("mesas", {})},
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
        upsert_account(access_token)
    except Exception as error:
        app.logger.exception("No se pudo registrar cuenta")
        flash(f"No se pudo registrar la cuenta: {error}", "error")
        return redirect(url_for("dashboard"))
    flash("Cuenta registrada correctamente. Actívala cuando quieras empezar a votar.", "success")
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
