"""
Phase 4 — the narration layer.

Pipeline for every call:

    metrics view -> payload -> prompt -> LLM -> guardrail validation -> result

If validation fails the response is returned WITH the violations attached
and `valid: False`. Nothing downstream (Phase 7 reports, Phase 9 UI) should
display a response whose `valid` flag is False.

Optional one retry: on failure the model is re-asked with its violations
appended, which fixes most first-attempt slips.
"""

from __future__ import annotations

import guardrails
import metrics
import prompts
from llm_provider import LLMError, get_provider


def _run(payload: dict, question: str = "", instruction: str | None = None,
         retry_on_fail: bool = True) -> dict:
    provider = get_provider()
    user = prompts.build_user_prompt(question, payload, instruction)

    try:
        text = provider.complete(prompts.SYSTEM_PROMPT, user)
    except LLMError as exc:
        return {"valid": False, "text": None, "error": str(exc),
                "provider": provider.name}

    report = guardrails.validate(text, payload)
    attempts = 1

    if not report["passed"] and retry_on_fail:
        correction = (
            user
            + "\n\nYOUR PREVIOUS ANSWER WAS REJECTED:\n"
            + "\n".join(f"- {v['type']}: {v['detail']}"
                        for v in report["violations"])
            + "\nRewrite it. Use only numbers from DATA. Make no claim "
              "about change over time."
        )
        try:
            text = provider.complete(prompts.SYSTEM_PROMPT, correction)
            report = guardrails.validate(text, payload)
            attempts = 2
        except LLMError as exc:
            return {"valid": False, "text": text, "error": str(exc),
                    "provider": provider.name, "attempts": attempts}

    return {
        "valid": report["passed"],
        "text": text,
        "validation": report,
        "provider": provider.name,
        "attempts": attempts,
    }


# ==========================================================================
# Narration jobs
# ==========================================================================
def narrate_kpi() -> dict:
    payload = metrics.kpi_summary()
    return _run(payload, instruction=prompts.TASK_KPI_NARRATION)


def narrate_risk_segments() -> dict:
    payload = metrics.risk_segments()
    return _run(payload, instruction=prompts.TASK_SEGMENT_BRIEF)


def explain_chart(metric_name: str, **kwargs) -> dict:
    """Module 8 — the AI Chart Explainer, driven by a metric name."""
    fn = metrics.REGISTRY.get(metric_name)
    if fn is None:
        return {"valid": False, "text": None,
                "error": f"unknown metric '{metric_name}'. "
                         f"Choose from {sorted(metrics.REGISTRY)}"}
    payload = fn(**kwargs) if kwargs else fn()
    return _run(payload, instruction=prompts.TASK_CHART_EXPLAINER)


def answer_question(question: str, metric_name: str | None = None) -> dict:
    """Constrained Q&A. Phase 5 replaces metric_name with generated SQL."""
    if metric_name:
        fn = metrics.REGISTRY.get(metric_name)
        if fn is None:
            return {"valid": False, "text": None,
                    "error": f"unknown metric '{metric_name}'"}
        payload = fn()
    else:
        payload = metrics.full_briefing()
    return _run(payload, question=question,
                instruction=prompts.TASK_ANSWER_QUESTION)