"""
Phase 5 — natural language to SQL.

Pipeline:

    question
      -> SQL model (Qwen)          generate a SELECT
      -> sql_guard.validate()      static checks, allowlist, LIMIT
      -> read-only execution       mode=ro connection; writes impossible
      -> narration model (Llama)   explain the rows
      -> guardrails.validate()     Phase 4 checks still apply
      -> answer

On failure the pipeline degrades rather than erroring: one regeneration
attempt with the rejection reason fed back, then a fallback to the closest
Phase 2 metric endpoint.
"""

from __future__ import annotations

import re

import pandas as pd
from sqlalchemy import create_engine, text

import config as C
import guardrails
import metrics
import prompts
import sql_guard
from llm_provider import LLMError, get_provider_for

# --------------------------------------------------------------------------
# Read-only engine. This is the layer that actually guarantees safety:
# even a validator bypass cannot write, because SQLite refuses at the driver.
_ro_engine = None


def ro_engine():
    global _ro_engine
    if _ro_engine is None:
        _ro_engine = create_engine(
            f"sqlite:///file:{C.DB_PATH}?mode=ro&uri=true",
            connect_args={"uri": True},
        )
    return _ro_engine


# --------------------------------------------------------------------------
SQL_SYSTEM_PROMPT = f"""\
You translate business questions into a single SQLite SELECT statement.

{sql_guard.describe_schema()}

RULES
- Output ONLY the SQL. No explanation, no markdown fences, no commentary.
- Exactly ONE statement. No semicolons.
- SELECT only. Never INSERT, UPDATE, DELETE, DROP, ALTER, CREATE or PRAGMA.
- Query only the views listed above. Raw tables are not accessible.
- Always include a LIMIT clause (at most 200).
- Percentage columns are already scaled to 100; do not multiply again.
- There is no date column. If the question asks about change over time,
  return: SELECT 'no time dimension' AS answer LIMIT 1
- There is no date column. If the question asks about change over time,
  return: SELECT 'no time dimension' AS answer FROM v_kpi_summary LIMIT 1
"""

FALLBACK_KEYWORDS = [
    (("segment", "priorit", "retention", "risk"), "risk_segments"),
    (("state", "region", "geography"), "churn_by_state"),
    (("service call", "support call", "complaint"), "churn_by_service_calls"),
    (("plan", "international", "voicemail"), "churn_by_plan"),
    (("cohort", "tenure", "new customer"), "cohort_profile"),
    (("period", "day", "evening", "night"), "revenue_by_period"),
    (("revenue", "arpu", "income"), "revenue_by_state"),
]


def _fallback_metric(question: str) -> str:
    q = question.lower()
    for keywords, metric in FALLBACK_KEYWORDS:
        if any(k in q for k in keywords):
            return metric
    return "kpi_summary"


# --------------------------------------------------------------------------
def generate_sql(question: str, feedback: str | None = None,
                 model: str | None = None) -> str:
    """Ask the SQL model for a statement. Raises LLMError on transport failure."""
    from llm_provider import GroqProvider, LLM_PROVIDER
    provider = (GroqProvider(model=model)
                if model and LLM_PROVIDER == "groq"
                else get_provider_for("sql"))

    user = f"QUESTION: {question}\n\nReturn one SQLite SELECT statement."
    if feedback:
        user += (f"\n\nYour previous attempt was rejected: {feedback}\n"
                 "Fix it and return only the corrected SQL.")
    return provider.complete(SQL_SYSTEM_PROMPT, user)


def execute_sql(sql: str) -> list[dict]:
    with ro_engine().connect() as conn:
        df = pd.read_sql(text(sql), conn)
    return df.to_dict(orient="records")


# --------------------------------------------------------------------------
def ask(question: str, narrate_result: bool = True,
        sql_model: str | None = None) -> dict:
    """Full natural-language question pipeline."""
    result = {
        "question": question,
        "sql": None,
        "rows": None,
        "answer": None,
        "valid": False,
        "path": None,
        "attempts": 0,
        "error": None,
    }

    feedback = None
    for attempt in (1, 2):
        result["attempts"] = attempt
        try:
            raw = generate_sql(question, feedback, model=sql_model)
        except LLMError as exc:
            result["error"] = f"SQL generation failed: {exc}"
            break

        try:
            sql = sql_guard.validate(raw)
        except sql_guard.SQLRejected as exc:
            feedback = str(exc)
            result["error"] = f"rejected: {exc}"
            continue

        try:
            rows = execute_sql(sql)
        except Exception as exc:
            feedback = f"SQL failed to execute: {type(exc).__name__}: {exc}"
            result["error"] = feedback
            continue

        result.update(sql=sql, rows=rows, path="generated_sql", error=None)
        break

    # --- fallback: route to a curated Phase 2 metric --------------------
    if result["rows"] is None:
        metric = _fallback_metric(question)
        fn = metrics.REGISTRY[metric]
        payload = fn()
        result.update(rows=payload["data"], path=f"fallback:{metric}")

    if not narrate_result:
        result["valid"] = True
        return result

    # --- narrate the rows through the Phase 4 pipeline ------------------
    payload = {
        "meta": metrics.SNAPSHOT_META,
        "query_result": result["rows"],
        "sql": result["sql"],
    }
    narrator = get_provider_for("narration")
    user = prompts.build_user_prompt(
        question, payload, prompts.TASK_ANSWER_QUESTION)
    try:
        answer = narrator.complete(prompts.SYSTEM_PROMPT, user)
    except LLMError as exc:
        result["error"] = f"narration failed: {exc}"
        return result

    report = guardrails.validate(answer, payload)
    result.update(answer=answer, valid=report["passed"], validation=report)
    return result