"""Single entry point for LLM calls: fixed settings and token accounting in one place."""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

from config import (
    LLM_MODEL,
    LLM_PRICE_INPUT_PER_M,
    LLM_PRICE_OUTPUT_PER_M,
    LLM_REASONING_EFFORT,
    LLM_SEED,
    LLM_TEMPERATURE,
    ROOT,
)


class LLMUnavailable(RuntimeError):
    """No key configured, or the provider could not be reached."""


@dataclass
class UsageLog:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    failures: int = 0
    by_purpose: dict = field(default_factory=dict)

    def record(self, purpose: str, input_tokens: int, output_tokens: int) -> None:
        self.calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        p = self.by_purpose.setdefault(purpose, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
        p["calls"] += 1
        p["input_tokens"] += input_tokens
        p["output_tokens"] += output_tokens

    def cost_usd(self) -> float:
        return (self.input_tokens * LLM_PRICE_INPUT_PER_M
                + self.output_tokens * LLM_PRICE_OUTPUT_PER_M) / 1_000_000

    def as_dict(self) -> dict:
        return {
            "model": LLM_MODEL,
            "temperature": LLM_TEMPERATURE,
            "seed": LLM_SEED,
            "reasoning_effort": LLM_REASONING_EFFORT,
            "calls": self.calls,
            "failures": self.failures,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "price_usd_per_million": {"input": LLM_PRICE_INPUT_PER_M, "output": LLM_PRICE_OUTPUT_PER_M},
            "estimated_cost_usd": round(self.cost_usd(), 6),
            "by_purpose": self.by_purpose,
        }


class LLMClient:
    def __init__(self, usage: UsageLog | None = None, groq_client=None):
        self.usage = usage or UsageLog()
        self._client = groq_client

    def _get_client(self):
        if self._client is None:
            load_dotenv(ROOT / ".env")
            key = os.environ.get("GROQ_API_KEY", "").strip()
            if not key:
                raise LLMUnavailable("GROQ_API_KEY is not set (see example.env)")
            from groq import Groq
            self._client = Groq(api_key=key)
        return self._client

    def complete(self, purpose: str, system: str, user: str, json_mode: bool = False) -> str:
        kwargs = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            response = self._get_client().chat.completions.create(
                model=LLM_MODEL,
                temperature=LLM_TEMPERATURE,
                seed=LLM_SEED,
                reasoning_effort=LLM_REASONING_EFFORT,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                **kwargs,
            )
        except LLMUnavailable:
            raise
        except Exception as exc:  # network, auth, rate limit: callers fall back
            self.usage.failures += 1
            raise LLMUnavailable(f"LLM call failed: {exc}") from exc

        self.usage.record(purpose, response.usage.prompt_tokens, response.usage.completion_tokens)
        return response.choices[0].message.content
