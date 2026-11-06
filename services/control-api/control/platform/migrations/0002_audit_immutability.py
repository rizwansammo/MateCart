from django.db import migrations

CREATE_FUNCTION = """
CREATE OR REPLACE FUNCTION platform_refuse_audit_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'console audit events are append-only';
END;
$$ LANGUAGE plpgsql;
"""

DROP_FUNCTION = "DROP FUNCTION IF EXISTS platform_refuse_audit_mutation();"

CREATE_TRIGGERS = """
CREATE TRIGGER platform_audit_refuse_update
BEFORE UPDATE ON platform_consoleauditevent
FOR EACH ROW EXECUTE FUNCTION platform_refuse_audit_mutation();
CREATE TRIGGER platform_audit_refuse_delete
BEFORE DELETE ON platform_consoleauditevent
FOR EACH ROW EXECUTE FUNCTION platform_refuse_audit_mutation();
"""

DROP_TRIGGERS = """
DROP TRIGGER IF EXISTS platform_audit_refuse_update ON platform_consoleauditevent;
DROP TRIGGER IF EXISTS platform_audit_refuse_delete ON platform_consoleauditevent;
"""


class Migration(migrations.Migration):
    dependencies = [("platform", "0001_initial")]
    operations = [
        migrations.RunSQL(CREATE_FUNCTION, DROP_FUNCTION),
        migrations.RunSQL(CREATE_TRIGGERS, DROP_TRIGGERS),
    ]
