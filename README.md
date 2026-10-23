# votabot — Bot de votación La Granja VIP (TV Azteca)

Vota de forma indefinida con múltiples cuentas de Google. Corre en Docker, reporta a Slack.

Consulta [GUIA_VOTABOT.md](GUIA_VOTABOT.md) para conceptos, configuracion y todos los comandos.
Consulta [GUIA_LOGIN_WEB.md](GUIA_LOGIN_WEB.md) para registrar cuentas desde HTTPS en la EC2.

## Arranque

    cp .env.example .env && nano .env      # SLACK_WEBHOOK=...
    python votabot.py login                # registra cuentas (una vez, abre Chrome)
    docker compose up -d --build
    python votabot.py status

## Comandos

### Encuestas
    python votabot.py encuestas                     # lista encuestas configuradas y conocidas
    python votabot.py encuesta vota                 # estado, mesas y concursantes de una encuesta
    python votabot.py encuesta https://vota.tvazteca.com/config/vota.json --activar

`encuesta --activar` selecciona el slug y deja votabot pausado. Después elige el
concursante con `vote` y ejecuta `on`. Puedes pasar cualquier slug publicado por
`vota.tvazteca.com` o su URL de configuración.

    python votabot.py help                          # ayuda con todos los comandos
    python votabot.py help vote                     # ayuda detallada de vote

### Encender / apagar
    python votabot.py on          # reanuda la votación (proceso sigue vivo, sin restart)
    python votabot.py off         # pausa la votación
    docker compose stop votabot   # apagado duro (y `up -d` para prender)

### Votar los 2 duelos indefinidamente
    python votabot.py duelos
Cubre TODAS las mesas de `vota` (Duelo de Nominación + Acompañante del Capataz)
y de `lagranjavip`. El bot lo toma en menos de 30 s, sin reiniciar.

### Elegir concursante y cuántos votos por solicitud
    python votabot.py vote "Rafa Mercadante" 10        # 10 votos/solicitud en todas las mesas activas
    python votabot.py vote "Rafa Mercadante" 6         # 6 a Rafa, los 4 restantes repartidos
    python votabot.py vote "Niurka" 10 --slug vota     # solo en un slug
    python votabot.py vote "Rafa" 10 --mesa voting2    # solo en una mesa concreta
    python votabot.py vote "Rafa" 10 --repartir        # repartir 10 entre todos

El máximo por solicitud lo fija la API (`maxVotes: 10`). Si quieres más de 10
votos al mismo tiempo, se consiguen enviando más solicitudes (el bot ya lo hace
en bucle).

### Reporte de avance (solicitudes y votos)
    python votabot.py report      # manda reporte a Slack AHORA
    python votabot.py stats       # contadores por mesa en consola
Cada solicitud lleva 10 votos. El reporte automático se manda cada 5 min
(`report_every` en config.json).

### Diagnóstico
    python votabot.py mesas       # lista slugs, mesas, configId y concursantes
    python votabot.py check       # fase/ronda registrada por cuenta y mesa
    python votabot.py list        # cuentas y personas
    python votabot.py test-slack  # prueba el webhook

### Cambiar de votación
    python votabot.py evento duelo        # solo lagranjavip
    python votabot.py evento capataz      # solo vota

## Verificar que está votando

    docker compose logs -f --tail=50 votabot    # línea por ciclo con solicitudes/votos
    curl -s localhost:8080/stats | python3 -m json.tool
    tail -f votabot_data/votos.csv              # ok / 409 / err### por solicitud

Si `solicitudes` sube y `errores`/`409` se mantienen bajos: está votando bien.

## Configuracion persistente

La configuracion runtime vive en `votabot_data/config.json`. El archivo raiz
`config.json` solo sirve como plantilla inicial durante el despliegue.

    {
      "slugs": ["vota", "lagranjavip"],
      "targets": { "vota": "Rafa Mercadante" },
      "votos":   { "vota": 10 },
      "spread": false,
      "pausado": false,
      "workers": 4,
      "delay_min": 1.2, "delay_max": 3.5,
      "report_every": 300,
      "unique_fp": true,
      "max_rounds_per_account": 0
    }

`targets` y `votos` aceptan `"slug"` (todas sus mesas) o `"slug/mesa"` (una sola).

## Notas técnicas

- `requireAllVotes: true` → la suma de votos debe ser exactamente `maxVotes`.
  El bot reparte el sobrante automáticamente.
- El payload enviado es idéntico al del sitio oficial:
  `votes, user_im, phase, round, slug, config_id, token, fp`.
- `phase` alterna `main_done` / `extra_done` igual que el cliente real.
