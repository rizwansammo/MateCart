"""Make the stock movement ledger append-only in PostgreSQL, below the ORM (D-054).

UPDATE and DELETE on inventory_stockmovement raise, whatever code path issues them. Corrections are
compensating movements. TRUNCATE stays available to the database owner for test flushes, as for
the audit table (audit 0002).
"""

from django.db import migrations

CREATE_APPEND_ONLY_TRIGGER = """
CREATE FUNCTION inventory_stockmovement_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'inventory_stockmovement is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$;

CREATE TRIGGER inventory_stockmovement_append_only
    BEFORE UPDATE OR DELETE ON inventory_stockmovement
    FOR EACH ROW EXECUTE FUNCTION inventory_stockmovement_append_only();
"""

DROP_APPEND_ONLY_TRIGGER = """
DROP TRIGGER inventory_stockmovement_append_only ON inventory_stockmovement;
DROP FUNCTION inventory_stockmovement_append_only();
"""


class Migration(migrations.Migration):
    dependencies = [("inventory", "0001_initial")]

    operations = [
        migrations.RunSQL(CREATE_APPEND_ONLY_TRIGGER, reverse_sql=DROP_APPEND_ONLY_TRIGGER),
    ]
