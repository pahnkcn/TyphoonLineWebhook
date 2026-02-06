"""Tests for error_handling module — CircuitBreaker, ChatbotError, ErrorHandler."""
import pytest
import time
from unittest.mock import MagicMock
from app.error_handling import (
    ChatbotError,
    ErrorCategory,
    ErrorSeverity,
    CircuitBreaker,
    CircuitState,
    ErrorHandler,
    get_error_handler,
)


class TestChatbotError:
    """Test ChatbotError creation and serialization."""

    def test_basic_creation(self):
        err = ChatbotError(
            message="test error",
            category=ErrorCategory.EXTERNAL_API,
            severity=ErrorSeverity.HIGH,
        )
        assert str(err) == "test error"
        assert err.category == ErrorCategory.EXTERNAL_API
        assert err.severity == ErrorSeverity.HIGH
        assert err.retry_able is True

    def test_default_user_message_for_database(self):
        err = ChatbotError(
            message="db failed",
            category=ErrorCategory.DATABASE,
        )
        assert "ข้อมูล" in err.user_message

    def test_default_user_message_for_critical(self):
        err = ChatbotError(
            message="critical",
            category=ErrorCategory.SYSTEM,
            severity=ErrorSeverity.CRITICAL,
        )
        assert "ร้ายแรง" in err.user_message

    def test_custom_user_message(self):
        err = ChatbotError(
            message="internal",
            category=ErrorCategory.SYSTEM,
            user_message="custom message",
        )
        assert err.user_message == "custom message"

    def test_to_dict(self):
        err = ChatbotError(
            message="test",
            category=ErrorCategory.VALIDATION,
            severity=ErrorSeverity.LOW,
        )
        d = err.to_dict()
        assert d["category"] == "validation"
        assert d["severity"] == "low"
        assert d["message"] == "test"
        assert "timestamp" in d

    def test_original_error_preserved(self):
        original = ValueError("root cause")
        err = ChatbotError(
            message="wrapped",
            category=ErrorCategory.SYSTEM,
            original_error=original,
        )
        assert err.original_error is original


class TestCircuitBreaker:
    """Test CircuitBreaker state transitions."""

    def test_closed_state_on_success(self):
        cb = CircuitBreaker(name="test", failure_threshold=3, timeout=1)
        result = cb.call(lambda: 42)
        assert result == 42
        assert cb.state == CircuitState.CLOSED
        assert cb.success_count == 1

    def test_stays_closed_below_threshold(self):
        cb = CircuitBreaker(name="test", failure_threshold=3, timeout=1)

        for _ in range(2):
            with pytest.raises(ChatbotError):
                cb.call(self._failing_fn)

        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 2

    def test_opens_at_threshold(self):
        cb = CircuitBreaker(name="test", failure_threshold=3, timeout=60)

        for _ in range(3):
            with pytest.raises(ChatbotError):
                cb.call(self._failing_fn)

        assert cb.state == CircuitState.OPEN
        assert cb.failure_count == 3

    def test_open_rejects_calls(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, timeout=60)

        with pytest.raises(ChatbotError):
            cb.call(self._failing_fn)

        assert cb.state == CircuitState.OPEN

        with pytest.raises(ChatbotError) as exc_info:
            cb.call(lambda: 42)
        assert "OPEN" in str(exc_info.value)

    def test_half_open_after_timeout(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, timeout=0)

        with pytest.raises(ChatbotError):
            cb.call(self._failing_fn)

        assert cb.state == CircuitState.OPEN

        # timeout=0 means it should immediately try half-open
        time.sleep(0.01)
        result = cb.call(lambda: "recovered")
        assert result == "recovered"
        assert cb.state == CircuitState.CLOSED

    def test_success_resets_failure_count(self):
        cb = CircuitBreaker(name="test", failure_threshold=3, timeout=1)

        with pytest.raises(ChatbotError):
            cb.call(self._failing_fn)
        assert cb.failure_count == 1

        cb.call(lambda: "ok")
        assert cb.failure_count == 0

    def test_get_stats(self):
        cb = CircuitBreaker(name="stats_test", failure_threshold=5, timeout=300)
        cb.call(lambda: True)
        stats = cb.get_stats()
        assert stats["name"] == "stats_test"
        assert stats["state"] == "closed"
        assert stats["total_calls"] == 1
        assert stats["success_count"] == 1

    def test_decorator_usage(self):
        cb = CircuitBreaker(name="deco", failure_threshold=3, timeout=60)

        @cb
        def guarded_fn(x):
            return x * 2

        assert guarded_fn(5) == 10

    @staticmethod
    def _failing_fn():
        raise RuntimeError("simulated failure")


class TestErrorHandler:
    """Test ErrorHandler basics."""

    def test_singleton_pattern(self):
        h1 = get_error_handler()
        h2 = get_error_handler()
        assert h1 is h2

    def test_handle_chatbot_error(self):
        handler = ErrorHandler()
        err = ChatbotError(
            message="test",
            category=ErrorCategory.DATABASE,
            severity=ErrorSeverity.HIGH,
        )
        result = handler.handle_error(err, user_id="u123")
        assert result is err
        assert result.context.get("user_id") == "u123"

    def test_handle_regular_exception(self):
        handler = ErrorHandler()
        result = handler.handle_error(ValueError("bad value"))
        assert isinstance(result, ChatbotError)

    def test_circuit_breakers_registered_via_add(self):
        handler = ErrorHandler()
        # Circuit breakers are no longer pre-populated; they live in app_main.py
        assert handler.circuit_breakers == {}
        cb = handler.add_circuit_breaker("test_api", failure_threshold=3, timeout=60)
        assert "test_api" in handler.circuit_breakers
        assert cb.failure_threshold == 3
