"""Append-only delivery attempts and consent evidence in PostgreSQL, below the ORM (D-087, D-090).

Delivery attempts refuse UPDATE; DELETE stays possible only through the retention clean-up that
removes their parent notification. Consent evidence refuses UPDATE and DELETE. TRUNCATE remains
available to the database owner for test flushes.
"""

from django.db import migrations

ATTEMPTS = """
CREATE FUNCTION notifications_attempt_no_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'notifications_deliveryattempt rows are never updated'
        USING ERRCODE = 'restrict_violation';
END;
$$;

CREATE TRIGGER notifications_attempt_no_update
    BEFORE UPDATE ON notifications_deliveryattempt
    FOR EACH ROW EXECUTE FUNCTION notifications_attempt_no_update();
"""

DROP_ATTEMPTS = """
DROP TRIGGER notifications_attempt_no_update ON notifications_deliveryattempt;
DROP FUNCTION notifications_attempt_no_update();
"""

CONSENT = """
CREATE FUNCTION notifications_consent_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'notifications_communicationconsent is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$;

CREATE TRIGGER notifications_consent_append_only
    BEFORE UPDATE OR DELETE ON notifications_communicationconsent
    FOR EACH ROW EXECUTE FUNCTION notifications_consent_append_only();
"""

DROP_CONSENT = """
DROP TRIGGER notifications_consent_append_only ON notifications_communicationconsent;
DROP FUNCTION notifications_consent_append_only();
"""


class Migration(migrations.Migration):
    dependencies = [("notifications", "0001_initial")]

    operations = [
        migrations.RunSQL(ATTEMPTS, reverse_sql=DROP_ATTEMPTS),
        migrations.RunSQL(CONSENT, reverse_sql=DROP_CONSENT),
    ]
