"""Make audit events append-only in PostgreSQL, below the ORM.

UPDATE and DELETE on audit_auditevent raise, whatever code path issues them (ORM, raw SQL, a bug).
TRUNCATE is left to the database owner for test-database flushes; in production the application's
runtime role will not own tables (Phase 17), and retention or archival (Phase 18) runs as a
separate maintenance task, never through the application.
"""

from django.db import migrations

CREATE_APPEND_ONLY_TRIGGER = """
CREATE FUNCTION audit_auditevent_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_auditevent is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$;

CREATE TRIGGER audit_auditevent_append_only
    BEFORE UPDATE OR DELETE ON audit_auditevent
    FOR EACH ROW EXECUTE FUNCTION audit_auditevent_append_only();
"""

DROP_APPEND_ONLY_TRIGGER = """
DROP TRIGGER audit_auditevent_append_only ON audit_auditevent;
DROP FUNCTION audit_auditevent_append_only();
"""


class Migration(migrations.Migration):
    dependencies = [("audit", "0001_initial")]

    operations = [
        migrations.RunSQL(CREATE_APPEND_ONLY_TRIGGER, reverse_sql=DROP_APPEND_ONLY_TRIGGER),
    ]
