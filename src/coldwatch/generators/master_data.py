"""Deterministic generation of warehouse and inventory master data."""

from __future__ import annotations

import hashlib
import json
import random
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb

from coldwatch.config import DEFAULT_SETTINGS_PATH, load_settings


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PRODUCT_CATALOG = PROJECT_ROOT / "config" / "products.toml"
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "coldwatch.duckdb"
DEFAULT_SUMMARY = PROJECT_ROOT / "reports" / "master_data_summary.json"

ZONE_TYPES = ("FROZEN", "COLD", "CHILLED", "AMBIENT", "HOT", "CONTROLLED")
SENSOR_LAYOUT = (
    ("TEMPERATURE", "TEMP", 1),
    ("TEMPERATURE", "TEMP", 2),
    ("HUMIDITY", "HUM", 1),
    ("HUMIDITY", "HUM", 2),
    ("OCCUPANCY", "OCC", 1),
)

TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "warehouse": (
        "warehouse_id", "site_code", "name", "address_line", "city", "region",
        "country", "timezone", "status", "created_at", "updated_at",
    ),
    "zone": (
        "zone_id", "warehouse_id", "name", "zone_type", "area_sqm",
        "occupancy_warning_limit_pct", "status", "created_at", "updated_at",
    ),
    "sensor": (
        "sensor_id", "sensor_type", "unit", "manufacturer", "model",
        "serial_number", "expected_interval_seconds", "calibration_due_at",
        "installed_at", "retired_at", "status", "created_at",
    ),
    "storage_requirement": (
        "requirement_id", "product_code", "product_category", "version",
        "temperature_min_c", "temperature_max_c", "humidity_min_pct",
        "humidity_max_pct", "effective_from", "effective_to", "status", "created_at",
    ),
    "requirement_allowed_zone_type": ("requirement_id", "zone_type"),
    "sensor_zone_assignment": (
        "assignment_id", "sensor_id", "zone_id", "valid_from", "valid_to",
        "rationale", "is_approved", "approved_by", "approved_at", "created_at",
    ),
    "inventory_lot": (
        "lot_id", "product_code", "product_name", "product_category",
        "requirement_id", "quantity_received", "quantity_unit", "manufactured_at",
        "received_at", "expires_at", "status", "created_at",
    ),
    "lot_zone_assignment": (
        "assignment_id", "lot_id", "zone_id", "assigned_quantity", "quantity_unit",
        "stored_from", "stored_to", "assignment_reason", "status", "created_at",
    ),
}

MASTER_TABLES = tuple(TABLE_COLUMNS)


class MasterDataGenerationError(ValueError):
    """Raised when master-data generation settings or target state are invalid."""


def _parse_utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise MasterDataGenerationError("start_timestamp must include a timezone.")
    return parsed.astimezone(timezone.utc)


def load_product_catalog(path: str | Path = DEFAULT_PRODUCT_CATALOG) -> list[dict[str, Any]]:
    """Load and validate the synthetic product and storage-requirement catalogue."""
    catalog_path = Path(path)
    if not catalog_path.is_file():
        raise FileNotFoundError(f"Product catalogue not found: {catalog_path}")

    with catalog_path.open("rb") as file:
        raw = tomllib.load(file)

    products = raw.get("products")
    if not isinstance(products, list) or not products:
        raise MasterDataGenerationError("Product catalogue must contain at least one [[products]] entry.")

    seen_codes: set[str] = set()
    for product in products:
        required = {
            "product_code", "product_name", "product_category", "quantity_unit",
            "temperature_min_c", "temperature_max_c", "humidity_min_pct",
            "humidity_max_pct", "allowed_zone_types", "shelf_life_days",
        }
        if not isinstance(product, dict) or not required.issubset(product):
            raise MasterDataGenerationError(f"Product entry must define: {sorted(required)}")
        code = product["product_code"]
        if not isinstance(code, str) or not code.strip() or code in seen_codes:
            raise MasterDataGenerationError(f"Invalid or duplicate product_code: {code!r}")
        seen_codes.add(code)
        if not product["allowed_zone_types"] or not set(product["allowed_zone_types"]).issubset(ZONE_TYPES):
            raise MasterDataGenerationError(f"Product {code} has invalid allowed_zone_types.")
        if product["temperature_min_c"] > product["temperature_max_c"]:
            raise MasterDataGenerationError(f"Product {code} has inverted temperature bounds.")
        if product["humidity_min_pct"] > product["humidity_max_pct"]:
            raise MasterDataGenerationError(f"Product {code} has inverted humidity bounds.")
        if not (0 <= product["humidity_min_pct"] <= product["humidity_max_pct"] <= 100):
            raise MasterDataGenerationError(f"Product {code} humidity bounds must be within 0..100.")
        if not isinstance(product["shelf_life_days"], int) or product["shelf_life_days"] <= 0:
            raise MasterDataGenerationError(f"Product {code} shelf_life_days must be a positive integer.")
    return products


def _even_distribution(total: int, groups: int) -> list[int]:
    base, remainder = divmod(total, groups)
    return [base + (1 if index < remainder else 0) for index in range(groups)]


def _checksum(rows: list[dict[str, Any]]) -> str:
    payload = json.dumps(rows, sort_keys=True, separators=(",", ":"), default=lambda value: value.isoformat())
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_master_data(
    settings: dict[str, Any] | None = None,
    *,
    profile: str | None = None,
    seed: int | None = None,
    product_catalog_path: str | Path = DEFAULT_PRODUCT_CATALOG,
) -> dict[str, list[dict[str, Any]]]:
    """Build a deterministic, in-memory bundle of parent and business master records."""
    settings = settings or load_settings()
    generation = settings["generation"]
    profiles = settings["profiles"]
    selected_profile = profile or generation["profile"]
    if selected_profile not in profiles:
        raise MasterDataGenerationError(
            f"Unknown profile {selected_profile!r}; choose one of {sorted(profiles)}."
        )
    counts = profiles[selected_profile]
    warehouse_count = counts["warehouses"]
    zone_count = counts["zones"]
    sensor_count = counts["sensors"]
    lot_count = counts["inventory_lots"]
    if warehouse_count > zone_count:
        raise MasterDataGenerationError("Each warehouse must receive at least one zone.")
    if sensor_count != zone_count * len(SENSOR_LAYOUT):
        raise MasterDataGenerationError(
            f"Current sensor layout requires exactly {len(SENSOR_LAYOUT)} sensors per zone; "
            f"profile has {sensor_count} sensors for {zone_count} zones."
        )

    random_seed = settings["project"]["random_seed"] if seed is None else seed
    rng = random.Random(random_seed)
    start_at = _parse_utc(generation["start_timestamp"])
    duration_days = int(generation.get("duration_days", 7))
    if duration_days <= 0:
        raise MasterDataGenerationError("generation.duration_days must be positive.")
    generated_at = start_at
    installed_at = start_at - timedelta(days=30)
    calibration_due_at = installed_at + timedelta(days=365)
    requirement_effective_from = start_at - timedelta(days=365)
    products = load_product_catalog(product_catalog_path)

    data: dict[str, list[dict[str, Any]]] = {table: [] for table in MASTER_TABLES}
    warehouses: list[dict[str, Any]] = []
    zones: list[dict[str, Any]] = []
    sensors: list[dict[str, Any]] = []
    sensor_zone_lookup: dict[str, str] = {}

    for warehouse_number in range(1, warehouse_count + 1):
        warehouse_id = f"WH-{warehouse_number:03d}"
        warehouses.append({
            "warehouse_id": warehouse_id,
            "site_code": f"SITE-{warehouse_number:03d}",
            "name": f"ColdWatch Warehouse {warehouse_number:03d}",
            "address_line": None,
            "city": f"Synthetic Hub {warehouse_number:03d}",
            "region": f"Synthetic Region {warehouse_number:02d}",
            "country": "IN",
            "timezone": "Asia/Kolkata",
            "status": "ACTIVE",
            "created_at": generated_at,
            "updated_at": generated_at,
        })

    zone_allocations = _even_distribution(zone_count, warehouse_count)
    global_zone_index = 0
    for warehouse, zones_for_warehouse in zip(warehouses, zone_allocations, strict=True):
        local_zone_number = 0
        for _ in range(zones_for_warehouse):
            local_zone_number += 1
            global_zone_index += 1
            zone_type = ZONE_TYPES[(global_zone_index - 1) % len(ZONE_TYPES)]
            zone_id = f"ZONE-{warehouse['warehouse_id']}-{local_zone_number:03d}"
            zone_name = f"{zone_type.title()} Zone {local_zone_number:03d}"
            zones.append({
                "zone_id": zone_id,
                "warehouse_id": warehouse["warehouse_id"],
                "name": zone_name,
                "zone_type": zone_type,
                "area_sqm": round(rng.uniform(500.0, 2500.0), 2),
                "occupancy_warning_limit_pct": 90.0,
                "status": "ACTIVE",
                "created_at": generated_at,
                "updated_at": generated_at,
            })

            for sensor_type, abbreviation, sequence in SENSOR_LAYOUT:
                sensor_id = f"SNS-{zone_id}-{abbreviation}-{sequence:02d}"
                unit = {
                    "TEMPERATURE": "degC",
                    "HUMIDITY": "pct_rh",
                    "OCCUPANCY": "pct_capacity",
                }[sensor_type]
                sensors.append({
                    "sensor_id": sensor_id,
                    "sensor_type": sensor_type,
                    "unit": unit,
                    "manufacturer": "ColdWatch Synthetic Instruments",
                    "model": f"CW-{abbreviation}-100",
                    "serial_number": f"CW-SERIAL-{global_zone_index:04d}-{abbreviation}-{sequence:02d}",
                    "expected_interval_seconds": int(generation["reading_interval_minutes"]) * 60,
                    "calibration_due_at": calibration_due_at,
                    "installed_at": installed_at,
                    "retired_at": None,
                    "status": "ACTIVE",
                    "created_at": generated_at,
                })
                sensor_zone_lookup[sensor_id] = zone_id

    # One versioned storage requirement per synthetic product.
    requirement_by_product: dict[str, str] = {}
    allowed_zone_types_by_product: dict[str, list[str]] = {}
    for product in products:
        requirement_id = f"REQ-{product['product_code']}-V01"
        requirement_by_product[product["product_code"]] = requirement_id
        allowed_zone_types_by_product[product["product_code"]] = list(product["allowed_zone_types"])
        data["storage_requirement"].append({
            "requirement_id": requirement_id,
            "product_code": product["product_code"],
            "product_category": product["product_category"],
            "version": 1,
            "temperature_min_c": product["temperature_min_c"],
            "temperature_max_c": product["temperature_max_c"],
            "humidity_min_pct": product["humidity_min_pct"],
            "humidity_max_pct": product["humidity_max_pct"],
            "effective_from": requirement_effective_from,
            "effective_to": None,
            "status": "ACTIVE",
            "created_at": generated_at,
        })
        for zone_type in product["allowed_zone_types"]:
            data["requirement_allowed_zone_type"].append({
                "requirement_id": requirement_id,
                "zone_type": zone_type,
            })

    zones_by_type: dict[str, list[dict[str, Any]]] = {}
    for zone in zones:
        zones_by_type.setdefault(zone["zone_type"], []).append(zone)

    # A baseline lot is assigned only to a zone type allowed by its requirement.
    # Keep the profile's simulated period consistent with its reading volume.
    # Starter is a shorter smoke-test window; full spans nearly seven days at
    # the configured five-minute cadence. This avoids artificial baseline gaps.
    max_readings_per_sensor = (int(counts["sensor_readings"]) + sensor_count - 1) // sensor_count
    cadence_seconds = int(generation["reading_interval_minutes"]) * 60
    coverage_seconds = max(1, (max_readings_per_sensor - 1) * cadence_seconds)
    duration_seconds = min(duration_days * 24 * 60 * 60, coverage_seconds)
    for lot_number in range(1, lot_count + 1):
        product = rng.choice(products)
        candidate_zones = [
            zone
            for zone_type in product["allowed_zone_types"]
            for zone in zones_by_type.get(zone_type, [])
        ]
        if not candidate_zones:
            raise MasterDataGenerationError(
                f"No zone exists for product {product['product_code']} allowed types "
                f"{product['allowed_zone_types']}."
            )
        zone = rng.choice(candidate_zones)
        received_at = start_at + timedelta(seconds=rng.randrange(duration_seconds))
        manufactured_at = received_at - timedelta(days=rng.randint(1, 90))
        expires_at = received_at + timedelta(days=product["shelf_life_days"])
        lot_id = f"LOT-2026-{lot_number:06d}"
        quantity = round(rng.uniform(10.0, 1000.0), 3)
        requirement_id = requirement_by_product[product["product_code"]]
        assignment_id = f"LZA-{lot_number:08d}"

        data["inventory_lot"].append({
            "lot_id": lot_id,
            "product_code": product["product_code"],
            "product_name": product["product_name"],
            "product_category": product["product_category"],
            "requirement_id": requirement_id,
            "quantity_received": quantity,
            "quantity_unit": product["quantity_unit"],
            "manufactured_at": manufactured_at,
            "received_at": received_at,
            "expires_at": expires_at,
            "status": "STORED",
            "created_at": received_at,
        })
        data["lot_zone_assignment"].append({
            "assignment_id": assignment_id,
            "lot_id": lot_id,
            "zone_id": zone["zone_id"],
            "assigned_quantity": quantity,
            "quantity_unit": product["quantity_unit"],
            "stored_from": received_at,
            "stored_to": None,
            "assignment_reason": "INITIAL_STORAGE",
            "status": "ACTIVE",
            "created_at": received_at,
        })

    for assignment_number, sensor in enumerate(sensors, start=1):
        data["sensor_zone_assignment"].append({
            "assignment_id": f"SZA-{assignment_number:08d}",
            "sensor_id": sensor["sensor_id"],
            "zone_id": sensor_zone_lookup[sensor["sensor_id"]],
            "valid_from": installed_at,
            "valid_to": None,
            "rationale": "INITIAL_INSTALLATION",
            "is_approved": False,
            "approved_by": None,
            "approved_at": None,
            "created_at": generated_at,
        })

    data["warehouse"] = warehouses
    data["zone"] = zones
    data["sensor"] = sensors
    return data


def validate_master_data_bundle(data: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    """Validate deterministic IDs, parent references, and baseline-compatible lot placement."""
    counts = {table: len(data.get(table, [])) for table in MASTER_TABLES}
    warehouse_ids = {row["warehouse_id"] for row in data["warehouse"]}
    zone_ids = {row["zone_id"] for row in data["zone"]}
    sensor_ids = {row["sensor_id"] for row in data["sensor"]}
    requirement_ids = {row["requirement_id"] for row in data["storage_requirement"]}
    lots_by_id = {row["lot_id"]: row for row in data["inventory_lot"]}
    lot_ids = set(lots_by_id)

    for table, key in (
        ("warehouse", "warehouse_id"), ("zone", "zone_id"), ("sensor", "sensor_id"),
        ("storage_requirement", "requirement_id"), ("inventory_lot", "lot_id"),
        ("sensor_zone_assignment", "assignment_id"), ("lot_zone_assignment", "assignment_id"),
    ):
        values = [row[key] for row in data[table]]
        if len(values) != len(set(values)):
            raise MasterDataGenerationError(f"Duplicate primary key detected in {table}.{key}.")

    for zone in data["zone"]:
        if zone["warehouse_id"] not in warehouse_ids:
            raise MasterDataGenerationError(f"Unknown warehouse for zone {zone['zone_id']}.")
    for assignment in data["sensor_zone_assignment"]:
        if assignment["sensor_id"] not in sensor_ids or assignment["zone_id"] not in zone_ids:
            raise MasterDataGenerationError(f"Broken sensor-zone assignment {assignment['assignment_id']}.")
    for lot in data["inventory_lot"]:
        if lot["requirement_id"] not in requirement_ids:
            raise MasterDataGenerationError(f"Unknown requirement for lot {lot['lot_id']}.")
    for assignment in data["lot_zone_assignment"]:
        if assignment["lot_id"] not in lot_ids or assignment["zone_id"] not in zone_ids:
            raise MasterDataGenerationError(f"Broken lot-zone assignment {assignment['assignment_id']}.")

    product_allowed_types = {
        row["requirement_id"]: {
            item["zone_type"]
            for item in data["requirement_allowed_zone_type"]
            if item["requirement_id"] == row["requirement_id"]
        }
        for row in data["storage_requirement"]
    }
    zone_type_by_id = {row["zone_id"]: row["zone_type"] for row in data["zone"]}
    for lot_assignment in data["lot_zone_assignment"]:
        lot = lots_by_id[lot_assignment["lot_id"]]
        allowed = product_allowed_types[lot["requirement_id"]]
        actual_zone_type = zone_type_by_id[lot_assignment["zone_id"]]
        if actual_zone_type not in allowed:
            raise MasterDataGenerationError(
                f"Baseline lot {lot['lot_id']} assigned to incompatible zone type {actual_zone_type}."
            )

    if counts["sensor_zone_assignment"] != counts["sensor"]:
        raise MasterDataGenerationError("Every generated sensor must have exactly one initial zone assignment.")
    if counts["lot_zone_assignment"] != counts["inventory_lot"]:
        raise MasterDataGenerationError("Every generated baseline lot must have one initial zone assignment.")
    return counts


def _assert_master_tables_empty(connection: duckdb.DuckDBPyConnection) -> None:
    populated = []
    for table in MASTER_TABLES:
        count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if count:
            populated.append(f"{table}={count}")
    if populated:
        raise MasterDataGenerationError(
            "Master-data tables are not empty (" + ", ".join(populated) +
            "). Generate into a fresh initialized database to avoid accidental duplicates."
        )


def _insert_rows(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    rows: list[dict[str, Any]],
) -> None:
    if not rows:
        return
    columns = TABLE_COLUMNS[table]
    column_sql = ", ".join(columns)
    placeholders = ", ".join("?" for _ in columns)
    sql = f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders})"
    connection.executemany(sql, [tuple(row[column] for column in columns) for row in rows])


def persist_master_data(
    database_path: str | Path,
    data: dict[str, list[dict[str, Any]]],
) -> dict[str, int]:
    """Persist a validated bundle in dependency order using one transaction."""
    counts = validate_master_data_bundle(data)
    db_path = Path(database_path)
    if not db_path.is_file():
        raise FileNotFoundError(
            f"DuckDB database not found: {db_path}. Run scripts/init_db.py first."
        )

    connection = duckdb.connect(str(db_path))
    try:
        _assert_master_tables_empty(connection)
        connection.execute("BEGIN TRANSACTION")
        # Parent tables must be inserted before dependent transaction tables.
        insertion_order = (
            "warehouse", "zone", "sensor", "storage_requirement",
            "requirement_allowed_zone_type", "sensor_zone_assignment",
            "inventory_lot", "lot_zone_assignment",
        )
        for table in insertion_order:
            _insert_rows(connection, table, data[table])
        connection.execute("COMMIT")

        expected = {
            table: len(data[table])
            for table in insertion_order
        }
        for table, expected_count in expected.items():
            actual = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if actual != expected_count:
                raise MasterDataGenerationError(
                    f"Persisted row count mismatch for {table}: expected {expected_count}, got {actual}."
                )
        return counts
    except Exception:
        try:
            connection.execute("ROLLBACK")
        except duckdb.TransactionException:
            pass
        raise
    finally:
        connection.close()


def write_master_data_summary(
    data: dict[str, list[dict[str, Any]]],
    *,
    profile: str,
    seed: int,
    output_path: str | Path = DEFAULT_SUMMARY,
) -> dict[str, Any]:
    """Write deterministic row counts and content checksums for generated master data."""
    validate_master_data_bundle(data)
    table_checksums = {table: _checksum(data[table]) for table in MASTER_TABLES}
    summary = {
        "project": "ColdWatch AI",
        "dataset_profile": profile,
        "seed": seed,
        "timestamp_convention": "UTC",
        "counts": {table: len(data[table]) for table in MASTER_TABLES},
        "table_sha256": table_checksums,
        "master_bundle_sha256": hashlib.sha256(
            json.dumps(table_checksums, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    }
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def generate_master_data(
    *,
    database_path: str | Path = DEFAULT_DATABASE,
    profile: str | None = None,
    seed: int | None = None,
    settings_path: str | Path = DEFAULT_SETTINGS_PATH,
    product_catalog_path: str | Path = DEFAULT_PRODUCT_CATALOG,
    summary_path: str | Path = DEFAULT_SUMMARY,
) -> dict[str, Any]:
    settings = load_settings(settings_path)
    selected_profile = profile or settings["generation"]["profile"]
    selected_seed = settings["project"]["random_seed"] if seed is None else seed
    data = build_master_data(
        settings,
        profile=selected_profile,
        seed=selected_seed,
        product_catalog_path=product_catalog_path,
    )
    counts = persist_master_data(database_path, data)
    summary = write_master_data_summary(
        data, profile=selected_profile, seed=selected_seed, output_path=summary_path
    )
    summary["persisted_counts"] = counts
    summary_path = Path(summary_path)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary
