-- An approval nobody answered before its incident finished is `closed`, not left `pending` (#277).
-- The trigger fires inside the statement that finishes the incident, so no reader ever sees a
-- finished incident with a pending approval, and a decision that races the close either won
-- before it (and stays) or finds no pending row. The UPDATE closes the ones already left behind.
CREATE TRIGGER approvals_close_with_incident
AFTER UPDATE OF status ON incidents
WHEN NEW.status IN ('resolved', 'closed', 'failed')
BEGIN
    UPDATE approvals SET status = 'closed' WHERE incident_id = NEW.id AND status = 'pending';
END;

UPDATE approvals SET status = 'closed'
WHERE status = 'pending'
  AND incident_id IN (SELECT id FROM incidents WHERE status IN ('resolved', 'closed', 'failed'));
