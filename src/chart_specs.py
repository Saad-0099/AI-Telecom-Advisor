"""
Phase 3 — chart specifications.

ONE definition per chart, consumed by BOTH renderers AND the LLM explainer.
Every decision that matters — which columns, what order, what the title
claims, where the annotation points, what the explanation should emphasise
— lives here and nowhere else. A matplotlib PNG, a Plotly HTML, and the
narration of the same chart therefore cannot disagree.

Three fields drive the explanation:

  explain_focus   What is actually interesting about THIS chart. A single
                  generic instruction across ten structurally different
                  charts produced circular explanations ("the driver is the
                  day-charge cliff, which is a driver of churn") and
                  non-actions ("closely monitor these customers").

  hypotheses      Candidate business interpretations. These are NOT
                  measurements — the dataset contains no evidence for them.
                  The explainer is instructed to present them as possible
                  explanations, never as findings.

  simulated       Marks a chart drawn from the Phase 6.5 generated panel.
                  Changes the colour to amber, forces a zero-based y-axis,
                  and the explain_focus requires disclosure in the prose.

DELIBERATELY NOT A PLOTTING DSL. Five chart kinds cover everything this
project needs. If a chart wants something exotic, write it as a one-off
function rather than growing this vocabulary — the failure mode of this
pattern is an abstraction that ends up harder to read than the two
renderers it replaced.
"""

from __future__ import annotations

# Chart kinds the renderers understand.
KINDS = {"bar", "barh", "grouped_bar", "line", "pie"}

# Shared palette so both renderers produce the same colours.
COLORS = {
    "normal": "#5B8FF9",
    "danger": "#E8684A",
    "warning": "#F6BD16",     # also the simulated-data colour
    "safe": "#5AD8A6",
    "muted": "#C2C8D5",
    "text": "#2C3543",
}


CHART_SPECS = {

    # ----------------------------------------------------------------------
    # DRIVER 1 — the service-call cliff
    # ----------------------------------------------------------------------
    "churn_by_service_calls": {
        "title": "Churn by number of customer service calls",
        "subtitle": ("Churn is flat from 0 to 3 calls, then jumps sharply. "
                     "This is a cliff, not a slope."),
        "metric": "churn_by_service_calls",
        "kind": "bar",
        "x": "customer_service_calls",
        "y": "churn_rate_pct",
        "x_label": "Customer service calls",
        "y_label": "Churn rate (%)",
        # Bars at or above this x value are drawn in the danger colour.
        "highlight_from_x": 4,
        # Counts 8 and 9 have only 2 customers each; their 50% and 100%
        # rates are noise. Mute them and label n so the chart cannot be
        # read as evidence about those buckets.
        "sample_size_col": "customers",
        "min_sample": 20,
        "annotation": {
            "at_x": 4,
            "text": "Cliff at 4+ calls",
        },
        "explain_focus": (
            "The THRESHOLD is the finding, not a trend. Say that 0, 1, 2 and "
            "3 calls all sit at roughly the same rate, and that the fourth "
            "call quadruples it. Name the specific point where the jump "
            "happens. Do not describe this as churn rising with call volume."
        ),
        "hypotheses": [
            "a fourth call implies the first three did not resolve the issue",
            "escalation count may be a proxy for unresolved frustration "
            "rather than a cause in itself",
            "the customer may already have decided to leave before calling",
        ],
        "caption": ("Bucket as '0-3' vs '4+'. Treating call count as a "
                    "continuous predictor understates the effect badly."),
    },

    # ----------------------------------------------------------------------
    # DRIVER 2 — the international plan
    # ----------------------------------------------------------------------
    "churn_by_plan": {
        "title": "Churn by plan combination",
        "subtitle": "International plan holders churn several times higher.",
        "metric": "churn_by_plan",
        "kind": "bar",
        "x": "intl_plan",
        "y": "churn_rate_pct",
        "series": "vmail_plan",       # grouping column
        "kind_override": "grouped_bar",
        "x_label": "International plan",
        "y_label": "Churn rate (%)",
        "explain_focus": (
            "Compare across BOTH dimensions. The international plan splits "
            "the groups sharply; the voicemail plan barely moves them. Say "
            "which of the two actually matters and which does not."
        ),
        "hypotheses": [
            "the international tariff may be poor value for typical usage",
            "these customers may be more price-sensitive or more likely to "
            "compare providers",
            "plan fit rather than service quality may be the issue",
        ],
        "caption": ("Voicemail plan has little independent effect; the "
                    "international plan is the driver."),
    },

    # ----------------------------------------------------------------------
    # DRIVER 3 — the day-usage cliff
    # ----------------------------------------------------------------------
    "churn_by_day_usage": {
        "title": "Churn by daytime charge band",
        "subtitle": ("Churn DIPS before it explodes. A linear model of day "
                     "charge would find almost nothing."),
        "metric": "churn_by_day_usage",
        "kind": "bar",
        "x": "day_charge_band",
        "y": "churn_rate_pct",
        "x_label": "Daytime charge band",
        "y_label": "Churn rate (%)",
        "highlight_labels": ["4. 45-50", "5. 50+"],
        "sample_size_col": "customers",
        "min_sample": 20,
        "annotation": {
            "at_label": "4. 45-50",
            "text": "Cliff at ~$45 (≈265 day minutes)",
        },
        "explain_focus": (
            "The SHAPE matters more than the level. Churn DROPS from the "
            "first band to the second before rising steeply. State that dip "
            "explicitly — a non-monotonic pattern is why this variable is "
            "nearly invisible to a linear model. Do not simply restate that "
            "day charge is a driver; describe what the pattern looks like."
        ),
        "hypotheses": [
            "bill shock — high daytime bills prompt customers to shop around",
            "tariff mismatch — heavy day callers may be on the wrong plan "
            "for their usage",
            "this is probably NOT a service-quality problem; these customers "
            "are not necessarily making more support calls",
        ],
        "caption": ("Found while building the Phase 6 rules engine. The "
                    "earlier two-driver segmentation reported a 'baseline' "
                    "of 8.2% that concealed this group."),
    },

    # ----------------------------------------------------------------------
    # The combined segmentation
    # ----------------------------------------------------------------------
    "risk_segments": {
        "title": "Churn rate by risk segment",
        "subtitle": "Segments built from the three confirmed drivers.",
        "metric": "risk_segments",
        "kind": "barh",
        "x": "segment",
        "y": "churn_rate_pct",
        "sort_by": "severity_rank",
        "sort_desc": False,
        "x_label": "Churn rate (%)",
        "y_label": "",
        "highlight_above_y": 45,
        "sample_size_col": "customers",
        "min_sample": 20,
        "explain_focus": (
            "Say which segment deserves attention FIRST, and justify the "
            "ranking by weighing rate against size. A very small segment "
            "with a high rate may matter less than a larger one with a "
            "moderate rate. Note that drivers compound: segments with more "
            "than one driver present sit higher."
        ),
        "hypotheses": [
            "each driver appears to act independently, so a customer with "
            "two is worse off than a customer with either alone",
            "the causes differ by segment, so the same intervention will "
            "not suit all of them",
        ],
        "caption": ("Segments are mutually exclusive and exhaustive: counts "
                    "sum to 3,333 and revenue to the portfolio total."),
    },

    "revenue_at_risk": {
        "title": "Revenue at risk by segment",
        "subtitle": ("Revenue attached to customers who churned, by segment. "
                     "The largest exposure is not the highest-rate segment."),
        "metric": "risk_segments",
        "kind": "barh",
        "x": "segment",
        "y": "revenue_at_risk",
        "sort_by": "revenue_at_risk",
        "sort_desc": True,
        "x_label": "Revenue at risk",
        "y_label": "",
        "value_prefix": "$",
        "explain_focus": (
            "The point is that rate and exposure rank DIFFERENTLY. Identify "
            "which segment carries the most absolute revenue risk despite "
            "not having the highest churn rate, and explain why a large "
            "low-rate group can outweigh a small high-rate one."
        ),
        "hypotheses": [
            "a low rate applied to a very large base still produces "
            "substantial absolute loss",
            "targeting only the highest-rate segments would leave the "
            "largest dollar exposure unaddressed",
        ],
        "caption": ("Rate and exposure rank differently. A large low-rate "
                    "segment can carry more absolute revenue risk than a "
                    "small high-rate one."),
    },

    "segment_sizes": {
        "title": "Customers per risk segment",
        "subtitle": "Most of the base carries no risk driver at all.",
        "metric": "risk_segments",
        "kind": "barh",
        "x": "segment",
        "y": "customers",
        "sort_by": "severity_rank",
        "sort_desc": False,
        "x_label": "Customers",
        "y_label": "",
        "explain_focus": (
            "This chart is about SCALE, not risk. Note how uneven the "
            "segments are and what that implies for operations: the "
            "highest-risk groups are small enough to handle individually, "
            "while the baseline is too large for per-customer outreach."
        ),
        "caption": "Segment sizes are highly uneven; read rates alongside n.",
    },

    # ----------------------------------------------------------------------
    # Cohort axis (there is no date axis — see SNAPSHOT_META)
    # ----------------------------------------------------------------------
    "cohort_risk_matrix": {
        "title": "Churn by tenure cohort and service-call bucket",
        "subtitle": ("The service-call cliff holds in every cohort. Tenure "
                     "itself does not predict churn."),
        "metric": "cohort_risk_matrix",
        "kind": "grouped_bar",
        "x": "cohort_label",
        "y": "churn_rate_pct",
        "series": "service_call_bucket",
        "sort_by": "cohort_sort",
        "x_label": "Tenure cohort",
        "y_label": "Churn rate (%)",
        "explain_focus": (
            "The finding here is an ABSENCE. Tenure does not separate the "
            "cohorts — the bars at the same call bucket are close across "
            "all four. The service-call split, by contrast, separates them "
            "sharply in every single cohort. Say both things plainly."
        ),
        "hypotheses": [
            "a driver that holds across every cohort is more likely to be "
            "structural than a lifecycle effect",
            "retention effort is better targeted by behaviour than by how "
            "long someone has been a customer",
        ],
        "caption": ("Tenure is the ordered axis in this project because the "
                    "dataset has no date column. It is useful for structure, "
                    "not as a churn driver."),
    },

    "cohort_profile": {
        "title": "Portfolio structure by tenure cohort",
        "subtitle": "Churn is flat across cohorts: 13.1% to 15.3%.",
        "metric": "cohort_profile",
        "kind": "bar",
        "x": "cohort_label",
        "y": "churn_rate_pct",
        "sort_by": "cohort_sort",
        "x_label": "Tenure cohort",
        "y_label": "Churn rate (%)",
        "y_max": 60,     # same scale as the driver charts, for honest contrast
        "explain_focus": (
            "This chart shows a NEGATIVE result and should be presented as "
            "one. State plainly that tenure does not predict churn here, "
            "and that the small spread between cohorts is not a meaningful "
            "pattern. The y-axis is deliberately scaled to match the driver "
            "charts so the flatness is visible rather than magnified."
        ),
        "hypotheses": [
            "an expected driver that turns out to be flat is a useful "
            "finding: it rules out tenure-based targeting",
        ],
        "caption": ("Plotted on the same y-scale as the driver charts to "
                    "show how little variation there is here by comparison."),
    },

    # ----------------------------------------------------------------------
    # Revenue
    # ----------------------------------------------------------------------
    "revenue_by_period": {
        "title": "Revenue by call period",
        "subtitle": "Daytime calls dominate revenue.",
        "metric": "revenue_by_period",
        "kind": "bar",
        "x": "period",
        "y": "revenue",
        "x_label": "Period",
        "y_label": "Revenue",
        "value_prefix": "$",
        "explain_focus": (
            "Describe the revenue concentration and connect it to risk: the "
            "period that generates most revenue is also the one whose heavy "
            "users churn most. That combination is the reason this chart "
            "matters."
        ),
        "hypotheses": [
            "daytime minutes are priced highest, so they dominate revenue "
            "and also drive the largest bills",
            "revenue concentration in one period means pricing changes "
            "there carry outsized effect in both directions",
        ],
        "caption": ("Period charges from a single snapshot, not monthly "
                    "recurring revenue."),
    },

    "top_states_by_churn": {
        "title": "Churn rate by state (50+ customers only)",
        "subtitle": "State-level rates are noisy; small states are excluded.",
        "metric": "churn_by_state",
        "metric_kwargs": {"min_customers": 50, "limit": 10},
        "kind": "barh",
        "x": "state",
        "y": "churn_rate_pct",
        "sort_by": "churn_rate_pct",
        "sort_desc": True,
        "x_label": "Churn rate (%)",
        "y_label": "",
        "sample_size_col": "customers",
        "min_sample": 50,
        "explain_focus": (
            "LEAD WITH THE CAUTION. These are small samples and the "
            "differences between states may well be noise. Do NOT recommend "
            "acting on any single state's rate. The honest conclusion is "
            "that geography is the weakest dimension in this dataset and "
            "the behavioural drivers are a better basis for targeting."
        ),
        "hypotheses": [
            "with roughly 65 customers per state, a few extra churners "
            "moves a state several percentage points",
            "apparent geographic variation may simply reflect the "
            "distribution of the behavioural drivers across states",
        ],
        "caption": ("51 states average ~65 customers each. Even filtered, "
                    "these rates carry several points of noise — the state "
                    "dimension is the weakest in this dataset."),
    },

    # ----------------------------------------------------------------------
    # SIMULATED history (Phase 6.5). Structure only — never churn.
    # ----------------------------------------------------------------------
    "sim_monthly_churn": {
        "title": "Monthly churn is FLAT by construction (simulated)",
        "subtitle": ("Drawn to demonstrate the ABSENCE of a trend. Bars, not "
                     "a line — a line would imply a trajectory that is not "
                     "in this data."),
        "metric": "sim_monthly_portfolio",
        # Bars, deliberately. A line chart of this series reads as a
        # trajectory: the eye connects the points and starts explaining the
        # peaks. Bars read as independent categories, which is what these
        # months actually are once churn timing has been generated.
        "kind": "bar",
        "x": "snapshot_month",
        "y": "churn_rate_pct",
        "x_label": "Month",
        "y_label": "Monthly churn rate (%)",
        # Fixed scale. Autoscaling a 2.5-point spread fills the frame and
        # turns sampling noise into a dramatic-looking pattern.
        "y_max": 20,
        "reference_line": {
            "value": 3.49,
            "label": "mean 3.49%",
        },
        "sample_size_col": "active_customers",
        "min_sample": 50,
        "simulated": True,
        "explain_focus": (
            "You MUST state that this history is simulated. The POINT of "
            "this chart is that there is NO pattern: every bar sits close "
            "to the mean and the variation between months is sampling "
            "noise from the generator, not customer behaviour. Say that "
            "explicitly. Do NOT describe any month as better or worse than "
            "another, do NOT identify a highest or lowest month, and do NOT "
            "suggest any action based on month-to-month differences."
        ),
        "hypotheses": [
            "an absence of pattern is itself informative here: it confirms "
            "the generator did not encode a trend the source data could "
            "not support",
        ],
        "caption": ("Included to demonstrate the absence of a trend, not to "
                    "show one. Churn timing in this panel was generated; a "
                    "different random seed would produce a different "
                    "arrangement of the same flat distribution. Real churn "
                    "movement over time cannot be measured from a single "
                    "snapshot."),
    },
    
    "sim_monthly_structure": {
        "title": "Active customers by month (simulated history)",
        "subtitle": ("SIMULATED. Shows portfolio shape only — churn in this "
                     "panel is flat by construction."),
        "metric": "sim_monthly_portfolio",
        "kind": "line",
        "x": "snapshot_month",
        "y": "active_customers",
        "x_label": "Month",
        "y_label": "Active customers",
        "simulated": True,
        "explain_focus": (
            "You MUST state that this history is simulated. Describe the "
            "stability of the active base and nothing more. Do NOT comment "
            "on churn: churn in this panel is flat by construction and any "
            "variation is sampling noise from the generator."
        ),
        "hypotheses": [
            "a stable active base means entries and exits are roughly in "
            "balance across the period shown",
        ],
        "caption": ("Generated from the snapshot, not observed. Included to "
                    "show portfolio shape; it contains no information the "
                    "snapshot did not already hold."),
    },

    "sim_monthly_revenue": {
        "title": "Monthly revenue (simulated history)",
        "subtitle": ("SIMULATED. Revenue derived by splitting each "
                     "customer's real total across their active months."),
        "metric": "sim_monthly_revenue",
        "kind": "line",
        "x": "snapshot_month",
        "y": "total_revenue",
        "x_label": "Month",
        "y_label": "Revenue",
        "value_prefix": "$",
        "simulated": True,
        "explain_focus": (
            "You MUST state that this history is simulated. Describe only "
            "the stability of monthly revenue. Do NOT treat any month-to-"
            "month movement as a trend: the split across months was "
            "generated, so variation here reflects the generator rather "
            "than customer behaviour."
        ),
        "caption": ("Each customer's real total charge was distributed "
                    "across their active months. Monthly totals sum back to "
                    "the real portfolio revenue."),
    },
}


def get_spec(name: str) -> dict:
    if name not in CHART_SPECS:
        raise KeyError(f"unknown chart '{name}'. "
                       f"Available: {sorted(CHART_SPECS)}")
    return CHART_SPECS[name]


def list_charts() -> list[dict]:
    return [{"id": k, "title": v["title"], "kind": v.get("kind_override",
                                                         v["kind"]),
             "metric": v["metric"],
             "simulated": bool(v.get("simulated")),
             "has_explain_focus": "explain_focus" in v}
            for k, v in CHART_SPECS.items()]


def validate_specs() -> list[str]:
    """Structural checks on every spec. Empty list means valid."""
    import metrics
    problems = []
    for name, spec in CHART_SPECS.items():
        for field in ("title", "metric", "kind", "x", "y"):
            if field not in spec:
                problems.append(f"{name}: missing '{field}'")
        kind = spec.get("kind_override", spec.get("kind"))
        if kind not in KINDS:
            problems.append(f"{name}: unknown kind '{kind}'")
        if spec.get("metric") not in metrics.REGISTRY:
            problems.append(f"{name}: metric '{spec.get('metric')}' "
                            f"is not in metrics.REGISTRY")
        if kind == "grouped_bar" and "series" not in spec:
            problems.append(f"{name}: grouped_bar requires 'series'")
        # Without focus text the explainer falls back to a generic prompt,
        # which reliably produces circular observations and vague actions.
        if "explain_focus" not in spec:
            problems.append(f"{name}: missing 'explain_focus' — the "
                            f"explanation will be generic")
        if not isinstance(spec.get("hypotheses", []), list):
            problems.append(f"{name}: 'hypotheses' must be a list")

        # A simulated chart whose focus text does not demand disclosure
        # would produce a caption that reads like observed history.
        if spec.get("simulated"):
            focus = spec.get("explain_focus", "").lower()
            if "simulated" not in focus:
                problems.append(
                    f"{name}: marked simulated but explain_focus does not "
                    f"require the model to disclose it")
            if spec.get("metric", "").startswith("sim_") is False:
                problems.append(
                    f"{name}: marked simulated but reads a non-simulated "
                    f"metric '{spec.get('metric')}'")
    return problems