from __future__ import annotations

import random
import time
from dataclasses import dataclass


class ProviderError(RuntimeError):
    """Raised when a fake provider fails."""


@dataclass(slots=True)
class ProviderResponse:
    provider: str
    text: str
    latency_ms: float
    input_tokens: int
    output_tokens: int
    estimated_cost: float


class FakeLLMProvider:
    """Deterministic-enough fake provider for local chaos tests.

    This avoids real API keys while still simulating latency, failures, and cost.
    """

    def __init__(self, name: str, fail_rate: float, base_latency_ms: int, cost_per_1k_tokens: float):
        self.name = name
        self.fail_rate = fail_rate
        self.base_latency_ms = base_latency_ms
        self.cost_per_1k_tokens = cost_per_1k_tokens

    def complete(self, prompt: str) -> ProviderResponse:
        start = time.perf_counter()
        jitter_ms = random.randint(0, 60)
        time.sleep((self.base_latency_ms + jitter_ms) / 1000.0)
        if random.random() < self.fail_rate:
            raise ProviderError(f"{self.name} simulated failure")
        input_tokens = max(1, len(prompt.split()))
        output_tokens = random.randint(20, 80)
        cost = (input_tokens + output_tokens) / 1000.0 * self.cost_per_1k_tokens
        latency_ms = (time.perf_counter() - start) * 1000
        return ProviderResponse(
            provider=self.name,
            text=f"[{self.name}] reliable answer for: {prompt[:60]}",
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=cost,
        )


class OpenRouterProvider:
    """Production LLM provider connected to OpenRouter API."""

    def __init__(
        self,
        name: str,
        api_key: str | None = None,
        model: str = "openai/gpt-4o-mini",
        base_url: str = "https://openrouter.ai/api/v1",
        timeout: float = 20.0,
        cost_per_1k_tokens: float = 0.0005,
    ):
        import os

        self.name = name
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.cost_per_1k_tokens = cost_per_1k_tokens

    def complete(self, prompt: str) -> ProviderResponse:
        import json
        import urllib.error
        import urllib.request

        if not self.api_key:
            raise ProviderError(f"OpenRouter API key is missing for provider '{self.name}'.")

        start = time.perf_counter()
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
        }
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=data_bytes,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/NamKhanh2128",
                "X-Title": "Production Reliability Gateway",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                status_code = resp.getcode()
                if status_code != 200:
                    raise ProviderError(f"OpenRouter HTTP {status_code}: {resp.read().decode('utf-8')}")
                resp_json = json.loads(resp.read().decode("utf-8"))
                latency_ms = (time.perf_counter() - start) * 1000.0

                choices = resp_json.get("choices", [])
                if not choices:
                    raise ProviderError("OpenRouter returned empty choices list.")
                text = choices[0].get("message", {}).get("content", "")

                usage = resp_json.get("usage", {})
                input_tokens = usage.get("prompt_tokens", max(1, len(prompt.split())))
                output_tokens = usage.get("completion_tokens", max(1, len(text.split())))
                cost = (input_tokens + output_tokens) / 1000.0 * self.cost_per_1k_tokens

                return ProviderResponse(
                    provider=self.name,
                    text=text,
                    latency_ms=latency_ms,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    estimated_cost=cost,
                )
        except urllib.error.HTTPError as err:
            err_body = err.read().decode("utf-8", errors="ignore")
            raise ProviderError(f"OpenRouter HTTP Error {err.code}: {err_body}") from err
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            raise ProviderError(f"OpenRouter Connection Error: {err}") from err
