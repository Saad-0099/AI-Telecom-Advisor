"""
Phase 7 — report content assembly.

Gathers everything the report needs BEFORE any rendering happens, so the
PDF layer is purely presentational and the content can be tested without
producing a document.

THREE KINDS OF NUMBER
---------------------
The report distinguishes them visually, not just in a footnote. A reader
who cannot tell them apart at a glance is being misled by omission.

  OBSERVED    measured from the source data. Reconciles to the CSV.
  PROJECTED   derived from the economic assumptions in rules.py. These are
              assumptions, not measurements - the dataset has no cost data.
  SIMULATED   from the Phase 6.5 generated history. Structure only; the
              churn in it is flat by construction.

TITLE
-----
"Portfolio Risk Report", never "Weekly Report". There are no weeks in this
data and the title must not imply otherwise.
"""

from __future__ import annotations

import guardrails
import metrics
import prompts
import recommend
import rules as R
from llm_provider import LLMError, get_provider_for

REPORT_TITLE = "Telecom Portfolio Risk Report"

# Marks used throughout the document and explained in the legend.
MARK = {
    "observed": "",          # unmarked: the default, measured from data
    "projected": "†",
    "simulated": "‡",
}

LEGEND = [
    ("Unmarked", "OBSERVED - measured from the source data."),
    (MARK["projected"], "PROJECTED - derived from the stated economic "
                        "assumptions, not measured."),
    (MARK["simulated"], "SIMULATED - from generated history. Shows portfolio "
                        "structure only; churn in it is flat by construction "
                        "and cannot support a forecast."),
]


# ==========================================================================
# Narration
# ==========================================================================
def _narrate(payload: dict, instruction: str) -> dict:
    """One validated LLM paragraph. Never computes; only describes."""
    provider = get_provider_for("narration")
    user = prompts.build_user_prompt("", payload, instruction)
    try:
        text = provider.complete(prompts.SYSTEM_PROMPT, user)
    except LLMError as exc:
        return {"valid": False, "text": None, "error": str(exc)}
    report = guardrails.validate(text, payload)
    return {"valid": report["passed"], "text": text, "validation": report}


def _explain_charts(names: list[str]) -> list[dict]:
    """AI explanation beneath each chart, grounded in that chart's own data.

    charts.explain() sends the plotted rows - not the whole metric payload -
    so the caption cannot describe something other than what is drawn.

    Each call is one LLM request. Keep the list per section short: nine
    charts plus five section narrations is already ~14 calls per report.
    """
    import charts
    out = []
    for name in names:
        try:
            result = charts.explain(name)
        except Exception as exc:
            out.append({"chart": name, "text": None, "valid": False,
                        "error": str(exc)})
            continue
        out.append({
            "chart": name,
            "text": result.get("text"),
            "valid": result.get("valid", False),
            "error": result.get("error"),
        })
    return out


SECTION_INSTRUCTIONS = {
    "executive_summary": (
        "Write a 4-6 sentence executive summary of the portfolio's current "
        "position. Lead with the single most decision-relevant fact. Name "
        "the confirmed churn drivers and say which customer group needs "
        "attention first. Do not list every figure - select."
    ),
    "drivers": (
        "Explain the three confirmed churn drivers to a non-technical "
        "manager. For each, quote the two rates given in the data — the "
        "rate below the threshold and the rate above it — and name the "
        "threshold. Do NOT compute the difference between them or state a "
        "gap in percentage points: use only figures that appear in the "
        "DATA block. Emphasise that all three are thresholds rather than "
        "gradual trends, and why that matters for targeting."
    ),
    "recommendations": (
        "Brief a retention manager on these segments. For each actionable "
        "one state who it targets, what to do, and why the data supports "
        "it. Rank by urgency. Say plainly that the projected values depend "
        "on the stated assumptions and are not measurements."
    ),
    "cohorts": (
        "Report a NEGATIVE result clearly. Tenure was expected to predict "
        "churn and does not: the cohorts are close together. Say so plainly, "
        "explain why that is useful to know, and state that retention effort "
        "should be targeted by behaviour rather than by how long someone has "
        "been a customer. Do not manufacture a pattern from the small "
        "differences between cohorts."
    ),
    "structure": (
        "Describe the portfolio's structure over the period shown. Cover "
        "the active base and revenue. You MUST state that this history is "
        "simulated. Do NOT comment on churn movement: churn in this panel "
        "is flat by construction and any variation is sampling noise."
    ),
}


# ==========================================================================
# Sections
# ==========================================================================
def section_kpi() -> dict:
    payload = metrics.kpi_summary()
    kpi = payload["data"]
    return {
        "id": "kpi",
        "title": "Portfolio at a glance",
        "kind": "observed",
        "tiles": [
            ("Customers", f"{kpi['total_customers']:,}"),
            ("Churn rate", f"{kpi['churn_rate_pct']}%"),
            ("Churned", f"{kpi['churned_customers']:,}"),
            ("Revenue", f"${kpi['total_revenue']:,.0f}"),
            ("ARPU", f"${kpi['arpu']:,.2f}"),
            ("Revenue at risk", f"${kpi['revenue_at_risk']:,.0f}"),
        ],
        "narration": _narrate(payload,
                              SECTION_INSTRUCTIONS["executive_summary"]),
    }


def section_drivers() -> dict:
    day_payload = metrics.churn_by_day_usage()
    # Roll the bands up to heavy/normal here if the metrics layer did not
    # supply a pre-aggregated bucket list, so this section works against
    # either shape of that payload.
    day_buckets = day_payload.get("buckets")
    if not day_buckets:
        agg: dict[str, dict] = {}
        for row in day_payload["data"]:
            b = agg.setdefault(row["day_usage_bucket"],
                               {"day_usage_bucket": row["day_usage_bucket"],
                                "customers": 0, "churned": 0})
            b["customers"] += row["customers"]
            b["churned"] += row["churned"]
        for b in agg.values():
            b["churn_rate_pct"] = round(b["churned"] * 100.0
                                        / b["customers"], 2)
        day_buckets = list(agg.values())

    payload = {
        "meta": metrics.SNAPSHOT_META,
        "service_calls": metrics.churn_by_service_calls()["buckets"],
        "plans": metrics.churn_by_plan()["data"],
        "day_usage": day_buckets,
    }
    calls = {b["service_call_bucket"]: b for b in payload["service_calls"]}
    day = {b["day_usage_bucket"]: b for b in day_buckets}
    intl = [p for p in payload["plans"] if p["intl_plan"] == "Intl plan"]
    intl_rate = round(sum(p["churned"] for p in intl) * 100.0
                      / sum(p["customers"] for p in intl), 2)
    no_intl = [p for p in payload["plans"] if p["intl_plan"] != "Intl plan"]
    no_intl_rate = round(sum(p["churned"] for p in no_intl) * 100.0
                         / sum(p["customers"] for p in no_intl), 2)

    chart_names = ["churn_by_service_calls", "churn_by_plan",
                   "churn_by_day_usage"]

    return {
        "id": "drivers",
        "title": "Confirmed churn drivers",
        "kind": "observed",
        "table": {
            "headers": ["Driver", "Threshold", "Below", "Above", "Customers"],
            "rows": [
                ["Customer service calls", "4 or more",
                 f"{calls['0-3']['churn_rate_pct']}%",
                 f"{calls['4+']['churn_rate_pct']}%",
                 f"{calls['4+']['customers']:,}"],
                ["International plan", "subscribed",
                 f"{no_intl_rate}%", f"{intl_rate}%",
                 f"{sum(p['customers'] for p in intl):,}"],
                ["Daytime charge", f"${R.HEAVY_DAY_CHARGE:.0f} or more",
                 f"{day['normal']['churn_rate_pct']}%",
                 f"{day['heavy']['churn_rate_pct']}%",
                 f"{day['heavy']['customers']:,}"],
            ],
        },
        "charts": chart_names,
        "chart_explanations": _explain_charts(chart_names),
        "narration": _narrate(payload, SECTION_INSTRUCTIONS["drivers"]),
    }


def section_segments() -> dict:
    payload = recommend.recommendations_payload()
    segments = [s for s in payload["segments"]
                if s["observed"]["customers"] > 0]

    rows = []
    for s in segments:
        o = s["observed"]
        p = s.get("projected")
        rows.append([
            s["name"],
            f"{o['customers']:,}",
            f"{o['churn_rate_pct']}%",
            f"${o['revenue_at_risk']:,.0f}",
            ("-" if not p
             else f"${p['expected']['net_value']:,.0f}{MARK['projected']}"),
        ])

    return {
        "id": "segments",
        "title": "Risk segments and recommended actions",
        "kind": "observed",
        "table": {
            "headers": ["Segment", "Customers", "Churn",
                        "Revenue at risk", f"Net value{MARK['projected']}"],
            "rows": rows,
        },
        "actions": [
            {"segment": s["name"], "action": s["action"],
             "offer": s["offer"] or "-",
             "customers": s["observed"]["customers"]}
            for s in segments if s["offer"]
        ],
        "assumptions": R.ECONOMICS,
        "charts": ["risk_segments", "segment_sizes", "revenue_at_risk"],
        # segment_sizes is deliberately not explained: it is a scale
        # reference beside the two charts that carry the argument.
        "chart_explanations": _explain_charts(
            ["risk_segments", "revenue_at_risk"]),
        "narration": _narrate(payload,
                              SECTION_INSTRUCTIONS["recommendations"]),
    }


def section_cohorts() -> dict:
    """The negative result. Presented deliberately, not buried.

    Tenure was expected to drive churn and does not. That is a finding, and
    a reason not to target by tenure - so it earns a section rather than a
    footnote.
    """
    payload = {
        "meta": metrics.SNAPSHOT_META,
        "cohorts": metrics.cohort_profile()["data"],
        "cohort_risk_matrix": metrics.cohort_risk_matrix()["data"],
    }
    rows = [[c["cohort_label"], f"{c['customers']:,}",
             f"{c['churn_rate_pct']}%", f"${c['arpu']:,.2f}",
             f"{c['avg_service_calls']}"]
            for c in payload["cohorts"]]

    chart_names = ["cohort_profile", "cohort_risk_matrix"]

    return {
        "id": "cohorts",
        "title": "Tenure: an expected driver that is not one",
        "kind": "observed",
        "table": {
            "headers": ["Cohort", "Customers", "Churn", "ARPU",
                        "Avg service calls"],
            "rows": rows,
        },
        "charts": chart_names,
        "chart_explanations": _explain_charts(chart_names),
        "narration": _narrate(payload, SECTION_INSTRUCTIONS["cohorts"]),
    }


def section_structure() -> dict | None:
    """Simulated-history section. Omitted entirely if the panel is absent."""
    try:
        payload = metrics.sim_monthly_portfolio()
    except Exception:
        return None
    rows = payload["data"]
    if not rows:
        return None

    active = [r["active_customers"] for r in rows]
    revenue = [r["revenue"] for r in rows]

    chart_names = ["sim_monthly_structure", "sim_monthly_revenue",
                   "sim_monthly_churn"]

    return {
        "id": "structure",
        "title": f"Portfolio structure over time{MARK['simulated']}",
        "kind": "simulated",
        "banner": (
            "SIMULATED HISTORY. This section is derived from generated "
            "monthly snapshots, not observed history. It shows portfolio "
            "structure only. Churn in this panel is flat by construction "
            "and cannot support a trend claim or a forecast."
        ),
        "tiles": [
            ("Months shown", f"{len(rows)}"),
            ("Active base", f"{min(active):,}-{max(active):,}"),
            ("Monthly revenue", f"${min(revenue):,.0f}-${max(revenue):,.0f}"),
        ],
        "charts": chart_names,
        "chart_explanations": _explain_charts(chart_names),
        "narration": _narrate(payload, SECTION_INSTRUCTIONS["structure"]),
    }


def section_caveats() -> dict:
    """Stated as a section, not a footnote. These are load-bearing."""
    return {
        "id": "caveats",
        "title": "What this report cannot tell you",
        "kind": "observed",
        "bullets": [
            "NO TIME DIMENSION. The source data is a single snapshot with no "
            "dates. Nothing here describes how any metric changed over time, "
            "and no figure in this report is a forecast.",

            "SIMULATED HISTORY SHOWS STRUCTURE, NOT TRENDS. The monthly "
            "panel in this report is generated from the snapshot, so it "
            "contains no information the snapshot did not already hold. "
            "Churn in it is deliberately flat: a trend there would reflect "
            "the generator's random seed, not customer behaviour. It is "
            "included to show portfolio shape and tenure lifecycle, both "
            "of which are legitimate uses of derived monthly data.",

            "TENURE IS NOT A DRIVER. Churn is flat across tenure cohorts "
            "(13.1%-15.3%). This was expected to matter and does not - a "
            "genuine finding, and a reason not to target by tenure.",

            "STATE-LEVEL FIGURES ARE NOISY. 51 states average about 65 "
            "customers each, so a handful of churners moves a state several "
            "percentage points. Geography is the weakest dimension here.",

            f"PROJECTED VALUES REST ON ASSUMPTIONS. Save rate, contact cost "
            f"and acquisition cost are industry-typical placeholders, not "
            f"measurements: the dataset contains no cost data. A save rate "
            f"of {R.ECONOMICS['assumed_save_rate']:.0%} is assumed, with a "
            f"sensitivity band of "
            f"{R.ECONOMICS['save_rate_low']:.0%}-"
            f"{R.ECONOMICS['save_rate_high']:.0%}.",

            "REVENUE IS PERIOD CHARGES. Not monthly recurring revenue and "
            "not annualised.",

            "CHURN RISK IS NOT PREDICTED HERE. Segments describe which "
            "customers resemble those who already churned. Scoring "
            "individual customers is a separate modelling problem.",
        ],
    }


# ==========================================================================
def build(include_charts: bool = True) -> dict:
    """Assemble the full report. Returns content only; no rendering."""
    sections = [section_kpi(), section_drivers(), section_segments(),
                section_cohorts()]

    structure = section_structure()
    if structure:
        sections.append(structure)

    sections.append(section_caveats())

    invalid = [s["id"] for s in sections
               if s.get("narration") and not s["narration"]["valid"]]

    # Chart captions are validated independently of section narration, so
    # a failed caption is reported without condemning the whole section.
    bad_captions = [e["chart"] for s in sections
                    for e in s.get("chart_explanations", [])
                    if e.get("text") and not e.get("valid")]

    return {
        "title": REPORT_TITLE,
        "legend": LEGEND,
        "sections": sections,
        "include_charts": include_charts,
        "narration_valid": not invalid,
        "invalid_sections": invalid,
        "invalid_captions": bad_captions,
        "reconciliation": recommend.reconcile(),
    }