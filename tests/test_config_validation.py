"""Tests for config.py startup validation — LOG_LEVEL, placeholder detection, numeric vars."""
import os
import pytest
from unittest.mock import patch
from app.config import load_config


# Base env with all required vars set to safe non-placeholder values
_BASE_ENV = {
    'LINE_CHANNEL_ACCESS_TOKEN': 'tok',
    'LINE_CHANNEL_SECRET': 'sec',
    'XAI_API_KEY': 'key',
    'MYSQL_HOST': 'localhost',
    'MYSQL_USER': 'root',
    'MYSQL_PASSWORD': 'pass',
    'MYSQL_DB': 'db',
    'ENVIRONMENT': 'development',
}


class TestLoadConfigLogLevel:
    """Verify LOG_LEVEL validation in load_config()."""

    def test_invalid_log_level_falls_back_to_info(self):
        env = {**_BASE_ENV, 'LOG_LEVEL': 'INVALID_LEVEL'}
        with patch.dict(os.environ, env, clear=False):
            cfg = load_config()
            assert cfg.LOG_LEVEL == 'INFO'

    def test_valid_log_level_debug(self):
        env = {**_BASE_ENV, 'LOG_LEVEL': 'DEBUG'}
        with patch.dict(os.environ, env, clear=False):
            cfg = load_config()
            assert cfg.LOG_LEVEL == 'DEBUG'

    def test_valid_log_level_warning(self):
        env = {**_BASE_ENV, 'LOG_LEVEL': 'WARNING'}
        with patch.dict(os.environ, env, clear=False):
            cfg = load_config()
            assert cfg.LOG_LEVEL == 'WARNING'


class TestLoadConfigPlaceholderDetection:
    """Verify placeholder detection in production mode."""

    def test_placeholder_token_exits_in_production(self):
        env = {**_BASE_ENV, 'LINE_CHANNEL_ACCESS_TOKEN': 'your_line_channel_access_token_here', 'ENVIRONMENT': 'production'}
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(SystemExit):
                load_config()

    def test_placeholder_password_exits_in_production(self):
        env = {**_BASE_ENV, 'MYSQL_PASSWORD': 'change_this_password', 'ENVIRONMENT': 'production'}
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(SystemExit):
                load_config()

    def test_placeholder_with_example_exits_in_production(self):
        env = {**_BASE_ENV, 'XAI_API_KEY': 'example_key_123', 'ENVIRONMENT': 'production'}
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(SystemExit):
                load_config()

    def test_no_exit_in_development_with_placeholders(self):
        env = {**_BASE_ENV, 'LINE_CHANNEL_ACCESS_TOKEN': 'your_token'}
        with patch.dict(os.environ, env, clear=False):
            cfg = load_config()
            assert cfg.ENVIRONMENT == 'development'

    def test_real_values_pass_in_production(self):
        env = {**_BASE_ENV, 'ENVIRONMENT': 'production'}
        with patch.dict(os.environ, env, clear=False):
            cfg = load_config()
            assert cfg.ENVIRONMENT == 'production'


class TestLoadConfigNumericValidation:
    """Verify numeric variable validation."""

    def test_non_numeric_port_exits(self):
        env = {**_BASE_ENV, 'PORT': 'not_a_number'}
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(SystemExit):
                load_config()

    def test_non_numeric_redis_port_exits(self):
        env = {**_BASE_ENV, 'REDIS_PORT': 'abc'}
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(SystemExit):
                load_config()

    def test_missing_required_var_exits(self):
        env = {**_BASE_ENV}
        with patch.dict(os.environ, env, clear=False):
            saved = os.environ.pop('LINE_CHANNEL_ACCESS_TOKEN', None)
            try:
                with pytest.raises(SystemExit):
                    load_config()
            finally:
                if saved is not None:
                    os.environ['LINE_CHANNEL_ACCESS_TOKEN'] = saved

    def test_valid_config_returns_config_object(self):
        with patch.dict(os.environ, _BASE_ENV, clear=False):
            cfg = load_config()
            assert cfg.LINE_CHANNEL_ACCESS_TOKEN == 'tok'
            assert cfg.REDIS_PORT == 6379
            assert cfg.PORT == 5000


class TestLoadConfigRagFlag:
    """Verify RAG_ENABLED parsing and defaults."""

    def test_rag_enabled_defaults_true_when_missing(self):
        env = {**_BASE_ENV}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop('RAG_ENABLED', None)
            cfg = load_config()
            assert cfg.RAG_ENABLED is True

    def test_rag_enabled_parses_false(self):
        env = {**_BASE_ENV, 'RAG_ENABLED': 'false'}
        with patch.dict(os.environ, env, clear=False):
            cfg = load_config()
            assert cfg.RAG_ENABLED is False

    def test_rag_extended_defaults_are_loaded(self):
        env = {**_BASE_ENV}
        with patch.dict(os.environ, env, clear=False):
            for key in ('RAG_EMBEDDING_DIM', 'RAG_TOP_K', 'RAG_FETCH_K', 'RAG_MAX_CONTEXT_CHARS'):
                os.environ.pop(key, None)
            cfg = load_config()
            assert cfg.RAG_EMBEDDING_DIM == 1536
            assert cfg.RAG_TOP_K == 5
            assert cfg.RAG_FETCH_K == 24
            assert cfg.RAG_MAX_CONTEXT_CHARS == 7000
