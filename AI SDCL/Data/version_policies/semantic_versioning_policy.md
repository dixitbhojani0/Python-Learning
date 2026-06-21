# Semantic Versioning Policy — Project Antlog

**Version**: 1.0  
**Owner**: Tech Lead  
**Last Updated**: 2026-05-01  
**Status**: ACTIVE

---

## Version Format

All releases follow `MAJOR.MINOR.PATCH` (SemVer 2.0).

- **MAJOR** — breaking change: removed endpoint, changed response schema, renamed required field
- **MINOR** — backward-compatible new feature: new endpoint, new optional field, new query param
- **PATCH** — backward-compatible fix: bug fix, performance improvement, documentation update

---

## What Constitutes a Breaking Change (requires MAJOR bump)

- Removing or renaming any API endpoint
- Removing or renaming a required request field
- Changing the type of any existing field (string → int, object → array)
- Changing HTTP status codes returned by existing endpoints
- Removing a previously supported authentication method
- Changing default behavior that callers depend on

---

## API Change Process

1. Breaking changes require a `migration_guide.md` in the PR
2. Deprecated endpoints must remain functional for minimum 2 sprint cycles (4 weeks)
3. Deprecation notice must be added to API response headers: `X-Deprecated: true, X-Sunset: {date}`
4. All breaking changes require tech lead approval before merge

---

## PR Requirements for Version Bumps

- PR title must include version bump type: `[MAJOR]`, `[MINOR]`, or `[PATCH]`
- `CHANGELOG.md` must be updated in the same PR
- For MAJOR changes: ADR must be created documenting the decision and migration path

---

## Current API Version

`v2` — base path `/api/v2/`. Version `v1` deprecated 2026-03-01, sunset 2026-07-01.
