"""Tests for services.redis_client — ResilientRedisClient and in-memory fallback."""
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
import redis as real_redis

from app.services.redis_client import ResilientRedisClient, _InMemoryFallback


class TestInMemoryFallback:
    """Test the LRU in-memory fallback cache."""

    def test_get_set(self):
        fb = _InMemoryFallback(max_size=10)
        fb.set("k1", "v1")
        assert fb.get("k1") == "v1"

    def test_get_missing(self):
        fb = _InMemoryFallback()
        assert fb.get("missing") is None

    def test_delete(self):
        fb = _InMemoryFallback()
        fb.set("k", "v")
        fb.delete("k")
        assert fb.get("k") is None

    def test_exists(self):
        fb = _InMemoryFallback()
        assert fb.exists("k") is False
        fb.set("k", "v")
        assert fb.exists("k") is True

    def test_lru_eviction(self):
        fb = _InMemoryFallback(max_size=3)
        fb.set("a", "1")
        fb.set("b", "2")
        fb.set("c", "3")
        fb.set("d", "4")  # Should evict "a"
        assert fb.get("a") is None
        assert fb.get("b") == "2"

    def test_clear(self):
        fb = _InMemoryFallback()
        fb.set("k", "v")
        fb.clear()
        assert fb.exists("k") is False


class TestResilientRedisClient:
    """Test ResilientRedisClient wrapping behavior."""

    @patch('app.services.redis_client.redis.ConnectionPool')
    @patch('app.services.redis_client.redis.Redis')
    def test_successful_connection(self, mock_redis_cls, mock_pool_cls):
        mock_instance = MagicMock()
        mock_instance.ping.return_value = True
        mock_redis_cls.return_value = mock_instance

        client = ResilientRedisClient(host='localhost')
        assert client.is_healthy is True

    @patch('app.services.redis_client.redis.ConnectionPool')
    @patch('app.services.redis_client.redis.Redis')
    def test_get_from_redis(self, mock_redis_cls, mock_pool_cls):
        mock_instance = MagicMock()
        mock_instance.ping.return_value = True
        mock_instance.get.return_value = "hello"
        mock_redis_cls.return_value = mock_instance

        client = ResilientRedisClient(host='localhost')
        assert client.get("key") == "hello"

    @patch('app.services.redis_client.redis.ConnectionPool')
    @patch('app.services.redis_client.redis.Redis')
    def test_set_writes_to_fallback(self, mock_redis_cls, mock_pool_cls):
        mock_instance = MagicMock()
        mock_instance.ping.return_value = True
        mock_instance.set.return_value = True
        mock_redis_cls.return_value = mock_instance

        client = ResilientRedisClient(host='localhost')
        client.set("key", "value")
        # Fallback should also have it
        assert client._fallback.get("key") == "value"

    @patch('app.services.redis_client.redis.ConnectionPool')
    @patch('app.services.redis_client.redis.Redis')
    def test_get_falls_back_to_memory(self, mock_redis_cls, mock_pool_cls):
        mock_instance = MagicMock()
        mock_instance.ping.return_value = True
        mock_instance.set.return_value = True
        mock_redis_cls.return_value = mock_instance

        client = ResilientRedisClient(host='localhost')
        client.set("key", "cached_value")

        # Simulate Redis failure on get
        mock_instance.get.side_effect = real_redis.RedisError("connection lost")
        # Reconnect also fails
        mock_redis_cls.side_effect = real_redis.RedisError("still down")

        result = client.get("key")
        assert result == "cached_value"

    @patch('app.services.redis_client.redis.ConnectionPool')
    @patch('app.services.redis_client.redis.Redis')
    def test_delete_clears_fallback(self, mock_redis_cls, mock_pool_cls):
        mock_instance = MagicMock()
        mock_instance.ping.return_value = True
        mock_instance.delete.return_value = 1
        mock_redis_cls.return_value = mock_instance

        client = ResilientRedisClient(host='localhost')
        client._fallback.set("key", "val")
        client.delete("key")
        assert client._fallback.exists("key") is False

    @patch('app.services.redis_client.redis.ConnectionPool')
    @patch('app.services.redis_client.redis.Redis')
    def test_lrange_returns_empty_on_failure(self, mock_redis_cls, mock_pool_cls):
        mock_instance = MagicMock()
        mock_instance.ping.return_value = True
        mock_redis_cls.return_value = mock_instance

        client = ResilientRedisClient(host='localhost')
        mock_instance.lrange.side_effect = real_redis.RedisError("down")
        mock_redis_cls.side_effect = real_redis.RedisError("still down")

        result = client.lrange("key", 0, -1)
        assert result == []

    @patch('app.services.redis_client.redis.ConnectionPool')
    @patch('app.services.redis_client.redis.Redis')
    def test_get_stats(self, mock_redis_cls, mock_pool_cls):
        mock_instance = MagicMock()
        mock_instance.ping.return_value = True
        mock_redis_cls.return_value = mock_instance

        client = ResilientRedisClient(host='localhost')
        stats = client.get_stats()
        assert stats['healthy'] is True
        assert 'fallback_size' in stats

    @patch('app.services.redis_client.redis.ConnectionPool')
    @patch('app.services.redis_client.redis.Redis')
    def test_close(self, mock_redis_cls, mock_pool_cls):
        mock_instance = MagicMock()
        mock_instance.ping.return_value = True
        mock_redis_cls.return_value = mock_instance

        client = ResilientRedisClient(host='localhost')
        client.close()
        assert client.is_healthy is False
        mock_instance.close.assert_called_once()

    @patch('app.services.redis_client.redis.ConnectionPool')
    @patch('app.services.redis_client.redis.Redis')
    def test_setex_writes_fallback(self, mock_redis_cls, mock_pool_cls):
        mock_instance = MagicMock()
        mock_instance.ping.return_value = True
        mock_instance.setex.return_value = True
        mock_redis_cls.return_value = mock_instance

        client = ResilientRedisClient(host='localhost')
        client.setex("key", 60, "value")
        assert client._fallback.get("key") == "value"

    @patch('app.services.redis_client.redis.ConnectionPool')
    @patch('app.services.redis_client.redis.Redis')
    def test_exists_falls_back(self, mock_redis_cls, mock_pool_cls):
        mock_instance = MagicMock()
        mock_instance.ping.return_value = True
        mock_redis_cls.return_value = mock_instance

        client = ResilientRedisClient(host='localhost')
        client._fallback.set("key", "v")

        mock_instance.exists.side_effect = real_redis.RedisError("down")
        mock_redis_cls.side_effect = real_redis.RedisError("still down")

        assert client.exists("key") is True
        assert client.exists("other") is False
