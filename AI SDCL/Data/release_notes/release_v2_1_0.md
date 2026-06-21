# Release Notes — v2.1.0

**Release Date**: 2026-05-15  
**Type**: MINOR  
**Released By**: Charlie (DevOps)  
**Status**: RELEASED

---

## Summary

Dashboard API improvements and auth service stability fixes. No breaking changes.

---

## New Features

- `GET /api/v2/dashboard/summary` — new endpoint returning aggregated project metrics
- User role field added to JWT response (optional, backward-compatible)
- Redis connection pool size configurable via `REDIS_MAX_CONNECTIONS` env var

---

## Bug Fixes

- Fixed intermittent 500 errors on `/api/v2/users` under high load (DB connection pool exhaustion)
- Fixed CORS headers missing on preflight requests to `/api/v2/auth`
- Fixed nginx timeout causing 502 errors on requests > 30 seconds

---

## Infrastructure Changes

- Redis upgraded from 7.2 to 7.4-alpine
- Qdrant collection reindexed after embedding model update (all-MiniLM-L6-v2 → v2)
- nginx keepalive timeout increased from 15s to 30s

---

## Known Issues

- Payment gateway webhook occasionally times out under load (tracking: SDLC-1031)
- Dashboard summary endpoint has 400ms p99 latency — optimization planned for v2.2.0

---

## Rollback Plan

If critical issues found: `git revert v2.1.0 && docker compose up -d && scripts/ingest.py --no-llm`
