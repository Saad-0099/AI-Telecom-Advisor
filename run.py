"""
Telecom Decision Intelligence Platform — entry point.

Puts src/ on the import path so every module can keep using flat imports
("import metrics") regardless of where the process was started from, then
dispatches to the requested command.

Usage:
    python run.py etl              rebuild the database from the CSV
    python run.py simulate         generate the SIMULATED monthly history
    python run.py views            build the SQL views
    python run.py check            run every offline check suite
    python run.py api              start the FastAPI server
    python run.py ui               start the Streamlit interface
    python run.py charts           list available charts
    python run.py export           write chart PNGs to exports/
    python run.py report           generate the PDF report into exports/
    python run.py evals [--live]   guardrail evals
    python run.py sqlevals [--live|--compare]
    python run.py all              full rebuild + all offline checks

Everything except --live and --compare runs offline and costs nothing.

ORDER MATTERS for a rebuild: etl -> simulate -> views. The simulated views
only build if the panel table already exists, so running views before
simulate silently skips them. 'all' sequences this correctly.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent
SRC = ROOT / "src"
TESTS = ROOT / "tests"
APP = ROOT / "app"
sys.path.insert(0, str(SRC))


def _run(script: pathlib.Path, *args: str) -> int:
    """Run a script in a subprocess so each gets a clean interpreter."""
    return subprocess.call([sys.executable, str(script), *args],
                           cwd=str(ROOT))


def cmd_etl(args): return _run(SRC / "etl.py", *args)
def cmd_simulate(args): return _run(SRC / "simulate_history.py", *args)
def cmd_views(args): return _run(SRC / "build_views.py", *args)
def cmd_report(args): return _run(SRC / "report_pdf.py", *args)
def cmd_evals(args): return _run(TESTS / "evals.py", *args)
def cmd_sqlevals(args): return _run(TESTS / "sql_evals.py", *args)


def cmd_check(args) -> int:
    """Every offline suite. No API calls, no quota spent."""
    suites = [
        ("Phase 1 — data", TESTS / "checks.py"),
        ("Phase 2 — views", TESTS / "checks_views.py"),
        ("Phase 6 — rules", TESTS / "checks_rules.py"),
        ("Phase 6.5 — simulated panel", TESTS / "checks_simulated.py"),
        ("Phase 4 — guardrails", TESTS / "evals.py"),
        ("Phase 5 — SQL guard", TESTS / "sql_evals.py"),
        ("Phase 7 — report", TESTS / "checks_report.py"),
        ("Phase 8 — scenarios", TESTS / "checks_scenario.py"),
    ]
    failed = []
    for label, script in suites:
        print(f"\n{'=' * 62}\n{label}\n{'=' * 62}")
        if _run(script) != 0:
            failed.append(label)
    print(f"\n{'=' * 62}")
    if failed:
        print(f"SUITES FAILED: {failed}")
        return 1
    print(f"ALL {len(suites)} SUITES PASSED")
    return 0


def cmd_api(args) -> int:
    import uvicorn
    print("Serving on http://127.0.0.1:8000  (docs at /docs)")
    uvicorn.run("api:app", host="127.0.0.1", port=8000,
                reload=True, app_dir=str(SRC))
    return 0


def cmd_ui(args) -> int:
    """Launch the Streamlit interface.

    Run through streamlit's own module rather than importing it, so the
    app gets a proper script-run context. Extra args pass straight
    through, e.g. `python run.py ui --server.port 8600`.
    """
    entry = APP / "app.py"
    if not entry.exists():
        print(f"Streamlit app not found at {entry}")
        return 1
    print("Starting the interface on http://localhost:8501")
    return subprocess.call(
        [sys.executable, "-m", "streamlit", "run", str(entry), *args],
        cwd=str(ROOT))


def cmd_charts(args) -> int:
    import chart_specs
    problems = chart_specs.validate_specs()
    for c in chart_specs.list_charts():
        tag = "  [SIMULATED]" if c.get("simulated") else ""
        print(f"  {c['id']:<26} {c['kind']:<13} {c['title']}{tag}")
    if problems:
        print("\nSPEC PROBLEMS:")
        for p in problems:
            print(f"  - {p}")
        return 1
    return 0


def cmd_export(args) -> int:
    import charts
    out = args[0] if args else None
    for f in charts.export_all(out):
        print("wrote", f)
    return 0


def cmd_all(args) -> int:
    """Full rebuild from the CSV, then every offline check.

    simulate runs BEFORE views: build_views.py skips the simulated view
    file when customer_snapshot_simulated does not exist yet.
    """
    for step in (cmd_etl, cmd_simulate, cmd_views):
        if step([]) != 0:
            print("build step failed; stopping")
            return 1
    return cmd_check([])


COMMANDS = {
    "etl": cmd_etl,
    "simulate": cmd_simulate,
    "views": cmd_views,
    "check": cmd_check,
    "api": cmd_api,
    "ui": cmd_ui,
    "charts": cmd_charts,
    "export": cmd_export,
    "report": cmd_report,
    "evals": cmd_evals,
    "sqlevals": cmd_sqlevals,
    "all": cmd_all,
}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        if len(sys.argv) > 1:
            print(f"Unknown command: {sys.argv[1]}")
            return 1
        return 0
    return COMMANDS[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    sys.exit(main())