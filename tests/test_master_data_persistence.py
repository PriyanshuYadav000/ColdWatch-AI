from pathlib import Path

import duckdb

from coldwatch.config import load_settings
from coldwatch.generators.master_data import build_master_data, persist_master_data


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "schema.sql"


def test_starter_master_data_persists_to_duckdb(tmp_path: Path):
    database_path = tmp_path / "coldwatch-test.duckdb"
    connection = duckdb.connect(str(database_path))
    try:
        connection.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    finally:
        connection.close()

    data = build_master_data(load_settings(), profile="starter", seed=20261022)
    counts = persist_master_data(database_path, data)

    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        assert connection.execute("SELECT COUNT(*) FROM warehouse").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM zone").fetchone()[0] == 10
        assert connection.execute("SELECT COUNT(*) FROM sensor").fetchone()[0] == 50
        assert connection.execute("SELECT COUNT(*) FROM inventory_lot").fetchone()[0] == 500
        assert connection.execute("SELECT COUNT(*) FROM lot_zone_assignment").fetchone()[0] == 500
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM lot_zone_assignment lza
            JOIN inventory_lot lot USING (lot_id)
            JOIN zone z USING (zone_id)
            JOIN requirement_allowed_zone_type allowed
              ON allowed.requirement_id = lot.requirement_id
             AND allowed.zone_type = z.zone_type
            """
        ).fetchone()[0] == 500
    finally:
        connection.close()

    assert counts["warehouse"] == 2
    assert counts["sensor_zone_assignment"] == 50
