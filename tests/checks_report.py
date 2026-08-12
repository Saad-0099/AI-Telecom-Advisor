"""
Phase 7 validation — executive report.

Run:  python run.py check        (offline, free — uses the mock provider)

The checks that matter are about DISCLOSURE. A report that mixes measured,
assumed and simulated figures without marking them is misleading even when
every individual number is correct.
"""

from __future__ import annotations

# This test lives in tests/ but imports modules from src/. Adding src/ to the
# path keeps the flat "import metrics" style working from either directory.
import pathlib as _pathlib
import sys as _sys
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent.parent / "src"))

import sys
import tempfile

import report_content as RC
import rules as R

results: list[tuple[bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    results.append((passed, name))
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def run() -> int:
    content = RC.build(include_charts=False)
    sections = {s["id"]: s for s in content["sections"]}

    print("\n=== STRUCTURE ===")
    for sid in ("kpi", "drivers", "segments", "caveats"):
        check(f"section '{sid}' present", sid in sections)
    check("title does not imply a time period",
          not any(w in content["title"].lower()
                  for w in ("weekly", "monthly", "quarterly", "annual")),
          content["title"])

    print("\n=== THREE NUMBER TYPES ARE DISTINGUISHED ===")
    check("legend explains all three", len(content["legend"]) == 3)
    legend_text = " ".join(d for _, d in content["legend"]).upper()
    for word in ("OBSERVED", "PROJECTED", "SIMULATED"):
        check(f"legend defines {word}", word in legend_text)

    seg = sections["segments"]
    net_cells = [c for row in seg["table"]["rows"] for c in row
                 if "$" in str(c) and str(c) != "-"]
    marked = [c for c in net_cells if RC.MARK["projected"] in str(c)]
    check("projected figures carry the dagger", len(marked) > 0,
          f"{len(marked)} marked cells")

    check("assumptions travel with the projections",
          "assumptions" in seg and "assumed_save_rate" in seg["assumptions"])
    check("assumptions are labelled as assumptions",
          "ASSUMPTION" in R.ECONOMICS["_note"].upper())

    print("\n=== SIMULATED SECTION IS QUARANTINED ===")
    if "structure" in sections:
        sim = sections["structure"]
        check("marked as simulated in its kind", sim["kind"] == "simulated")
        check("title carries the simulated mark",
              RC.MARK["simulated"] in sim["title"])
        check("has a warning banner", bool(sim.get("banner")))
        banner = sim.get("banner", "").upper()
        check("banner says the history is simulated", "SIMULATED" in banner)
        check("banner forbids trend claims",
              "FLAT BY CONSTRUCTION" in banner or "CANNOT SUPPORT" in banner)
        # The narration itself must disclose, not just the banner: a reader
        # may quote the paragraph without the surrounding chrome. The
        # GUARDRAIL enforces this at runtime (undisclosed_simulation), so
        # this check only applies to a real model — the mock provider emits
        # fixed text and would fail it spuriously.
        import llm_provider
        text = (sim.get("narration") or {}).get("text") or ""
        if text and llm_provider.LLM_PROVIDER != "mock":
            check("narration discloses simulation",
                  any(w in text.lower() for w in
                      ("simulat", "synthetic", "generated")),
                  text[:70])
        else:
            print("  (skipping narration-disclosure check on mock provider; "
                  "the guardrail enforces it against a real model)")
    else:
        print("  (simulated panel absent — section correctly omitted)")

    print("\n=== CAVEATS ARE STATED, NOT FOOTNOTED ===")
    caveats = " ".join(sections["caveats"]["bullets"]).upper()
    for topic, needle in [
        ("no time dimension", "NO TIME DIMENSION"),
        ("tenure is not a driver", "TENURE IS NOT A DRIVER"),
        ("state figures are noisy", "NOISY"),
        ("projections rest on assumptions", "ASSUMPTIONS"),
        ("revenue is period charges", "PERIOD CHARGES"),
        ("risk is not predicted", "NOT PREDICTED"),
    ]:
        check(f"caveat: {topic}", needle in caveats)

    print("\n=== NARRATION IS VALIDATED ===")
    check("all narration passed guardrails", content["narration_valid"],
          str(content["invalid_sections"]))
    narrated = [s for s in content["sections"] if s.get("narration")]
    check("every narrated section carries its validation report",
          all("validation" in s["narration"] or "error" in s["narration"]
              for s in narrated),
          f"{len(narrated)} narrated sections")

    print("\n=== RECONCILIATION IS REPORTED ===")
    rec = content["reconciliation"]
    for key in ("customers", "churned", "revenue"):
        check(f"{key} reconciles", rec[key]["match"])

    print("\n=== PDF RENDERS ===")
    try:
        import report_pdf
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
            path = fh.name
        report_pdf.render(content, path)
        size = _pathlib.Path(path).stat().st_size
        check("PDF written", size > 5000, f"{size:,} bytes")
        _pathlib.Path(path).unlink(missing_ok=True)
    except ImportError as exc:
        check("PDF written", False, f"reportlab missing: {exc}")

    failed = [n for ok, n in results if not ok]
    print("\n" + "=" * 60)
    if failed:
        print(f"FAILED {len(failed)}/{len(results)}: {failed}")
        return 1
    print(f"ALL {len(results)} CHECKS PASSED — report discloses its "
          f"number types.")
    return 0


if __name__ == "__main__":
    sys.exit(run())