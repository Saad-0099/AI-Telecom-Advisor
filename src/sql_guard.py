"""
Phase 5 — SQL guard.

Defence in depth. Three independent layers, any one of which would stop a
destructive query on its own:

  1. THIS MODULE      static validation before execution
  2. READ-ONLY CONN   SQLite opened mode=ro; writes fail at the driver
  3. VIEW ALLOWLIST   only the 11 curated views are reachable

Layer 2 is the one that actually guarantees safety — a parser can always be
fooled by something clever. Layers 1 and 3 exist to fail fast with a useful
message and to keep the model on the curated metrics rather than raw tables.
"""

from __future__ import annotations

import re

import sqlparse

# --------------------------------------------------------------------------
# Only these are queryable. Raw tables are deliberately excluded: the views
# carry the frozen metric definitions from Phase 2, so routing the model
# through them keeps generated answers consistent with the dashboard.
ALLOWED_VIEWS = {
    "v_customer_profile",
    "v_churn_by_day_usage",
    "v_churn_features",
    "v_kpi_summary",
    "v_churn_by_state",
    "v_revenue_by_state",
    "v_churn_by_service_calls",
    "v_churn_by_plan",
    "v_cohort_profile",
    "v_cohort_risk_matrix",
    "v_revenue_by_period",
    "v_risk_segments",
}

# SQLite-relevant only. An earlier version included exec/execute/call/
# merge/grant/revoke, none of which exist in SQLite, and "execute" caused a
# false rejection of a perfectly valid generated query. Blocking keywords
# the dialect does not have adds no safety and costs accuracy.
FORBIDDEN_KEYWORDS = {
    "insert", "update", "delete", "drop", "alter", "create", "replace",
    "truncate", "attach", "detach", "pragma", "vacuum", "reindex",
    "load_extension", "writefile", "readfile", "edit",
}

MAX_LIMIT = 200
DEFAULT_LIMIT = 50

FENCE_RE = re.compile(r"^\s*```(?:sql)?\s*|\s*```\s*$", re.IGNORECASE)
TABLE_RE = re.compile(r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)",
                      re.IGNORECASE)
LIMIT_RE = re.compile(r"\blimit\s+(\d+)", re.IGNORECASE)


class SQLRejected(ValueError):
    """Raised when generated SQL fails validation."""

# Reasoning models wrap deliberation in tags before the answer. Those blocks
# contain words like "select" in ordinary prose, so they must be removed
# BEFORE any statement extraction or the extractor slices out English.
THINK_RE = re.compile(
    r"<(think|thinking|reasoning|scratchpad)>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
# Unterminated block: the response was truncated mid-thought.
OPEN_THINK_RE = re.compile(
    r"<(think|thinking|reasoning|scratchpad)>.*$",
    re.IGNORECASE | re.DOTALL,
)

# Reasoning models wrap deliberation in tags before the answer. Those blocks
# contain words like "select" in ordinary prose ("I will select the risk
# segments view"), so they must be removed BEFORE any statement extraction
# or the extractor slices out English instead of SQL. This is what produced
# 0/20 when Qwen 3.6 was benchmarked; GPT OSS 120B also lists `reasoning`
# among its supported features.
THINK_RE = re.compile(
    r"<(think|thinking|reasoning|analysis|scratchpad)>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
# Unterminated block: the response was truncated mid-thought.
OPEN_THINK_RE = re.compile(
    r"<(think|thinking|reasoning|analysis|scratchpad)>.*$",
    re.IGNORECASE | re.DOTALL,
)

def strip_markdown(sql: str) -> str:
    """Pull the statement out of reasoning traces, code fences and prose.

    Order matters: reasoning blocks first, then a fenced ```sql block if one
    survives (the model's own signal of where the statement is, and more
    reliable than keyword scanning), and only then a scan for the first
    SELECT as a last resort.
    """
    sql = THINK_RE.sub(" ", sql)
    sql = OPEN_THINK_RE.sub(" ", sql)
    sql = FENCE_RE.sub("", sql.strip())

    fenced = re.search(r"```sql\s*(.+?)```", sql, re.IGNORECASE | re.DOTALL)
    if fenced:
        sql = fenced.group(1)

    m = re.search(r"\b(select|with)\b", sql, re.IGNORECASE)
    if m:
        sql = sql[m.start():]
    return sql.strip().rstrip(";").strip()

def _strip_comments(sql: str) -> str:
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return sql


def validate(sql: str, allowed: set[str] | None = None) -> str:
    """Validate and normalise generated SQL. Returns the safe statement.

    Raises SQLRejected with a specific reason on any violation.
    """
    allowed = allowed or ALLOWED_VIEWS
    sql = strip_markdown(sql)

    if not sql:
        raise SQLRejected("empty statement")

    # --- single statement only ----------------------------------------
    statements = [s for s in sqlparse.split(sql) if s.strip()]
    if len(statements) > 1:
        raise SQLRejected(
            f"multiple statements ({len(statements)}); only one SELECT allowed")

    bare = _strip_comments(sql).lower()

    # --- must be a read ------------------------------------------------
    parsed = sqlparse.parse(sql)
    if not parsed:
        raise SQLRejected("could not parse statement")
    stmt_type = parsed[0].get_type()
    if stmt_type != "SELECT":
        raise SQLRejected(f"statement type is {stmt_type}, expected SELECT")

    if not re.match(r"^\s*(select|with)\b", bare):
        raise SQLRejected("statement must begin with SELECT or WITH")

    # --- forbidden keywords --------------------------------------------
    for kw in FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{re.escape(kw)}\b", bare):
            raise SQLRejected(f"forbidden keyword: {kw}")

    # --- table allowlist ------------------------------------------------
    referenced = {t.lower() for t in TABLE_RE.findall(bare)}
    # CTE names defined in this query are legitimate references.
    cte_names = {m.lower() for m in re.findall(
        r"\b(?:with|,)\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+as\s*\(", bare)}
    unknown = referenced - allowed - cte_names
    if unknown:
        raise SQLRejected(
            f"references non-allowlisted objects: {sorted(unknown)}. "
            f"Query only these views: {sorted(allowed)}")
    if not referenced:
        raise SQLRejected("no FROM clause found")

    # --- enforce a LIMIT ------------------------------------------------
    m = LIMIT_RE.search(bare)
    if m:
        n = int(m.group(1))
        if n > MAX_LIMIT:
            sql = LIMIT_RE.sub(f"LIMIT {MAX_LIMIT}", sql, count=1)
    else:
        sql = f"{sql}\nLIMIT {DEFAULT_LIMIT}"

    return sql


def describe_schema() -> str:
    """Schema text injected into the SQL-generation prompt."""
    return """\
Available views (query ONLY these — raw tables are not accessible):

v_kpi_summary            one row. total_customers, churned_customers,
                         churn_rate_pct, total_revenue, arpu,
                         revenue_at_risk, states_covered,
                         intl_plan_subscribers, intl_plan_adoption_pct,
                         high_service_call_customers, avg_service_calls

v_customer_profile       one row per customer. customer_id, state, area_code,
                         account_length_days, cohort_label, cohort_sort,
                         international_plan, voice_mail_plan,
                         day/eve/night_minutes|calls|charge, intl_minutes,
                         intl_calls, intl_charge, total_minutes, total_charge,
                         customer_service_calls, high_service_calls,
                         service_call_bucket ('0-3' | '4+'), churned

v_churn_features         per customer. customer_id, state, cohort_label,
                         account_length_days, international_plan,
                         voice_mail_plan, customer_service_calls,
                         service_call_bucket, high_service_calls,
                         total_charge, total_minutes, day_charge,
                         intl_charge, risk_factor_count (0-2), churned

v_churn_by_state         per state. state, customers, churned,
                         churn_rate_pct, revenue, arpu, revenue_at_risk

v_revenue_by_state       per state. state, customers, revenue, arpu,
                         revenue_share_pct, churn_rate_pct

v_churn_by_service_calls per call count. customer_service_calls,
                         service_call_bucket, customers, churned,
                         churn_rate_pct, arpu

v_churn_by_plan          per plan combo. intl_plan, vmail_plan, customers,
                         churned, churn_rate_pct, arpu, revenue

v_cohort_profile         per tenure cohort. cohort_id, cohort_label,
                         cohort_sort, customers, share_pct, churned,
                         churn_rate_pct, revenue, arpu, avg_service_calls,
                         intl_plan_pct

v_cohort_risk_matrix     cohort x bucket. cohort_label, cohort_sort,
                         service_call_bucket, customers, churned,
                         churn_rate_pct, revenue

v_revenue_by_period      per period. period ('day'|'eve'|'night'|'intl'),
                         revenue, minutes, calls, avg_charge_per_customer

v_risk_segments          per segment. segment, customers, churned,
                         churn_rate_pct, revenue, arpu, revenue_at_risk

v_churn_by_day_usage     per day-charge band. day_charge_band,
                         day_usage_bucket ('heavy' | 'normal'), customers,
                         churned, churn_rate_pct, avg_day_minutes, revenue                         

NOTES
- Dialect is SQLite.
- Booleans are stored as 0/1.
- There is NO date or time column anywhere. The data is one snapshot.
- Percentages (churn_rate_pct, share_pct) are ALREADY multiplied by 100.
- State-level rows are small (~65 customers each); filter customers >= 50
  when ranking states, or the result is statistical noise.
- THREE churn drivers, all cliffs: 4+ service calls, international plan,
  and day_charge >= 45. Never model any of them as continuous.
"""