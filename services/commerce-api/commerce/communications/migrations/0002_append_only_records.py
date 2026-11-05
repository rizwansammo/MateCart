"""Append-only conversation timelines and delivery attempts in PostgreSQL, below the ORM (D-092).

Conversation events refuse UPDATE and DELETE: assignment and status history is evidence.
Delivery attempts refuse UPDATE; DELETE stays possible for the 90-day retention clean-up
(``communications.retention``). TRUNCATE remains available to the database owner for test
flushes.
"""

from django.db import migrations

EVENTS = """
CREATE FUNCTION communications_event_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'communications_conversationevent is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$;

CREATE TRIGGER communications_event_append_only
    BEFORE UPDATE OR DELETE ON communications_conversationevent
    FOR EACH ROW EXECUTE FUNCTION communications_event_append_only();
"""

DROP_EVENTS = """
DROP TRIGGER communications_event_append_only ON communications_conversationevent;
DROP FUNCTION communications_event_append_only();
"""

ATTEMPTS = """
CREATE FUNCTION communications_attempt_no_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'communications_deliveryattempt rows are never updated'
        USING ERRCODE = 'restrict_violation';
END;
$$;

CREATE TRIGGER communications_attempt_no_update
    BEFORE UPDATE ON communications_deliveryattempt
    FOR EACH ROW EXECUTE FUNCTION communications_attempt_no_update();
"""

DROP_ATTEMPTS = """
DROP TRIGGER communications_attempt_no_update ON communications_deliveryattempt;
DROP FUNCTION communications_attempt_no_update();
"""


class Migration(migrations.Migration):
    dependencies = [("communications", "0001_initial")]

    operations = [
        migrations.RunSQL(EVENTS, reverse_sql=DROP_EVENTS),
        migrations.RunSQL(ATTEMPTS, reverse_sql=DROP_ATTEMPTS),
    ]
