"""
Phase 8 validation — scenario analysis.

Run:  python run.py check        (offline, free)

The checks that matter are about HONESTY OF FRAMING. A scenario engine
that reports a single confident number for a hypothetical built on
observational data is the most misleading thing this project could ship.
"""

from __future__ import annotations

# This test lives in tests/ but imports modules from src/. Adding src/ to the
# path keeps the flat "import metrics" style working from either directory.
import pathlib as _pathlib
import sys as _sys
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent.parent / "src"))

import sys

import guardrails
import scenario as S

results: list[tuple[bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    results.append((passed, name))
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def run() -> int:
    print("\n=== LEVERS ===")
    levers = S.list_levers()
    check("at least three levers defined", len(levers) >= 3, f"{len(levers)}")
    check("every lever has a bounded maximum",
          all(0 < l["max_pct"] <= 100 for l in levers))
    check("every lever names its mechanism",
          all("mechanism" in S.LEVERS[l["id"]] for l in levers))

    print("\n=== ARITHMETIC ===")
    r = S.run_scenario("reduce_escalations", 30)
    c = r["band"]["central"]
    check("scenario churn is below baseline",
          c["scenario_churn_pct"] < c["baseline_churn_pct"],
          f"{c['baseline_churn_pct']}% -> {c['scenario_churn_pct']}%")
    check("improvement equals the difference",
          abs(c["improvement_pp"]
              - (c["baseline_churn_pct"] - c["scenario_churn_pct"])) < 0.02)
    check("affected count is a share of the group",
          c["customers_affected"] <= c["group_size"],
          f"{c['customers_affected']} of {c['group_size']}")
    check("zero change is rejected",
          _raises(lambda: S.run_scenario("reduce_escalations", 0)))
    check("over-maximum change is rejected",
          _raises(lambda: S.run_scenario("reduce_escalations", 80)))
    check("unknown lever is rejected",
          _raises(lambda: S.run_scenario("nonsense", 10)))

    print("\n=== EFFICACY IS EXPLICIT AND BANDED ===")
    check("four efficacy levels reported",
          all(k in r["band"] for k in
              ("low", "central", "high", "upper_bound")))
    check("efficacy assumptions are labelled as assumptions",
          "ASSUMPTION" in S.EFFICACY["_note"].upper())
    # The band must actually widen, or reporting it is decoration.
    lo = r["band"]["low"]["improvement_pp"]
    hi = r["band"]["high"]["improvement_pp"]
    ub = r["band"]["upper_bound"]["improvement_pp"]
    check("improvement increases with efficacy", lo < hi < ub,
          f"{lo} < {hi} < {ub}")
    check("central estimate is below the upper bound",
          c["improvement_pp"] < ub,
          f"central {c['improvement_pp']} vs bound {ub}")
    # Treating the whole association as causal is the optimistic reading;
    # the central case must not sit there.
    check("central efficacy is well below 1.0",
          S.EFFICACY["central"] <= 0.5, str(S.EFFICACY["central"]))
    check("range is stated in words", "ranges from" in r["range_note"])

    print("\n=== CAUSAL CAVEAT TRAVELS WITH THE RESULT ===")
    meta = r["meta"]
    check("marked as PROJECTED", meta["data_origin"] == "PROJECTED")
    check("says it is not a forecast", "not a forecast" in meta["warning"].lower())
    check("states the data is observational",
          "observational" in meta["causal_caveat"].lower())
    check("explains what efficacy means",
          "causal" in meta["causal_caveat"].lower())
    check("economic assumptions included", "economic_assumptions" in r)

    print("\n=== GUARDRAILS ON SCENARIO NARRATION ===")
    framed = ("Under this scenario, churn would fall from 14.49% to "
              "14.10%, an improvement of 0.39 points.")
    check("hypothetical framing passes",
          guardrails.validate(framed, r)["passed"])

    unframed = "Churn decreased from 14.49% to 14.10%."
    v = guardrails.validate(unframed, r)
    check("unframed conditional is rejected", not v["passed"],
          str([x["type"] for x in v["violations"]]))

    forecast = "Under this scenario churn will drop to 14.10% next quarter."
    v = guardrails.validate(forecast, r)
    check("forecast language is rejected", not v["passed"],
          str([x["type"] for x in v["violations"]]))

    print("\n=== LEVER COMPARISON ===")
    comp = S.compare_levers(30)
    check("all levers compared", len(comp["ranked"]) == len(S.LEVERS))
    check("ranked by net value",
          all(comp["ranked"][i]["net_value"] >= comp["ranked"][i + 1]["net_value"]
              for i in range(len(comp["ranked"]) - 1)))
    check("comparison warns it is not a recommendation",
          "not a recommendation" in comp["meta"]["warning"].lower())

    print("\n=== SUMMARY ===")
    print(f"  {'lever':<44}{'affected':>10}{'gain pp':>10}{'net':>12}")
    for x in comp["ranked"]:
        print(f"  {x['label']:<44}{x['customers_affected']:>10.0f}"
              f"{x['improvement_pp']:>10.2f}{x['net_value']:>12,.0f}")

    failed = [n for ok, n in results if not ok]
    print("\n" + "=" * 60)
    if failed:
        print(f"FAILED {len(failed)}/{len(results)}: {failed}")
        return 1
    print(f"ALL {len(results)} CHECKS PASSED — scenarios are banded and "
          f"framed as hypotheticals.")
    return 0


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except S.ScenarioError:
        return True


if __name__ == "__main__":
    sys.exit(run())