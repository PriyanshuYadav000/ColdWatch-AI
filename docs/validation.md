# Dataset Validation

## Purpose

The post-injection validator checks the baseline artifacts, operational DuckDB state, controlled anomaly evidence, separate ground truth, and audit trails. It treats the nine documented synthetic exceptions as expected scenarios and verifies that they are represented explicitly rather than silently ignored.

## Run

After generating a baseline and injecting anomalies, run:

```bash
python scripts/validate_dataset.py --profile starter
```

For the challenge-scale profile, use `--profile full`.

The command writes `reports/validation_<profile>.json` and exits with code 0 only when every validation check passes. A failed check is included in the JSON report with its actual and expected values.

Optional path overrides are available for isolated test environments:

```bash
python scripts/validate_dataset.py \
  --database /path/to/coldwatch.duckdb \
  --generated-root /path/to/generated \
  --ground-truth-root /path/to/ground_truth \
  --report-root /path/to/reports
```

## Validation groups

- Required artifact and schema presence.
- Baseline telemetry generation mode, row counts, and Parquet SHA-256 checksums.
- Ground-truth columns, nine expected issue types, unique scenario IDs, and manifest checksum.
- Master-data counts and inventory placement history.
- Concurrent inventory allocation not exceeding the lot's received quantity.
- Sensor unit/type compatibility, bounded percentage readings, quality values, and timestamp order.
- Per-sensor cadence and explicit gap-tombstone reconciliation.
- Reading revision ledger consistency with current operational values.
- Normal door OPEN/CLOSE pairing and duration; separate verification of the injected 25-minute door event.
- Effective-dated sensor-zone mapping overlaps and incompatible lot-zone assignments.
- Scenario-specific evidence for all nine injected issue types.
- Isolation of ground-truth labels from operational DuckDB tables.

## Interpretation

The validator deliberately expects one incompatible lot-zone assignment, one unapproved overlapping sensor mapping, two missing sensor observations, and one alarm with no inventory-impact links. These are labelled test cases, not unexpected data corruption. Any additional or missing evidence causes the corresponding check to fail.

The original baseline Parquet files remain unchanged by anomaly injection. The operational DuckDB copy contains the revised readings and explicit gap records; the revision ledger retains the original reading values.
