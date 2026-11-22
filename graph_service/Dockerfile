# Servicio de grafo (Graphiti + FalkorDB) aislado del backend: Graphiti exige neo4j>=5.26 y OASIS fija neo4j==5.23.0.
FROM python:3.12.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
ENV GRAPHITI_TELEMETRY_ENABLED=false PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
