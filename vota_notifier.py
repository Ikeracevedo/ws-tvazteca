"""Avisador de votación La Granja VIP.

Consulta el estado público de la votación y te notifica (Windows y/o Telegram)
cuando está abierta, con un intervalo mínimo entre avisos. El voto lo emites tú.
"""
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).parent
CONFIG = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
STATE_FILE = BASE / "state.json"


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state), encoding="utf-8")


def fetch_status() -> dict:
    req = urllib.request.Request(
        CONFIG["status_url"], headers={"User-Agent": "Mozilla/5.0 vota-notifier"}
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def notify_windows(title: str, message: str) -> None:
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$n = New-Object System.Windows.Forms.NotifyIcon;"
        "$n.Icon = [System.Drawing.SystemIcons]::Information;"
        "$n.Visible = $true;"
        f"$n.ShowBalloonTip(10000, '{title}', '{message}', 'Info');"
        "Start-Sleep -Seconds 11; $n.Dispose()"
    )
    subprocess.Popen(
        ["powershell", "-NoProfile", "-Command", ps],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def notify_telegram(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        return
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    urllib.request.urlopen(
        f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=20
    )


def notify_slack(text: str) -> None:
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        print("SLACK_WEBHOOK_URL no definida; se omite Slack")
        return
    req = urllib.request.Request(
        url,
        data=json.dumps({"text": text}).encode(),
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req, timeout=20)


def in_quiet_hours(now: datetime) -> bool:
    start, end = CONFIG["quiet_hours"]  # p.ej. [0, 7]
    return start <= now.hour < end


def check_once() -> None:
    now = datetime.now()
    state = load_state()
    try:
        status = fetch_status()
    except Exception as exc:  # red caída, etc.
        print(f"[{now:%H:%M}] error consultando estado: {exc}")
        return

    is_open = bool(status.get("voting_open"))
    print(f"[{now:%H:%M}] voting_open={is_open}")
    if not is_open:
        state["last_open"] = False
        save_state(state)
        return
    if in_quiet_hours(now):
        return

    last = state.get("last_notified")
    min_gap = timedelta(hours=CONFIG["notify_every_hours"])
    just_opened = not state.get("last_open", False)
    if not just_opened and last and now - datetime.fromisoformat(last) < min_gap:
        return

    msg = "Votación abierta en La Granja VIP. Entra y vota por tu favorito."
    if "windows" in CONFIG["channels"]:
        notify_windows("La Granja VIP", msg)
    if "telegram" in CONFIG["channels"]:
        notify_telegram(f"{msg}\n{CONFIG['vote_url']}")
    if "slack" in CONFIG["channels"]:
        notify_slack(f":ballot_box_with_ballot: {msg}\n<{CONFIG['vote_url']}|Votar ahora>")
    if CONFIG.get("open_browser"):
        webbrowser.open(CONFIG["vote_url"])
    state.update(last_notified=now.isoformat(), last_open=True)
    save_state(state)


def main() -> None:
    if "--test" in sys.argv:  # prueba de canales sin esperar votación
        if "slack" in CONFIG["channels"]:
            notify_slack("Prueba del avisador de La Granja VIP :white_check_mark:")
        return
    if "--once" in sys.argv:  # para Programador de tareas de Windows
        check_once()
        return
    while True:
        check_once()
        time.sleep(CONFIG["poll_seconds"])


if __name__ == "__main__":
    main()
