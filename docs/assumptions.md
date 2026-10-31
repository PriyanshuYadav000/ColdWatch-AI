# Assumptions and Configuration Decisions

The challenge permits reasonable assumptions where details are unspecified. All values below are synthetic demonstration defaults, not claims about regulatory or industry standards.

- All internal timestamps are stored in UTC.
- The nominal sensor reading interval is five minutes.
- Warehouse/zone/sensor and inventory assignments are effective-dated.
- Temperature and humidity limits are product/lot-specific and stored in `StorageRequirement`.
- Occupancy telemetry is represented as zone-capacity utilization in percent (0–100%), not a people headcount. Each zone has an occupancy warning threshold; the global warning fraction and door-open warning duration are configurable in `config/settings.toml`.
- Door events are discrete OPEN/CLOSE records from a door-event source. Door duration is calculated by pairing sequential events for the same zone and door; unmatched events remain explicit exceptions.
- A missing reading is unknown monitoring coverage, never an assumed normal reading.
- Environmental events are grouped into incidents using documented grouping rules; source readings remain preserved.
- Potential exposure does not automatically mean product rejection. Quality personnel make disposition decisions.
- Ground truth is isolated from detector inputs and used only by the evaluator.
