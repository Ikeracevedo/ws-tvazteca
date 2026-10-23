#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
votabot.py — Bot de votación TV Azteca (La Granja VIP) · v2.0

  python votabot.py login                       registra cuentas de Google (una vez, con navegador)
  python votabot.py run                         arranca el demonio (vota lo que diga config.json)
  python votabot.py duelos                      vota TODAS las mesas de 'vota' (indefinido)
  python votabot.py vote "Rafa Mercadante" 10   elige concursante y votos por solicitud
  python votabot.py report                      manda reporte a Slack AHORA
  python votabot.py off / on                    pausa / reanuda (el proceso sigue vivo)
  python votabot.py status / stats / mesas       estado, contadores y mesas detectadas
  python votabot.py list / probe / check / test-slack
"""

import argparse, csv, hashlib, json, os, random, re, signal, sys, threading, time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlparse
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
RE_MESA = re.compile(r"^voting\d*$")

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
METRICS  = DATA_DIR / "metricas.json"
CONFIG   = DATA_DIR / "config.json"
LEGACY_CONFIG = Path("config.json")

# ----------------------------------------------------------------- CONFIG
DEFAULTS = {
    "slugs": ["vota"],          # slugs a cubrir
    "targets": {},              # "slug" o "slug/mesa" -> nombre del concursante
    "votos": {},                # "slug" o "slug/mesa" -> votos por solicitud (<= maxVotes)
    "spread": False,            # repartir entre todos en vez de cargar al target
    "pausado": False,
    "workers": 4,
    "delay_min": 1.2, "delay_max": 3.5,
    "unique_fp": True,
    "poll_status_every": 15,
    "save_every": 60,
    "report_every": 300,        # segundos entre reportes automáticos a Slack
    "descansos": True,
    "http_port": 8080,
    "max_rounds_per_account": 0,   # 0 = ilimitado
    "slack_webhook": None,
}

def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG.exists():
        try:
            cfg.update(json.loads(CONFIG.read_text("utf-8")))
        except Exception as e:
            print("config.json invalido:", e)
    elif LEGACY_CONFIG.exists():
        try:
            cfg.update(json.loads(LEGACY_CONFIG.read_text("utf-8")))
            save_config(cfg)
        except Exception as e:
            print("config.json invalido:", e)
    return cfg

def save_config(cfg: dict):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), "utf-8")

def load_json(p, d):
    try: return json.loads(Path(p).read_text("utf-8"))
    except Exception: return d

def save_json(p, data):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(p) + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
    tmp.replace(p)

def resolver(cfg, slug, mesa):
    """Devuelve (target, votos) para slug/mesa, con fallback a slug y a '*'."""
    for k in (f"{slug}/{mesa}", slug, "*"):
        if k in cfg.get("targets", {}):
            return cfg["targets"][k], cfg.get("votos", {}).get(k)
    return None, None

def normalizar_slug(valor):
    """Acepta un slug o una URL de vota.tvazteca.com y devuelve el slug."""
    texto = valor.strip()
    if not texto:
        raise ValueError("indica el slug o la URL de la encuesta")
    ruta = urlparse(texto).path if "://" in texto else texto
    if "/config/" in ruta:
        ruta = ruta.rsplit("/config/", 1)[1]
    slug = ruta.strip("/").split("/")[-1]
    slug = re.sub(r"(?:-status)?\.json$", "", slug)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", slug):
        raise ValueError(f"slug invalido: {valor}")
    return slug

def slugs_disponibles(cfg=None):
    configurados = (cfg or load_config()).get("slugs") or []
    return list(dict.fromkeys([*configurados, *SLUGS_CONOCIDOS]))

# ----------------------------------------------------------------- SLACK
def slack(titulo: str, texto: str = "", color: str = "#36a64f", campos=None):
    wh = os.environ.get("SLACK_WEBHOOK") or load_config().get("slack_webhook")
    if not wh: return
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": f"*{titulo}*\n{texto}"}}]
    if campos:
        blocks.append({"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*{k}*\n{v}"} for k, v in campos]})
    blocks.append({"type": "context", "elements": [
        {"type": "mrkdwn", "text": f"🕒 {datetime.now():%Y-%m-%d %H:%M:%S}"}]})
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

def log_voto(mesa, email, rnd, resultado):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    nuevo = not LOG.exists()
    with LOG.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if nuevo: w.writerow(["fecha", "mesa", "cuenta", "ronda", "resultado"])
        w.writerow([datetime.now().isoformat(timespec="seconds"), mesa, email, rnd, resultado])

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

def mesas_de(cfg: dict):
    """Todas las mesas de votación de un config: voting, voting2, voting3..."""
    out = []
    for k, v in (cfg or {}).items():
        if RE_MESA.match(k) and isinstance(v, dict) and v.get("configId"):
            if v.get("enabled", True) is False:
                continue
            out.append((k, v))
    return sorted(out, key=lambda x: (len(x[0]), x[0]))

def build_votes(contestants, max_votes, target=None, spread=False, custom=None, n=None):
    """Devuelve (votes, aviso). requireAllVotes=true: la suma DEBE ser max_votes.
    NUNCA llama sys.exit: si el target no existe, cae al primero y devuelve aviso."""
    names = {c["id"]: c["name"] for c in contestants if not c.get("deleted")}
    votes = {i: 0 for i in names}
    aviso = None
    if not names:
        return votes, "sin concursantes"

    if custom:
        for part in custom.split(","):
            k, _, val = part.partition("=")
            hit = [i for i, nm in names.items() if k.strip().lower() in nm.lower()]
            if not hit:
                aviso = f"'{k.strip()}' no está en la lista (ignorado)"
                continue
            votes[hit[0]] = int(val)
        falta = max_votes - sum(votes.values())
        if falta > 0:
            ids = list(votes)
            for idx, i in enumerate(ids):
                votes[i] += falta // len(ids) + (1 if idx < falta % len(ids) else 0)
    elif target:
        hit = [i for i, nm in names.items() if target.lower() in nm.lower()]
        if not hit:
            hit = [list(names)[0]]
            aviso = f"'{target}' NO está en esta votación → votando por '{names[hit[0]]}'"
        cuantos = max(1, min(int(n or max_votes), max_votes))
        votes[hit[0]] = cuantos
        resto = max_votes - cuantos
        otros = [i for i in names if i != hit[0]]
        if resto and otros:
            for idx, i in enumerate(otros):
                votes[i] = resto // len(otros) + (1 if idx < resto % len(otros) else 0)
    elif spread:
        base, rem = divmod(max_votes, len(names))
        for idx, i in enumerate(names): votes[i] = base + (1 if idx < rem else 0)
    else:
        votes[list(names)[0]] = max_votes
    return votes, aviso
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

def mostrar_encuesta(s, slug, incluir_concursantes=True):
    st, cfg = fetch_cfg(s, slug)
    print(f"\n=== {slug} === activa={st.get('active')} abierta={st.get('voting_open')}")
    mesas = mesas_de(cfg)
    if not mesas:
        print("  Sin mesas de votacion activas.")
        return
    for mesa, datos in mesas:
        print(f"  · mesa {mesa:<8} maxVotes={datos.get('maxVotes')} configId={datos['configId'][:8]}")
        print(f"      {datos.get('title') or cfg.get('nombre') or '-'}")
        if incluir_concursantes:
            concursantes = ", ".join(
                concursante["name"] for concursante in datos.get("contestants", [])
                if not concursante.get("deleted")
            )
            print(f"      {concursantes or 'Sin concursantes'}")

def cmd_encuestas(args):
    s = new_session()
    print("Encuestas configuradas y conocidas:")
    for slug in slugs_disponibles():
        try:
            st, cfg = fetch_cfg(s, slug)
            mesas = mesas_de(cfg)
            estado = "ABIERTA" if st.get("active") and st.get("voting_open") is not False else "cerrada"
            print(f"  · {slug:<20} {estado:<7} mesas={len(mesas)}")
        except Exception as e:
            print(f"  · {slug:<20} no disponible ({str(e)[:70]})")

def cmd_encuesta(args):
    try:
        slug = normalizar_slug(args.encuesta)
    except ValueError as e:
        print(f"Error: {e}")
        return
    try:
        mostrar_encuesta(new_session(), slug)
    except Exception as e:
        print(f"No pude consultar '{slug}': {e}")
        return
    if args.activar:
        cfg = load_config()
        cfg["slugs"] = [slug]
        cfg["pausado"] = True
        save_config(cfg)
        print(f"Encuesta activa -> {slug}. Votabot queda PAUSADO: elige target y ejecuta 'on'.")

def cmd_mesas(args):
    s = new_session()
    slugs = [normalizar_slug(slug) for slug in args.slugs] if args.slugs else slugs_disponibles()
    for slug in slugs:
        try: mostrar_encuesta(s, slug)
        except Exception as e: print(f"[{slug}] error {e}")

def cmd_probe(args):
    cmd_mesas(args)

def cmd_check(args):
    s = new_session()
    slugs = [normalizar_slug(slug) for slug in args.slugs] if args.slugs else slugs_disponibles()
    for a in load_json(ACCOUNTS, []):
        for slug in slugs:
            try:
                _, cfg = fetch_cfg(s, slug)
                for k, v in mesas_de(cfg):
                    r = s.get(f"{API}/api/user-state",
                              params={"user_im": a["im"], "config_id": v["configId"]}, timeout=20).json()
                    print(f"  {a['email']:<32} [{slug}/{k}] phase={r.get('phase') or '-'} round={r.get('round')}")
            except Exception as e:
                print(f"  {a['email']} error {e}")

def cmd_target(args):
    cmd_vote(argparse.Namespace(nombre=args.nombre, votos=None, slug=args.slug,
                                mesa=None, repartir=False, todos=True, total=None))

def cmd_vote(args):
    cfg = load_config()
    slugs = cfg.get("slugs") or ["vota"]
    if args.slug:       claves = [args.slug]
    elif args.todos:    claves = list(slugs)
    else:               claves = [slugs[0]]
    if args.mesa:
        claves = [f"{c}/{args.mesa}" for c in claves]

    s = new_session()                      # ← validación contra la lista real
    for c in claves:
        slug, _, mesa = c.partition("/")
        try:
            _, conf = fetch_cfg(s, slug)
            lista = []
            for mk, mv in mesas_de(conf):
                if mesa and mk != mesa: continue
                lista += [x["name"] for x in mv["contestants"] if not x.get("deleted")]
            hit = sorted({x for x in lista if args.nombre.lower() in x.lower()})
            if hit: print(f"  ✔ {c}: coincide con {', '.join(hit)}")
            else:   print(f"  ⚠ {c}: '{args.nombre}' NO está. Opciones: {', '.join(sorted(set(lista)))}")
        except Exception as e:
            print(f"  (no pude validar {c}: {e})")

    for k in claves:
        cfg["targets"][k] = args.nombre
        if args.votos is not None: cfg["votos"][k] = args.votos
    if args.repartir: cfg["spread"] = True
    save_config(cfg)
    detalle = f"{args.nombre}" + (f" · {args.votos} votos/solicitud" if args.votos else "")
    print(f"target -> {detalle}  en: {', '.join(claves)}")
    slack("🎯 Concursante cambiado", f"Ahora se vota por *{detalle}*\nMesas: `{', '.join(claves)}`")
    
def cmd_duelos(args):
    cfg = load_config()
    cfg["slugs"] = ["vota", "lagranjavip"]
    save_config(cfg)
    print("slugs -> vota, lagranjavip (todas sus mesas). El bot lo toma en <30s.")
    slack("🔁 Modo DUELOS", "Cubriendo *todas* las mesas de `vota` y `lagranjavip` de forma indefinida.")

def cmd_evento(args):
    cfg = load_config()
    slug = EVENTOS.get(args.nombre.lower(), normalizar_slug(args.nombre))
    cfg["slugs"] = [slug]
    save_config(cfg)
    print(f"evento -> {args.nombre} (slug {slug})")
    slack("🔁 Votación cambiada", f"Nuevo evento: *{args.nombre}*")

def cmd_on(args):
    cfg = load_config(); cfg["pausado"] = False; save_config(cfg)
    print("votabot ACTIVADO"); slack("▶️ votabot ACTIVADO", "Reanudando votación.", color="#2eb886")

def cmd_off(args):
    cfg = load_config(); cfg["pausado"] = True; save_config(cfg)
    print("votabot PAUSADO"); slack("⏸️ votabot PAUSADO", "Deja de votar. El proceso sigue vivo.", color="#e0a800")

def cmd_status(args):
    cfg = load_config()
    m = load_json(METRICS, {})
    est = load_json(STATE, {})
    print(f"slugs: {cfg['slugs']}  pausado: {cfg['pausado']}  workers: {cfg['workers']}")
    print(f"targets: {cfg['targets']}  votos: {cfg['votos']}")
    print(f"cuentas: {len(load_json(ACCOUNTS, []))}  delay: {cfg['delay_min']}-{cfg['delay_max']}s")
    hb = HEART.stat().st_mtime if HEART.exists() else 0
    print(f"ultimo latido: {int(time.time()-hb)}s atras" if hb else "sin latido")
    print(f"solicitudes totales: {m.get('solicitudes',0)}  votos totales: {m.get('votos',0)}")
    for cid, d in est.items():
        if d: print(f"  mesa {cid[:8]}: {len(d)} cuentas, max ronda={max(d.values())}")

def cmd_stats(args):
    m = load_json(METRICS, {})
    if not m: print("sin metricas todavia (el bot no ha corrido)"); return
    print(f"uptime: {int(time.time()-m.get('inicio',time.time()))}s")
    print(f"TOTAL solicitudes={m.get('solicitudes',0)} votos={m.get('votos',0)} "
          f"ok={m.get('ok',0)} errores={m.get('err',0)} 409={m.get('conflictos',0)}")
    for k, d in (m.get("mesas") or {}).items():
        print(f"  {k:<20} solicitudes={d.get('solicitudes',0):>7} votos={d.get('votos',0):>8} "
              f"errores={d.get('err',0)} target={d.get('target')}")

def cmd_test_slack(args):
    slack("✅ Prueba de votabot", "Si ves esto, el webhook funciona.",
          campos=[("Slugs", ", ".join(load_config()["slugs"])), ("Targets", str(load_config()["targets"]))])
    print("enviado")

def enviar_reporte(forzar=False):
    m = load_json(METRICS, {})
    if not m: return
    lineas = []
    for k, d in (m.get("mesas") or {}).items():
        lineas.append(f"• `{k}` → *{d.get('target') or '?'}*: {d.get('solicitudes',0)} solicitudes / "
                      f"{d.get('votos',0)} votos")
    dur = max(1, time.time() - m.get("inicio", time.time()))
    slack("📊 Reporte votabot",
          "\n".join(lineas) or "sin mesas activas",
          color="#439fe0",
          campos=[("Solicitudes totales", f"{m.get('solicitudes',0)}"),
                  ("Votos totales", f"*{m.get('votos',0)}*"),
                  ("Errores", str(m.get("err",0))),
                  ("409 (ya votó)", str(m.get("conflictos",0))),
                  ("Ritmo", f"{m.get('solicitudes',0)/dur*60:.1f} sol/min"),
                  ("Cuentas", str(len(load_json(ACCOUNTS, []))))])
    print("[report] enviado")

def cmd_report(args):
    enviar_reporte(forzar=True)

# ----------------------------------------------------------------- RUN (demonio)
class Stats:
    def __init__(self):
        self.lock = threading.Lock(); self.ok = self.err = self.skip = 0; self.t0 = time.time()
    def add(self, k):
        with self.lock: setattr(self, k, getattr(self, k) + 1)
    def resumen(self):
        with self.lock: return self.ok, self.err, self.skip, time.time() - self.t0

def cmd_run(args):
    cfg = load_config()
    if args.workers:    cfg["workers"] = args.workers
    if args.delay_min:  cfg["delay_min"] = args.delay_min
    if args.delay_max:  cfg["delay_max"] = args.delay_max
    if args.http_port:  cfg["http_port"] = args.http_port
    if args.slugs:      cfg["slugs"] = [x.strip() for x in args.slugs.split(",") if x.strip()]
    save_config(cfg)

    accs = asegurar_personas(load_json(ACCOUNTS, []))
    if not accs: sys.exit("No hay cuentas. Corre: python votabot.py login")

    stop, stats = threading.Event(), Stats()
    lock = threading.Lock()
    estado = load_json(STATE, {})
    shared = {}                      # "slug/mesa" -> {...}
    pausa = {"on": bool(cfg.get("pausado"))}
    sesion_stats = {}
    metricas = {"inicio": time.time(), "solicitudes": 0, "votos": 0,
                "ok": 0, "err": 0, "conflictos": 0, "mesas": {}}

    def m_de(mesa):
        d = metricas["mesas"].setdefault(mesa, {"solicitudes": 0, "votos": 0, "err": 0, "conflictos": 0, "target": None})
        return d

    # ---- monitor: vigila configs, targets y pausa
    def monitor():
        s = new_session()
        abierto_prev = {}
        aviso_prev = {}
        while not stop.is_set():
            cfg = load_config()
            with lock: pausa["on"] = bool(cfg.get("pausado"))
            slugs = cfg.get("slugs") or ["vota"]
            vistas = set()
            for slug in slugs:
                try:
                    st, c = fetch_cfg(s, slug)
                except Exception as e:
                    print("[monitor]", slug, str(e)[:80])
                    with lock:
                        for clave, datos in shared.items():
                            if clave.startswith(slug + "/"):
                                datos["abierto"] = False
                    continue
                abierto = bool(st.get("active")) and st.get("voting_open") is True
                for mesa, v in mesas_de(c):
                    clave = f"{slug}/{mesa}"
                    vistas.add(clave)
                    target, nv = resolver(cfg, slug, mesa)
                    votes, aviso = build_votes(v["contestants"], v.get("maxVotes", 10),
                                               target, cfg.get("spread"), None, nv)
                    titulo = v.get("title") or c.get("nombre") or clave
                    with lock:
                        shared[clave] = {"abierto": abierto, "slug": slug, "mesa": mesa,
                                         "cid": v["configId"], "votes": votes,
                                         "titulo": titulo, "target": target}
                        m_de(clave)["target"] = target
                    if aviso and aviso_prev.get(clave) != aviso:
                        slack("⚠️ Target no encontrado", f"`{clave}`: {aviso}", color="#e0a800")
                    aviso_prev[clave] = aviso
                    if abierto and not abierto_prev.get(clave):
                        n = max(1, min(int(cfg["workers"]), len(accs)))
                        slack("🟢 VOTACIÓN INICIADA", f"*{titulo}*\n`{clave}`", color="#2eb886",
                              campos=[("Concursante", str(target)), ("Cuentas", str(len(accs))),
                                      ("Workers", str(n)), ("Delay", f"{cfg['delay_min']}-{cfg['delay_max']}s")])
                    abierto_prev[clave] = abierto
                # mesas que ya no existen
                for clave in [k for k in list(shared) if k.startswith(slug + "/") and k not in vistas]:
                    with lock: shared.pop(clave, None)
            with lock:
                config_ids_activos = {mesa["cid"] for mesa in shared.values() if mesa.get("cid")}
                for config_id in list(estado):
                    if config_id not in config_ids_activos:
                        estado.pop(config_id, None)
                save_json(STATE, estado)
            stop.wait(load_config()["poll_status_every"])

    # ---- worker
    def trabajador(idx, cuentas):
        sesion = new_session()
        time.sleep(random.uniform(0, 8))
        contador = {}
        while not stop.is_set():
            if pausa["on"]:
                stop.wait(3); continue
            with lock:
                objetivos = [(k, dict(d)) for k, d in shared.items()
                             if d.get("abierto") and d.get("cid") and d.get("votes")]
            if not objetivos:
                stop.wait(3); continue
            for a in cuentas:
                if stop.is_set(): return
                for clave, d in objetivos:
                    if pausa["on"] or stop.is_set(): break
                    if not shared.get(clave, {}).get("abierto"): continue
                    per = a.get("persona", {})
                    sesion.headers["User-Agent"] = per.get("ua", UA_POOL[0])
                    sesion.headers["Accept-Language"] = per.get("lang", LANGS[0])
                    cid = d["cid"]
                    with lock:
                        rnd = estado.setdefault(cid, {}).get(a["im"], 0)
                        maxr = load_config()["max_rounds_per_account"]
                    if maxr and rnd >= maxr: continue
                    phase = "main_done" if rnd % 2 == 0 else "extra_done"
                    fp = f"{a['fp']}-{rnd}" if load_config()["unique_fp"] else a["fp"]
                    try:
                        r = send_vote(sesion, d["slug"], cid, d["votes"], a["im"], phase, rnd, fp)
                    except Exception:
                        stats.add("err"); time.sleep(2); continue
                    nvotos = sum(d["votes"].values())
                    with lock:
                        mm = m_de(clave)
                        mm["solicitudes"] += 1; mm["votos"] += nvotos
                        metricas["solicitudes"] += 1; metricas["votos"] += nvotos
                    if r.status_code == 200:
                        stats.add("ok")
                        with lock:
                            metricas["ok"] += 1
                            estado.setdefault(cid, {})[a["im"]] = rnd + 1
                            e = sesion_stats.setdefault(a["im"], {"email": a["email"], "votos": 0})
                            e["votos"] += nvotos
                        log_voto(clave, a["email"], rnd, "ok")
                        contador[a["im"]] = contador.get(a["im"], 0) + 1
                    elif r.status_code == 409:
                        stats.add("skip")
                        with lock:
                            metricas["conflictos"] += 1; m_de(clave)["conflictos"] += 1
                            estado.setdefault(cid, {})[a["im"]] = rnd + 1
                        log_voto(clave, a["email"], rnd, "409")
                    elif r.status_code == 429:
                        stats.add("skip")
                        with lock: m_de(clave)["err"] += 1
                        stop.wait(float(r.headers.get("Retry-After") or 5))
                    else:
                        stats.add("err")
                        with lock:
                            metricas["err"] += 1; m_de(clave)["err"] += 1
                        log_voto(clave, a["email"], rnd, f"err{r.status_code}")
                    base = random.uniform(load_config()["delay_min"], load_config()["delay_max"])
                    time.sleep(base * per.get("ritmo", 1.0))
                    if load_config()["descansos"] and contador.get(a["im"], 0) >= per.get("descanso_cada", 999):
                        contador[a["im"]] = 0
                        stop.wait(random.randint(per.get("descanso_min", 20), per.get("descanso_max", 120)))

    def guardador():
        while not stop.is_set():
            stop.wait(load_config()["save_every"])
            with lock:
                save_json(STATE, estado)
                save_json(METRICS, metricas)

    def reportador():
        while not stop.is_set():
            stop.wait(load_config()["report_every"])
            with lock: save_json(METRICS, metricas)
            enviar_reporte()

    def apagar(*_):
        print("\ncerrando..."); stop.set()

    signal.signal(signal.SIGINT, apagar); signal.signal(signal.SIGTERM, apagar)

    def http_server():
        port = load_config()["http_port"]
        class H(BaseHTTPRequestHandler):
            def _json(self, code, obj):
                self.send_response(code)
                self.send_header("Content-Type", "application/json"); self.end_headers()
                self.wfile.write(json.dumps(obj, ensure_ascii=False, indent=2).encode())
            def do_GET(self):
                if self.path.startswith("/health"):
                    age = time.time() - (HEART.stat().st_mtime if HEART.exists() else 0)
                    return self._json(200 if age < 120 else 503,
                                      {"ok": age < 120, "latido_hace": round(age, 1)})
                ok, err, skip, dt = stats.resumen()
                with lock:
                    body = {"pausado": pausa["on"], "mesas": {k: v.get("titulo") for k, v in shared.items()},
                            "solicitudes": metricas["solicitudes"], "votos": metricas["votos"],
                            "ok": metricas["ok"], "saltados": skip, "errores": metricas["err"],
                            "conflictos_409": metricas["conflictos"],
                            "votos_por_seg": round(ok / max(dt, 1), 3),
                            "cuentas": len(accs), "uptime": int(dt),
                            "detalle": metricas["mesas"]}
                self._json(200, body)
            def log_message(self, *a): pass
        try: ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
        except Exception as e: print("[http]", e)

    n = max(1, min(int(load_config()["workers"]), len(accs)))
    hilos = [threading.Thread(target=monitor, daemon=True),
             threading.Thread(target=guardador, daemon=True),
             threading.Thread(target=reportador, daemon=True),
             threading.Thread(target=http_server, daemon=True)]
    for i in range(n):
        hilos.append(threading.Thread(target=trabajador, args=(i, accs[i::n]), daemon=True))
    for h in hilos: h.start()

    slack("🚀 votabot arrancado",
          f"slugs=`{','.join(load_config()['slugs'])}` targets=`{load_config()['targets']}` cuentas={len(accs)} workers={n}",
          color="#439fe0")
    print(f"votabot | cuentas={len(accs)} workers={n} | http://localhost:{load_config()['http_port']}/ | Ctrl+C para salir")

    try:
        while not stop.is_set():
            latido(); stop.wait(20)
            ok, err, skip, dt = stats.resumen()
            print(f"[{datetime.now():%H:%M:%S}] {'PAUSADO' if pausa['on'] else 'activo'} | "
                  f"mesas={len(shared)} solicitudes={metricas['solicitudes']} votos={metricas['votos']} "
                  f"err={err} 409={metricas['conflictos']} | {ok/max(dt,1):.2f} v/s")
    except KeyboardInterrupt:
        apagar()
    for h in hilos: h.join(timeout=3)
    with lock:
        save_json(STATE, estado); save_json(METRICS, metricas)
    slack("🛑 votabot detenido",
          f"Solicitudes: {metricas['solicitudes']} · Votos: {metricas['votos']}")
    print("listo.")

# ----------------------------------------------------------------- CLI
if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Votabot: consulta y vota encuestas de vota.tvazteca.com.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Ejemplos:
  python votabot.py encuestas
  python votabot.py encuesta vota
  python votabot.py encuesta https://vota.tvazteca.com/config/vota.json --activar
  python votabot.py vote \"Rafa Mercadante\" 10 --slug vota
  python votabot.py help vote""",
    )
    sub = ap.add_subparsers(dest="cmd", required=True, title="Comandos")

    p = sub.add_parser("login", help="Registra cuentas de Google"); p.add_argument("--headless", action="store_true", help="Usa navegador sin interfaz"); p.set_defaults(f=cmd_login)
    sub.add_parser("list", help="Lista cuentas registradas").set_defaults(f=cmd_list)
    sub.add_parser("encuestas", help="Lista encuestas configuradas y conocidas").set_defaults(f=cmd_encuestas)
    p = sub.add_parser("encuesta", help="Consulta una encuesta por slug o URL"); p.add_argument("encuesta", help="Slug o URL de /config/<slug>.json"); p.add_argument("--activar", action="store_true", help="La configura y deja el bot pausado"); p.set_defaults(f=cmd_encuesta)
    p = sub.add_parser("mesas", help="Lista mesas y concursantes"); p.add_argument("slugs", nargs="*", help="Slugs opcionales"); p.set_defaults(f=cmd_mesas)
    p = sub.add_parser("probe", help="Alias de mesas"); p.add_argument("slugs", nargs="*", help="Slugs opcionales"); p.set_defaults(f=cmd_mesas)
    p = sub.add_parser("check", help="Consulta ronda de cada cuenta"); p.add_argument("slugs", nargs="*", help="Slugs opcionales"); p.set_defaults(f=cmd_check)
    p = sub.add_parser("verify", help="Alias de check"); p.add_argument("slugs", nargs="*", help="Slugs opcionales"); p.set_defaults(f=cmd_check)
    sub.add_parser("status", help="Muestra configuracion y actividad").set_defaults(f=cmd_status)
    sub.add_parser("stats", help="Muestra metricas acumuladas").set_defaults(f=cmd_stats)
    sub.add_parser("report", help="Envia reporte inmediato a Slack").set_defaults(f=cmd_report)
    sub.add_parser("test-slack", help="Prueba el webhook de Slack").set_defaults(f=cmd_test_slack)
    sub.add_parser("on", help="Reanuda la votacion").set_defaults(f=cmd_on)
    sub.add_parser("off", help="Pausa la votacion").set_defaults(f=cmd_off)
    sub.add_parser("duelos", help="Activa vota y lagranjavip").set_defaults(f=cmd_duelos)

    p = sub.add_parser("evento", help="Selecciona una encuesta por alias o slug"); p.add_argument("nombre"); p.set_defaults(f=cmd_evento)

    p = sub.add_parser("vote", help="Define concursante y votos por solicitud")
    p.add_argument("nombre", help="Nombre completo o parcial del concursante")
    p.add_argument("votos", nargs="?", type=int, help="Votos al concursante por solicitud")
    p.add_argument("--slug", help="Solo esta encuesta"); p.add_argument("--mesa", help="Solo esta mesa")
    p.add_argument("--todos", action="store_true", help="Aplica a todas las encuestas configuradas"); p.add_argument("--repartir", action="store_true", help="Reparte votos entre concursantes")
    p.add_argument("--total", type=int, help="Reservado para compatibilidad")
    p.set_defaults(f=cmd_vote)

    p = sub.add_parser("target", help="Alias de vote sin cantidad"); p.add_argument("nombre"); p.add_argument("--slug")
    p.set_defaults(f=cmd_target)

    p = sub.add_parser("run", help="Inicia el demonio de votacion")
    p.add_argument("--workers", type=int, help="Numero de workers"); p.add_argument("--delay-min", type=float, help="Espera minima entre solicitudes")
    p.add_argument("--delay-max", type=float, help="Espera maxima entre solicitudes"); p.add_argument("--http-port", type=int, help="Puerto HTTP de estado")
    p.add_argument("--slugs", help="Slugs separados por comas")
    p.set_defaults(f=cmd_run)

    p = sub.add_parser("help", help="Muestra ayuda general o de un comando")
    p.add_argument("comando", nargs="?", help="Comando del que se quiere ayuda")
    p.set_defaults(f=lambda args: ap.parse_args([args.comando, "--help"]) if args.comando else ap.print_help())

    a = ap.parse_args(); a.f(a)
