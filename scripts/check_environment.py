"""Check that the ColdWatch development environment is installed."""
from __future__ import annotations

import importlib
import sys

REQUIRED_MODULES = {
    "numpy": "numpy",
    "pandas": "pandas",
    "pyarrow": "pyarrow",
    "duckdb": "duckdb",
    "fastapi": "fastapi",
    "pydantic": "pydantic",
    "uvicorn": "uvicorn",
    "streamlit": "streamlit",
    "pytest": "pytest",
}


def main() -> int:
    print(f"Python: {sys.version.split()[0]}")
    if sys.version_info[:2] != (3, 12):
        print("WARNING: This project targets Python 3.12.x.")

    missing: list[str] = []
    for module_name, package_name in REQUIRED_MODULES.items():
        try:
            module = importlib.import_module(module_name)
            version = getattr(module, "__version__", "installed")
            print(f"[OK] {package_name}: {version}")
        except ImportError:
            print(f"[MISSING] {package_name}")
            missing.append(package_name)

    if missing:
        print("\nInstall missing dependencies with: python -m pip install -r requirements.txt")
        return 1

    print("\nEnvironment check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
