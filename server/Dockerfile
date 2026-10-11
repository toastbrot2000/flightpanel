FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY flightpanel ./flightpanel
ENV DATA_DIR=/data HTTP_PORT=8080 PYTHONUNBUFFERED=1
VOLUME ["/data"]
EXPOSE 8080
# /ok antwortet 503 mit Gründen, wenn etwas nicht stimmt (alte Daten, ESP32 weg, Fehler im Code)
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/ok', timeout=4)"
CMD ["python", "-m", "flightpanel.main"]
