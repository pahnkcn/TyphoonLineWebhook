"""Tests for the token counter module."""
import pytest
from app.token_counter import TokenCounter


@pytest.fixture
def counter():
    """Create a TokenCounter instance for testing."""
    return TokenCounter(cache_size=100)


class TestTokenCounter:
    """Tests for TokenCounter basic functionality."""

    def test_count_tokens_english(self, counter):
        text = "Hello, how are you doing today?"
        count = counter.count_tokens(text)
        assert isinstance(count, int)
        assert count > 0

    def test_count_tokens_thai(self, counter):
        text = "สวัสดีครับ วันนี้เป็นอย่างไรบ้าง"
        count = counter.count_tokens(text)
        assert isinstance(count, int)
        assert count > 0

    def test_count_tokens_empty(self, counter):
        count = counter.count_tokens("")
        assert count == 0

    def test_count_tokens_consistency(self, counter):
        """Same input should always return the same count (caching)."""
        text = "This is a test message for consistency"
        count1 = counter.count_tokens(text)
        count2 = counter.count_tokens(text)
        assert count1 == count2

    def test_longer_text_more_tokens(self, counter):
        short = "Hello"
        long = "Hello, this is a much longer text that should have more tokens"
        assert counter.count_tokens(long) > counter.count_tokens(short)


class TestCountMessageTokens:
    """Tests for counting tokens in message arrays."""

    def test_single_message(self, counter):
        messages = [{"role": "user", "content": "Hello"}]
        count = counter.count_message_tokens(messages)
        assert isinstance(count, int)
        assert count > 0

    def test_multiple_messages(self, counter):
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        count = counter.count_message_tokens(messages)
        assert count > 0

    def test_empty_messages(self, counter):
        count = counter.count_message_tokens([])
        assert count == 0

    def test_more_messages_more_tokens(self, counter):
        short = [{"role": "user", "content": "Hi"}]
        long = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello! How can I help you today?"},
            {"role": "user", "content": "I need some advice about something important"},
        ]
        assert counter.count_message_tokens(long) > counter.count_message_tokens(short)


class TestCountTokensList:
    """Tests for counting tokens in a list of texts (via count_tokens)."""

    def test_list_of_texts(self, counter):
        texts = ["Hello", "World", "How are you"]
        counts = counter.count_tokens(texts)
        assert isinstance(counts, list)
        assert all(isinstance(c, int) and c > 0 for c in counts)

    def test_empty_list(self, counter):
        counts = counter.count_tokens([])
        assert counts == []


class TestEstimateCompletionTokens:
    """Tests for estimating completion tokens."""

    def test_estimate_returns_tuple(self, counter):
        prompt_tokens = counter.count_tokens("Tell me about drugs")
        result = counter.estimate_completion_tokens(prompt_tokens)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert all(isinstance(v, int) for v in result)
        assert result[0] == prompt_tokens
        assert result[1] > 0
