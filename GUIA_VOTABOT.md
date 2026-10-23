# Guía de votabot

## Idea principal

Votabot consulta las encuestas de `vota.tvazteca.com`. Antes de enviar votos,
el monitor confirma que la encuesta está activa y que su estado es abierto. Si
la API informa que está cerrada, inactiva o no responde, esa encuesta se pausa
y no se programan más votos para ella.

## Encuesta, slug y mesa

- **Encuesta**: un evento de votación completo. Por ejemplo, `vota` y
  `lagranjavip` son encuestas distintas.
- **Slug**: el identificador corto de una encuesta en la URL. En
  `https://vota.tvazteca.com/config/vota.json`, el slug es `vota`.
- **Mesa**: una sección de votación dentro de una encuesta. Una encuesta puede
  tener `voting`, `voting2`, `voting3` y más mesas. Cada mesa tiene sus propios
  concursantes, `configId` y máximo de votos por solicitud.

Ejemplo: `vota/voting` significa la mesa `voting` dentro de la encuesta `vota`.

## Encuestas disponibles

Ejecuta este comando para consultar el estado real:

```powershell
python votabot.py encuestas
```

Estado confirmado durante esta sesión:

```text
vota        ABIERTA  mesas=1
lagranjavip cerrada  mesas=1
```

`vota ABIERTA mesas=1` significa que la encuesta `vota` acepta votos y tiene
una mesa activa. `lagranjavip cerrada mesas=1` significa que tiene una mesa
configurada, pero no acepta votos ahora. El bot no vota en una encuesta cerrada.

No existe una API que enumere automáticamente todos los slugs del proveedor.
`encuestas` lista los slugs configurados en votabot y los conocidos. Para
consultar cualquier encuesta del proveedor, indica su slug o URL de
configuración.

## Consultar y seleccionar una encuesta

```powershell
python votabot.py encuesta vota
python votabot.py encuesta https://vota.tvazteca.com/config/vota.json
```

Ambos comandos muestran el estado, las mesas y los concursantes. No cambian la
configuración.

```powershell
python votabot.py encuesta vota --activar
```

`--activar` guarda `vota` como la única encuesta configurada y deja el bot
pausado. Después define el concursante y reanuda:

```powershell
python votabot.py vote "Rafa Mercadante" 10 --slug vota
python votabot.py on
```

La pausa evita empezar a votar por el primer concursante de una encuesta nueva
antes de que selecciones un target.

## Una o dos encuestas al mismo tiempo

Votabot admite varias encuestas: cada worker recorre las mesas abiertas de cada
slug configurado. Para usar dos encuestas:

```powershell
python votabot.py duelos
```

Este modo configura `vota` y `lagranjavip`. Solo vota en las que estén abiertas.
Dos encuestas duplican las solicitudes por ciclo y comparten las mismas cuentas.
Para un ritmo moderado, usa una sola encuesta; usa dos solo cuando necesitas
cubrir dos eventos abiertos a la vez.

## Configuración recomendada: un concursante, todos los votos

Guarda este contenido completo en `votabot_data/config.json`. Cambia
`Rafa Mercadante` por el concursante que aparezca en `python votabot.py encuesta vota`.

```json
{
  "slugs": ["vota"],
  "targets": {
    "vota": "Rafa Mercadante"
  },
  "votos": {
    "vota": 10
  },
  "spread": false,
  "pausado": false,
  "workers": 2,
  "delay_min": 4.0,
  "delay_max": 8.0,
  "unique_fp": false,
  "poll_status_every": 15,
  "save_every": 60,
  "report_every": 300,
  "descansos": true,
  "http_port": 8080,
  "max_rounds_per_account": 0,
  "slack_webhook": null
}
```

`votos: 10` entrega los diez votos de cada solicitud al concursante definido.
`workers: 2` y esperas de 4 a 8 segundos mantienen un ritmo moderado. Los
descansos están activos. Ninguna configuración garantiza que un proveedor no
identifique automatización; esta reduce la frecuencia de solicitudes y no
modifica el estado de una encuesta cerrada.

## Comandos

| Comando | Función |
|---|---|
| `python votabot.py help` | Muestra todos los comandos y ejemplos. |
| `python votabot.py help vote` | Muestra las opciones de `vote`. |
| `python votabot.py login` | Registra cuentas de Google con navegador visible. |
| `python votabot.py list` | Lista las cuentas registradas. |
| `python votabot.py encuestas` | Lista encuestas configuradas y conocidas con su estado. |
| `python votabot.py encuesta <slug-o-url>` | Muestra estado, mesas y concursantes sin cambiar la configuración. |
| `python votabot.py encuesta <slug-o-url> --activar` | Configura una encuesta y pausa el bot para elegir target. |
| `python votabot.py mesas [slug ...]` | Muestra mesas y concursantes de las encuestas indicadas. |
| `python votabot.py probe [slug ...]` | Alias de `mesas`. |
| `python votabot.py vote "Nombre" 10 --slug vota` | Define concursante y votos por solicitud. |
| `python votabot.py target "Nombre" --slug vota` | Alias de `vote` sin indicar cantidad. |
| `python votabot.py evento <alias-o-slug>` | Selecciona una encuesta por alias (`capataz`, `duelo`) o slug. |
| `python votabot.py duelos` | Configura `vota` y `lagranjavip`. |
| `python votabot.py on` | Reanuda el envío de votos. |
| `python votabot.py off` | Pausa el envío de votos sin detener el proceso. |
| `python votabot.py status` | Muestra configuración, cuentas, latido y estado. |
| `python votabot.py stats` | Muestra solicitudes, votos, errores y métricas por mesa. |
| `python votabot.py check [slug ...]` | Consulta fase y ronda de cada cuenta. |
| `python votabot.py verify [slug ...]` | Alias de `check`. |
| `python votabot.py report` | Envía un reporte inmediato a Slack. |
| `python votabot.py test-slack` | Comprueba el webhook de Slack. |
| `python votabot.py run` | Inicia votabot manualmente fuera de Docker. |

## Flujo recomendado

```powershell
python votabot.py encuestas
python votabot.py encuesta vota --activar
python votabot.py vote "Rafa Mercadante" 10 --slug vota
python votabot.py on
python votabot.py status
```

El comando más importante es `encuesta <slug-o-url> --activar`: selecciona una
encuesta nueva de forma segura, la deja pausada y permite revisar concursantes
antes de votar.
