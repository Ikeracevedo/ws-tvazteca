#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
votabot.py — Bot de votación TV Azteca (La Granja VIP)

  python votabot.py login                 registra cuentas de Google (local, una vez)
  python votabot.py run                   demonio: vota mientras la votación esté abierta
  python votabot.py target "Rafa Mercadante"   cambia el concursante en caliente
  python votabot.py evento duelo          cambia de votación en caliente
  python votabot.py status                estado actual
  python votabot.py list / probe / check / verify / test-slack

Solo `login` necesita Playwright. El resto es requests puro.
"""

import argparse, csv, hashlib, json, os, random, re, signal, sys, threading, time, uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote
import requests

# ----------------------------------------------------------------- CONSTANTES
API             = "https://vota.tvazteca.com"
LOGIN_API       = "https://1uh17u5ix2.execute-api.us-east-1.amazonaws.com/dev/email/login_provider"
LOGIN_API_KEY   = "ISqeHtLglpaavcyV2TwXq7pSe1aX5SEC7zZAfg32"
GOOGLE_CLIENT   = "96709254426-q203hhjgoutj1k0k14vpvv05ahbkb8k2.apps.googleusercontent.com"
PAGE_URL        = "https://www.tvazteca.com/aztecauno/la-granja-vip/vota/"
REDIRECT_CANDIDATES = ["https://vota.tvazteca.com/auth/google-callback",
                       "https://www.tvazteca.com/auth/google-callback"]
EVENTOS = {"capataz": "vota", "acompanante": "vota", "vota": "vota",
           "duelo": "lagranjavip", "granjero": "lagranjavip", "lagranjavip": "lagranjavip"}
SLUGS_CONOCIDOS = ["vota", "lagranjavip"]

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]
LANGS = ["es-MX,es;q=0.9", "es-CO,es;q=0.8", "es-419,es;q=0.9", "es-MX,es;q=0.8,en;q=0.6"]

DATA_DIR = Path("votabot_data")
ACCOUNTS = DATA_DIR / "accounts.json"
STATE    = DATA_DIR / "state.json"
LOG      = DATA_DIR / "votos.csv"
HEART    = DATA_DIR / "heartbeat.txt"
CONFIG   = Path("config.json")

# ----------------------------------------------------------------- CONFIG
DEFAULTS = {
    "evento": "capataz", "slug": None, "target": None, "votes": None, "spread": False,
    "workers": 4, "delay_min": 1.2, "delay_max": 3.5, "unique_fp": True,
    "poll_status_every": 15, "save_every": 60, "reload_every": 30,
    "descansos": True, "http_port": 8080, "max_rounds_per_account": 10**9,
}

def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG.exists():
        try: cfg.update(json.loads(CONFIG.read_text("utf-8")))
        except Exception as e: print("config.json invalido:", e)
    return cfg

def save_config(cfg: dict):
    CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), "utf-8")

def load_json(p, d):
    try: return json.loads(Path(p).read_text("utf-8"))
    except Exception: return d

def save_json(p, data):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(p) + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
    tmp.replace(p)

# ----------------------------------------------------------------- SLACK
def slack(titulo: str, texto: str = "", color: str = "#36a64f", campos=None):
    wh = os.environ.get("SLACK_WEBHOOK") or load_config().get("slack_webhook")
    if not wh: return
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": f"*{titulo}*\n{texto}"}}]
    if campos:
        blocks.append({"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*{k}*\n{v}"} for k, v in campos]})
    blocks.append({"type": "context", "elements": [
        {"type": "mrkdwn", "text": f"🕒 {datetime.now():%Y-%m-%d %H:%M:%S} · `{os.uname().nodename if hasattr(os,'uname') else 'host'}`"}]})
    try:
        requests.post(wh, json={"attachments": [{"color": color, "blocks": blocks}]}, timeout=10)
    except Exception as e:
        print("[slack] error:", str(e)[:80])

# ----------------------------------------------------------------- UTILS
def new_session(persona=None):
    p = persona or {}
    s = requests.Session()
    s.headers.update({
        "User-Agent": p.get("ua", UA_POOL[0]),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": p.get("lang", LANGS[0]),
        "Origin": "https://www.tvazteca.com",
        "Referer": PAGE_URL,
        "Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty",
    })
    return s

def log_voto(slug, email, rnd, resultado):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    nuevo = not LOG.exists()
    with LOG.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if nuevo: w.writerow(["fecha", "evento", "cuenta", "ronda", "resultado"])
        w.writerow([datetime.now().isoformat(timespec="seconds"), slug, email, rnd, resultado])

def latido():
    HEART.parent.mkdir(parents=True, exist_ok=True)
    HEART.write_text(str(time.time()), "utf-8")

def nueva_persona():
    return {"ua": random.choice(UA_POOL), "lang": random.choice(LANGS),
            "ritmo": round(random.uniform(0.75, 1.5), 2),
            "descanso_cada": random.randint(25, 70),
            "descanso_min": random.randint(20, 60),
            "descanso_max": random.randint(90, 240)}

# ----------------------------------------------------------------- LOGIN
def _auth_url(redirect):
    return ("https://accounts.google.com/o/oauth2/v2/auth"
            f"?client_id={quote(GOOGLE_CLIENT)}&redirect_uri={quote(redirect)}"
            "&response_type=token"
            f"&scope={quote('openid email profile')}"
            "&include_granted_scopes=true&prompt=select_account")

def google_token(profile: Path, headless=False, cdp=None):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        kwargs = dict(user_data_dir=str(profile), headless=headless,
                      viewport={"width": 1366, "height": 900}, locale="es-MX",
                      args=["--disable-blink-features=AutomationControlled",
                            "--no-first-run", "--no-default-browser-check"],
                      ignore_default_args=["--enable-automation"])
        try: ctx = p.chromium.launch_persistent_context(channel="chrome", **kwargs)
        except Exception:
            print("   (sin Chrome, uso Chromium)"); ctx = p.chromium.launch_persistent_context(**kwargs)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            for redir in REDIRECT_CANDIDATES:
                page.goto(_auth_url(redir), wait_until="domcontentloaded")
                page.wait_for_timeout(1500)
                if "redirect_uri_mismatch" in page.content():
                    print(f"   x redirect no registrado: {redir}"); continue
                for _ in range(4):
                    if "signin/rejected" in page.url:
                        try: page.get_by_role("button", name=re.compile("Reintentar", re.I)).click(timeout=6000)
                        except Exception: page.reload(wait_until="domcontentloaded")
                        page.wait_for_timeout(2500)
                    else: break
                if "signin/rejected" in page.url:
                    print(f"   x Google rechaza con {redir}"); continue
                print("   -> INICIA SESION. NO cierres la ventana hasta que diga 'token obtenido'")
                fin = time.time() + 600
                while time.time() < fin:
                    if "access_token=" in page.url: break
                    try: page.wait_for_timeout(200)
                    except Exception:
                        v = list(ctx.pages)
                        if not v: raise RuntimeError("cerraste la ventana antes de terminar")
                        page = v[-1]
                else: raise RuntimeError("timeout esperando token")
                tok = re.search(r"access_token=([^&]+)", page.url).group(1)
                print("   token obtenido ✔")
                return tok
            raise RuntimeError("ningun redirect funciono")
        finally:
            try: ctx.close()
            except Exception: pass

def exchange_for_im(access_token):
    s = new_session()
    info = s.get("https://www.googleapis.com/oauth2/v3/userinfo",
                 headers={"Authorization": f"Bearer {access_token}"}).json()
    payload = {"provider": {"type": "gmail", "token": access_token},
               "data_user": {"email": info.get("email", ""), "gender": info.get("gender", ""),
                             "birthday": "", "age_range": "", "phone_number": "",
                             "fullname": info.get("name", ""), "im": ""},
               "data_device": {"channelId": 3, "device": "WEB", "deviceType": "Computer",
                               "deviceId": "", "deviceIdType": "", "deviceModel": "Chrome 131",
                               "operative_system": "Windows 10", "adid": "", "idfa": ""},
               "origin_url": PAGE_URL, "traffic_source": ""}
    r = s.post(LOGIN_API, json=payload, timeout=30,
               headers={"Content-Type": "application/json", "X-Api-Key": LOGIN_API_KEY})
    r.raise_for_status()
    d = r.json()
    im = (d.get("user") or {}).get("im") or d.get("im") or info.get("sub", "")
    if not im: raise RuntimeError(f"sin im: {d}")
    return im, info

def cmd_login(args):
    accs = load_json(ACCOUNTS, [])
    (DATA_DIR / "profiles").mkdir(parents=True, exist_ok=True)
    print(f"Cuentas registradas: {len(accs)}")
    while True:
        n = len(accs) + 1
        print(f"\n=== Cuenta #{n} ===")
        try:
            im, info = exchange_for_im(google_token(DATA_DIR / "profiles" / f"cuenta{n}", args.headless))
        except KeyboardInterrupt: break
        except Exception as e:
            print("  !! error:", e)
            if input("  ¿reintentar? [S/n] ").strip().lower() == "n": break
            continue
        accs = [a for a in accs if a.get("email") != info.get("email")]
        accs.append({"email": info.get("email"), "name": info.get("name"), "im": im,
                     "fp": hashlib.md5(im.encode()).hexdigest(), "persona": nueva_persona()})
        save_json(ACCOUNTS, accs)
        print(f"  ✔ {info.get('email')}")
        if input("\n¿Agregar otra? [S/n] ").strip().lower() == "n": break

def asegurar_personas(accs):
    cambiado = False
    for a in accs:
        if "persona" not in a: a["persona"] = nueva_persona(); cambiado = True
    if cambiado: save_json(ACCOUNTS, accs)
    return accs

# ----------------------------------------------------------------- API
def fetch_cfg(s, slug):
    st  = s.get(f"{API}/config/{slug}-status.json", timeout=20).json()
    cfg = s.get(f"{API}/config/{slug}.json", timeout=20).json()
    return st, cfg

def build_votes(contestants, max_votes, target=None, spread=False, custom=None):
    names = {c["id"]: c["name"] for c in contestants if not c.get("deleted")}
    votes = {i: 0 for i in names}
    if custom:
        for part in custom.split(","):
            k, _, n = part.partition("=")
            hit = [i for i, nm in names.items() if k.strip().lower() in nm.lower()]
            if not hit: sys.exit(f"No encontre '{k.strip()}'. Opciones: {', '.join(names.values())}")
            votes[hit[0]] = int(n)
    elif target:
        hit = [i for i, nm in names.items() if target.lower() in nm.lower()]
        if not hit: sys.exit(f"No encontre '{target}'. Opciones: {', '.join(names.values())}")
        votes[hit[0]] = max_votes
    elif spread:
        base, rem = divmod(max_votes, len(names))
        for idx, i in enumerate(names): votes[i] = base + (1 if idx < rem else 0)
    else:
        votes[list(names)[0]] = max_votes
    return votes

def send_vote(s, slug, cid, votes, im, phase, rnd, fp):
    try:
        token = s.get(f"{API}/api/vote-token", params={"fp": fp}, timeout=20).json().get("token", "")
    except Exception:
        token = ""
    return s.post(f"{API}/api/vote-public", timeout=30, json={
        "votes": votes, "user_im": im, "phase": phase, "round": rnd,
        "slug": slug, "config_id": cid, "token": token, "fp": fp})

# ----------------------------------------------------------------- COMANDOS
def cmd_list(args):
    for a in load_json(ACCOUNTS, []):
        p = a.get("persona", {})
        print(f"  {a['email']:<34} ritmo={p.get('ritmo','-')} ua={p.get('ua','')[:38]}...")

def cmd_probe(args):
    s = new_session()
    for slug in SLUGS_CONOCIDOS:
        try: st, cfg = fetch_cfg(s, slug)
        except Exception as e: print(f"[{slug}] error {e}"); continue
        v = cfg.get("voting") or {}
        print(f"\n=== {slug} ===\n  titulo: {cfg.get('nombre') or v.get('title')}")
        print(f"  activa: {st.get('active')}  abierta: {st.get('voting_open')}")
        print("  nominados:", ", ".join(c["name"] for c in v.get("contestants", []) if not c.get("deleted")))

def cmd_check(args):
    s = new_session()
    for a in load_json(ACCOUNTS, []):
        for slug in SLUGS_CONOCIDOS:
            try:
                _, cfg = fetch_cfg(s, slug)
                cid = (cfg.get("voting") or {}).get("configId")
                if not cid: continue
                r = s.get(f"{API}/api/user-state", params={"user_im": a["im"], "config_id": cid}, timeout=20).json()
                print(f"  {a['email']:<32} [{slug}] phase={r.get('phase') or '-'} round={r.get('round')}")
            except Exception as e: print(f"  {a['email']} error {e}")

def cmd_target(args):
    cfg = load_config(); cfg["target"] = args.nombre; save_config(cfg)
    print(f"target -> {args.nombre}   (el bot lo toma en <= {cfg['reload_every']}s)")
    slack(f"🎯 Concursante cambiado", f"Ahora se vota por *{args.nombre}*")

def cmd_evento(args):
    cfg = load_config(); cfg["evento"] = args.nombre; save_config(cfg)
    print(f"evento -> {args.nombre} (slug {EVENTOS.get(args.nombre, '?')})")
    slack(f"🔁 Votación cambiada", f"Nuevo evento: *{args.nombre}*")

def cmd_status(args):
    cfg = load_config()
    est = load_json(STATE, {})
    print(f"evento: {cfg['evento']}  target: {cfg['target']}  workers: {cfg['workers']}")
    print(f"delay: {cfg['delay_min']}-{cfg['delay_max']}s  cuentas: {len(load_json(ACCOUNTS, []))}")
    hb = HEART.stat().st_mtime if HEART.exists() else 0
    print(f"ultimo latido: {int(time.time()-hb)}s atras" if hb else "sin latido")
    for cid, d in est.items():
        if d: print(f"  {cid[:8]}: {len(d)} cuentas con rondas, max ronda={max(d.values())}")

def cmd_test_slack(args):
    slack("✅ Prueba de votabot", "Si ves esto, el webhook funciona.",
          campos=[("Evento", load_config()["evento"]), ("Target", str(load_config()["target"]))])
    print("enviado")

# ----------------------------------------------------------------- RUN (demonio)
class Stats:
    def __init__(self):
        self.lock = threading.Lock(); self.ok = self.err = self.skip = 0; self.t0 = time.time()
    def add(self, k):
        with self.lock: setattr(self, k, getattr(self, k) + 1)
    def resumen(self):
        with self.lock: return self.ok, self.err, self.skip, time.time() - self.t0

def cmd_run(args):
    accs = asegurar_personas(load_json(ACCOUNTS, []))
    if not accs: sys.exit("No hay cuentas. Corre: python votabot.py login")

    stop, stats = threading.Event(), Stats()
    lock = threading.Lock()
    estado = load_json(STATE, {})
    shared = {"abierto": False, "slug": None, "cid": None, "votes": {}, "titulo": ""}
    sesion_stats = {}
    inicio_actual = [None]

    # ---- monitor: vigila la config, el estado y recarga config.json
    def monitor():
        s = new_session()
        abierto_prev = None; titulo_prev = None
        while not stop.is_set():
            try:
                cfg = load_config()
                slug = EVENTOS.get((cfg.get("evento") or "").lower(), cfg.get("slug")) or "vota"
                st, c = fetch_cfg(s, slug)
                v = c.get("voting") or {}
                abierto = bool(st.get("active")) and st.get("voting_open") is not False
                if v:
                    votes = build_votes(v["contestants"], v.get("maxVotes", 10),
                                        cfg.get("target"), cfg.get("spread"), cfg.get("votes"))
                    titulo = c.get("nombre") or v.get("title") or slug
                    with lock:
                        shared.update(abierto=abierto, slug=slug, cid=v["configId"],
                                      votes=votes, titulo=titulo, target=cfg.get("target"))
                else:
                    with lock: shared.update(abierto=False, slug=slug, cid=None)

                if abierto and abierto_prev is not True:
                    inicio_actual[0] = time.time()
                    sesion_stats.clear()
                    n = max(1, min(int(cfg["workers"]), len(accs)))
                    slack("🟢 VOTACIÓN INICIADA", f"*{shared.get('titulo','')}*",
                          color="#2eb886",
                          campos=[("Concursante", str(cfg.get("target"))),
                                  ("Cuentas", str(len(accs))), ("Workers", str(n)),
                                  ("Delay", f"{cfg['delay_min']}-{cfg['delay_max']}s"),
                                  ("Estimado", f"~{int(n*60/((cfg['delay_min']+cfg['delay_max'])/2+0.3))} votos/min")])
                elif not abierto and abierto_prev is True:
                    dur = time.time() - (inicio_actual[0] or time.time())
                    total = sum(v["votos"] for v in sesion_stats.values())
                    lineas = "\n".join(f"• {v['email']} — {v['votos']} envíos" for v in
                                       sorted(sesion_stats.values(), key=lambda x: -x["votos"])[:25])
                    slack("🔴 VOTACIÓN FINALIZADA",
                          f"Duración: *{dur/60:.1f} min*\nEnvíos totales: *{total}*\nVotos al concursante: *{total*10}*\n\n{lineas}",
                          color="#e01e5a",
                          campos=[("Concursante", str(cfg.get("target"))), ("Cuentas activas", str(len(sesion_stats)))])
                    save_json(DATA_DIR / f"resumen_{datetime.now():%Y%m%d_%H%M}.json",
                              {"inicio": inicio_actual[0], "duracion": dur, "total": total,
                               "detalle": sesion_stats})
                if titulo != titulo_prev and titulo_prev is not None:
                    slack("ℹ️ Nuevo evento detectado", str(titulo))
                abierto_prev, titulo_prev = abierto, titulo
            except Exception as e:
                with lock: shared["abierto"] = False
                print("[monitor]", str(e)[:100])
            stop.wait(load_config()["poll_status_every"])

    # ---- worker: una cuenta = una persona
    def trabajador(idx, cuentas):
        sesion = new_session()
        time.sleep(random.uniform(0, 8))                     # arranque escalonado
        contador = {}
        while not stop.is_set():
            for a in cuentas:
                if stop.is_set(): return
                if not shared["abierto"]: break
                with lock:
                    slug, cid, votes = shared["slug"], shared["cid"], dict(shared["votes"])
                if not cid or not votes: break

                per = a.get("persona", {})
                if idx != id(a) % 1 and False: pass
                sesion.headers["User-Agent"] = per.get("ua", UA_POOL[0])
                sesion.headers["Accept-Language"] = per.get("lang", LANGS[0])

                with lock: rnd = estado.setdefault(cid, {}).get(a["im"], 0)
                phase = "main_done" if rnd == 0 else ("extra_done" if rnd % 2 else "main_done")
                fp = f"{a['fp']}-{rnd}" if load_config()["unique_fp"] else a["fp"]
                try:
                    r = send_vote(sesion, slug, cid, votes, a["im"], phase, rnd, fp)
                except Exception:
                    stats.add("err"); time.sleep(2); continue

                if r.status_code == 200:
                    stats.add("ok"); log_voto(slug, a["email"], rnd, "ok")
                    with lock:
                        estado.setdefault(cid, {})[a["im"]] = rnd + 1
                        e = sesion_stats.setdefault(a["im"], {"email": a["email"], "votos": 0})
                        e["votos"] += 1
                    contador[a["im"]] = contador.get(a["im"], 0) + 1
                elif r.status_code == 409:
                    stats.add("skip"); log_voto(slug, a["email"], rnd, "409")
                    with lock: estado.setdefault(cid, {})[a["im"]] = rnd + 1
                elif r.status_code == 429:
                    esp = float(r.headers.get("Retry-After") or 5)
                    stats.add("skip"); stop.wait(esp)
                else:
                    stats.add("err"); log_voto(slug, a["email"], rnd, f"err{r.status_code}")

                # pausa humana
                base = random.uniform(load_config()["delay_min"], load_config()["delay_max"])
                time.sleep(base * per.get("ritmo", 1.0))
                # descanso ocasional
                if load_config()["descansos"] and contador.get(a["im"], 0) >= per.get("descanso_cada", 999):
                    contador[a["im"]] = 0
                    stop.wait(random.randint(per.get("descanso_min", 20), per.get("descanso_max", 120)))
            if not shared["abierto"]: stop.wait(3)

    def guardador():
        while not stop.is_set():
            stop.wait(load_config()["save_every"])
            with lock: save_json(STATE, estado)

    def apagar(*_):
        print("\ncerrando..."); stop.set()

    signal.signal(signal.SIGINT, apagar); signal.signal(signal.SIGTERM, apagar)

    # ---- servidor HTTP de observabilidad
    def http_server():
        port = load_config()["http_port"]
        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                ok, err, skip, dt = stats.resumen()
                body = json.dumps({"abierto": shared["abierto"], "evento": shared.get("slug"),
                                   "target": shared.get("target"), "votos": ok, "saltados": skip,
                                   "errores": err, "votos_por_seg": round(ok / max(dt, 1), 3),
                                   "cuentas": len(accs), "uptime": int(dt),
                                   "rondas": {k: max(v.values()) if v else 0 for k, v in estado.items()}},
                                  ensure_ascii=False, indent=2).encode()
                self.send_response(200 if self.path != "/health" or HEART.exists() else 503)
                self.send_header("Content-Type", "application/json"); self.end_headers()
                self.wfile.write(body)
            def log_message(self, *a): pass
        try: ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
        except Exception as e: print("[http]", e)

    n = max(1, min(int(load_config()["workers"]), len(accs)))
    hilos = [threading.Thread(target=monitor, daemon=True),
             threading.Thread(target=guardador, daemon=True),
             threading.Thread(target=http_server, daemon=True)]
    for i in range(n):
        hilos.append(threading.Thread(target=trabajador, args=(i, accs[i::n]), daemon=True))
    for h in hilos: h.start()

    slack("🚀 votabot arrancado",
          f"evento=`{load_config()['evento']}` target=`{load_config()['target']}` cuentas={len(accs)} workers={n}",
          color="#439fe0")
    print(f"votabot | cuentas={len(accs)} workers={n} | http://localhost:{load_config()['http_port']}/ | Ctrl+C para salir")

    try:
        while not stop.is_set():
            latido(); stop.wait(20)
            ok, err, skip, dt = stats.resumen()
            print(f"[{datetime.now():%H:%M:%S}] {'ABIERTO' if shared['abierto'] else 'en espera'} | "
                  f"votos={ok} saltados={skip} errores={err} | {ok/max(dt,1):.2f} v/s | {str(shared.get('titulo'))[:40]}")
    except KeyboardInterrupt:
        apagar()
    for h in hilos: h.join(timeout=3)
    with lock: save_json(STATE, estado)
    slack("🛑 votabot detenido", "Proceso finalizado correctamente.")
    print("listo.")

# ----------------------------------------------------------------- CLI
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("login"); p.add_argument("--headless", action="store_true"); p.set_defaults(f=cmd_login)
    sub.add_parser("list").set_defaults(f=cmd_list)
    sub.add_parser("probe").set_defaults(f=cmd_probe)
    sub.add_parser("check").set_defaults(f=cmd_check)
    sub.add_parser("status").set_defaults(f=cmd_status)
    sub.add_parser("test-slack").set_defaults(f=cmd_test_slack)

    p = sub.add_parser("target"); p.add_argument("nombre"); p.set_defaults(f=cmd_target)
    p = sub.add_parser("evento"); p.add_argument("nombre"); p.set_defaults(f=cmd_evento)

    p = sub.add_parser("run")
    p.add_argument("--workers", type=int); p.add_argument("--delay-min", type=float)
    p.add_argument("--delay-max", type=float); p.add_argument("--http-port", type=int)
    p.set_defaults(f=cmd_run)

    a = ap.parse_args(); a.f(a)