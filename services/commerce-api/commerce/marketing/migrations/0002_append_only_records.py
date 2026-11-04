"""Enforce append-only marketing records in PostgreSQL, below the ORM (D-082, D-083).

The loyalty ledger refuses UPDATE and DELETE: balances are corrected only by new entries.
Consent records refuse UPDATE; DELETE stays possible so the two-year retention clean-up can
remove expired decisions. TRUNCATE remains available to the database owner for test flushes.
"""

from django.db import migrations

LEDGER = """
CREATE FUNCTION marketing_loyaltyledger_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'marketing_loyaltyledger is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$;

CREATE TRIGGER marketing_loyaltyledger_append_only
    BEFORE UPDATE OR DELETE ON marketing_loyaltyledger
    FOR EACH ROW EXECUTE FUNCTION marketing_loyaltyledger_append_only();
"""

DROP_LEDGER = """
DROP TRIGGER marketing_loyaltyledger_append_only ON marketing_loyaltyledger;
DROP FUNCTION marketing_loyaltyledger_append_only();
"""

CONSENT = """
CREATE FUNCTION marketing_consentrecord_no_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'marketing_consentrecord rows are never updated'
        USING ERRCODE = 'restrict_violation';
END;
$$;

CREATE TRIGGER marketing_consentrecord_no_update
    BEFORE UPDATE ON marketing_consentrecord
    FOR EACH ROW EXECUTE FUNCTION marketing_consentrecord_no_update();
"""

DROP_CONSENT = """
DROP TRIGGER marketing_consentrecord_no_update ON marketing_consentrecord;
DROP FUNCTION marketing_consentrecord_no_update();
"""


class Migration(migrations.Migration):
    dependencies = [("marketing", "0001_initial")]

    operations = [
        migrations.RunSQL(LEDGER, reverse_sql=DROP_LEDGER),
        migrations.RunSQL(CONSENT, reverse_sql=DROP_CONSENT),
    ]
