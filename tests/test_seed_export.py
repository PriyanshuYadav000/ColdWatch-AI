from __future__ import annotations

from pathlib import Path

import duckdb

from coldwatch.exports.seed_bundle import export_seed_bundle


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "schema.sql"


def test_exported_csv_and_sql_seed_bundle_restores_rows(tmp_path: Path, monkeypatch):
    source = tmp_path / "source.duckdb"
    con = duckdb.connect(str(source))
    try:
        con.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
        con.execute(
            """
            INSERT INTO warehouse (
                warehouse_id, site_code, name, city, country, timezone,
                status, created_at, updated_at
            )
            VALUES (
                'WH-001', 'SITE-001', 'Test Warehouse', 'Jaipur', 'India', 'UTC',
                'ACTIVE', TIMESTAMPTZ '2026-10-22T00:00:00Z',
                TIMESTAMPTZ '2026-10-22T00:00:00Z'
            )
            """
        )
    finally:
        con.close()

    bundle_dir = tmp_path / "bundle"
    manifest = export_seed_bundle(
        database_path=source,
        output_dir=bundle_dir,
        tables=["warehouse"],
    )

    assert manifest["ground_truth_included"] is False
    assert manifest["tables"][0]["row_count"] == 1
    assert (bundle_dir / "warehouse.csv").is_file()
    assert (bundle_dir / "schema.sql").is_file()
    assert (bundle_dir / "seed_data.sql").is_file()
    assert (bundle_dir / "manifest.json").is_file()

    restored = tmp_path / "restored.duckdb"
    restore = duckdb.connect(str(restored))
    try:
        restore.execute((bundle_dir / "schema.sql").read_text(encoding="utf-8"))
        monkeypatch.chdir(bundle_dir)
        restore.execute((bundle_dir / "seed_data.sql").read_text(encoding="utf-8"))
        row = restore.execute(
            "SELECT warehouse_id, name, city FROM warehouse"
        ).fetchone()
        assert row == ("WH-001", "Test Warehouse", "Jaipur")
    finally:
        restore.close()
