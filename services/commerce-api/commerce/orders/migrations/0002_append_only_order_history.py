"""Make order history append-only in PostgreSQL, below the ORM (D-071).

UPDATE and DELETE on orders_orderstatusevent raise, whatever code path issues them. TRUNCATE
stays available to the database owner for test flushes, as for the stock ledger (inventory 0002).
"""

from django.db import migrations

CREATE_APPEND_ONLY_TRIGGER = """
CREATE FUNCTION orders_orderstatusevent_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'orders_orderstatusevent is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$;

CREATE TRIGGER orders_orderstatusevent_append_only
    BEFORE UPDATE OR DELETE ON orders_orderstatusevent
    FOR EACH ROW EXECUTE FUNCTION orders_orderstatusevent_append_only();
"""

DROP_APPEND_ONLY_TRIGGER = """
DROP TRIGGER orders_orderstatusevent_append_only ON orders_orderstatusevent;
DROP FUNCTION orders_orderstatusevent_append_only();
"""


class Migration(migrations.Migration):
    dependencies = [("orders", "0001_initial")]

    operations = [
        migrations.RunSQL(CREATE_APPEND_ONLY_TRIGGER, reverse_sql=DROP_APPEND_ONLY_TRIGGER),
    ]
