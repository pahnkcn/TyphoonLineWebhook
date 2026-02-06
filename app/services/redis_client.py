"""Resilient Redis client with automatic reconnection and in-memory fallback.

Wraps a standard ``redis.Redis`` instance and adds:
- Automatic reconnection with exponential backoff on failures
- In-memory LRU fallback for critical get/set operations when Redis is down
- Health-check aware connection pooling
- Transparent proxy of all standard Redis methods
"""
import json
import logging
import time
import threading
from collections import OrderedDict
from typing import Any, Optional

import redis

# Sentinel to distinguish "Redis returned None" from "Redis is unreachable"
_MISS = object()


class _InMemoryFallback:
    """Thread-safe LRU dict used when Redis is unreachable."""

    def __init__(self, max_size: int = 1000):
        self._store: OrderedDict = OrderedDict()
        self._max_size = max_size
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
                return self._store[key]
        return None

    def set(self, key: str, value: str) -> None:
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = value
            while len(self._store) > self._max_size:
                self._store.popitem(last=False)

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def exists(self, key: str) -> bool:
        with self._lock:
            return key in self._store

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


class ResilientRedisClient:
    """Drop-in replacement for ``redis.Redis`` with resilience features.

    Parameters
    ----------
    host, port, db, decode_responses, **kwargs
        Passed directly to ``redis.Redis`` / ``redis.ConnectionPool``.
    max_reconnect_attempts : int
        How many consecutive reconnect attempts before giving up and
        falling back to the in-memory cache.
    base_backoff : float
        Base wait (seconds) between reconnect attempts (doubles each try).
    fallback_cache_size : int
        Maximum entries in the in-memory LRU fallback cache.
    health_check_interval : int
        Seconds between automatic connection health checks (0 to disable).
    """

    def __init__(
        self,
        host: str = 'localhost',
        port: int = 6379,
        db: int = 0,
        decode_responses: bool = True,
        max_reconnect_attempts: int = 5,
        base_backoff: float = 1.0,
        fallback_cache_size: int = 1000,
        health_check_interval: int = 30,
        **kwargs,
    ):
        self._conn_kwargs = dict(
            host=host, port=port, db=db,
            decode_responses=decode_responses,
            health_check_interval=health_check_interval,
            socket_timeout=kwargs.pop('socket_timeout', 5),
            socket_connect_timeout=kwargs.pop('socket_connect_timeout', 5),
            retry_on_timeout=True,
            **kwargs,
        )
        self._max_reconnect = max_reconnect_attempts
        self._base_backoff = base_backoff
        self._fallback = _InMemoryFallback(max_size=fallback_cache_size)
        self._client: Optional[redis.Redis] = None
        self._healthy = False
        self._lock = threading.Lock()
        self._reconnecting = False

        self._connect()

    # ── Connection management ───────────────────────────────────────

    def _connect(self) -> bool:
        """Create a new Redis connection, return True on success."""
        try:
            pool = redis.ConnectionPool(**self._conn_kwargs)
            client = redis.Redis(connection_pool=pool)
            client.ping()
            self._client = client
            self._healthy = True
            logging.info("Redis connection established")
            return True
        except redis.RedisError as e:
            logging.warning(f"Redis connection failed: {e}")
            self._healthy = False
            return False

    def _reconnect(self) -> bool:
        """Try to reconnect with exponential backoff.

        Sleeps happen outside the lock so other threads are not blocked
        for the full backoff duration.
        """
        with self._lock:
            if self._healthy:
                return True
            # Another thread is already reconnecting — don't pile on
            if self._reconnecting:
                return False
            self._reconnecting = True

        try:
            for attempt in range(1, self._max_reconnect + 1):
                wait = self._base_backoff * (2 ** (attempt - 1))
                logging.info(f"Redis reconnect attempt {attempt}/{self._max_reconnect} (waiting {wait:.1f}s)")
                time.sleep(wait)
                if self._connect():
                    logging.info("Redis reconnected successfully")
                    return True
            logging.error("Redis reconnection failed after all attempts — using in-memory fallback")
            return False
        finally:
            with self._lock:
                self._reconnecting = False

    @property
    def is_healthy(self) -> bool:
        return self._healthy

    def _check_health(self) -> bool:
        """Quick health check; triggers reconnect if unhealthy."""
        if self._healthy and self._client is not None:
            try:
                self._client.ping()
                return True
            except redis.RedisError:
                self._healthy = False
        return self._reconnect()

    # ── Safe execution wrapper ──────────────────────────────────────

    def _safe_execute(self, method_name: str, *args, **kwargs):
        """Execute a Redis command with automatic reconnection on failure."""
        if not self._healthy:
            self._reconnect()

        if self._client is not None and self._healthy:
            try:
                result = getattr(self._client, method_name)(*args, **kwargs)
                return result
            except redis.RedisError as e:
                logging.warning(f"Redis {method_name} failed: {e}")
                self._healthy = False
                # Try one reconnect
                if self._reconnect():
                    try:
                        return getattr(self._client, method_name)(*args, **kwargs)
                    except redis.RedisError as e2:
                        logging.error(f"Redis {method_name} failed after reconnect: {e2}")
                        self._healthy = False

        return _MISS

    # ── Core Redis methods with fallback ────────────────────────────

    def get(self, key: str) -> Optional[str]:
        result = self._safe_execute('get', key)
        if result is not _MISS:
            # Redis answered (value or None for missing key) — return as-is
            return result
        # Redis unreachable — fallback to in-memory
        return self._fallback.get(key)

    def set(self, key: str, value: str, **kwargs) -> bool:
        # Always write to fallback for resilience
        self._fallback.set(key, value if isinstance(value, str) else str(value))
        result = self._safe_execute('set', key, value, **kwargs)
        return result is not _MISS

    def setex(self, key: str, time_seconds: int, value: str) -> bool:
        self._fallback.set(key, value if isinstance(value, str) else str(value))
        result = self._safe_execute('setex', key, time_seconds, value)
        return result is not _MISS

    def delete(self, *keys: str) -> int:
        for k in keys:
            self._fallback.delete(k)
        result = self._safe_execute('delete', *keys)
        return result if result is not _MISS else 0

    def exists(self, key: str) -> bool:
        result = self._safe_execute('exists', key)
        if result is not _MISS:
            return bool(result)
        return self._fallback.exists(key)

    def ping(self) -> bool:
        result = self._safe_execute('ping')
        return result is not _MISS and bool(result)

    # ── Proxy methods (no fallback, just resilient) ─────────────────

    def incr(self, key: str, amount: int = 1):
        return self._safe_execute('incr', key, amount)

    def expire(self, key: str, time_seconds: int) -> bool:
        result = self._safe_execute('expire', key, time_seconds)
        return bool(result) if result is not _MISS else False

    def lpush(self, key: str, *values) -> Optional[int]:
        return self._safe_execute('lpush', key, *values)

    def lrange(self, key: str, start: int, end: int) -> list:
        result = self._safe_execute('lrange', key, start, end)
        return result if result is not _MISS else []

    def ltrim(self, key: str, start: int, end: int) -> bool:
        result = self._safe_execute('ltrim', key, start, end)
        return bool(result) if result is not _MISS else False

    def hset(self, name: str, key: str = None, value: str = None, mapping: dict = None):
        if mapping:
            return self._safe_execute('hset', name, mapping=mapping)
        return self._safe_execute('hset', name, key, value)

    def hget(self, name: str, key: str) -> Optional[str]:
        return self._safe_execute('hget', name, key)

    def zscore(self, name: str, value: str):
        return self._safe_execute('zscore', name, value)

    def zadd(self, name: str, mapping: dict, **kwargs):
        return self._safe_execute('zadd', name, mapping, **kwargs)

    def zrangebyscore(self, name: str, min_score, max_score, **kwargs):
        result = self._safe_execute('zrangebyscore', name, min_score, max_score, **kwargs)
        return result if result is not _MISS else []

    def zrem(self, name: str, *values):
        return self._safe_execute('zrem', name, *values)

    def scan_iter(self, match: str = '*', count: int = 100):
        """Iterate over keys matching a pattern. Returns empty iterator on failure."""
        if not self._healthy:
            self._reconnect()
        if self._client is not None and self._healthy:
            try:
                yield from self._client.scan_iter(match=match, count=count)
                return
            except redis.RedisError as e:
                logging.warning(f"Redis scan_iter failed: {e}")
                self._healthy = False
        # Generator with no yields = empty iterator
        return

    def pipeline(self, transaction: bool = True):
        """Return a Redis pipeline. Falls back to None if unavailable."""
        if not self._healthy:
            self._reconnect()
        if self._client is not None and self._healthy:
            try:
                return self._client.pipeline(transaction=transaction)
            except redis.RedisError as e:
                logging.warning(f"Redis pipeline failed: {e}")
                self._healthy = False
        return None

    def close(self) -> None:
        """Close the Redis connection."""
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
        self._healthy = False

    def get_stats(self) -> dict:
        """Return connection health stats."""
        return {
            'healthy': self._healthy,
            'fallback_size': len(self._fallback._store),
            'fallback_max_size': self._fallback._max_size,
        }

    # ── Proxy unknown methods to underlying client ──────────────────

    def __getattr__(self, name: str):
        """Proxy any unimplemented method through _safe_execute."""
        def proxy(*args, **kwargs):
            return self._safe_execute(name, *args, **kwargs)
        return proxy
