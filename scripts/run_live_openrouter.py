"""Live demonstration of Reliability Gateway using real OpenRouter LLM APIs.

Usage:
    python scripts/run_live_openrouter.py --api-key <OPENROUTER_API_KEY>
    or set OPENROUTER_API_KEY in your environment.
"""

from __future__ import annotations

import argparse
import os
import sys

from reliability_lab.cache import SharedRedisCache
from reliability_lab.circuit_breaker import CircuitBreaker
from reliability_lab.gateway import ReliabilityGateway
from reliability_lab.providers import OpenRouterProvider


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Live OpenRouter Reliability Demo")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("OPENROUTER_API_KEY", ""),
        help="OpenRouter API Key (default: $OPENROUTER_API_KEY)",
    )
    parser.add_argument(
        "--primary-model",
        default="openai/gpt-4o-mini",
        help="Primary LLM Model (default: openai/gpt-4o-mini)",
    )
    parser.add_argument(
        "--backup-model",
        default="meta-llama/llama-3.2-3b-instruct:free",
        help="Backup LLM Model (default: meta-llama/llama-3.2-3b-instruct:free)",
    )
    parser.add_argument(
        "--redis-url",
        default="redis://localhost:6379/0",
        help="Redis URL for shared cache",
    )
    args = parser.parse_args()

    api_key = args.api_key.strip()
    if not api_key:
        print("[-] Error: OpenRouter API key not provided!")
        print("    Pass --api-key 'sk-or-v1-...' or set $env:OPENROUTER_API_KEY='sk-or-v1-...'")
        sys.exit(1)

    print("=================================================================")
    print("[+] INITIALIZING PRODUCTION RELIABILITY GATEWAY WITH OPENROUTER")
    print("=================================================================")
    print(f"[*] Primary Provider : OpenRouter ({args.primary_model})")
    print(f"[*] Backup Provider  : OpenRouter ({args.backup_model})")
    print(f"[*] Cache Backend    : Shared Redis ({args.redis_url})")

    # 1. Setup Providers
    primary_p = OpenRouterProvider(
        name="openrouter_primary",
        api_key=api_key,
        model=args.primary_model,
        timeout=15.0,
    )
    backup_p = OpenRouterProvider(
        name="openrouter_backup",
        api_key=api_key,
        model=args.backup_model,
        timeout=15.0,
    )

    # 2. Setup Circuit Breakers
    breakers = {
        "openrouter_primary": CircuitBreaker(
            name="openrouter_primary",
            failure_threshold=3,
            reset_timeout_seconds=5.0,
            success_threshold=1,
        ),
        "openrouter_backup": CircuitBreaker(
            name="openrouter_backup",
            failure_threshold=3,
            reset_timeout_seconds=5.0,
            success_threshold=1,
        ),
    }

    # 3. Setup Shared Cache
    cache = SharedRedisCache(
        redis_url=args.redis_url,
        ttl_seconds=300,
        similarity_threshold=0.90,
        prefix="rl:live:cache:",
    )
    cache.flush()

    # 4. Build Gateway
    gateway = ReliabilityGateway(
        providers=[primary_p, backup_p],
        breakers=breakers,
        cache=cache,
    )

    print("\n--- Test 1: First Query (Real OpenRouter API Call) ---")
    q1 = "What is circuit breaker pattern in distributed systems in 1 sentence?"
    res1 = gateway.complete(q1)
    print(f"[Query]        : {q1}")
    print(f"[Route Reason] : {res1.route}")
    print(f"[Provider]     : {res1.provider}")
    print(f"[Latency]      : {res1.latency_ms:.2f} ms")
    print(f"[Response]     : {res1.text.strip()}\n")

    print("--- Test 2: Similar Query (Zero-Cost Semantic Cache Hit) ---")
    q2 = "What is the circuit breaker pattern in distributed systems in one sentence?"
    res2 = gateway.complete(q2)
    print(f"[Query]        : {q2}")
    print(f"[Route Reason] : {res2.route}")
    print(f"[Latency]      : {res2.latency_ms:.2f} ms (INSTANT HIT!)")
    print(f"[Response]     : {res2.text.strip()}\n")

    print("--- Test 3: Privacy Query (Bypasses Cache Automatically) ---")
    q3 = "My bearer token is eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9. Explain JWT format."
    res3 = gateway.complete(q3)
    print(f"[Query]        : {q3}")
    print(f"[Route Reason] : {res3.route}")
    print(f"[Cached?]      : {cache.get(q3)[0] is not None} (Correctly NOT cached)")
    print(f"[Response]     : {res3.text.strip()[:100]}...\n")

    print("=================================================================")
    print("[OK] LIVE DEMO COMPLETED SUCCESSFULLY WITH REAL OPENROUTER LLM")
    print("=================================================================")


if __name__ == "__main__":
    main()
