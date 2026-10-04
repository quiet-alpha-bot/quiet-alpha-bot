"""Validate the schema and every SQL statement against a real PostgreSQL.

Usage:
    PSQL_ARGS="-h HOST -p PORT -U USER" python tests/validate_sql.py DBNAME

* creates a fresh database, applies the migration twice (idempotency)
* PREPAREs every statement in automation/queries.py (syntax + types)
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation import queries  # noqa: E402

PSQL = ["psql", "-v", "ON_ERROR_STOP=1", "-q", "-X"] + shlex.split(os.getenv("PSQL_ARGS", ""))


def run(db: str, sql: str) -> None:
    result = subprocess.run(PSQL + ["-d", db], input=sql, text=True, capture_output=True)
    if result.returncode != 0:
        raise SystemExit(f"SQL failed:\n{sql[:400]}\n---\n{result.stderr}")


def main() -> None:
    db = sys.argv[1] if len(sys.argv) > 1 else "qa_validate"
    run("postgres", f"DROP DATABASE IF EXISTS {db}; CREATE DATABASE {db};")
    migrations = sorted((ROOT / "automation" / "migrations").glob("*.sql"))
    for _ in range(2):  # idempotent
        for path in migrations:
            run(db, path.read_text())
    print(f"{len(migrations)} migrations applied twice: OK")

    count = 0
    for name in sorted(dir(queries)):
        value = getattr(queries, name)
        if not name.isupper() or not isinstance(value, str):
            continue
        run(db, f"PREPARE stmt_{name.lower()} AS {value};")
        count += 1
    print(f"prepared {count} statements: OK")


if __name__ == "__main__":
    main()
