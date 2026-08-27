from __future__ import annotations

import hashlib
import math
import re
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Shared utilities — use these in both ResponseCache and SharedRedisCache
# ---------------------------------------------------------------------------

PRIVACY_PATTERNS = re.compile(
    r"\b(balance|password|credit.card|ssn|social.security|user.\d+|account.\d+)\b",
    re.IGNORECASE,
)


def _is_uncacheable(query: str) -> bool:
    """Return True if query contains privacy-sensitive keywords."""
    return bool(PRIVACY_PATTERNS.search(query))


def _looks_like_false_hit(query: str, cached_key: str) -> bool:
    """Return True if query and cached key contain different 4-digit numbers (years, IDs)."""
    nums_q = set(re.findall(r"\b\d{4}\b", query))
    nums_c = set(re.findall(r"\b\d{4}\b", cached_key))
    return bool(nums_q and nums_c and nums_q != nums_c)


# ---------------------------------------------------------------------------
# In-memory cache (existing)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CacheEntry:
    key: str
    value: str
    created_at: float
    metadata: dict[str, str]


class ResponseCache:
    """Simple in-memory cache with semantic similarity and false-hit guardrails."""

    def __init__(self, ttl_seconds: int, similarity_threshold: float):
        self.ttl_seconds = ttl_seconds
        self.similarity_threshold = similarity_threshold
        self._entries: list[CacheEntry] = []
        self.false_hit_log: list[dict[str, object]] = []

    def get(self, query: str) -> tuple[str | None, float]:
        """Look up a cached response by semantic similarity.

        1. Return (None, 0.0) if _is_uncacheable(query) — privacy check
        2. Evict expired entries (compare time.time() - created_at vs ttl_seconds)
        3. Find best matching entry using self.similarity(query, entry.key)
        4. If best_score >= similarity_threshold:
           a. Check _looks_like_false_hit(query, best_key) — if true, log to
              self.false_hit_log and return (None, best_score)
           b. Otherwise return (best_value, best_score)
        5. Return (None, best_score) if no match above threshold
        """
        if _is_uncacheable(query):
            return None, 0.0
        now = time.time()
        self._entries = [e for e in self._entries if (now - e.created_at) <= self.ttl_seconds]
        if not self._entries:
            return None, 0.0

        best_score = -1.0
        best_entry: CacheEntry | None = None
        for entry in self._entries:
            score = self.similarity(query, entry.key)
            if score > best_score:
                best_score = score
                best_entry = entry

        if best_entry is not None and best_score >= self.similarity_threshold:
            if _looks_like_false_hit(query, best_entry.key):
                self.false_hit_log.append(
                    {
                        "query": query,
                        "cached_key": best_entry.key,
                        "score": best_score,
                        "reason": "date_or_number_mismatch",
                    }
                )
                return None, best_score
            return best_entry.value, best_score

        return None, max(0.0, best_score if best_score != -1.0 else 0.0)

    def set(self, query: str, value: str, metadata: dict[str, str] | None = None) -> None:
        """Store a response in cache with privacy guardrail."""
        if _is_uncacheable(query):
            return
        self._entries.append(
            CacheEntry(
                key=query,
                value=value,
                created_at=time.time(),
                metadata=metadata or {},
            )
        )

    @staticmethod
    def similarity(a: str, b: str) -> float:
        """Compute semantic similarity between two strings using character n-grams + word tokens."""
        if a == b:
            return 1.0

        def tokenize(text: str) -> list[str]:
            words = re.findall(r"\w+", text.lower())
            tokens: list[str] = []
            for w in words:
                tokens.append(w)
                if len(w) >= 3:
                    for i in range(len(w) - 2):
                        tokens.append(w[i : i + 3])
            return tokens

        tokens_a = tokenize(a)
        tokens_b = tokenize(b)
        if not tokens_a or not tokens_b:
            return 0.0

        ca = Counter(tokens_a)
        cb = Counter(tokens_b)
        dot = sum(ca[k] * cb[k] for k in ca if k in cb)
        mag_a = math.sqrt(sum(v * v for v in ca.values()))
        mag_b = math.sqrt(sum(v * v for v in cb.values()))
        if mag_a == 0.0 or mag_b == 0.0:
            return 0.0
        return dot / (mag_a * mag_b)


# ---------------------------------------------------------------------------
# Redis shared cache (new)
# ---------------------------------------------------------------------------


class SharedRedisCache:
    """Redis-backed shared cache for multi-instance deployments."""

    def __init__(
        self,
        redis_url: str,
        ttl_seconds: int,
        similarity_threshold: float,
        prefix: str = "rl:cache:",
    ):
        import redis as redis_lib

        self.ttl_seconds = ttl_seconds
        self.similarity_threshold = similarity_threshold
        self.prefix = prefix
        self.false_hit_log: list[dict[str, object]] = []
        self._redis: Any = redis_lib.Redis.from_url(redis_url, decode_responses=True)
        self._redis_error_type: type[Exception] = redis_lib.RedisError

    def ping(self) -> bool:
        """Check Redis connectivity."""
        try:
            return bool(self._redis.ping())
        except (self._redis_error_type, OSError):
            return False

    def get(self, query: str) -> tuple[str | None, float]:
        """Look up a cached response from Redis."""
        if _is_uncacheable(query):
            return None, 0.0
        exact_key = f"{self.prefix}{self._query_hash(query)}"
        try:
            exact_resp = self._redis.hget(exact_key, "response")
            if exact_resp is not None:
                return exact_resp, 1.0

            best_score = 0.0
            best_val: str | None = None
            best_cached_query = ""
            for key in self._redis.scan_iter(f"{self.prefix}*"):
                data = self._redis.hgetall(key)
                cached_query = data.get("query")
                cached_resp = data.get("response")
                if cached_query and cached_resp:
                    score = ResponseCache.similarity(query, cached_query)
                    if score > best_score:
                        best_score = score
                        best_val = cached_resp
                        best_cached_query = cached_query

            if best_val is not None and best_score >= self.similarity_threshold:
                if _looks_like_false_hit(query, best_cached_query):
                    self.false_hit_log.append(
                        {
                            "query": query,
                            "cached_key": best_cached_query,
                            "score": best_score,
                            "reason": "date_or_number_mismatch",
                        }
                    )
                    return None, best_score
                return best_val, best_score

            return None, best_score
        except (self._redis_error_type, OSError):
            return None, 0.0

    def set(self, query: str, value: str, metadata: dict[str, str] | None = None) -> None:
        """Store a response in Redis with TTL."""
        if _is_uncacheable(query):
            return
        key = f"{self.prefix}{self._query_hash(query)}"
        try:
            self._redis.hset(key, mapping={"query": query, "response": value})
            self._redis.expire(key, self.ttl_seconds)
        except (self._redis_error_type, OSError):
            return

    def flush(self) -> None:
        """Remove all entries with this cache prefix (for testing)."""
        for key in self._redis.scan_iter(f"{self.prefix}*"):
            self._redis.delete(key)

    def close(self) -> None:
        """Close Redis connection."""
        if self._redis is not None:
            self._redis.close()

    @staticmethod
    def _query_hash(query: str) -> str:
        """Deterministic short hash for a query string."""
        return hashlib.md5(query.lower().strip().encode()).hexdigest()[:12]
