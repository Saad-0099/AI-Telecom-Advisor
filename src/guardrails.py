"""
Phase 4 — output guardrails.

A prompt instruction is a request. This module is the enforcement: every
LLM response is checked against the payload it was given BEFORE anyone
sees it.

Four checks:
  1. NUMBER GROUNDING  every figure in the text must exist in the payload
  2. TEMPORAL CLAIMS   no assertion of change over time
  3. FORECAST CLAIMS   nothing in this project predicts what will happen
  4. FALSE DRIVERS     no tenure-causes-churn claim, no linear service-call claim

Three payload origins relax rule 2 in different, narrow ways:

  (default)   snapshot data. No temporal claims at all.
  SIMULATED   Phase 6.5 panel. Structural time-series language allowed,
              but only when disclosed, and never about churn.
  PROJECTED   Phase 8 scenarios. Conditional language allowed, but only
              when framed as a hypothetical.

Origin is detected from the payload itself, never passed in as a flag, so
a caller cannot accidentally unlock relaxed rules for real snapshot data.

Design note on check 1: we deliberately allow small integers (0-12) without
a payload match, because they appear in ordinary prose ("three factors",
"the top 5 states") and flagging them produced constant false positives.
The tradeoff is that a fabricated small count could slip through; every
number large enough to be a metric is still caught.

KNOWN LIMITATION: this is phrase matching, which cannot fully distinguish
asserting a claim from refuting one. Six false positives were found and
fixed during development; no false negative has been observed. The failure
mode is conservative — it blocks good answers rather than passing bad ones.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------

# Past-tense / comparative temporal claims. These assert a change that
# cannot exist in a single snapshot.
FORBIDDEN_TEMPORAL = [
    "increased", "decreased", "rose", "fell", "grew", "declined",
    "improved", "worsened", "dropped", "climbed", "surged", "plunged",
    "year-over-year", "year over year", "yoy", "month-over-month",
    "quarter-over-quarter", "last month", "last quarter", "last year",
    "previous period", "prior period", "previous quarter", "prior year",
    "up from", "down from", "compared to last", "since last",
    "trending", "trend line", "over time", "historically",
    "growth rate", "decline rate",
]

# Bare verbs that are fine as recommendations ("increase retention spend")
# but suspicious as claims. Reported as warnings, never as failures.
SOFT_TEMPORAL = ["increase", "decrease", "grow", "decline", "trend", "growth"]

# Claims contradicted by the data: tenure does not drive churn here.
FALSE_TENURE_CLAIMS = [
    "newer customers churn", "new customers churn more",
    "longer tenure", "as customers mature", "as tenure increases",
    "tenure drives", "tenure is a driver", "tenure predicts",
    "early-life churn", "lifecycle churn", "churn declines with tenure",
    "churn rises with tenure", "loyal customers are less likely to churn",
]

# Claims that misrepresent the service-call cliff as a gradient.
FALSE_SLOPE_CLAIMS = [
    "gradually increases with", "linear relationship", "steadily rises with",
    "proportional to the number of calls", "each additional call increases",
    "the more calls, the higher", "rises steadily with",
]

# The thousands-group branch MUST allow a trailing decimal, or "31,566.93"
# is matched as "31,566" and the regex then starts fresh at ".93",
# producing a phantom 93 that no payload contains. Llama wrote "$31,566.93"
# and happened not to trigger it; GPT OSS omits the currency symbol and
# does. \u202f (narrow no-break space) is included in the separator class
# because GPT OSS uses it between a figure and its unit.
NUMBER_RE = re.compile(
    r"\$?[\s\u202f\u00a0]*"
    r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"[\s\u202f\u00a0]*%?"
)
SMALL_INT_ALLOWANCE = 12   # see module docstring

NEGATION_MARKERS = [
    "no ", "not ", "n't", "cannot", "can not", "never", "without",
    "does not", "do not", "unable", "lacks", "absent", "there is no",
    "unavailable", "unsupported", "does not support", "cannot support",
    # Refusal vocabulary the model actually uses in practice.
    "forbidden", "impossible", "insufficient", "single snapshot",
    "only contains", "is null", "not available", "not provided",
    "not possible", "no prior", "no historical",
]

# Must be generous: a refusal often puts the negation at the start of a long
# clause, e.g. "The data does not support a comparison of churn rates between
# newer and long-tenured customers over time" puts "does not" 86 chars before
# "over time". A 60-char window produced false positives on correct refusals.
NEGATION_WINDOW = 180  # characters to look back

# Words that must appear when a response makes a time-based claim from the
# simulated panel. Structural, not documentation: a screenshot of a chart
# outlives its caption, so the prose itself has to carry the disclosure.
SIMULATION_DISCLOSURES = [
    "simulated", "simulation", "synthetic", "generated history",
    "not real history", "illustrative",
]

# Words that frame a statement as a hypothetical rather than an assertion.
# Scenario narration must contain at least one, or a conditional result
# reads as a claim about what is going to happen.
HYPOTHETICAL_MARKERS = [
    "scenario", "hypothetical", "if ", "would", "could", "assuming",
    "under this", "were ", "estimated", "projected", "illustrative",
    "modelled", "modeled", "simulation",
]

# Language that turns a calculation into a prediction. Forbidden for EVERY
# payload origin, including scenarios: the arithmetic says what things
# WOULD look like under an assumption, never what is going to happen.
FORECAST_CLAIMS = [
    "will fall", "will drop", "will decrease", "will rise", "will increase",
    "will improve", "will worsen", "will churn", "will reduce",
    "is expected to fall", "is expected to drop", "is expected to rise",
    "we predict", "we forecast", "the forecast", "our projection shows",
    "next quarter", "next month", "next year", "going forward",
    "is going to", "are going to",
]

# A temporal phrase within this distance of a churn word is a churn-trend
# claim, which stays forbidden even for simulated data.
CHURN_WORDS = ["churn", "attrition", "cancellation", "customers lost",
               "customers leaving", "retention rate"]
CHURN_PROXIMITY = 90


def _near_churn_word(lowered: str, phrase: str) -> bool:
    """True if any occurrence of `phrase` sits near a churn word."""
    start = 0
    while True:
        idx = lowered.find(phrase, start)
        if idx == -1:
            return False
        window = lowered[max(0, idx - CHURN_PROXIMITY):
                         idx + len(phrase) + CHURN_PROXIMITY]
        if any(w in window for w in CHURN_WORDS):
            return True
        start = idx + len(phrase)


# Phrases that FOLLOW a driver claim and defuse it. A correct answer often
# reads "newer customers churn at similar rates to long-tenured ones" — the
# qualifier comes after the pattern, so a backward-only window misses it.
FLATNESS_MARKERS = [
    "similar", "flat", "comparable", "same rate", "no different",
    "not more", "no more", "no higher", "no greater", "not significantly",
    "roughly equal", "little difference",
    "no meaningful difference", "is not a driver", "does not drive",
    "not a churn driver", "ranging from", "essentially the same",
]

FORWARD_WINDOW = 120  # characters to look ahead

# Phrases signalling a CROSS-SECTIONAL comparison rather than a temporal
# one. "Churn increases from 5.0% to 59.0% when this factor is present"
# compares two groups in the same snapshot; it is not a claim about time.
# Without this, the validator rejects a correct description of a segment
# difference purely because the verb happens to be "increases".
COMPARISON_MARKERS = [
    "from", "to", "when", "among", "for customers", "versus", "vs",
    "compared with", "compared to", "against", "between",
    "with this factor", "without", "present", "segment", "group",
    "band", "cohort", "bucket", "threshold", "those who", "customers who",
]

COMPARISON_WINDOW = 100

# Phrases that make a temporal word part of a REQUIREMENT rather than a
# claim. "To assess change over time you would need a prior period" states
# what would be necessary; it asserts nothing about what happened. The
# negation that licenses it usually sits in the preceding sentence, so
# _all_negated's sentence clamping cannot see it.
REQUIREMENT_MARKERS = [
    "you would need", "would require", "would need", "to assess",
    "to determine", "to measure", "to calculate", "in order to",
    "we would need", "requires ", "is needed", "are needed",
    "would have to", "cannot show whether", "cannot be calculated",
]

REQUIREMENT_WINDOW = 120


def _is_requirement(lowered: str, phrase: str) -> bool:
    """True if EVERY occurrence of `phrase` sits inside a requirement.

    Checks backwards only: the requirement framing always precedes the
    temporal word ("to assess change over time...").
    """
    start = 0
    while True:
        idx = lowered.find(phrase, start)
        if idx == -1:
            return True
        back = lowered[max(0, idx - REQUIREMENT_WINDOW):idx]
        if not any(m in back for m in REQUIREMENT_MARKERS):
            return False
        start = idx + len(phrase)


def _is_cross_sectional(lowered: str, phrase: str) -> bool:
    """True if EVERY occurrence of `phrase` reads as a segment comparison.

    Requires the sentence to contain a figure pair as well as comparison
    vocabulary, so a bare "revenue increased" is still caught.
    """
    start = 0
    while True:
        idx = lowered.find(phrase, start)
        if idx == -1:
            return True
        window = lowered[max(0, idx - COMPARISON_WINDOW):
                         idx + len(phrase) + COMPARISON_WINDOW]
        for stop in (".", "!", "?", ";", "\n"):
            if stop in window[:COMPARISON_WINDOW]:
                window = window[window[:COMPARISON_WINDOW].rfind(stop) + 1:]
        # Two figures in the same clause means a comparison of two values,
        # which in a snapshot can only be between groups.
        figures = re.findall(r"\d+(?:\.\d+)?\s*%?", window)
        has_pair = len(figures) >= 2
        has_marker = any(m in window for m in COMPARISON_MARKERS)
        if not (has_pair and has_marker):
            return False
        start = idx + len(phrase)


def _claim_defused(lowered: str, phrase: str) -> bool:
    """True if EVERY occurrence of a driver claim is negated or qualified.

    Checks backwards for negation ("newer customers do not churn more") and
    forwards for flatness ("newer customers churn at similar rates").
    """
    start = 0
    while True:
        idx = lowered.find(phrase, start)
        if idx == -1:
            return True
        back = lowered[max(0, idx - NEGATION_WINDOW):idx]
        for stop in (".", "!", "?", ";", "\n"):
            if stop in back:
                back = back.rsplit(stop, 1)[1]
        fwd = lowered[idx:idx + len(phrase) + FORWARD_WINDOW]
        for stop in (".", "!", "?", ";", "\n"):
            if stop in fwd:
                fwd = fwd.split(stop, 1)[0]
        # Backward context accepts BOTH vocabularies: "churn is flat across
        # cohorts, so newer customers churn..." is defused by the flatness
        # statement even though no negation word appears.
        negated = any(m in back for m in NEGATION_MARKERS + FLATNESS_MARKERS)
        qualified = any(m in fwd for m in FLATNESS_MARKERS)
        if not (negated or qualified):
            return False
        start = idx + len(phrase)


def _all_negated(lowered: str, phrase: str) -> bool:
    """True if EVERY occurrence of `phrase` sits inside a negation.

    One un-negated occurrence is enough to constitute a violation, so we
    require all of them to be negated before dismissing the phrase.
    """
    start = 0
    while True:
        idx = lowered.find(phrase, start)
        if idx == -1:
            return True
        window = lowered[max(0, idx - NEGATION_WINDOW):idx]
        # A negation only applies within its own sentence. Without this,
        # "There is no prior period. Revenue increased." would be excused
        # by the 'no' belonging to the previous sentence.
        for stop in (".", "!", "?", ";", "\n"):
            if stop in window:
                window = window.rsplit(stop, 1)[1]
        if not any(m in window for m in NEGATION_MARKERS):
            return False
        start = idx + len(phrase)


# --------------------------------------------------------------------------
# Payload origin
# --------------------------------------------------------------------------
def _has_origin(payload: dict, origin: str) -> bool:
    """Search the payload for a data_origin marker anywhere in the tree."""
    target = origin.upper()

    def walk(node) -> bool:
        if isinstance(node, dict):
            if str(node.get("data_origin", "")).upper() == target:
                return True
            return any(walk(v) for v in node.values())
        if isinstance(node, (list, tuple)):
            return any(walk(v) for v in node)
        return False
    return walk(payload)


def _is_simulated(payload: dict) -> bool:
    """True when the payload comes from the simulated history panel.

    Detected from the payload itself rather than passed in as a flag, so a
    caller cannot accidentally unlock temporal claims for real data.
    """
    return _has_origin(payload, "SIMULATED")


def _is_projected(payload: dict) -> bool:
    """True when the payload is a hypothetical scenario calculation.

    Scenario output is inherently conditional ("churn would fall to
    14.10%"), which the temporal rules would otherwise reject. Same
    detection route as _is_simulated, for the same reason.
    """
    return _has_origin(payload, "PROJECTED")


# --------------------------------------------------------------------------
def collect_payload_numbers(obj) -> set[float]:
    """Every numeric value anywhere in the payload, plus rounded variants."""
    found: set[float] = set()

    def walk(node):
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, (list, tuple)):
            for v in node:
                walk(v)
        elif isinstance(node, bool):
            pass                      # True/False are not figures
        elif isinstance(node, (int, float)):
            found.add(float(node))
        elif isinstance(node, str):
            for m in NUMBER_RE.finditer(node):
                try:
                    found.add(float(m.group(1).replace(",", "")))
                except ValueError:
                    pass

    walk(obj)

    # Rounded forms: the model may write 67.9 or 68 for a stored 67.86.
    expanded = set(found)
    for v in found:
        expanded.update({round(v), round(v, 1), round(v, 2)})
        if v != 0:
            expanded.add(abs(v))
    return expanded


def extract_numbers(text: str) -> list[float]:
    out = []
    for m in NUMBER_RE.finditer(text):
        try:
            out.append(float(m.group(1).replace(",", "")))
        except ValueError:
            pass
    return out


def _grounded(value: float, allowed: set[float], rel_tol: float) -> bool:
    if value in allowed:
        return True
    for a in allowed:
        if a == 0:
            if abs(value) < 1e-9:
                return True
            continue
        if abs(value - a) <= abs(a) * rel_tol:
            return True
    return False


# --------------------------------------------------------------------------
def validate(text: str, payload: dict, rel_tol: float = 0.01) -> dict:
    """Check an LLM response against the payload that produced it.

    Returns a report dict. `passed` is False if any hard violation fired.
    """
    simulated = _is_simulated(payload)
    projected = _is_projected(payload)
    lowered = text.lower()
    violations: list[dict] = []
    warnings: list[dict] = []

    # --- 1. number grounding ------------------------------------------
    allowed = collect_payload_numbers(payload)
    ungrounded = []
    for value in extract_numbers(text):
        if value.is_integer() and abs(value) <= SMALL_INT_ALLOWANCE:
            continue
        if not _grounded(value, allowed, rel_tol):
            ungrounded.append(value)
    if ungrounded:
        violations.append({
            "type": "ungrounded_number",
            "detail": f"figures absent from payload: {sorted(set(ungrounded))}",
        })

    # --- 2. temporal claims -------------------------------------------
    # A phrase inside a negation is a correct REFUSAL, not a violation:
    # "there is no prior period" must pass, "up from the prior period"
    # must fail. A phrase inside a segment comparison is also fine:
    # "churn increases from 5.0% to 59.0%" compares groups, not periods.
    hits = [p for p in FORBIDDEN_TEMPORAL
            if p in lowered
            and not _all_negated(lowered, p)
            and not _is_cross_sectional(lowered, p)
            and not _is_requirement(lowered, p)]

    if hits and projected:
        # A scenario is explicitly hypothetical, so conditional language is
        # expected. It must still be FRAMED as one, or a conditional result
        # reads as an assertion about what happened.
        if not any(m in lowered for m in HYPOTHETICAL_MARKERS):
            violations.append({
                "type": "unframed_hypothetical",
                "detail": ("describes a change without framing it as a "
                           "hypothetical scenario"),
            })

    elif hits and simulated:
        # Structural time-series language is permitted, but churn trends
        # are not: simulated churn is flat by construction.
        churn_trend = [p for p in hits if _near_churn_word(lowered, p)]
        if churn_trend:
            violations.append({
                "type": "simulated_churn_trend",
                "detail": (f"claims a churn trend from simulated history, "
                           f"which is flat by construction: {churn_trend}"),
            })
        if not any(m in lowered for m in SIMULATION_DISCLOSURES):
            violations.append({
                "type": "undisclosed_simulation",
                "detail": ("makes a time-based claim from simulated history "
                           "without saying the history is simulated"),
            })

    elif hits:
        violations.append({
            "type": "temporal_claim",
            "detail": f"asserts change over time: {hits}",
        })

    # --- 3. forecast claims -------------------------------------------
    # Checked INDEPENDENTLY of the temporal hits. "will drop" is not in
    # FORBIDDEN_TEMPORAL, so gating this on `hits` let a scenario be
    # presented as a prediction without any violation firing.
    forecast_hits = [p for p in FORECAST_CLAIMS if p in lowered]
    if forecast_hits:
        violations.append({
            "type": "forecast_claim",
            "detail": (f"states what WILL happen. Nothing in this project "
                       f"forecasts: {forecast_hits}"),
        })

    soft = [w for w in SOFT_TEMPORAL
            if re.search(rf"\b{re.escape(w)}\b", lowered)]
    if soft:
        warnings.append({
            "type": "soft_temporal",
            "detail": f"check these are recommendations, not claims: {soft}",
        })

    # --- 4. false drivers ---------------------------------------------
    tenure_hits = [p for p in FALSE_TENURE_CLAIMS
                   if p in lowered and not _claim_defused(lowered, p)]
    if tenure_hits:
        violations.append({
            "type": "false_tenure_claim",
            "detail": f"tenure does not drive churn in this data: {tenure_hits}",
        })

    slope_hits = [p for p in FALSE_SLOPE_CLAIMS
                  if p in lowered and not _claim_defused(lowered, p)]
    if slope_hits:
        violations.append({
            "type": "false_slope_claim",
            "detail": f"service calls are a cliff, not a slope: {slope_hits}",
        })

    return {
        "passed": not violations,
        "violations": violations,
        "warnings": warnings,
        "data_origin": ("PROJECTED" if projected
                        else "SIMULATED" if simulated else "OBSERVED"),
        "numbers_checked": len(extract_numbers(text)),
    }


def format_report(report: dict) -> str:
    if report["passed"] and not report["warnings"]:
        return (f"OK ({report['numbers_checked']} numbers verified, "
                f"{report.get('data_origin', 'OBSERVED')})")
    lines = []
    for v in report["violations"]:
        lines.append(f"  VIOLATION [{v['type']}] {v['detail']}")
    for w in report["warnings"]:
        lines.append(f"  warning   [{w['type']}] {w['detail']}")
    head = "PASSED with warnings" if report["passed"] else "FAILED"
    return head + "\n" + "\n".join(lines)