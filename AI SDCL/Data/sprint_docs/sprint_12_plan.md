# Sprint 12 Planning — Project Antlog
**Sprint Duration:** 2026-05-26 to 2026-06-05  
**Team:** Alice (Backend), Bob (Frontend), Charlie (DevOps), Diana (QA)  
**Sprint Goal:** Complete dashboard feature and payment gateway integration

---

## Features In This Sprint

### 1. Dashboard API Feature
- **Ticket:** SDLC-1038
- **Assigned:** Alice (Backend Developer)
- **Status:** IN PROGRESS
- **Description:** Build `/api/v2/dashboard` endpoint returning real-time metrics
- **Progress:** 60% complete. API logic done. Integration tests pending.
- **Blocker:** `/api/v2/users` returning 500 after nginx config change on May 28

### 2. Payment Gateway Integration
- **Ticket:** SDLC-1031
- **Assigned:** Bob (Frontend Developer)
- **Status:** BLOCKED
- **Description:** Integrate Stripe payment gateway for checkout flow
- **Blocker:** Vendor SSL certificate renewal pending since May 20. Stripe sandbox not reachable.
- **Impact:** Cannot test payment flows until certificate is renewed

### 3. User Authentication Refactor
- **Ticket:** SDLC-1025
- **Assigned:** Alice (Backend Developer)
- **Status:** DONE
- **Completed:** 2026-05-27
- **Notes:** JWT refresh token logic updated. All tests passing.

### 4. CI/CD Pipeline Update
- **Ticket:** SDLC-1040
- **Assigned:** Charlie (DevOps)
- **Status:** IN PROGRESS
- **Description:** Migrate from Jenkins to GitHub Actions
- **Progress:** 80% complete. Dev and staging pipelines working. Production pending.

---

## Sprint Health

| Metric | Value |
|---|---|
| Total tickets | 8 |
| Done | 2 |
| In Progress | 3 |
| Blocked | 2 |
| Not Started | 1 |
| Completion % | 25% |
| Days remaining | 2 |
| Risk level | HIGH |

---

## Identified Risks

1. **Dashboard blocked by nginx issue** — API 500 errors reported May 28. No ticket created yet.
2. **Payment gateway vendor unresponsive** — Cert renewal request sent May 20, no response after 11 days.
3. **Sprint velocity below target** — Only 25% done with 2 days left. Descoping likely needed.

---

## Previous Sprint Reference (Sprint 11)

- Sprint 11 velocity: 18 story points delivered out of 22 planned
- Sprint 11 blockers: AWS credential rotation caused 1-day delay
- Auth service issue in Sprint 11 was resolved by nginx header fix (see ADR-001)
