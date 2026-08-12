"""
Phase 6 — recommendation engine.

    v_churn_features  →  rules  →  segments + computed impact  →  LLM justification

Every number is computed here in SQL or Python. The LLM receives the
finished figures and writes prose around them; the Phase 4 guardrails then
verify that every figure it used came from the payload.

Two kinds of number appear in the output and they are labelled differently:

  OBSERVED   counts, churn rates, revenue — measured from the data
  PROJECTED  expected saves, net value, ROI — derived from ECONOMICS,
             which are assumptions, not measurements
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import text

import guardrails
import metrics
import prompts
import rules as R
from llm_provider import LLMError, get_provider_for


# ==========================================================================
# Segment evaluation
# ==========================================================================
def _assignment_sql() -> str:
    """CASE expression assigning each customer to their first matching rule."""
    branches = "\n".join(
        f"        WHEN {r['where']} THEN '{r['id']}'"
        for r in R.rules_by_priority()
    )
    return f"""
        SELECT
            customer_id,
            state,
            cohort_label,
            customer_service_calls,
            international_plan,
            total_charge,
            churned,
            CASE
{branches}
            END AS rule_id
        FROM v_churn_features
    """


def assignments() -> pd.DataFrame:
    """One row per customer with their assigned rule."""
    with metrics.engine().connect() as conn:
        return pd.read_sql(text(_assignment_sql()), conn)


def evaluate() -> list[dict]:
    """Evaluate every rule. Returns segments with observed + projected figures."""
    df = assignments()
    econ = R.ECONOMICS
    total_customers = len(df)
    out = []

    for rule in R.rules_by_priority():
        seg = df[df.rule_id == rule["id"]]
        n = len(seg)

        if n == 0:
            out.append({
                "rule_id": rule["id"], "name": rule["name"],
                "priority": rule["priority"], "action": rule["action"],
                "offer": rule["offer"], "observed": {"customers": 0},
                "projected": None,
                "note": "no customers match this rule",
            })
            continue

        churned = int(seg.churned.sum())
        churn_rate = churned / n
        revenue = float(seg.total_charge.sum())
        arpu = float(seg.total_charge.mean())
        revenue_at_risk = float(seg.loc[seg.churned == 1, "total_charge"].sum())

        observed = {
            "customers": n,
            "share_of_base_pct": round(n / total_customers * 100, 2),
            "churned": churned,
            "churn_rate_pct": round(churn_rate * 100, 2),
            "revenue": round(revenue, 2),
            "arpu": round(arpu, 2),
            "revenue_at_risk": round(revenue_at_risk, 2),
            "avg_service_calls": round(float(seg.customer_service_calls.mean()), 2),
        }

        projected = (None if rule["offer"] is None
                     else _project(n, churn_rate, arpu, rule, econ))

        out.append({
            "rule_id": rule["id"],
            "name": rule["name"],
            "priority": rule["priority"],
            "action": rule["action"],
            "offer": rule["offer"],
            "rationale_facts": rule["rationale_facts"],
            "observed": observed,
            "projected": projected,
        })

    return out


def _project(n: int, churn_rate: float, arpu: float,
             rule: dict, econ: dict) -> dict:
    """Expected impact. EVERY figure here rests on ECONOMICS assumptions."""
    horizon = econ["value_horizon_periods"]
    at_risk = n * churn_rate

    def scenario(save_rate: float) -> dict:
        saved = at_risk * save_rate
        # Revenue retained over the horizon, less the discount given away.
        gross = saved * arpu * horizon
        discount_cost = gross * (rule["offer_discount_pct"] / 100)
        contact_cost = n * econ["retention_contact_cost"]
        avoided_acq = saved * econ["acquisition_cost"]
        net = gross - discount_cost - contact_cost + avoided_acq
        total_cost = discount_cost + contact_cost
        return {
            "save_rate_assumed": round(save_rate, 2),
            "customers_retained": round(saved, 1),
            "gross_revenue_retained": round(gross, 2),
            "discount_cost": round(discount_cost, 2),
            "contact_cost": round(contact_cost, 2),
            "acquisition_cost_avoided": round(avoided_acq, 2),
            "net_value": round(net, 2),
            "roi_pct": (round(net / total_cost * 100, 1)
                        if total_cost > 0 else None),
        }

    return {
        "_basis": "PROJECTED from assumptions in ECONOMICS, not observed",
        "customers_at_risk": round(at_risk, 1),
        "expected": scenario(econ["assumed_save_rate"]),
        "sensitivity": {
            "low": scenario(econ["save_rate_low"]),
            "high": scenario(econ["save_rate_high"]),
        },
    }


# ==========================================================================
# Customer lists — the audit trail
# ==========================================================================
def customers_for_rule(rule_id: str, limit: int = 1000) -> pd.DataFrame:
    """Exportable list of the actual customers a rule targets.

    This is what makes a recommendation actionable rather than rhetorical:
    every segment traces to real IDs a retention team could work.
    """
    valid = {r["id"] for r in R.RULES}
    if rule_id not in valid:
        raise ValueError(f"unknown rule_id '{rule_id}'. Choose from {sorted(valid)}")

    df = assignments()
    seg = df[df.rule_id == rule_id].copy()
    return seg.sort_values("total_charge", ascending=False).head(limit)


def export_target_list(rule_id: str, path: str, limit: int = 1000) -> str:
    df = customers_for_rule(rule_id, limit)
    df.to_csv(path, index=False)
    return path


# ==========================================================================
# Reconciliation
# ==========================================================================
def reconcile() -> dict:
    """Segment totals must equal the portfolio. Guards against overlapping
    or non-exhaustive rules silently losing customers."""
    segments = evaluate()
    kpi = metrics.kpi_summary()["data"]

    seg_customers = sum(s["observed"]["customers"] for s in segments)
    seg_churned = sum(s["observed"].get("churned", 0) for s in segments)
    seg_revenue = sum(s["observed"].get("revenue", 0) for s in segments)

    return {
        "customers": {"segments": seg_customers,
                      "portfolio": kpi["total_customers"],
                      "match": seg_customers == kpi["total_customers"]},
        "churned": {"segments": seg_churned,
                    "portfolio": kpi["churned_customers"],
                    "match": seg_churned == kpi["churned_customers"]},
        "revenue": {"segments": round(seg_revenue, 2),
                    "portfolio": kpi["total_revenue"],
                    "match": abs(seg_revenue - kpi["total_revenue"]) < 0.05},
    }


# ==========================================================================
# Payload + narration
# ==========================================================================
def recommendations_payload() -> dict:
    segments = evaluate()
    actionable = [s for s in segments if s.get("projected")]
    total_net = sum(s["projected"]["expected"]["net_value"] for s in actionable)

    return {
        "meta": {
            **metrics.SNAPSHOT_META,
            "figures_marked_observed": "measured from the data",
            "figures_marked_projected": "derived from stated assumptions",
        },
        "economics": R.ECONOMICS,
        "segments": segments,
        "portfolio_expected_net_value": round(total_net, 2),
        "reconciliation": reconcile(),
    }


def narrate_recommendations() -> dict:
    """LLM writes the justification. It computes nothing."""
    payload = recommendations_payload()
    instruction = (
        "Brief a retention manager on these recommendation segments. For "
        "each actionable segment state who it targets, what to do, and why "
        "the data supports it. Rank by urgency. Clearly separate OBSERVED "
        "figures from PROJECTED ones, and say plainly that projected values "
        "depend on the stated assumptions. Do not compute new numbers."
    )
    provider = get_provider_for("narration")
    user = prompts.build_user_prompt("", payload, instruction)

    try:
        text_out = provider.complete(prompts.SYSTEM_PROMPT, user)
    except LLMError as exc:
        return {"valid": False, "text": None, "error": str(exc),
                "payload": payload}

    report = guardrails.validate(text_out, payload)
    return {"valid": report["passed"], "text": text_out,
            "validation": report, "payload": payload}