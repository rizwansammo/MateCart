#!/bin/sh
# MateCart Store database roles (D-118). Runs once, on the first start of an empty data volume.
#
#   POSTGRES_USER          bootstrap superuser, used only by this script and maintenance
#   MATECART_DB_OWNER      owns the database and schema; used only by the migrate job/backups
#   MATECART_DB_APP_ROLE   runtime role for api/worker/beat: data privileges only, no DDL
#
# Values are passed as psql variables and quoted by psql (:'v' / :"v"), never spliced into SQL.
set -eu

: "${MATECART_DB_OWNER:?}" "${MATECART_DB_OWNER_PASSWORD:?}"
: "${MATECART_DB_APP_ROLE:?}" "${MATECART_DB_APP_PASSWORD:?}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=db="$POSTGRES_DB" \
  --set=owner="$MATECART_DB_OWNER" --set=owner_password="$MATECART_DB_OWNER_PASSWORD" \
  --set=app="$MATECART_DB_APP_ROLE" --set=app_password="$MATECART_DB_APP_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD %L',
              :'owner', :'owner_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'owner') \gexec
SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD %L',
              :'app', :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app') \gexec
ALTER DATABASE :"db" OWNER TO :"owner";
ALTER SCHEMA public OWNER TO :"owner";
REVOKE ALL ON DATABASE :"db" FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE :"db" TO :"app";
GRANT USAGE ON SCHEMA public TO :"app";
SQL
