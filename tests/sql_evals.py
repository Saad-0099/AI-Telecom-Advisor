"""
Phase 5 validation — SQL guard tests plus head-to-head model comparison.

Run:
    python sql_evals.py                  guard tests only (free, offline)
    python sql_evals.py --live           guard tests + pipeline on default model
    python sql_evals.py --compare        benchmark two models against each other

The comparison measures which model writes better SQL against THIS schema
rather than relying on general reputation.

IMPORTANT: 429 responses are counted separately and EXCLUDED from every
denominator. A benchmark that scores infrastructure failure as capability
failure will confidently tell you the wrong thing — an earlier run of this
harness reported Qwen at 0/20 when almost every call had simply been rate
limited by the preceding model's token usage.
"""

from __future__ import annotations
# This test lives in tests/ but imports modules from src/. Adding src/ to the
# path keeps the flat "import metrics" style working from either directory.
import pathlib as _pathlib
import sys as _sys
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent.parent / "src"))

import sys
import time

import sql_guard
import text_to_sql

PASS, FAIL = 0, 0


def report(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


# ==========================================================================
# SUITE A — guard tests (no LLM)
# ==========================================================================
MUST_BLOCK = [
    ("stacked drop", "SELECT * FROM v_kpi_summary; DROP TABLE customer"),
    ("bare drop", "DROP TABLE customer"),
    ("raw table access", "SELECT * FROM customer"),
    ("sqlite_master probe", "SELECT * FROM sqlite_master"),
    ("pragma", "PRAGMA table_info(customer)"),
    ("insert", "INSERT INTO customer VALUES (1)"),
    ("update", "UPDATE churn_record SET churned = 0"),
    ("delete", "DELETE FROM customer"),
    ("attach", "ATTACH DATABASE 'evil.db' AS e"),
    ("load_extension", "SELECT load_extension('evil')"),
    ("classic injection", "'; DROP TABLE customer; --"),
    ("create view", "CREATE VIEW hack AS SELECT 1"),
    ("no from clause", "SELECT 1"),
]

MUST_ALLOW = [
    ("simple select",
     "SELECT * FROM v_risk_segments"),
    ("ordered with filter",
     "SELECT state, churn_rate_pct FROM v_churn_by_state "
     "WHERE customers >= 50 ORDER BY churn_rate_pct DESC LIMIT 5"),
    ("markdown fenced",
     "```sql\nSELECT * FROM v_kpi_summary\n```"),
    ("prose preamble stripped",
     "Here is the query:\nSELECT segment FROM v_risk_segments"),
    ("cte",
     "WITH t AS (SELECT * FROM v_churn_by_state) SELECT state FROM t LIMIT 3"),
    ("join across views",
     "SELECT c.cohort_label, r.segment FROM v_cohort_profile c "
     "JOIN v_risk_segments r ON 1=1 LIMIT 10"),
    ("aggregate",
     "SELECT service_call_bucket, SUM(customers) FROM v_churn_by_service_calls "
     "GROUP BY service_call_bucket"),
    # Regression: an earlier FORBIDDEN_KEYWORDS list included "execute",
    # which SQLite does not even have, and it rejected valid generated SQL.
    ("column alias resembling a keyword",
     "SELECT segment AS execution_priority FROM v_risk_segments"),
    ("no-time-dimension sentinel",
     "SELECT 'no time dimension' AS answer FROM v_kpi_summary LIMIT 1"),
]


def suite_guard() -> None:
    print("\n=== SUITE A: SQL guard (no LLM) ===")
    print("  -- must block --")
    for name, sql in MUST_BLOCK:
        try:
            sql_guard.validate(sql)
            report(name, False, "LEAKED — validation passed")
        except sql_guard.SQLRejected as exc:
            report(name, True, str(exc)[:50])

    print("  -- must allow --")
    for name, sql in MUST_ALLOW:
        try:
            out = sql_guard.validate(sql)
            report(name, "limit" in out.lower(), "LIMIT enforced")
        except sql_guard.SQLRejected as exc:
            report(name, False, f"wrongly blocked: {exc}")

    print("  -- limit clamping --")
    out = sql_guard.validate("SELECT * FROM v_kpi_summary LIMIT 9999")
    report("oversized LIMIT clamped", f"LIMIT {sql_guard.MAX_LIMIT}" in out, out[-20:])
    out = sql_guard.validate("SELECT * FROM v_kpi_summary")
    report("missing LIMIT injected",
           f"LIMIT {sql_guard.DEFAULT_LIMIT}" in out, out[-20:])


# ==========================================================================
# SUITE B — question set
# ==========================================================================
# expected_views: at least one must appear in the generated SQL
QUESTIONS = [
    ("Which customer segment has the highest churn rate?",
     ["v_risk_segments"]),
    ("What is the overall churn rate?",
     ["v_kpi_summary", "v_customer_profile"]),
    ("How many customers do we have in total?",
     ["v_kpi_summary", "v_customer_profile"]),
    ("Which five states have the worst churn among states with at least 50 customers?",
     ["v_churn_by_state"]),
    ("What is total revenue?",
     ["v_kpi_summary", "v_revenue_by_state", "v_revenue_by_period"]),
    ("Do customers with the international plan churn more?",
     ["v_churn_by_plan", "v_customer_profile", "v_churn_features"]),
    ("How does churn vary by number of service calls?",
     ["v_churn_by_service_calls", "v_customer_profile"]),
    ("Which tenure cohort has the most customers?",
     ["v_cohort_profile"]),
    ("What share of revenue comes from daytime calls?",
     ["v_revenue_by_period"]),
    ("How many customers have four or more service calls?",
     ["v_kpi_summary", "v_churn_by_service_calls", "v_customer_profile",
      "v_churn_features"]),
    ("List the top 10 highest-spending customers who churned",
     ["v_customer_profile", "v_churn_features"]),
    ("What is ARPU for customers on the international plan?",
     ["v_churn_by_plan", "v_customer_profile", "v_churn_features"]),
    ("Which state generates the most revenue?",
     ["v_revenue_by_state", "v_churn_by_state"]),
    ("How much revenue is at risk from churning customers?",
     ["v_kpi_summary", "v_risk_segments", "v_churn_by_state"]),
    ("Compare churn between the 0-3 and 4+ service call groups",
     ["v_churn_by_service_calls", "v_cohort_risk_matrix", "v_customer_profile"]),
    ("How many customers have both risk factors?",
     ["v_churn_features", "v_risk_segments"]),
    ("What is the average number of service calls?",
     ["v_kpi_summary", "v_customer_profile"]),
    ("Show churn rate by cohort and service call bucket",
     ["v_cohort_risk_matrix"]),
    ("What percentage of customers subscribe to the international plan?",
     ["v_kpi_summary", "v_churn_by_plan"]),
    # This one SHOULD NOT produce a real query — there is no time dimension.
    # A good model returns the 'no time dimension' sentinel from the prompt.
    ("How did churn change compared to last quarter?", []),
]


def score_model(model: str | None, label: str) -> dict:
    """Run every question through one model and score the outcome."""
    stats = {"label": label, "n": len(QUESTIONS), "valid_sql": 0,
             "executed": 0, "right_view": 0, "rows_returned": 0,
             "fallbacks": 0, "retries": 0, "rate_limited": 0,
             "errors": [], "seconds": 0.0}
    t0 = time.time()

    for question, expected in QUESTIONS:
        res = text_to_sql.ask(question, narrate_result=False, sql_model=model)
        if res["path"] == "generated_sql":
            stats["valid_sql"] += 1
            stats["executed"] += 1
            sql_lower = (res["sql"] or "").lower()
            if not expected or any(v in sql_lower for v in expected):
                stats["right_view"] += 1
            if res["rows"]:
                stats["rows_returned"] += 1
        else:
            stats["fallbacks"] += 1
            # A 429 is a quota problem, not a model-quality problem. Counting
            # it as a generation failure would make the comparison meaningless.
            if res["error"] and "rate limit" in res["error"].lower():
                stats["rate_limited"] += 1
            else:
                stats["errors"].append(f"{question[:45]} -> {res['error']}")
        if res["attempts"] > 1:
            stats["retries"] += 1

    stats["seconds"] = round(time.time() - t0, 1)
    return stats


def print_scorecard(rows: list[dict]) -> None:
    print("\n" + "=" * 74)
    print("SQL GENERATION SCORECARD")
    print("=" * 74)
    header = (f"{'model':<24}{'valid':>7}{'exec':>7}{'view':>7}"
              f"{'rows':>7}{'retry':>7}{'429':>6}{'sec':>7}")
    print(header)
    print("-" * 74)
    for s in rows:
        scored = s["n"] - s["rate_limited"]
        print(f"{s['label']:<24}{s['valid_sql']:>4}/{scored:<2}"
              f"{s['executed']:>4}/{scored:<2}{s['right_view']:>4}/{scored:<2}"
              f"{s['rows_returned']:>4}/{scored:<2}{s['retries']:>7}"
              f"{s['rate_limited']:>6}{s['seconds']:>7}")
    print("-" * 74)
    print("valid = passed the SQL guard | exec = ran without error")
    print("view  = queried an appropriate view | rows = returned data")
    print("retry = needed a second generation attempt")
    print("429   = dropped for rate limiting; EXCLUDED from the denominators")
    if any(s["rate_limited"] for s in rows):
        print("\nWARNING: rate limits hit. Scores are over a reduced sample.")
        print("Raise LLM_MIN_INTERVAL (currently "
              f"{__import__('llm_provider').MIN_CALL_INTERVAL}s) and re-run.")

    for s in rows:
        if s["errors"]:
            print(f"\n  {s['label']} genuine failures:")
            for e in s["errors"][:6]:
                print(f"    - {e[:110]}")


# ==========================================================================
def main() -> int:
    suite_guard()

    if "--compare" in sys.argv:
        from llm_provider import LLM_PROVIDER, MIN_CALL_INTERVAL, provider_status
        st = provider_status()
        print(f"\n  provider: {st['provider']} | {st['detail']}")
        if not st["available"]:
            print("  SKIPPED — provider unavailable")
        elif LLM_PROVIDER != "groq":
            print("  SKIPPED — comparison needs LLM_PROVIDER=groq")
        else:
            candidates = [
                ("llama-3.3-70b-versatile", "Llama 3.3 70B"),
                ("qwen/qwen3.6-27b", "Qwen 3.6 27B"),
            ]
            est = len(QUESTIONS) * len(candidates) * MIN_CALL_INTERVAL / 60
            print(f"  throttle: {MIN_CALL_INTERVAL}s between calls "
                  f"(~{est:.1f} min for {len(QUESTIONS) * len(candidates)} calls)")
            results = []
            for model, label in candidates:
                print(f"\n  running {label} over {len(QUESTIONS)} questions...")
                results.append(score_model(model, label))
            print_scorecard(results)

    elif "--live" in sys.argv:
        from llm_provider import provider_status
        st = provider_status()
        print(f"\n=== SUITE B: pipeline ({st['detail']}) ===")
        if not st["available"]:
            print("  SKIPPED — provider unavailable")
        else:
            s = score_model(None, "default")
            print_scorecard([s])
    else:
        print("\n(guard tests only — pass --live or --compare for model runs)")

    print("\n" + "=" * 60)
    print(f"{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())