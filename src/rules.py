"""
Phase 6 — recommendation rules.

Rules are DATA, not code and not prompt text. Three consequences:

  1. Every recommendation is deterministic and reproducible.
  2. Tuning a threshold is a one-line edit here, not a prompt rewrite.
  3. Each rule traces to an exportable list of real customer IDs.

The LLM's only job in this phase is writing the justification paragraph.
It computes nothing.

ECONOMIC ASSUMPTIONS are declared separately below and travel with every
result. They are ASSUMPTIONS, not measurements — the dataset contains no
cost data. Never present derived figures as observed facts.
"""

from __future__ import annotations

# ==========================================================================
# ECONOMIC ASSUMPTIONS
# ==========================================================================
# None of these come from the data. They are industry-typical placeholders
# that make expected-impact arithmetic possible. Every output that depends
# on them is labelled so, and the values ship inside the payload so any
# reader can see what was assumed.
ECONOMICS = {
    "_note": "ASSUMPTIONS, not measurements. The dataset has no cost data.",

    # What it costs to run one retention intervention (call + admin).
    "retention_contact_cost": 8.00,

    # What it costs to acquire a replacement customer.
    "acquisition_cost": 45.00,

    # Fraction of at-risk customers a well-targeted intervention retains.
    # This is the single most uncertain number here; sensitivity to it is
    # reported alongside every result.
    "assumed_save_rate": 0.30,

    # Sensitivity band explored in the output.
    "save_rate_low": 0.15,
    "save_rate_high": 0.45,

    # Horizon over which retained revenue is counted. The data is a single
    # snapshot of period charges, so "value" means N periods of the
    # customer's observed charge, NOT an annualised figure.
    "value_horizon_periods": 12,
}


# ==========================================================================
# RULES
# ==========================================================================
# `where` is a SQL predicate evaluated against v_churn_features.
# Rules are evaluated in priority order; each customer is assigned to the
# FIRST rule they match, so segments are mutually exclusive and counts sum
# to the customer base.

# Three confirmed churn drivers, each an independent CLIFF:
#   high_service_calls   4+ calls          10.3% -> 45.8%
#   international_plan   subscriber        11.5% -> 42.4%
#   heavy_day_usage      day_charge >= 45   ~5%  -> 59.0%
#
# The third was found while building this phase: Phase 2's v_risk_segments
# reported a "baseline" of 8.2%, which concealed 166 customers churning at
# 59%. With all three drivers separated, the true no-driver baseline is 5.0%.
#
# day_charge >= 45 is roughly 265 daytime minutes at $0.17/min. The effect is
# independent: it raises churn in every combination of the other two.
HEAVY_DAY_CHARGE = 45.0

RULES = [
    {
        "id": "R1_triple",
        "name": "Critical — all three risk drivers",
        "priority": 1,
        "where": (f"high_service_calls = 1 AND international_plan = 1 "
                  f"AND day_charge >= {HEAVY_DAY_CHARGE}"),
        "action": "Immediate senior retention call, same day",
        "offer": "Premium support + tariff review + 20% discount, 3 periods",
        "offer_discount_pct": 20,
        "rationale_facts": [
            "every confirmed churn driver present simultaneously",
            "very small segment, so individual handling is feasible",
        ],
    },
    {
        "id": "R2_calls_plus",
        "name": "Severe — 4+ service calls with a second driver",
        "priority": 2,
        "where": ("high_service_calls = 1 AND (international_plan = 1 "
                  f"OR day_charge >= {HEAVY_DAY_CHARGE})"),
        "action": "Senior retention specialist call within 48 hours",
        "offer": "Premium support tier + 15% discount for 3 periods",
        "offer_discount_pct": 15,
        "rationale_facts": [
            "service calls are the strongest single driver",
            "a second driver compounds the risk substantially",
            "unresolved support issues are addressable, unlike tariff fit",
        ],
    },
    {
        "id": "R3_heavy_day",
        "name": "Severe — heavy daytime usage",
        "priority": 3,
        "where": f"day_charge >= {HEAVY_DAY_CHARGE}",
        "action": "Tariff review call within 5 working days",
        "offer": "Move to a daytime bundle or higher-allowance plan",
        "offer_discount_pct": 10,
        "rationale_facts": [
            "heavy daytime callers churn at many times the baseline rate",
            "the pattern suggests bill shock rather than a service failure",
            "a tariff that fits actual usage addresses the cause directly",
        ],
    },
    {
        "id": "R4_high_calls",
        "name": "High — 4+ service calls only",
        "priority": 4,
        "where": "high_service_calls = 1",
        "action": "Proactive support review call within 5 working days",
        "offer": "Service credit + dedicated support contact",
        "offer_discount_pct": 10,
        "rationale_facts": [
            "churn jumps sharply at the 4-call threshold rather than rising "
            "gradually with call volume",
            "the effect holds in every tenure cohort",
        ],
    },
    {
        "id": "R5_intl_plan",
        "name": "Elevated — international plan only",
        "priority": 5,
        "where": "international_plan = 1",
        "action": "Plan review — check the tariff fits actual usage",
        "offer": "Tariff optimisation or downgrade without penalty",
        "offer_discount_pct": 0,
        "rationale_facts": [
            "international plan holders churn at several times baseline",
            "likely a pricing or fit problem rather than a service problem",
        ],
    },
    {
        "id": "R6_baseline",
        "name": "Baseline — no risk drivers",
        "priority": 6,
        "where": "1 = 1",   # catch-all; must remain last
        "action": "No action",
        "offer": None,
        "offer_discount_pct": 0,
        "rationale_facts": [
            "no confirmed churn driver present",
            "churn near the portfolio floor",
        ],
    },
]


def rules_by_priority() -> list[dict]:
    return sorted(RULES, key=lambda r: r["priority"])


def validate_rules() -> list[str]:
    """Structural checks. Returns a list of problems; empty means valid."""
    problems = []
    seen_ids, seen_priorities = set(), set()

    for rule in RULES:
        for field in ("id", "name", "priority", "where", "action"):
            if field not in rule:
                problems.append(f"{rule.get('id', '?')}: missing '{field}'")
        if rule["id"] in seen_ids:
            problems.append(f"duplicate rule id: {rule['id']}")
        seen_ids.add(rule["id"])
        if rule["priority"] in seen_priorities:
            problems.append(f"duplicate priority: {rule['priority']}")
        seen_priorities.add(rule["priority"])

    ordered = rules_by_priority()
    if ordered[-1]["where"].strip() != "1 = 1":
        problems.append(
            "the lowest-priority rule must be the catch-all '1 = 1', "
            "otherwise some customers match no rule and vanish from the "
            "totals")
    for rule in ordered[:-1]:
        if rule["where"].strip() == "1 = 1":
            problems.append(
                f"{rule['id']} is a catch-all but is not last; every "
                f"lower-priority rule would be unreachable")
    return problems