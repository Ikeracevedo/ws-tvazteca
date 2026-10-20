# Avisador La Granja VIP

Te avisa cuando la votación está abierta, máximo 1 aviso cada `notify_every_hours` horas.
No vota por ti: la página exige login (Google/correo) y el voto lo haces tú desde el celular.

## Uso
```
python vota_notifier.py          # bucle continuo
python vota_notifier.py --once   # una consulta (para Programador de tareas)
```

## Config (`config.json`)
- `notify_every_hours`: 1, 3, etc.
- `quiet_hours`: `[0, 7]` = sin avisos de 00:00 a 07:00.
- `channels`: `windows`, `telegram`.

## Slack (canal activo por defecto)
1. https://api.slack.com/apps → Create New App → From scratch → Incoming Webhooks → On → Add New Webhook to Workspace → elige tu canal/DM.
2. Copia la URL y guárdala como variable de usuario (no en el código):
   `setx SLACK_WEBHOOK_URL "https://hooks.slack.com/services/..."`
3. Abre una terminal nueva y prueba: `python vota_notifier.py --test`

## Telegram (opcional, agrega "telegram" a `channels`)
1. Crea un bot con @BotFather y copia el token.
2. Escríbele al bot y obtén tu chat id (https://api.telegram.org/bot<TOKEN>/getUpdates).
3. Define variables de entorno `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID`.

## Programarlo cada 10 min (PowerShell)
```
schtasks /Create /SC MINUTE /MO 10 /TN "VotaNotifier" /TR "python C:\Users\Iker\Documents\Personal\ws-tvazteca\vota_notifier.py --once"
```

Fuente de estado: `/config/vota-status.json` (campo `voting_open`).
