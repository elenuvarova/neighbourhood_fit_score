# Stage 1: build frontend
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: runtime — light Python, NO heavy geo libs (those live in pipeline/ only)
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=3001
WORKDIR /app/backend
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ .
COPY --from=frontend-build /app/frontend/dist ./public/

# Run as a non-root user. Own /app so seed.py can write the SQLite fallback
# (local/dev) and uvicorn can read the app + built SPA at runtime.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 3001

# Probe the app's own health route. We use python3 (always present in
# python:3.12-slim) rather than wget/curl, which are NOT in the slim image.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python3 -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','3001')+'/api/health', timeout=4).status==200 else 1)" || exit 1

CMD ["sh", "-c", "python seed.py && exec uvicorn app.main:app --host 0.0.0.0 --port $PORT"]
