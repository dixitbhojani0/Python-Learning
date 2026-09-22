-- Runs once, on first container initialization (docker-entrypoint-initdb.d).
--
-- Why this file exists: the POSTGRES_USER in docker-compose.yml ("platform")
-- is created as a Postgres SUPERUSER by the official postgres image.
-- Superusers bypass Row-Level Security unconditionally — even with
-- FORCE ROW LEVEL SECURITY set on a table — so the application must never
-- connect as that role at runtime, or the tenant-isolation guarantee this
-- whole platform depends on (§J) silently does nothing.
--
-- "platform" (superuser) still owns the tables and runs migrations (DDL
-- needs elevated privileges anyway). "platform_app" is what the running
-- application connects as — ordinary privileges, fully subject to RLS.
CREATE ROLE platform_app WITH LOGIN PASSWORD 'platform_app_dev_only';

GRANT CONNECT ON DATABASE platform TO platform_app;
GRANT USAGE ON SCHEMA public TO platform_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO platform_app;

-- So a future migration's new tables are usable by the app role without
-- editing this file again.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO platform_app;
