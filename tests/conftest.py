"""
Shared test fixtures and setup.

This conftest.py patches external services (Redis, MySQL, LINE API)
before the app package is imported, so that tests can run without
live connections.

HOW IT WORKS:
  pytest loads conftest.py before collecting test modules.
  We monkey-patch ``redis.Redis`` so that any instance returned
  is a MagicMock (no real TCP connection).  We also patch
  ``mysql.connector.connect`` similarly.
"""
import os
import sys
from unittest.mock import MagicMock, patch

# ── Set required environment variables BEFORE any app imports ──
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test_token")
os.environ.setdefault("LINE_CHANNEL_SECRET", "test_secret")
os.environ.setdefault("XAI_API_KEY", "test_xai_key")
os.environ.setdefault("XAI_MODEL", "grok-test")
os.environ.setdefault("REDIS_HOST", "localhost")
os.environ.setdefault("REDIS_PORT", "6379")
os.environ.setdefault("REDIS_DB", "0")
os.environ.setdefault("MYSQL_HOST", "localhost")
os.environ.setdefault("MYSQL_PORT", "3306")
os.environ.setdefault("MYSQL_USER", "test")
os.environ.setdefault("MYSQL_PASSWORD", "test")
os.environ.setdefault("MYSQL_DB", "test_chatbot")
os.environ.setdefault("ENVIRONMENT", "testing")

# ── Build a mock Redis instance ──
_mock_redis_instance = MagicMock()
_mock_redis_instance.ping.return_value = True
_mock_redis_instance.get.return_value = None
_mock_redis_instance.set.return_value = True
_mock_redis_instance.exists.return_value = False
_mock_redis_instance.lrange.return_value = []
_mock_redis_instance.lpush.return_value = 1
_mock_redis_instance.ltrim.return_value = True
_mock_redis_instance.setex.return_value = True
_mock_redis_instance.delete.return_value = 1
_mock_redis_instance.incr.return_value = 1
_mock_redis_instance.expire.return_value = True
_mock_redis_instance.close.return_value = None

# Patch redis.Redis constructor BEFORE app is imported
import redis as _real_redis
_original_redis_cls = _real_redis.Redis
_real_redis.Redis = MagicMock(return_value=_mock_redis_instance)

# ── Patch mysql.connector so no real DB connection is attempted ──
try:
    import mysql.connector as _real_mysql_connector
    import mysql.connector.pooling as _real_mysql_pooling

    _mock_conn = MagicMock()
    _mock_conn.is_connected.return_value = True
    _mock_cursor = MagicMock()
    _mock_cursor.fetchall.return_value = []
    _mock_cursor.fetchone.return_value = None
    _mock_conn.cursor.return_value = _mock_cursor

    _real_mysql_connector.connect = MagicMock(return_value=_mock_conn)

    # Patch the connection pool so DatabaseManager.init_pool() doesn't connect
    _mock_pool = MagicMock()
    _mock_pool.get_connection.return_value = _mock_conn
    _real_mysql_pooling.MySQLConnectionPool = MagicMock(return_value=_mock_pool)
except ImportError:
    pass
