"""
Phase 8 — scenario analysis.

Parameterised, not free-text. A manager picks a lever and a magnitude; the
engine applies a predefined rule and computes the result in Python. The LLM
explains the finished calculation and never produces a number.

THE CAUSAL PROBLEM — read this before trusting any output
---------------------------------------------------------
The data is OBSERVATIONAL. It shows that customers with 4+ service calls
churn at 51.7% while others churn at 11.25%. It does NOT show that the
calls caused the churn. The causation plausibly runs the other way: people
call because they have already decided to leave.

So "reduce escalations by 30%" cannot simply move 30% of that group onto
the low-risk rate. That would assume the association is entirely causal and
entirely reversible, which is the single most optimistic reading available.

Every scenario therefore carries an explicit EFFICACY parameter: what
fraction of the observed risk gap an intervention actually recovers.
Efficacy 1.0 is the upper bound, not the expected case. Results are
reported as a band across a plausible efficacy range, never as one number.

WHAT A SCENARIO IS NOT
----------------------
It is not a forecast. It says "if this many customers moved between these
groups, the portfolio arithmetic would look like this." It says nothing
about whether the intervention will work, or when.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import text

import metrics
import rules as R

# --------------------------------------------------------------------------
# How much of the observed risk gap an intervention is assumed to recover.
# Central estimate is deliberately low: on observational data, most of an
# association is usually NOT recoverable by removing the marker.
EFFICACY = {
    "_note": ("ASSUMPTION, not a measurement. The data cannot tell us how "
              "much of the risk gap is causal and reversible."),
    "low": 0.20,
    "central": 0.40,
    "high": 0.70,
    "upper_bound": 1.00,   # reported for reference; treats the gap as fully causal
}

# --------------------------------------------------------------------------
# The levers. Each names the flag it acts on and what the reduction means.
LEVERS = {
    "reduce_escalations": {
        "label": "Reduce service escalations",
        "flag": "high_service_calls",
        "description": ("Fewer customers reach 4 or more service calls, "
                        "through faster first-contact resolution."),
        "mechanism": "moves customers out of the 4+ service-call group",
        "max_pct": 50,
        "plausible_because": ("first-contact resolution is a standard "
                              "operational lever with a known cost"),
    },
    "reduce_intl_plan_mismatch": {
        "label": "Correct international-plan mismatch",
        "flag": "international_plan",
        "description": ("Customers on an international plan that does not "
                        "fit their usage are moved to a suitable tariff."),
        "mechanism": "moves customers off the international plan",
        "max_pct": 50,
        "plausible_because": ("plan migration is directly actionable, though "
                              "it forfeits the plan's revenue"),
    },
    "reduce_heavy_day_usage": {
        "label": "Move heavy daytime users to a suitable bundle",
        "flag": "heavy_day_usage",
        "description": ("Customers billing $45 or more in daytime charges "
                        "are moved onto a daytime bundle, reducing bill "
                        "shock."),
        "mechanism": "moves customers out of the heavy daytime-usage group",
        "max_pct": 50,
        "plausible_because": ("tariff change is actionable, but reduces "
                              "per-customer revenue"),
    },
}


class ScenarioError(ValueError):
    pass


# ==========================================================================
def _features() -> pd.DataFrame:
    return pd.read_sql(text("SELECT * FROM v_churn_features"),
                       metrics.engine())


def list_levers() -> list[dict]:
    return [{"id": k, "label": v["label"], "description": v["description"],
             "max_pct": v["max_pct"]} for k, v in LEVERS.items()]


def _apply(df: pd.DataFrame, flag: str, pct: float,
           efficacy: float) -> dict:
    """Move `pct` of the flagged group toward the unflagged churn rate.

    Only `efficacy` of the risk gap is recovered. The rest is treated as
    association that removing the marker does not undo.
    """
    n = len(df)
    flagged = df[df[flag] == 1]
    unflagged = df[df[flag] == 0]

    if len(flagged) == 0:
        raise ScenarioError(f"no customers carry the flag '{flag}'")

    rate_flagged = float(flagged.churned.mean())
    rate_unflagged = float(unflagged.churned.mean())
    gap = rate_flagged - rate_unflagged

    moved = len(flagged) * (pct / 100.0)
    # Each moved customer's risk falls by efficacy x gap, not the full gap.
    churners_avoided = moved * gap * efficacy

    base_churners = float(df.churned.sum())
    new_churners = base_churners - churners_avoided

    return {
        "customers_affected": round(moved, 1),
        "group_size": len(flagged),
        "group_churn_rate_pct": round(rate_flagged * 100, 2),
        "other_churn_rate_pct": round(rate_unflagged * 100, 2),
        "risk_gap_pp": round(gap * 100, 2),
        "efficacy_assumed": efficacy,
        "churners_avoided": round(churners_avoided, 1),
        "baseline_churn_pct": round(base_churners / n * 100, 2),
        "scenario_churn_pct": round(new_churners / n * 100, 2),
        "improvement_pp": round((base_churners - new_churners) / n * 100, 2),
    }


def _economics(result: dict, arpu: float) -> dict:
    """Value of the avoided churn. Rests on ECONOMICS assumptions."""
    econ = R.ECONOMICS
    saved = result["churners_avoided"]
    horizon = econ["value_horizon_periods"]

    revenue_retained = saved * arpu * horizon
    acquisition_avoided = saved * econ["acquisition_cost"]
    intervention_cost = (result["customers_affected"]
                         * econ["retention_contact_cost"])
    net = revenue_retained + acquisition_avoided - intervention_cost

    return {
        "_basis": "PROJECTED from ECONOMICS assumptions, not observed",
        "revenue_retained": round(revenue_retained, 2),
        "acquisition_cost_avoided": round(acquisition_avoided, 2),
        "intervention_cost": round(intervention_cost, 2),
        "net_value": round(net, 2),
        "roi_pct": (round(net / intervention_cost * 100, 1)
                    if intervention_cost > 0 else None),
    }


# ==========================================================================
def run_scenario(lever: str, pct: float) -> dict:
    """Evaluate one lever at one magnitude, across the efficacy band."""
    if lever not in LEVERS:
        raise ScenarioError(f"unknown lever '{lever}'. "
                            f"Choose from {sorted(LEVERS)}")
    spec = LEVERS[lever]
    if not 0 < pct <= spec["max_pct"]:
        raise ScenarioError(f"pct must be between 0 and {spec['max_pct']}")

    df = _features()
    arpu = float(df.total_charge.mean())

    band = {}
    for name in ("low", "central", "high", "upper_bound"):
        eff = EFFICACY[name]
        result = _apply(df, spec["flag"], pct, eff)
        result["economics"] = _economics(result, arpu)
        band[name] = result

    central = band["central"]

    return {
        "meta": {
            **metrics.SNAPSHOT_META,
            "data_origin": "PROJECTED",
            "warning": (
                "This is a HYPOTHETICAL calculation, not a forecast. It "
                "answers: if this many customers moved between these groups, "
                "how would the portfolio arithmetic change? It says nothing "
                "about whether the intervention would work."),
            "causal_caveat": (
                "The data is OBSERVATIONAL. It shows an association between "
                "the marker and churn, not that removing the marker removes "
                "the churn. The efficacy assumption below is how much of "
                "that association is treated as causal and reversible."),
        },
        "lever": {"id": lever, **spec},
        "change_pct": pct,
        "efficacy_assumptions": EFFICACY,
        "economic_assumptions": R.ECONOMICS,
        "headline": {
            "baseline_churn_pct": central["baseline_churn_pct"],
            "scenario_churn_pct": central["scenario_churn_pct"],
            "improvement_pp": central["improvement_pp"],
            "customers_affected": central["customers_affected"],
            "net_value": central["economics"]["net_value"],
        },
        "band": band,
        "range_note": (
            f"Improvement ranges from "
            f"{band['low']['improvement_pp']} to "
            f"{band['high']['improvement_pp']} percentage points across the "
            f"plausible efficacy range, and reaches "
            f"{band['upper_bound']['improvement_pp']} only if the entire "
            f"observed association is causal and fully reversible."),
    }


def compare_levers(pct: float = 30.0) -> dict:
    """All levers at the same magnitude, ranked by central net value."""
    results = []
    for lever in LEVERS:
        try:
            s = run_scenario(lever, pct)
        except ScenarioError:
            continue
        results.append({
            "lever": lever,
            "label": LEVERS[lever]["label"],
            "customers_affected": s["headline"]["customers_affected"],
            "improvement_pp": s["headline"]["improvement_pp"],
            "net_value": s["headline"]["net_value"],
            "risk_gap_pp": s["band"]["central"]["risk_gap_pp"],
        })
    results.sort(key=lambda r: r["net_value"], reverse=True)

    return {
        "meta": {
            "data_origin": "PROJECTED",
            "warning": ("Hypothetical comparison at a fixed magnitude. Not "
                        "a forecast, and not a recommendation on its own: "
                        "the levers differ in cost and feasibility, which "
                        "this ranking does not capture."),
        },
        "change_pct": pct,
        "efficacy_assumed": EFFICACY["central"],
        "ranked": results,
    }


# ==========================================================================
def narrate(lever: str, pct: float) -> dict:
    """LLM explains a computed scenario. It calculates nothing."""
    import guardrails
    import prompts
    from llm_provider import LLMError, get_provider_for

    payload = run_scenario(lever, pct)

    instruction = (
        "Explain this scenario to a manager in 4-6 sentences.\n"
        "1. State what the scenario assumes and what it computes.\n"
        "2. Give the central estimate, then the RANGE. The range matters "
        "more than the point estimate here.\n"
        "3. State plainly that this is a hypothetical calculation, not a "
        "forecast, and that it rests on an assumption about how much of "
        "the observed association is causal and reversible.\n"
        "Do not compute any new number. Do not describe this as a "
        "prediction of what will happen."
    )

    provider = get_provider_for("narration")
    user = prompts.build_user_prompt("", payload, instruction)
    try:
        text_out = provider.complete(prompts.SYSTEM_PROMPT, user)
    except LLMError as exc:
        return {"valid": False, "text": None, "error": str(exc),
                "scenario": payload}

    report = guardrails.validate(text_out, payload)
    return {"valid": report["passed"], "text": text_out,
            "validation": report, "scenario": payload}