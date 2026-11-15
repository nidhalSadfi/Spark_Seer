# One image for the API and the worker (docker-compose.prod.yml picks the command).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# requirements-worker.txt = requirements.txt + CrewAI (the API never imports CrewAI, spec I1).
COPY requirements.txt requirements-worker.txt ./
RUN pip install -r requirements-worker.txt

COPY . .
# Data (parquet, masks, world dataset, raw FIRMS responses) is mounted at /app/data at run time.
EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
