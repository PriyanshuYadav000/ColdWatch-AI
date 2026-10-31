# Database schemas

- `schema.sql` is the canonical DuckDB relational schema.
- `data_dictionary.md` documents every column, key, enum, unit, uniqueness rule, and validation rule.
- `../docs/erd.md` documents entity relationships, cardinality, optionality, effective dates, and lifecycle-history design.

## Initialize a local database

From the repository root, activate the project virtual environment and run:

```bash
python scripts/init_db.py
```

The default local database path is `data/coldwatch.duckdb`. Local DuckDB files are excluded from Git by `.gitignore`.

Use a temporary database path when testing:

```bash
python scripts/init_db.py --database /tmp/coldwatch-test.duckdb
```

The schema is safe to apply repeatedly because it uses `CREATE TABLE IF NOT EXISTS`. It creates empty tables only; generation and seed data are separate project steps.

## Validate schema

```bash
pytest -q tests/test_schema.py
```

The tests check expected table creation, repeatable schema application, foreign-key enforcement, paired requirement bounds, and alarm evidence reference integrity.
