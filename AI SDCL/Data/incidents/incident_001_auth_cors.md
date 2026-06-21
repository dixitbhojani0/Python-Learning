# Incident Report — INC-001: CORS Error on Auth Service

**Date**: 2026-05-25  
**Severity**: HIGH  
**Duration**: 2h 15m  
**Affected Service**: `/api/v2/auth`  
**Reported By**: Alice (Backend Dev)  
**Resolved By**: Charlie (DevOps)  
**Status**: RESOLVED

---

## Timeline

| Time | Event |
|------|-------|
| 10:00 | Alice reports 500 errors on `/api/v2/users` in Slack #backend |
| 10:05 | Charlie identifies nginx config change deployed at 09:45 |
| 10:15 | Error isolated to missing `Access-Control-Allow-Origin` CORS header |
| 10:30 | Root cause confirmed: nginx upstream keepalive config broke CORS preflight |
| 11:30 | Fix deployed: nginx CORS headers restored in `nginx.conf` |
| 12:15 | All services confirmed healthy, monitoring for 30 minutes |

---

## Root Cause

Nginx configuration update at 09:45 removed the `add_header Access-Control-Allow-Origin *` directive from the upstream proxy block. This caused all preflight CORS requests to `/api/v2/auth` to return 500 instead of 200 with the required headers.

Secondary issue: DB connection pool was set to 5 connections — exhausted under the load caused by retry storms from frontend clients hitting the CORS error.

---

## Resolution

1. Restored CORS headers in nginx config (see ADR-001 for nginx standards)
2. Increased DB connection pool from 5 to 20 (`DB_POOL_SIZE=20` in `.env`)
3. Added nginx CORS config to deployment checklist to prevent regression

---

## Lessons Learned

- Nginx config changes must be reviewed against ADR-001 before deploy
- DB connection pool size should be monitored — alert when utilization > 80%
- CORS errors trigger frontend retry storms — add exponential backoff to frontend HTTP client

---

## Tickets Created

- SDLC-1038: nginx CORS header regression — RESOLVED
- SDLC-1039: DB connection pool monitoring alert — IN PROGRESS
