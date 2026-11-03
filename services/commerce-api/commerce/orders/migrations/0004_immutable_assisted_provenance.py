"""Make merchant-assisted order provenance immutable in PostgreSQL, below the ORM (D-077).

UPDATE and DELETE on orders_assistedorder raise, whatever code path issues them. TRUNCATE stays
available to the database owner for test flushes, as for order history (0002).
"""

from django.db import migrations

CREATE_IMMUTABLE_TRIGGER = """
CREATE FUNCTION orders_assistedorder_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'orders_assistedorder is immutable: % is not allowed', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$;

CREATE TRIGGER orders_assistedorder_immutable
    BEFORE UPDATE OR DELETE ON orders_assistedorder
    FOR EACH ROW EXECUTE FUNCTION orders_assistedorder_immutable();
"""

DROP_IMMUTABLE_TRIGGER = """
DROP TRIGGER orders_assistedorder_immutable ON orders_assistedorder;
DROP FUNCTION orders_assistedorder_immutable();
"""


class Migration(migrations.Migration):
    dependencies = [("orders", "0003_assisted_orders")]

    operations = [
        migrations.RunSQL(CREATE_IMMUTABLE_TRIGGER, reverse_sql=DROP_IMMUTABLE_TRIGGER),
    ]
