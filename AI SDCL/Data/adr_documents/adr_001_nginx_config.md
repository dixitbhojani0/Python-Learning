# ADR-001: Nginx Reverse Proxy Configuration
**Date:** 2026-04-15  
**Status:** ACTIVE  
**Author:** Charlie (DevOps)  
**Reviewers:** Alice, Diana

---

## Context

The Antlog backend services (`auth-service`, `dashboard-api`, `payment-service`) are deployed
behind an nginx reverse proxy. All incoming traffic from the React frontend passes through nginx
before reaching any backend service. This ADR documents the nginx configuration decisions and
known failure modes.

---

## Decision

Use nginx as reverse proxy with:
- Upstream keep-alive connections (max 32 per upstream)
- Connection timeout: 30s
- Read/write timeout: 60s
- CORS headers set at nginx level (not per-service)

---

## CORS Configuration

Nginx adds these headers on every response:

```nginx
add_header 'Access-Control-Allow-Origin' '$http_origin' always;
add_header 'Access-Control-Allow-Methods' 'GET, POST, PUT, DELETE, OPTIONS' always;
add_header 'Access-Control-Allow-Headers' 'Authorization, Content-Type, X-Token' always;
```

### Known Issue: CORS headers drop after nginx reload

When nginx config is reloaded (`nginx -s reload`), CORS headers may temporarily disappear
for 5-15 seconds while worker processes restart. This causes CORS errors on the frontend.

**Resolution procedure:**
1. Run `nginx -s reload`
2. Wait 10 seconds for worker restart
3. Hit the affected endpoint and verify `Access-Control-Allow-Origin` header is present
4. If still missing, run `nginx -t` to check config validity

**Past incident:** May 25, 2026 — Alice reported CORS error on `/api/v2/auth` after DevOps
ran nginx config change. Resolved by Charlie running `nginx -s reload` and verifying headers.
Ticket: SDLC-0987 (RESOLVED).

---

## DB Connection Pool Configuration

The auth-service uses PostgreSQL with SQLAlchemy connection pool.

```yaml
# db.yaml (auth-service config)
pool_size: 20           # normal load
max_overflow: 10        # burst capacity
pool_timeout: 30        # seconds before giving up
pool_recycle: 3600      # recycle connections every hour
```

### Known Issue: Pool exhaustion under high load

Symptoms: `TimeoutError: QueuePool limit of size 20 overflow 10 reached`
Root cause: High concurrent requests during load test exceed pool capacity.

**Resolution:**
1. Check current connections: `SELECT count(*) FROM pg_stat_activity WHERE datname='antlog';`
2. If > 25 active connections, increase pool_size to 50 in db.yaml
3. Restart auth-service: `docker compose restart auth-service`
4. Monitor: pool exhaustion should stop within 2 minutes

**Past incident:** May 28, 2026 — Charlie (BE dev) confirmed pool exhaustion in #backend Slack.
Appears correlated with nginx config change that triggered connection reset.

---

## Consequences

- CORS management is centralized — good for consistency, risky during reloads
- Pool exhaustion risk exists under load test conditions — mitigated by overflow setting
- Any nginx change must be followed by header verification (added to DevOps runbook)

---

## Related Decisions

- ADR-002: JWT authentication strategy
- ADR-003: Docker networking configuration
