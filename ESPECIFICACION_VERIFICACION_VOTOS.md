# Especificación de verificación de votos

## Objetivo

Confirmar que votabot detecta una encuesta abierta, envía una solicitud válida,
recibe la respuesta del proveedor y conserva evidencia operativa para revisar
cada ciclo. El panel muestra métricas del proceso, no un conteo global oficial.

## Resultado de la investigación pública

La investigación revisó el sitio público de votación y las publicaciones de TV
Azteca. El proveedor confirma al usuario el registro de una votación en la
pantalla y describe rondas de votos extra. No expone un endpoint público con el
acumulado global por concursante. Por tanto, una respuesta HTTP `200` confirma
que el backend aceptó la solicitud, pero el conteo oficial solo lo confirma la
interfaz o una publicación de TV Azteca.

Fuentes públicas consultadas:

- [Guía oficial de votación y confirmación](https://www.tvazteca.com/aztecauno/la-granja-vip/como-votar-y-que-dias-abren-y-cierran-las-votaciones/)
- [Guía oficial de votos extra](https://www.tvazteca.com/aztecauno/la-granja-vip/se-abren-votaciones-salvar-granjero-favorito-cuarta-semana-la-granja-vip/)
- [Guía oficial de la dinámica El Más Gallito](https://www.tvazteca.com/aztecauno/la-granja-vip/como-votar-por-el-mas-gallito-paso-a-paso/)

## Endpoints confirmados

Base: `https://vota.tvazteca.com`

| Endpoint | Uso | Evidencia que entrega |
|---|---|---|
| `GET /config/{slug}-status.json` | Estado de una encuesta | `active`, `voting_open`, versión de configuración |
| `GET /config/{slug}.json` | Mesas y participantes | `configId`, concursantes, máximo de votos y reglas |
| `GET /api/vote-token?fp={fp}` | Token de un envío | Token requerido por el proveedor |
| `POST /api/vote-public` | Registro del voto | `200` aceptado, `409` ronda ya usada, `429` límite de frecuencia |
| `GET /api/user-state?user_im={im}&config_id={cid}` | Estado de una cuenta | Fase y ronda del usuario en esa mesa |

## Criterios de aceptación

1. `status.json` devuelve `active=true` y `voting_open=true`.
2. La configuración contiene una mesa habilitada y el concursante elegido.
3. El envío recibe `200` y el bot incrementa `ok`, `solicitudes` y `votos`.
4. El registro local añade una línea `ok` en `votabot_data/votos.csv`.
5. El estado local incrementa la ronda de la cuenta en `votabot_data/state.json`.
6. El panel refleja los contadores en un máximo de cinco segundos.
7. Un `409` incrementa `conflictos_409` y avanza la ronda sin contar un voto
   aceptado. Un `429` registra el límite y respeta `Retry-After`.

## Protocolo operativo

1. Ejecuta `python votabot.py encuesta vota` y confirma participantes y mesa.
2. Selecciona el objetivo con `python votabot.py vote "Manelyk" 10 --slug vota`.
3. Ejecuta `python votabot.py on` y espera un ciclo de quince segundos.
4. Consulta `python votabot.py stats` y el panel HTTPS.
5. Revisa `votabot_data/votos.csv` y `votabot_data/metricas.json`.
6. Confirma visualmente el mensaje de votos registrados en la web oficial con
   una cuenta autorizada.
7. Ejecuta `python votabot.py off` al terminar la validación.

## Límites verificables

- `200` es la confirmación técnica de recepción del backend.
- El API público no entrega el total acumulado de todos los usuarios.
- El bot no inventa resultados ni calcula una clasificación global.
- El panel muestra la actividad y los resultados de las solicitudes de tus
  cuentas, con actualización automática cada cinco segundos.

## Próximas mejoras recomendadas

1. Historial por cuenta anónima con hora, código HTTP y ronda.
2. Gráfica de votos aceptados por minuto y alertas cuando aumenten `429` o
   errores.
3. Botón de pausa y reanudación protegido por contraseña en el panel.
4. Exportación CSV de métricas sin datos personales.
5. Prueba de salud que alerte cuando una encuesta cierre o cambie de `configId`.
