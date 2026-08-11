"""
Phase 6 validation — rules engine.

Run:  python checks_rules.py        (offline, free — no LLM calls)

The important checks are exhaustiveness and mutual exclusivity. If rules
overlap or leave gaps, customers silently vanish from the totals and every
downstream recommendation is quietly wrong.
"""

from __future__ import annotations

import sys

import recommend
import rules as R

results: list[tuple[bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    results.append((passed, name))
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def run() -> int:
    print("\n=== RULE DEFINITIONS ===")
    problems = R.validate_rules()
    check("rule definitions are structurally valid", not problems, str(problems))
    check("catch-all rule is last",
          R.rules_by_priority()[-1]["where"].strip() == "1 = 1")

    print("\n=== ASSIGNMENT ===")
    df = recommend.assignments()
    check("every customer is assigned a rule", df.rule_id.notna().all(),
          f"{int(df.rule_id.isna().sum())} unassigned")
    check("no customer assigned twice", len(df) == df.customer_id.nunique())

    print("\n=== RECONCILIATION AGAINST PORTFOLIO ===")
    rec = recommend.reconcile()
    for key, val in rec.items():
        check(f"{key} reconciles", val["match"],
              f"segments={val['segments']} portfolio={val['portfolio']}")

    print("\n=== SEGMENT SANITY ===")
    segments = recommend.evaluate()
    populated = [s for s in segments if s["observed"]["customers"] > 0]
    check("all rules match at least one customer",
          len(populated) == len(R.RULES),
          f"{len(R.RULES) - len(populated)} empty")

    rates = [(s["rule_id"], s["observed"]["churn_rate_pct"]) for s in populated]
    ordered = all(rates[i][1] >= rates[i + 1][1] for i in range(len(rates) - 1))
    check("churn rate decreases with priority", ordered,
          " > ".join(f"{r}={v}%" for r, v in rates))

    baseline = [s for s in populated if s["rule_id"] == "R6_baseline"][0]
    check("baseline churn is below 10%",
          baseline["observed"]["churn_rate_pct"] < 10,
          f"{baseline['observed']['churn_rate_pct']}%")

    print("\n=== PROJECTIONS ARE LABELLED ===")
    for s in populated:
        if s.get("projected"):
            check(f"{s['rule_id']} projection carries a basis note",
                  "_basis" in s["projected"])
            check(f"{s['rule_id']} reports sensitivity",
                  "sensitivity" in s["projected"])
            break

    payload = recommend.recommendations_payload()
    check("payload ships the economic assumptions", "economics" in payload)
    check("assumptions are marked as assumptions",
          "ASSUMPTION" in payload["economics"]["_note"].upper())

    print("\n=== CUSTOMER LISTS ARE EXPORTABLE ===")
    sample = recommend.customers_for_rule("R3_heavy_day", limit=10)
    check("target list returns real customer ids",
          len(sample) > 0 and "customer_id" in sample.columns,
          f"{len(sample)} rows")
    try:
        recommend.customers_for_rule("nonsense")
        check("unknown rule id is rejected", False, "no error raised")
    except ValueError:
        check("unknown rule id is rejected", True)

    print("\n=== SEGMENT SUMMARY ===")
    print(f"  {'rule':<18}{'n':>6}{'churn%':>9}{'revenue':>12}{'net (proj)':>13}")
    for s in populated:
        o = s["observed"]
        p = s.get("projected")
        net = "-" if not p else f"{p['expected']['net_value']:,.0f}"
        print(f"  {s['rule_id']:<18}{o['customers']:>6}{o['churn_rate_pct']:>9}"
              f"{o['revenue']:>12,.0f}{net:>13}")

    failed = [n for ok, n in results if not ok]
    print("\n" + "=" * 60)
    if failed:
        print(f"FAILED {len(failed)}/{len(results)}: {failed}")
        return 1
    print(f"ALL {len(results)} CHECKS PASSED — Phase 6 complete.")
    return 0


if __name__ == "__main__":
    sys.exit(run())