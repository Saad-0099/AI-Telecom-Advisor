"""
Phase 2 — build the metric views.

Run:  python build_views.py
Idempotent: every view is dropped and recreated.
"""

from __future__ import annotations

import logging
import sys

from sqlalchemy import create_engine, text

import config as C

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s",
                    stream=sys.stdout)
log = logging.getLogger("views")

VIEWS_SQL = C.PROJECT_ROOT / "views.sql"


def split_statements(sql: str) -> list[str]:
    """Strip '--' comments FIRST, then split on semicolons.

    Order matters: comment text may itself contain a semicolon, which
    would corrupt the split if done the other way round.
    """
    stripped = "\n".join(
        line for line in sql.splitlines()
        if not line.strip().startswith("--")
    )
    return [s.strip() for s in stripped.split(";") if s.strip()]


def run():
    if not VIEWS_SQL.exists():
        raise FileNotFoundError(f"Missing {VIEWS_SQL}")

    engine = create_engine(C.DB_URL)
    statements = split_statements(VIEWS_SQL.read_text(encoding="utf-8"))
    log.info("Executing %d statements from views.sql", len(statements))

    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))

    with engine.connect() as conn:
        views = [r[0] for r in conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='view' ORDER BY name"))]

    log.info("Created %d views:", len(views))
    for v in views:
        with engine.connect() as conn:
            n = conn.execute(text(f"SELECT COUNT(*) FROM {v}")).scalar()
        log.info("  %-28s %6d rows", v, n)

    log.info("Phase 2 views built.")
    return views


if __name__ == "__main__":
    run()