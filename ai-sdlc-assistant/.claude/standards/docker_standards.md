# Docker Standards — AI SDLC Assistant

Rules for Docker Compose, Dockerfiles, service configuration, and what runs where.

---

## Development vs Production Architecture

| Component | Development | Production |
|-----------|-------------|------------|
| Backend (FastAPI) | Local Python (`uvicorn --reload`) | Docker container |
| Frontend (Chainlit) | Local Python (`chainlit run`) | Docker container |
| Admin (Streamlit) | Local Python (`streamlit run`) | Docker container |
| Qdrant | Docker container | Docker container |
| Redis | Docker container | Docker container |

**Why**: In development, Python apps run locally so you get instant hot-reload without rebuilding images. Only infrastructure (Qdrant, Redis) runs in Docker. In production, everything containerizes.

---

## Service Naming Convention

All services prefixed with `sdlc-`:

```yaml
services:
  sdlc-qdrant:
    container_name: sdlc-qdrant
    ...
  sdlc-redis:
    container_name: sdlc-redis
    ...
  sdlc-backend:      # production only
    container_name: sdlc-backend
    ...
```

---

## docker-compose.yml — Development (Current)

Only Qdrant and Redis:

```yaml
version: "3.9"

services:
  sdlc-qdrant:
    image: qdrant/qdrant:v1.14.0    # pin exact version — never use :latest
    container_name: sdlc-qdrant
    ports:
      - "6333:6333"   # REST API
      - "6334:6334"   # gRPC
    volumes:
      - ./data/qdrant_storage:/qdrant/storage
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:6333/health"]
      interval: 10s
      timeout: 5s
      retries: 3
    restart: unless-stopped

  sdlc-redis:
    image: redis:7.4-alpine           # pin exact version
    container_name: sdlc-redis
    ports:
      - "6379:6379"
    command: redis-server --maxmemory 256mb --maxmemory-policy allkeys-lru --save 60 1
    volumes:
      - ./data/redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 3
    restart: unless-stopped
```

---

## Required Rules for Every Service

1. **Pin image versions** — never `image: redis:latest`. If Redis releases a breaking change, your app breaks silently.

2. **Always define healthchecks** — without them, Docker has no way to know if the service is actually ready (just that the container started).

3. **Named volumes for persistence** — use `./data/{service}_storage` paths so data survives container restarts.

4. **`restart: unless-stopped`** — services restart automatically after a crash but stop cleanly when you run `docker-compose down`.

5. **Expose only needed ports** — Qdrant needs 6333 (REST). If you don't use gRPC, don't expose 6334.

---

## Port Conventions

| Service | Port | Purpose |
|---------|------|---------|
| Qdrant | 6333 | REST API (used by `qdrant-client`) |
| Qdrant | 6334 | gRPC (optional — only expose if used) |
| Redis | 6379 | Default Redis port |
| FastAPI backend | 8000 | API server |
| Chainlit frontend | 8080 | Chat UI |
| Streamlit admin | 8501 | Admin panel |

---

## Environment Variables — Never in Dockerfile

Secrets and config go in `.env`, referenced via `env_file` in docker-compose:

```yaml
services:
  sdlc-backend:
    build: ./docker/Dockerfile.backend
    env_file:
      - .env       # loads all vars from .env file
```

**Never**:
```dockerfile
# WRONG — secret in Dockerfile
ENV GROQ_API_KEY=gsk_...
```

---

## Volume Mounts — Rules

- **Data volumes**: Use relative paths under `./data/` (committed to .gitignore)
- **Source mounts** (development only): Mount source for hot-reload
- **Never mount** `.env` file, `.git/`, or `__pycache__/`

```yaml
volumes:
  - ./data/qdrant_storage:/qdrant/storage   # ✅ data persistence
  - ./backend:/app/backend                   # ✅ dev source mount (backend only)
  - ./.env:/app/.env                          # ❌ never do this
```

---

## Docker Commands Reference

```bash
# Start infra (Qdrant + Redis) — use this in development
docker-compose up -d

# Check services are healthy
docker-compose ps

# View logs
docker-compose logs -f sdlc-qdrant
docker-compose logs -f sdlc-redis

# Stop and remove containers (keeps volumes)
docker-compose down

# Stop and remove containers AND volumes (clears all data — use for fresh start)
docker-compose down -v

# Check Qdrant health manually
curl http://localhost:6333/health

# Check Redis health manually
docker exec sdlc-redis redis-cli ping
```

---

## .gitignore Rules for Docker Data

These paths must be in `.gitignore` (already are — do not remove):

```
data/qdrant_storage/
data/redis_data/
```

Data stored by Qdrant and Redis is generated at runtime and should never be committed.

---

## Production Dockerfile Pattern (Future — Phase not yet reached)

When containerizing the backend in production:

```dockerfile
# docker/Dockerfile.backend
FROM python:3.11-slim AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM python:3.11-slim AS runtime

WORKDIR /app
# Copy only installed packages from builder
COPY --from=builder /usr/local/lib/python3.11 /usr/local/lib/python3.11
COPY --from=builder /usr/local/bin /usr/local/bin
# Copy source
COPY backend/ ./backend/
COPY config/ ./config/

# Non-root user for security
RUN useradd -m appuser && chown -R appuser /app
USER appuser

EXPOSE 8000
CMD ["uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Multi-stage build keeps the final image small (no build tools in production image).
