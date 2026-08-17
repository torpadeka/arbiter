"""Provider-agnostic LLM client.

One interface, several backends, selected by `LLM_PROVIDER` in .env. Everything
except Anthropic speaks the OpenAI-compatible `/chat/completions` shape, so
free tiers (Gemini, Groq, Cerebras, OpenRouter) and local Ollama all work
without touching call sites.

Only two operations are needed anywhere in Arbiter:
    structured()  — JSON matching a schema (tier B extraction)
    text()        — prose (answer rendering)

Structured output is requested as a hard `json_schema` where the provider
supports it, because the predicate vocabulary is enforced *by the schema*: the
extractor must not be able to invent a predicate. Providers that only support
loose JSON mode fall back to schema-in-prompt plus client-side validation, and
`strict_schema` reports which happened so the README can be honest about it.
"""

from __future__ import annotations

import json
import os
import random
import re
import time
from dataclasses import dataclass

import httpx

try:  # .env is convenience, not a dependency
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

# base_url, default model, whether the provider enforces a JSON Schema.
PRESETS: dict[str, tuple[str, str, bool]] = {
    # The `-latest` alias, not a pinned version: extraction is mechanical, the
    # free tier's request quota is the binding constraint rather than model
    # capability, and pinned Gemini versions get retired for new keys
    # (gemini-2.5-flash-lite now 404s with "no longer available to new users").
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "gemini-flash-lite-latest", True),
    "groq": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile", False),
    "cerebras": ("https://api.cerebras.ai/v1", "llama-3.3-70b", False),
    "openrouter": ("https://openrouter.ai/api/v1", "meta-llama/llama-3.3-70b-instruct:free", False),
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini", True),
    "ollama": ("http://127.0.0.1:11434/v1", "qwen2.5:7b", False),
    "anthropic": ("https://api.anthropic.com", "claude-opus-5", True),
}

RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    pass


@dataclass
class LLMConfig:
    provider: str = ""
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    timeout: float = 120.0
    max_attempts: int = 5

    @classmethod
    def from_env(cls, model_env: str = "") -> "LLMConfig":
        provider = (os.getenv("LLM_PROVIDER") or "gemini").lower()
        if provider not in PRESETS:
            raise LLMError(f"unknown LLM_PROVIDER {provider!r}; expected one of {', '.join(PRESETS)}")
        base, default_model, _ = PRESETS[provider]
        key = os.getenv("LLM_API_KEY") or os.getenv(f"{provider.upper()}_API_KEY") or ""
        if provider == "anthropic":
            key = key or os.getenv("ANTHROPIC_API_KEY", "")
        if not key and provider != "ollama":
            raise LLMError(
                f"no API key for provider {provider!r}. Set LLM_API_KEY in .env "
                f"(or {provider.upper()}_API_KEY)."
            )
        model = (os.getenv(model_env) if model_env else "") or os.getenv("LLM_MODEL") or default_model
        return cls(
            provider=provider,
            api_key=key,
            base_url=os.getenv("LLM_BASE_URL") or base,
            model=model,
        )


class LLM:
    def __init__(self, config: LLMConfig | None = None, model_env: str = "") -> None:
        self.cfg = config or LLMConfig.from_env(model_env)
        headers = {"Content-Type": "application/json"}
        if self.cfg.provider == "anthropic":
            headers |= {"x-api-key": self.cfg.api_key, "anthropic-version": "2023-06-01"}
        elif self.cfg.api_key:
            headers["Authorization"] = f"Bearer {self.cfg.api_key}"
        self._http = httpx.Client(base_url=self.cfg.base_url, timeout=self.cfg.timeout, headers=headers)

    @property
    def strict_schema(self) -> bool:
        """True when the provider enforces the JSON Schema server-side."""
        return PRESETS[self.cfg.provider][2]

    # --- transport ---------------------------------------------------------

    def _post(self, path: str, body: dict) -> dict:
        """POST with backoff on rate limits and transient server errors.

        Free tiers rate-limit aggressively, so `Retry-After` is honoured when
        present rather than guessed at.
        """
        last = ""
        for attempt in range(self.cfg.max_attempts):
            try:
                resp = self._http.post(path, json=body)
            except httpx.TransportError as exc:
                last = f"transport: {exc}"
            else:
                if resp.status_code < 400:
                    return resp.json()
                last = f"HTTP {resp.status_code}: {resp.text[:300]}"
                if resp.status_code not in RETRY_STATUS:
                    raise LLMError(last)
                wait = float(resp.headers.get("retry-after") or 0) or min(30.0, 2**attempt)
                time.sleep(wait + random.uniform(0, 0.5))
                continue
            time.sleep(min(30.0, 2**attempt) + random.uniform(0, 0.5))
        raise LLMError(f"failed after {self.cfg.max_attempts} attempts — {last}")

    # --- operations --------------------------------------------------------

    def structured(self, system: str, user: str, schema: dict, max_tokens: int = 4096) -> dict:
        """Return JSON conforming to `schema`."""
        if self.cfg.provider == "anthropic":
            payload = self._post("/v1/messages", {
                "model": self.cfg.model,
                "max_tokens": max_tokens,
                "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                "messages": [{"role": "user", "content": user}],
                "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            })
            if payload.get("stop_reason") == "refusal":
                return {}
            text = "".join(b.get("text", "") for b in payload.get("content", []) if b.get("type") == "text")
            return _loads(text)

        body: dict = {
            "model": self.cfg.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if self.strict_schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "extraction", "schema": schema, "strict": True},
            }
        else:
            # Loose JSON mode: the schema goes in the prompt and is validated
            # client-side by the caller.
            body["response_format"] = {"type": "json_object"}
            body["messages"][0]["content"] += (
                "\n\nReturn ONLY JSON matching this JSON Schema exactly:\n" + json.dumps(schema)
            )

        payload = self._post("/chat/completions", body)
        choices = payload.get("choices") or []
        if not choices:
            raise LLMError(f"no choices in response: {str(payload)[:300]}")
        return _loads(choices[0].get("message", {}).get("content") or "")

    def text(self, system: str, user: str, max_tokens: int = 1000) -> str:
        if self.cfg.provider == "anthropic":
            payload = self._post("/v1/messages", {
                "model": self.cfg.model,
                "max_tokens": max_tokens,
                "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                "messages": [{"role": "user", "content": user}],
                "output_config": {"effort": "low"},
            })
            if payload.get("stop_reason") == "refusal":
                return ""
            return "".join(b.get("text", "") for b in payload.get("content", []) if b.get("type") == "text").strip()

        payload = self._post("/chat/completions", {
            "model": self.cfg.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        })
        choices = payload.get("choices") or []
        return (choices[0].get("message", {}).get("content") or "").strip() if choices else ""

    def close(self) -> None:
        self._http.close()


def _loads(text: str) -> dict:
    """Parse JSON, tolerating providers that wrap it in prose or code fences."""
    text = (text or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise LLMError(f"response was not JSON: {text[:200]}")


def available() -> bool:
    """Whether an LLM is configured — the pipeline runs without one."""
    try:
        LLMConfig.from_env()
        return True
    except LLMError:
        return False
