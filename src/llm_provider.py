"""
Phase 4/5 — LLM provider abstraction.

Three interchangeable backends selected by the LLM_PROVIDER env var:

    mock    default. No network, instant, deterministic. Used by the eval
            harness so tests cost nothing and run offline.
    groq    demo path. Llama 3.3 70B on Groq's LPU hardware. Fast + free.
    ollama  offline fallback. Local llama3.1:8b via http://localhost:11434.

Configuration lives in a .env file beside this module:
    LLM_PROVIDER=groq
    GROQ_API_KEY=gsk_...
    GROQ_MODEL=llama-3.3-70b-versatile
    GROQ_SQL_MODEL=llama-3.3-70b-versatile

Phase 5 adds ROLE-BASED SELECTION: narration and SQL generation are
different jobs, so get_provider_for('sql') and get_provider_for('narration')
return providers pointed at different models AND different token budgets.
Both are per-instance, so one process can run several configurations at
once. Only Groq varies by role; mock and ollama ignore it.

Nothing else in the codebase constructs a provider directly — everything
goes through get_provider() or get_provider_for().
"""


from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod

from dotenv import load_dotenv
# .env sits at the PROJECT ROOT, one level above src/. load_dotenv() with no
# argument searches upward from the CWD, which fails when the process is
# started from elsewhere, so the path is given explicitly.
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), ".env"))

# --------------------------------------------------------------------------
# Configuration (env-driven so no secret is ever committed)
# --------------------------------------------------------------------------
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "mock").lower()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")

TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.1"))  # low: we want facts

# Default budget, used when no role is specified.
MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "2000"))

TIMEOUT_S = int(os.getenv("LLM_TIMEOUT", "120"))

# Different jobs want different models. Narration wants fluent prose;
# SQL generation wants code accuracy. Override either via env var.
ROLE_MODELS = {
    "narration": os.getenv("GROQ_NARRATION_MODEL", GROQ_MODEL),
    "sql": os.getenv("GROQ_SQL_MODEL", GROQ_MODEL),
}

# Reasoning models emit a thinking trace before the answer. A budget tuned
# for narration truncates them mid-thought, which surfaces as malformed SQL
# rather than as an obvious "response was cut off" error. Benchmarking Qwen
# 3.6 at 700 tokens produced 0/20 for exactly this reason.
ROLE_MAX_TOKENS = {
    "narration": MAX_TOKENS,
    "sql": int(os.getenv("LLM_SQL_MAX_TOKENS", "2500")),
}

# Groq's free tier is limited by TOKENS per minute, not just requests. The
# SQL prompt carries the full schema (~900 tokens), so a 20-question eval
# run overruns TPM long before it overruns RPM. Space the calls out and
# back off on 429 rather than failing the run.
MIN_CALL_INTERVAL = float(os.getenv("LLM_MIN_INTERVAL", "3.0"))  # seconds
MAX_RETRIES_429 = int(os.getenv("LLM_MAX_RETRIES", "4"))


class LLMError(RuntimeError):
    pass


class RateLimited(LLMError):
    """429 that survived every backoff attempt."""


class Truncated(LLMError):
    """Response hit the token ceiling before finishing."""


# Module-level throttle shared by every Groq instance, since the quota is
# per-account, not per-object.
_last_call_at = 0.0


def _throttle() -> None:
    global _last_call_at
    wait = MIN_CALL_INTERVAL - (time.monotonic() - _last_call_at)
    if wait > 0:
        time.sleep(wait)
    _last_call_at = time.monotonic()


# --------------------------------------------------------------------------
class Provider(ABC):
    name: str = "base"

    @abstractmethod
    def complete(self, system: str, user: str) -> str:
        """Return the model's text response."""

    def available(self) -> tuple[bool, str]:
        return True, "ok"


# --------------------------------------------------------------------------
class MockProvider(Provider):
    """Deterministic stub. Returns text built ONLY from the payload, so the
    guardrail validator can be tested without spending a single API call."""

    name = "mock"

    def __init__(self, model: str | None = None,
                 max_tokens: int | None = None):
        # Accepts the same kwargs as the real providers so get_provider_for()
        # can construct any backend uniformly.
        self.model = model or "mock"
        self.max_tokens = max_tokens or MAX_TOKENS

    def complete(self, system: str, user: str) -> str:
        # SQL role: return a valid, allowlisted SELECT so the Phase 5
        # pipeline can be exercised end-to-end without a network call.
        if "SELECT statement" in system or "SELECT statement" in user:
            return ("SELECT segment, customers, churn_rate_pct "
                    "FROM v_risk_segments ORDER BY churn_rate_pct DESC LIMIT 5")

        # Narration role: echo only figures actually present in the DATA
        # block, so the mock is grounded by construction and the pipeline
        # can be tested end-to-end without a network call.
        import re
        nums = re.findall(r":\s*(-?\d+(?:\.\d+)?)", user)[:3]
        shown = ", ".join(nums) if nums else "no figures supplied"
        return (
            f"Observation: the portfolio metrics include {shown}. "
            "Driver: the high service-call segment concentrates the risk. "
            "Recommendation: prioritise proactive outreach to that segment."
        )


# --------------------------------------------------------------------------
class GroqProvider(Provider):
    name = "groq"

    def __init__(self, model: str | None = None,
                 max_tokens: int | None = None):
        # Per-instance so one process can run different models and budgets
        # for different roles simultaneously.
        self.model = model or GROQ_MODEL
        self.max_tokens = max_tokens or MAX_TOKENS

    def available(self) -> tuple[bool, str]:
        if not GROQ_API_KEY:
            return False, "GROQ_API_KEY is not set"
        return True, f"groq / {self.model}"

    def _request(self, system: str, user: str) -> dict:
        body = json.dumps({
            "model": self.model,
            "temperature": TEMPERATURE,
            "max_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }).encode("utf-8")

        req = urllib.request.Request(
            GROQ_URL, data=body, method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {GROQ_API_KEY}",
                # Cloudflare fronts api.groq.com and blocks the default
                # "Python-urllib/3.x" agent with HTTP 403 code 1010.
                "User-Agent": "telecom-dip/0.5 (python-urllib)",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return json.loads(resp.read())

    def complete(self, system: str, user: str) -> str:
        ok, msg = self.available()
        if not ok:
            raise LLMError(msg)

        detail = ""
        for attempt in range(MAX_RETRIES_429 + 1):
            _throttle()
            try:
                data = self._request(system, user)
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:400]
                if exc.code == 400:
                    raise LLMError(
                        f"Groq rejected the request (400). Usually a malformed "
                        f"parameter. {detail}") from exc
                if exc.code == 401:
                    raise LLMError("Groq rejected the API key (401).") from exc
                if exc.code == 403 and "1010" in detail:
                    raise LLMError(
                        "Cloudflare blocked the client signature (403/1010)."
                    ) from exc
                if exc.code == 404:
                    # Providers decommission models on their own schedule;
                    # Groq retired Llama 3.3 70B on 16 Aug 2026 with about
                    # two weeks' notice. This is a config problem, not a
                    # code problem, so say exactly which line to edit.
                    raise LLMError(
                        f"Groq does not recognise model '{self.model}' (404). "
                        f"The model may have been decommissioned. List the "
                        f"live models at https://api.groq.com/openai/v1/models "
                        f"and update GROQ_MODEL / GROQ_SQL_MODEL in .env."
                    ) from exc
                if exc.code != 429:
                    raise LLMError(f"Groq HTTP {exc.code}: {detail}") from exc
                if attempt == MAX_RETRIES_429:
                    raise RateLimited(
                        f"Groq rate limit after {attempt + 1} attempts. "
                        f"Lower concurrency or raise LLM_MIN_INTERVAL. {detail}"
                    ) from exc
                # Honour Retry-After when present, else exponential backoff
                # with jitter so retries do not synchronise.
                retry_after = exc.headers.get("retry-after")
                delay = (float(retry_after) if retry_after
                         else (2 ** attempt) + random.random())
                time.sleep(min(delay, 30.0))
            except urllib.error.URLError as exc:
                raise LLMError(f"Cannot reach Groq: {exc.reason}") from exc

        try:
            choice = data["choices"][0]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"Unexpected Groq response shape: {data}") from exc

        # A reasoning model that runs out of budget mid-thought returns a
        # fragment, which reaches the SQL guard as "unrecognized token" and
        # sends you hunting the wrong bug. Name the real cause.
        if choice.get("finish_reason") == "length":
            raise LLMError(
                f"Response truncated at {self.max_tokens} tokens "
                f"(model '{self.model}'). If this is a reasoning model, "
                f"raise LLM_SQL_MAX_TOKENS in .env.")

        return choice["message"]["content"].strip()


# --------------------------------------------------------------------------
class OllamaProvider(Provider):
    name = "ollama"

    def __init__(self, model: str | None = None,
                 max_tokens: int | None = None):
        self.model = model or OLLAMA_MODEL
        self.max_tokens = max_tokens or MAX_TOKENS

    def available(self) -> tuple[bool, str]:
        tags = OLLAMA_URL.replace("/api/chat", "/api/tags")
        try:
            with urllib.request.urlopen(tags, timeout=5) as resp:
                models = [m["name"] for m in json.loads(resp.read())["models"]]
        except Exception as exc:
            return False, f"Ollama not reachable at {tags}: {exc}"
        if self.model not in models:
            return False, (f"model '{self.model}' not pulled. "
                           f"Run: ollama pull {self.model}. Have: {models}")
        return True, f"ollama / {self.model}"

    def complete(self, system: str, user: str) -> str:
        body = json.dumps({
            "model": self.model,
            "stream": False,
            "options": {"temperature": TEMPERATURE,
                        "num_predict": self.max_tokens},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }).encode("utf-8")

        req = urllib.request.Request(
            OLLAMA_URL, data=body, method="POST",
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                data = json.loads(resp.read())
        except urllib.error.URLError as exc:
            raise LLMError(f"Cannot reach Ollama: {exc.reason}") from exc

        try:
            return data["message"]["content"].strip()
        except KeyError as exc:
            raise LLMError(f"Unexpected Ollama response shape: {data}") from exc


# --------------------------------------------------------------------------
_REGISTRY = {
    "mock": MockProvider,
    "groq": GroqProvider,
    "ollama": OllamaProvider,
}

_instance: Provider | None = None


def get_provider(name: str | None = None) -> Provider:
    """Return the active provider. Cached unless an explicit name is given."""
    global _instance
    if name:
        if name not in _REGISTRY:
            raise LLMError(f"Unknown provider '{name}'. "
                           f"Choose from {sorted(_REGISTRY)}")
        return _REGISTRY[name]()
    if _instance is None:
        if LLM_PROVIDER not in _REGISTRY:
            raise LLMError(f"LLM_PROVIDER='{LLM_PROVIDER}' is not valid. "
                           f"Choose from {sorted(_REGISTRY)}")
        _instance = _REGISTRY[LLM_PROVIDER]()
    return _instance


def get_provider_for(role: str) -> Provider:
    """Provider tuned for a specific job ('narration' or 'sql').

    Only Groq varies by role; mock and ollama accept the kwargs but their
    behaviour is unchanged, so offline evals and the local fallback keep
    working exactly as before.
    """
    cls = _REGISTRY.get(LLM_PROVIDER, MockProvider)
    if LLM_PROVIDER == "groq":
        return cls(model=ROLE_MODELS.get(role, GROQ_MODEL),
                   max_tokens=ROLE_MAX_TOKENS.get(role, MAX_TOKENS))
    return get_provider()


def provider_status() -> dict:
    p = get_provider()
    ok, msg = p.available()
    return {"provider": p.name, "available": ok, "detail": msg,
            "role_models": ROLE_MODELS if p.name == "groq" else None,
            "role_max_tokens": ROLE_MAX_TOKENS,
            "temperature": TEMPERATURE, "max_tokens": MAX_TOKENS}