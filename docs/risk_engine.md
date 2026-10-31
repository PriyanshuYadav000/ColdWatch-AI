# Deterministic Risk Engine

## Purpose and boundaries

The risk engine evaluates operational DuckDB records and configured rule thresholds. It does not open or read `data/ground_truth`, anomaly manifests, or the evaluation CSV. Expected labels remain isolated for independent evaluation after detection.

The engine writes a JSON report for review and, unless started with `--dry-run`, persists generated alarms, evidence links, and inventory-impact links. Existing alarms are never silently rewritten. The deliberately incomplete impact-assessment control is reported as a finding, but its missing links are not auto-created.

## Run a read-only detection preview

After anomaly injection and successful dataset validation:

```bash
python scripts/run_risk_engine.py --profile starter --dry-run
```

This writes `reports/risk_engine_starter.json` and does not persist alarms or impacts.

### Build a clean isolated challenge database

The latest injector keeps the deliberately invalid sensor mapping on a sensor not already used by another injected sensor anomaly. To refresh the challenge dataset without overwriting the existing local database, copy the saved pre-anomaly baseline:

```bash
cp -n data/coldwatch-pre-anomaly-backup.duckdb data/coldwatch-risk-engine.duckdb
python scripts/init_db.py --database data/coldwatch-risk-engine.duckdb
python scripts/inject_anomalies.py \
  --profile starter \
  --database data/coldwatch-risk-engine.duckdb \
  --ground-truth-root data/ground_truth-risk-engine \
  --report-root reports/risk-engine
python scripts/validate_dataset.py \
  --profile starter \
  --database data/coldwatch-risk-engine.duckdb \
  --ground-truth-root data/ground_truth-risk-engine \
  --report-root reports/risk-engine
```

This preserves the existing `data/coldwatch.duckdb`, ground-truth folder, and default reports. The copied database starts from the pre-injection master-data and telemetry backup, so the existing baseline Parquet files can be reused.

## Persist detected findings

After reviewing the dry-run report:

```bash
python scripts/run_risk_engine.py --profile starter
```

Alarm and evidence writes are transactional. Deterministic source keys and identifiers make repeated runs idempotent. Existing alarms with the same incident are reused; the deliberately incomplete negative-control alarm is left untouched. Use the report to review findings before using these generated alarms operationally.

The report includes the detector type, rule ID, severity, entity, zone, event window, evidence source identifiers, affected lot IDs, persistence counts, and whether the ground-truth artifact was used (always `false`).

## Rules in the first engine

| Rule | Operational input | Decision |
|---|---|---|
| Temperature excursion | Temperature SensorReading + effective SensorZoneAssignment + overlapping lot requirement | Reading falls outside the lot-specific temperature minimum/maximum |
| Humidity excursion | Humidity SensorReading + effective SensorZoneAssignment + overlapping lot requirement | Reading falls outside the lot-specific humidity minimum/maximum |
| Occupancy limit | Occupancy SensorReading + Zone | Reading exceeds configured fraction of zone warning threshold |
| Door left open | Sequential DoorEvent records | OPEN-to-next-CLOSE duration exceeds configured warning duration |
| Sensor outage | SensorReadingGap | Consecutive explicit missing intervals meet the configured count |
| Sensor drift | Redundant temperature sensors in the same zone | Residual relative to peer sensor readings rises by configured tolerance over eight consecutive five-minute observations |
| Incompatible storage | LotZoneAssignment + StorageRequirement + RequirementAllowedZoneType | Lot is in a zone type not allowed by its requirement |
| Invalid sensor mapping | Overlapping SensorZoneAssignment records | Different-zone assignments overlap without approval and rationale |
| Incomplete impact assessment | Alarm + overlapping LotZoneAssignment + AlarmInventoryImpact | An alarm marked FAILED has no inventory-impact links despite overlapping stock |

## Inventory exposure

For a detection with an unambiguous zone and event interval, the engine joins lot placements using half-open time overlap:

- lot placement starts before the risk interval ends;
- lot placement ends after the risk interval starts, or remains open;
- for temperature/humidity findings, only lot IDs whose requirements were breached are linked;
- for other zone risks, all lots in the zone/time overlap are candidates.

A sensor with competing overlapping mappings that lack rationale/approval is excluded from environmental readings attribution. The mapping error is raised as a separate finding; the engine does not guess which zone's inventory should be assigned to that sensor. The negative-control incomplete alarm is never repaired automatically.

## Assumptions and limitations

- All thresholds are synthetic illustrative settings, not regulatory or universal cold-chain limits.
- Temperature/humidity rules compare readings with the applicable lot requirement, not a universal warehouse-wide threshold.
- The current drift rule is a peer-relative heuristic over eight consecutive five-minute observations. It is not a calibrated metrology assessment.
- Outage detection consumes explicit gap/tombstone rows. A production ingestion monitor must create these records from expected sensor cadence.
- The first version opens deterministic alarms; inspection and remediation workflow orchestration is a subsequent milestone.
- A risk finding is not proof that inventory is damaged. Product disposition remains a human quality decision.
