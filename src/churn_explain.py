"""
Phase 10 — SHAP attribution and AI explanation.

Three levels, each validated by the Phase 4 guardrails:

  1. MODEL COMPARISON   what the metrics mean, and which model to pick
  2. GLOBAL DRIVERS     which features matter across the whole base
  3. PER-CUSTOMER       why THIS customer scored what they scored

WHAT SHAP MEASURES, AND WHAT IT DOES NOT
----------------------------------------
A SHAP value is a feature's contribution to a PREDICTION, not to the
outcome. "Service calls contributed +0.31 to this score" is correct;
"service calls caused this customer to churn" is not. The distinction is
subtle enough that models routinely blur it, so the prompt forbids causal
language explicitly and the guardrail rejects it.

THE ACTION DOES NOT COME FROM THE MODEL
---------------------------------------
SHAP says WHY a customer scored high. rules.py already says WHAT TO DO
about each driver. Keeping those separate means the recommendation stays
deterministic even though the score is probabilistic.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import churn_model as M
import guardrails
import prompts
from llm_provider import LLMError, get_provider_for

# Maps a model feature to the rules-engine driver it corresponds to, so a
# SHAP attribution can be turned into a deterministic recommended action.
FEATURE_TO_ACTION = {
    "customer_service_calls": {
        "driver": "4+ service calls",
        "action": "Proactive support review call",
        "cause": "unresolved support issue",
    },
    "international_plan": {
        "driver": "international plan",
        "action": "Plan review — check the tariff fits actual usage",
        "cause": "tariff may not fit usage",
    },
    "day_charge": {
        "driver": "heavy daytime usage",
        "action": "Move to a daytime bundle",
        "cause": "bill shock from high daytime charges",
    },
    "total_charge": {
        "driver": "high overall spend",
        "action": "Tariff review",
        "cause": "overall bill level",
    },
}


# ==========================================================================
def _explainer_and_matrix(sample: int = 500):
    """SHAP values for the winning model over a sample of customers."""
    import shap

    bundle = M.load_model()
    df = M.load_data()
    X = df[bundle["features"]]

    entry = bundle["fitted"][bundle["winner"]]
    if entry["scale"]:
        X_model = pd.DataFrame(bundle["scaler"].transform(X),
                               columns=bundle["features"])
    else:
        X_model = X

    sub = X_model.sample(min(sample, len(X_model)),
                         random_state=M.RANDOM_SEED)
    explainer = shap.TreeExplainer(entry["model"])
    values = explainer.shap_values(sub)
    return bundle, df, X_model, sub, explainer, values


def global_importance(sample: int = 500) -> dict:
    """Mean absolute SHAP contribution per feature."""
    bundle, _, _, sub, _, values = _explainer_and_matrix(sample)
    mean_abs = np.abs(values).mean(axis=0)

    rows = sorted(
        [{"feature": f, "mean_abs_shap": round(float(v), 4)}
         for f, v in zip(bundle["features"], mean_abs)],
        key=lambda r: r["mean_abs_shap"], reverse=True)

    total = sum(r["mean_abs_shap"] for r in rows) or 1.0
    for r in rows:
        r["share_pct"] = round(r["mean_abs_shap"] / total * 100, 1)

    return {
        "meta": {
            "data_origin": "MODEL",
            "measures": ("contribution to the model's PREDICTION, not to "
                         "the outcome. A high value means the feature moved "
                         "the score, not that it caused the churn"),
            "sample_size": len(sub),
            "model": M.model_metrics()["models"][bundle["winner"]]["label"],
        },
        "features": rows,
        "confirms_rules_engine": [
            r["feature"] for r in rows[:3]
            if r["feature"] in FEATURE_TO_ACTION],
    }


def explain_customer(customer_id: int) -> dict:
    """Risk score plus per-feature SHAP contributions for one customer."""
    import shap

    bundle = M.load_model()
    df = M.load_data()
    row = df[df.customer_id == customer_id]
    if row.empty:
        raise ValueError(f"customer {customer_id} not found")

    X = row[bundle["features"]]
    entry = bundle["fitted"][bundle["winner"]]
    X_model = (pd.DataFrame(bundle["scaler"].transform(X),
                            columns=bundle["features"])
               if entry["scale"] else X)

    score = float(entry["model"].predict_proba(X_model)[:, 1][0])
    explainer = shap.TreeExplainer(entry["model"])
    values = explainer.shap_values(X_model)[0]

    contribs = sorted(
        [{"feature": f,
          "value": float(row[f].iloc[0]),
          "shap": round(float(v), 4),
          "direction": "raises risk" if v > 0 else "lowers risk"}
         for f, v in zip(bundle["features"], values)],
        key=lambda c: abs(c["shap"]), reverse=True)

    # The action is looked up deterministically from rules.py, never
    # generated by the model.
    top_raising = [c for c in contribs if c["shap"] > 0][:3]
    actions = [FEATURE_TO_ACTION[c["feature"]]
               for c in top_raising if c["feature"] in FEATURE_TO_ACTION]

    return {
        "meta": {
            "data_origin": "MODEL",
            "claim": ("this customer resembles those who already churned to "
                      "this degree"),
            "not_a_claim": ("not a prediction that they will leave, and not "
                            "a statement about when"),
            "shap_note": ("contributions are to the SCORE, not to the "
                          "outcome"),
        },
        "customer_id": int(customer_id),
        "risk_score": round(score, 4),
        "flagged": bool(score >= bundle["threshold"]),
        "threshold": round(float(bundle["threshold"]), 4),
        "actually_churned": int(row[M.TARGET].iloc[0]),
        "contributions": contribs,
        "recommended_actions": actions,
    }


# ==========================================================================
def _narrate(payload: dict, instruction: str) -> dict:
    provider = get_provider_for("narration")
    user = prompts.build_user_prompt("", payload, instruction)
    try:
        text = provider.complete(prompts.SYSTEM_PROMPT, user)
    except LLMError as exc:
        return {"valid": False, "text": None, "error": str(exc),
                "payload": payload}
    report = guardrails.validate(text, payload)
    return {"valid": report["passed"], "text": text,
            "validation": report, "payload": payload}


SHARED_RULES = (
    "\n\nABSOLUTE RULES FOR MODEL OUTPUT:\n"
    "- SHAP values measure contribution to the SCORE, not to the outcome. "
    "Never say a feature 'caused' churn.\n"
    "- A risk score is a probability, never a verdict. Never write that a "
    "customer 'will' churn.\n"
    "- The model was trained on a single snapshot. It ranks resemblance to "
    "past churners; it does not forecast timing.\n"
    "- Use only the numbers in DATA."
)


def narrate_comparison() -> dict:
    payload = M.model_metrics()
    instruction = (
        "Explain this model comparison to a non-technical manager in 5-7 "
        "sentences.\n"
        "1. Say why accuracy is NOT used here, citing the base rate given.\n"
        "2. Compare the models on the metrics provided. Note where a "
        "simpler model matches a more complex one, if it does.\n"
        "3. Explain the two operating points: one matched to retention "
        "capacity, one balancing precision against recall. Say what the "
        "tradeoff means in practice.\n"
        "4. State which model you would use and why." + SHARED_RULES)
    return _narrate(payload, instruction)


def narrate_global() -> dict:
    payload = global_importance()
    instruction = (
        "Explain which factors the model relies on, in 4-6 sentences.\n"
        "1. Name the top contributors and their relative shares.\n"
        "2. Say whether these match the three drivers the rules engine "
        "already uses — that agreement (or disagreement) is the point.\n"
        "3. State plainly that these are contributions to the model's "
        "score, not evidence of causation." + SHARED_RULES)
    return _narrate(payload, instruction)


def narrate_customer(customer_id: int) -> dict:
    payload = explain_customer(customer_id)
    instruction = (
        "Explain this customer's risk score to a retention agent in 3-5 "
        "sentences.\n"
        "1. State the score and whether it is above the flagging "
        "threshold.\n"
        "2. Name the factors that raised it most, using the SHAP values "
        "given.\n"
        "3. State the recommended action from the data. Do not invent an "
        "action that is not listed.\n"
        "Be direct — an agent is about to make a call." + SHARED_RULES)
    return _narrate(payload, instruction)