"""Protect tracking and fraud evidence from UPDATE and DELETE in PostgreSQL."""

from django.db import migrations

CREATE_APPEND_ONLY_TRIGGERS = """
CREATE FUNCTION fulfillment_trackingevent_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'fulfillment_trackingevent is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$;
CREATE TRIGGER fulfillment_trackingevent_append_only
    BEFORE UPDATE OR DELETE ON fulfillment_trackingevent
    FOR EACH ROW EXECUTE FUNCTION fulfillment_trackingevent_append_only();

CREATE FUNCTION fulfillment_fraudflag_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'fulfillment_fraudflag is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$;
CREATE TRIGGER fulfillment_fraudflag_append_only
    BEFORE UPDATE OR DELETE ON fulfillment_fraudflag
    FOR EACH ROW EXECUTE FUNCTION fulfillment_fraudflag_append_only();

CREATE FUNCTION fulfillment_fraudreview_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'fulfillment_fraudreview is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$;
CREATE TRIGGER fulfillment_fraudreview_append_only
    BEFORE UPDATE OR DELETE ON fulfillment_fraudreview
    FOR EACH ROW EXECUTE FUNCTION fulfillment_fraudreview_append_only();
"""

DROP_APPEND_ONLY_TRIGGERS = """
DROP TRIGGER fulfillment_fraudreview_append_only ON fulfillment_fraudreview;
DROP FUNCTION fulfillment_fraudreview_append_only();
DROP TRIGGER fulfillment_fraudflag_append_only ON fulfillment_fraudflag;
DROP FUNCTION fulfillment_fraudflag_append_only();
DROP TRIGGER fulfillment_trackingevent_append_only ON fulfillment_trackingevent;
DROP FUNCTION fulfillment_trackingevent_append_only();
"""


class Migration(migrations.Migration):
    dependencies = [("fulfillment", "0001_initial")]

    operations = [
        migrations.RunSQL(
            CREATE_APPEND_ONLY_TRIGGERS,
            reverse_sql=DROP_APPEND_ONLY_TRIGGERS,
        )
    ]
