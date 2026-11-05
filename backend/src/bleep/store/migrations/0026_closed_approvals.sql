-- An approval nobody answered before its incident finished is `closed`, not left `pending` (#277).
-- approvals.status is now pending, approved, rejected, instructed or closed.
--
-- The first trigger fires inside the statement that finishes the incident, so a decision that
-- races the close either won before it (and stays) or finds no pending row. The second closes a
-- question stored after the incident finished: the run's Approval node can commit its INSERT just
-- after an outside close, before the cancel of the run reaches it. With both, no finished incident
-- keeps a pending approval. The UPDATE closes the ones already left behind.
CREATE TRIGGER approvals_close_with_incident
AFTER UPDATE OF status ON incidents
WHEN NEW.status IN ('resolved', 'closed', 'failed')
BEGIN
    UPDATE approvals SET status = 'closed' WHERE incident_id = NEW.id AND status = 'pending';
END;

CREATE TRIGGER approvals_closed_on_insert
AFTER INSERT ON approvals
WHEN NEW.status = 'pending' AND EXISTS (
    SELECT 1 FROM incidents i
    WHERE i.id = NEW.incident_id AND i.status IN ('resolved', 'closed', 'failed')
)
BEGIN
    UPDATE approvals SET status = 'closed' WHERE id = NEW.id;
END;

UPDATE approvals SET status = 'closed'
WHERE status = 'pending'
  AND incident_id IN (SELECT id FROM incidents WHERE status IN ('resolved', 'closed', 'failed'));
