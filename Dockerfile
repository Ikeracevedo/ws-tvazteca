FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt requirements-login.txt .
RUN pip install --no-cache-dir -r requirements.txt -r requirements-login.txt \
    && playwright install --with-deps chromium
COPY . .
HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
  CMD python -c "import time,pathlib,sys;p=pathlib.Path('votabot_data/heartbeat.txt');sys.exit(0 if p.exists() and time.time()-p.stat().st_mtime<150 else 1)"
CMD ["python", "votabot.py", "run"]
