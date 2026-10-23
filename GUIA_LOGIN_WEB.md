# Registro web de cuentas

Este panel registra cuentas directamente desde el navegador y guarda
`votabot_data/accounts.json` en la EC2. Ese archivo no se sube a Git.

## 1. Elastic IP y grupo de seguridad

1. En AWS EC2, asigna una Elastic IP y asígnala a la instancia.
2. En el Security Group de la instancia, permite entrada TCP `80` y `443` desde
   `0.0.0.0/0` e `::/0`.
3. Mantén SSH (`22`) limitado a tu IP.

## 2. DuckDNS

1. Crea un subdominio en [DuckDNS](https://www.duckdns.org), por ejemplo
   `tvazteca.duckdns.org`.
2. En DuckDNS, actualiza el registro con la Elastic IP de tu instancia.
3. Espera a que este comando desde tu ordenador devuelva la Elastic IP:

```powershell
nslookup tvazteca.duckdns.org
```

Con Elastic IP el registro no cambia mientras mantengas esa IP asignada.

## 3. Cliente OAuth de Google

1. Abre Google Cloud Console y selecciona o crea un proyecto.
2. Configura la pantalla de consentimiento OAuth y añade tu correo como usuario
   de prueba mientras la aplicación siga en modo Testing.
3. Crea `Credentials` → `Create Credentials` → `OAuth client ID` → `Web application`.
4. Añade esta URI de redirección autorizada, sustituyendo el dominio:

```text
https://tvazteca.duckdns.org/auth/google/callback
```

5. Copia el Client ID y Client Secret. La URI debe coincidir exactamente con el
   dominio, esquema HTTPS y ruta configurados.

## 4. Variables de entorno

En la EC2, dentro del repositorio, crea o edita `.env`:

```dotenv
SLACK_WEBHOOK=
VOTABOT_DOMAIN=tvazteca.duckdns.org
GOOGLE_OAUTH_CLIENT_ID=pega_aqui_el_client_id
GOOGLE_OAUTH_CLIENT_SECRET=pega_aqui_el_client_secret
VOTABOT_ADMIN_PASSWORD=elige_una_contrasena_larga_y_unica
VOTABOT_SESSION_SECRET=genera_un_secreto_aleatorio_de_64_caracteres
```

Genera `VOTABOT_SESSION_SECRET` con:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Protege el archivo:

```bash
chmod 600 .env
```

## 5. Caddy y HTTPS

`docker-compose.yml` arranca Caddy automáticamente. `Caddyfile` recibe
`VOTABOT_DOMAIN`, obtiene y renueva el certificado HTTPS, y redirige el tráfico
al servicio privado `login` dentro de Docker.

Después de configurar DNS y `.env`, despliega:

```bash
git pull --ff-only
sudo docker compose up -d --build
sudo docker compose logs -f caddy login
```

Abre en el navegador:

```text
https://tvazteca.duckdns.org/login
```

Inicia sesión con `VOTABOT_ADMIN_PASSWORD`, pulsa **Registrar cuenta con
Google**, completa Google y vuelve al panel. Repite el botón para cada cuenta:
el panel conserva todas las cuentas y solo reemplaza una existente cuando el
correo es el mismo. Las cuentas quedan guardadas en `votabot_data/accounts.json`
dentro de la EC2.

## Operación

```bash
sudo docker compose ps
sudo docker compose logs -f login
sudo docker compose logs -f caddy
sudo docker compose restart login
```

No publiques el puerto `8081`: solo Caddy debe exponer el panel mediante HTTPS.

## Activar una cuenta para votar

Cada cuenta nueva queda en estado **Pendiente**. En el panel pulsa **Iniciar a
votar** junto al correo que quieres usar. El panel envía a Slack el mensaje
"Ya estamos trabajando con la nueva cuenta" y `votabot` incorpora esa cuenta
en el siguiente ciclo de consulta, sin reiniciar el contenedor.

La misma pantalla actualiza cada cinco segundos las cuentas activas, los votos
enviados, respuestas aceptadas, rechazadas, conflictos `409` y las mesas de la
encuesta configurada. Las cuentas existentes antes de esta actualización se
mantienen activas para no interrumpir una votación que ya estaba en curso.
