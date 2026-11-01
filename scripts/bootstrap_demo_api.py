"""Create the synthetic starter database used by the hosted demo API.

The hosted preview intentionally uses a small synthetic dataset. On ephemeral
hosts, the database and bootstrap marker disappear when the instance is recycled,
so the next process start regenerates the dataset deterministically.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME_DIR = Path("/tmp/coldwatch-demo")


def _run(*arguments: str) -> None:
    command = [sys.executable, *arguments]
    print(f"\n[ColdWatch bootstrap] Running: {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


def main() -> int:
    runtime_dir = Path(
        os.getenv("COLDWATCH_RUNTIME_DIR", str(DEFAULT_RUNTIME_DIR))
    ).expanduser().resolve()
    database_path = Path(
        os.getenv("COLDWATCH_DB_PATH", str(runtime_dir / "coldwatch-api.duckdb"))
    ).expanduser().resolve()
    profile = os.getenv("COLDWATCH_DEMO_PROFILE", "starter").strip().lower()

    if profile not in {"starter", "full"}:
        raise SystemExit("COLDWATCH_DEMO_PROFILE must be 'starter' or 'full'.")

    marker = runtime_dir / "bootstrap-complete.txt"
    if marker.is_file() and database_path.is_file() and database_path.stat().st_size > 0:
        print(f"[ColdWatch bootstrap] Reusing initialized database: {database_path}", flush=True)
        return 0

    runtime_dir.mkdir(parents=True, exist_ok=True)
    database_path.parent.mkdir(parents=True, exist_ok=True)

    # Clean up an interrupted previous attempt before rebuilding from the fixed seed.
    for stale_file in (database_path, Path(f"{database_path}.wal"), marker):
        try:
            stale_file.unlink()
        except FileNotFoundError:
            pass

    generated_root = PROJECT_ROOT / "data" / "generated"
    generated_profile = generated_root / profile
    shutil.rmtree(generated_profile, ignore_errors=True)

    ground_truth_root = runtime_dir / "ground_truth"
    reports_root = runtime_dir / "reports"
    shutil.rmtree(ground_truth_root, ignore_errors=True)
    shutil.rmtree(reports_root, ignore_errors=True)
    ground_truth_root.mkdir(parents=True, exist_ok=True)
    reports_root.mkdir(parents=True, exist_ok=True)

    db = str(database_path)
    generated = str(generated_root)
    ground_truth = str(ground_truth_root)
    reports = str(reports_root)

    _run("scripts/init_db.py", "--database", db)
    _run("scripts/generate_master_data.py", "--profile", profile, "--database", db)
    _run("scripts/generate_telemetry.py", "--profile", profile, "--database", db)
    _run(
        "scripts/inject_anomalies.py",
        "--profile", profile,
        "--database", db,
        "--ground-truth-root", ground_truth,
        "--report-root", str(reports_root),
    )
    _run(
        "scripts/validate_dataset.py",
        "--profile", profile,
        "--database", db,
        "--generated-root", generated,
        "--ground-truth-root", ground_truth,
        "--report-root", str(reports_root),
    )
    _run(
        "scripts/run_risk_engine.py",
        "--profile", profile,
        "--database", db,
        "--report-root", str(reports_root / "risk-engine"),
    )

    marker.write_text(
        f"profile={profile}\ndatabase={database_path}\n",
        encoding="utf-8",
    )
    print(f"[ColdWatch bootstrap] Demo database ready: {database_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
