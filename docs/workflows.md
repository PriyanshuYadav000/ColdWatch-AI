# Alarm, Inspection, and Remediation Workflows

## Purpose

The workflow service provides transactional lifecycle operations for alarms, inspections, and remediation actions. Every state change updates the current operational row and appends a corresponding history row in the same DuckDB transaction.

Ground-truth files are not read by workflow services.

## Alarm lifecycle

| Current status | Allowed next status |
|---|---|
| OPEN | ACKNOWLEDGED, INVESTIGATING |
| ACKNOWLEDGED | INVESTIGATING |
| INVESTIGATING | ACKNOWLEDGED, RESOLVED |
| RESOLVED | CLOSED, INVESTIGATING |
| CLOSED | None |

An alarm cannot be resolved unless `impact_assessment_status = COMPLETE`. This prevents an alarm with an unresolved inventory-impact assessment from being closed as resolved. A closed alarm is terminal.

Use `transition_alarm` with an actor and non-empty reason. An optional `assigned_to` value updates the assignee during the transition. Every transition appends to `alarm_status_history`.

## Inspection lifecycle

| Current status | Allowed next status |
|---|---|
| PENDING | IN_PROGRESS, CANCELLED |
| IN_PROGRESS | COMPLETED, CANCELLED |
| COMPLETED | None |
| CANCELLED | None |

Create inspections only for alarms that are not RESOLVED or CLOSED. A lot-specific inspection must reference a lot already linked to that alarm through `alarm_inventory_impact`.

Starting an inspection records `started_at`. Completing it requires:
- prior IN_PROGRESS status;
- non-empty findings;
- an explicit product disposition;
- completion timestamp not earlier than the start time.

For `PRODUCT_QUALITY`, `NOT_APPLICABLE` is not a valid completion disposition. Other inspection types may use `NOT_APPLICABLE` when no product disposition is relevant. Each transition is appended to `inspection_status_history`.

## Remediation lifecycle

| Current status | Allowed next status |
|---|---|
| PLANNED | IN_PROGRESS, CANCELLED |
| IN_PROGRESS | COMPLETED, CANCELLED |
| COMPLETED | None |
| CANCELLED | None |

A remediation can only be created from a COMPLETED inspection. Where the inspection is lot-specific, the remediation must use the same lot. Any specified lot must be included in the related alarm's inventory-impact links.

A remediation must be started before completion. Each transition is appended to `remediation_status_history`.

## Independent verification

A remediation must be COMPLETED before it can be verified.

| Current verification status | Allowed next status |
|---|---|
| PENDING | PASSED, FAILED, NOT_REQUIRED |
| FAILED | PASSED, NOT_REQUIRED |
| PASSED | None |
| NOT_REQUIRED | None |

Verification updates `remediation.verification_status` and `verified_at`, and appends a row to `remediation_verification_history` with reviewer, timestamp, prior status, new status, and reason. Verification timestamps cannot precede remediation completion. A FAILED verification may be followed by another remediation attempt only through a new remediation record or an explicit future workflow extension; the current model supports re-verification after the corrective work remains recorded as COMPLETED.

## Python service usage

The initial workflow API is a Python service module at `src/coldwatch/workflows/services.py`. It exposes:

- `transition_alarm(...)`
- `create_inspection(...)`
- `transition_inspection(...)`
- `create_remediation(...)`
- `transition_remediation(...)`
- `verify_remediation(...)`

All methods accept `database_path` for isolated environments and accept timezone-aware timestamps for deterministic testing. The default database path is `data/coldwatch.duckdb`.

Example:

```python
from coldwatch.workflows.services import transition_alarm

result = transition_alarm(
    alarm_id="ALM-CW-...",
    new_status="ACKNOWLEDGED",
    changed_by="warehouse.operator",
    reason="Reviewed the alarm and assigned an owner.",
)
print(result)
```

Invalid lifecycle changes raise `WorkflowError`. Services use a transaction for state and history updates together; a failed operation is rolled back and leaves no partial history entry.

## Database update

After pulling a schema change, apply the idempotent schema initializer to the selected database:

```bash
python scripts/init_db.py --database data/coldwatch-risk-engine.duckdb
```

The schema uses `CREATE TABLE IF NOT EXISTS`, so this creates the new verification-history table without rebuilding the database's existing rows.

## Human decision boundary

Risk alarms identify conditions for review. They do not automatically prove that inventory is damaged. Inspections record evidence and a human disposition; remediation records corrective action; independent verification records whether that action passed its follow-up check.
