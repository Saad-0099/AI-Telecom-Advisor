"""
Phase 4 — output guardrails.

A prompt instruction is a request. This module is the enforcement: every
LLM response is checked against the payload it was given BEFORE anyone
sees it.

Three checks:
  1. NUMBER GROUNDING  every figure in the text must exist in the payload
  2. TEMPORAL CLAIMS   no assertion of change over time
  3. FALSE DRIVERS     no tenure-causes-churn claim, no linear service-call claim

Design note on check 1: we deliberately allow small integers (0-12) without
a payload match, because they appear in ordinary prose ("three factors",
"the top 5 states") and flagging them produced constant false positives.
The tradeoff is that a fabricated small count could slip through; every
number large enough to be a metric is still caught.
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

NUMBER_RE = re.compile(r"\$?\s*(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*%?")

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

def _is_simulated(payload: dict) -> bool:
    """True when the payload comes from the simulated history panel.

    Detected from the payload itself rather than passed in as a flag, so a
    caller cannot accidentally unlock temporal claims for real data.
    """
    def walk(node) -> bool:
        if isinstance(node, dict):
            if str(node.get("data_origin", "")).upper() == "SIMULATED":
                return True
            return any(walk(v) for v in node.values())
        if isinstance(node, (list, tuple)):
            return any(walk(v) for v in node)
        return False
    return walk(payload)

# --------------------------------------------------------------------------
def validate(text: str, payload: dict, rel_tol: float = 0.01) -> dict:
    """Check an LLM response against the payload that produced it.
    
    Returns a report dict. `passed` is False if any hard violation fired.
    """
    simulated = _is_simulated(payload)
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
    # must fail. We inspect the text immediately preceding each match.
    hits = [p for p in FORBIDDEN_TEMPORAL
            if p in lowered and not _all_negated(lowered, p)]

    if hits and not simulated:
        violations.append({
            "type": "temporal_claim",
            "detail": f"asserts change over time: {hits}",
        })
    elif simulated:
        # Structural time-series language is permitted, but churn trends
        # are not: simulated churn is flat by construction.
        churn_trend = [p for p in hits if _near_churn_word(lowered, p)]
        if churn_trend:
            violations.append({
                "type": "simulated_churn_trend",
                "detail": (f"claims a churn trend from simulated history, "
                           f"which is flat by construction: {churn_trend}"),
            })
        if hits and not any(m in lowered for m in SIMULATION_DISCLOSURES):
            violations.append({
                "type": "undisclosed_simulation",
                "detail": ("makes a time-based claim from simulated history "
                           "without saying the history is simulated"),
            })

    # --- 3. false drivers ---------------------------------------------
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
        "numbers_checked": len(extract_numbers(text)),
    }


def format_report(report: dict) -> str:
    if report["passed"] and not report["warnings"]:
        return f"OK ({report['numbers_checked']} numbers verified)"
    lines = []
    for v in report["violations"]:
        lines.append(f"  VIOLATION [{v['type']}] {v['detail']}")
    for w in report["warnings"]:
        lines.append(f"  warning   [{w['type']}] {w['detail']}")
    head = "PASSED with warnings" if report["passed"] else "FAILED"
    return head + "\n" + "\n".join(lines)