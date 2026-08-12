"""
Phase 2 — build the metric views.

Run:  python run.py views
Idempotent: every view is dropped and recreated.

Two SQL files are executed:

  views.sql            the real snapshot views. Always built.
  views_simulated.sql  the Phase 6.5 simulated-history views. Built ONLY if
                       the panel table exists, so a fresh clone works
                       without running the simulator first.
"""

from __future__ import annotations

import logging
import pathlib
import sys

from sqlalchemy import create_engine, text

import config as C

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s",
                    stream=sys.stdout)
log = logging.getLogger("views")

# Both .sql files live beside this module in src/, not at the project root.
HERE = pathlib.Path(__file__).resolve().parent
VIEWS_SQL = HERE / "views.sql"
VIEWS_SIM_SQL = HERE / "views_simulated.sql"

SIM_TABLE = "customer_snapshot_simulated"


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


def _execute_file(engine, path: pathlib.Path) -> int:
    statements = split_statements(path.read_text(encoding="utf-8"))
    log.info("Executing %d statements from %s", len(statements), path.name)
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
    return len(statements)


def run():
    if not VIEWS_SQL.exists():
        raise FileNotFoundError(f"Missing {VIEWS_SQL}")

    engine = create_engine(C.DB_URL)
    _execute_file(engine, VIEWS_SQL)

    # --- simulated-history views (optional) --------------------------------
    with engine.connect() as conn:
        has_panel = conn.execute(text(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
            f"AND name='{SIM_TABLE}'")).scalar()

    if VIEWS_SIM_SQL.exists() and has_panel:
        _execute_file(engine, VIEWS_SIM_SQL)
    elif VIEWS_SIM_SQL.exists():
        log.info("Skipping %s: table '%s' not found. "
                 "Run 'python run.py simulate' first.",
                 VIEWS_SIM_SQL.name, SIM_TABLE)

    # --- report ------------------------------------------------------------
    with engine.connect() as conn:
        views = [r[0] for r in conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='view' ORDER BY name"))]

    real = [v for v in views if not v.startswith("v_sim_")]
    sim = [v for v in views if v.startswith("v_sim_")]

    log.info("Created %d views (%d real, %d simulated):",
             len(views), len(real), len(sim))
    for v in views:
        with engine.connect() as conn:
            n = conn.execute(text(f"SELECT COUNT(*) FROM {v}")).scalar()
        tag = "  [SIMULATED]" if v.startswith("v_sim_") else ""
        log.info("  %-28s %6d rows%s", v, n, tag)

    log.info("Views built.")
    return views


if __name__ == "__main__":
    run()