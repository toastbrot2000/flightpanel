# Basis-Image fest per Digest; ein neueres übernimmt tools/escrow.py erst nach der Wartezeit
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /data && chown 1000:1000 /data
WORKDIR /app
COPY requirements.lock .
# Nur die Versionen und Dateien aus dem Lock (tools/escrow.py), geprüft per Prüfsumme
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY flightpanel ./flightpanel
ENV DATA_DIR=/data HTTP_PORT=8080 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# Läuft ohne Rechte: nur /data (und /tmp, siehe compose.yml) sind beschreibbar
USER 1000:1000
VOLUME ["/data"]
EXPOSE 8080
# /ok antwortet 503 mit Gründen, wenn etwas nicht stimmt (alte Daten, ESP32 weg, Fehler im Code)
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/ok', timeout=4)"
CMD ["python", "-m", "flightpanel.main"]
