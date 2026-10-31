# Synthetic Telemetry Generation

## Purpose

The telemetry generator creates a valid, normal baseline after master data has been generated. It does not inject excursions, sensor outages, sensor drift, or door-open violations. Those are separate labelled anomaly-injection scenarios in a later milestone.

## Inputs and dependencies

- `warehouse`, `zone`, `sensor`, and `sensor_zone_assignment` tables must already be populated.
- Every active sensor must have exactly one sensor-to-zone assignment covering the telemetry period.
- The configured reading cadence must match each sensor's `expected_interval_seconds`.
- The output profile's `sensor_readings` count is distributed as evenly as possible across all sensors.
- Random streams are derived from the configured seed and stable sensor/zone indexes, so generation is independent of Python's randomized hash seed.

## Reading cadence and profile coverage

All sensors emit at the configured five-minute interval. The simulation end is calculated from the largest per-sensor reading count, not from an arbitrary seven-day window.

| Profile | Sensors | Total readings | Readings per sensor | Time from first to last reading |
|---|---:|---:|---:|---:|
| Starter | 50 | 10,000 | 200 | 16 hours 35 minutes |
| Full | 500 | 1,000,000 | 2,000 | 6 days 22 hours 35 minutes |

The start timestamp is `2026-10-22T00:00:00Z`. If a future profile's total is not divisible by its sensor count, the first sensors in stable ID order receive one additional reading; per-sensor counts therefore differ by at most one.

## Baseline signal assumptions

These are synthetic demonstration values selected to remain inside the configured illustrative product requirements for compatible zone types. They are not regulatory or universal warehouse standards.

| Zone type | Temperature centre | Humidity centre | Rationale |
|---|---:|---:|---|
| FROZEN | -21°C | 50% RH | Frozen product baseline |
| COLD | 5°C | 45% RH | Cold-chain baseline |
| CHILLED | 5°C | 62% RH | Shared plausible baseline for dairy and fresh produce ranges |
| AMBIENT | 22°C | 50% RH | Ambient packaged goods baseline |
| HOT | 25°C | 48% RH | Industrial material baseline |
| CONTROLLED | 5°C | 45% RH | Controlled pharmaceutical baseline |

Readings include a small sinusoidal signal, sensor-specific bias, and seeded Gaussian noise. Occupancy is represented as percentage of zone capacity and varies around 50% with a daily pattern. Humidity and occupancy are bounded to 0–100%; temperature is not clipped to an arbitrary universal range.

Each zone has two temperature sensors, two humidity sensors, and one occupancy sensor. Baseline sensor readings have quality status `GOOD`. Sensor drift and other quality anomalies are introduced later as separately labelled exceptions.

## Door-event assumptions

Each zone has a synthetic `DOOR-01`. Normal events are generated as chronologically paired `OPEN` and `CLOSE` records. The configurable normal event rate is two pairs per zone per simulated day. Normal open duration is between two and eight minutes, below the illustrative ten-minute warning setting. Unmatched or excessively long events are reserved for anomaly injection.

## Output and persistence

The command:

```bash
python scripts/generate_telemetry.py --profile starter
```

writes:

- `data/generated/<profile>/telemetry/sensor_readings.parquet`
- `data/generated/<profile>/telemetry/door_events.parquet`
- `data/generated/<profile>/telemetry/telemetry_summary.json`

Parquet generation is batched using `generation.batch_size`; the complete million-row readings table is not held in memory. DuckDB bulk-loads the completed Parquet file inside a transaction. The command refuses to run if `sensor_reading` or `door_event` is already populated.

The summary records expected and persisted row counts, profile, seed, UTC coverage, per-sensor count range, canonical content hashes, and Parquet file hashes. A repeatability test generates the same starter data in two fresh databases and compares counts and hashes.

## Rebuilding after configuration changes

Generated DuckDB files are local and excluded from Git. A Git pull updates code/configuration but does not rebuild the existing local database. When a master-data catalogue or generator rule changes, back up the old database and generate a fresh database before running the telemetry generator.

All sample values are synthetic. No external warehouse or personal data is used.
