"""Tests for services.context_manager — summarization helpers and filter_messages_for_api."""
import pytest
from app.services.context_manager import (
    chunk_conversation_history,
    filter_messages_for_api,
)


class TestChunkConversationHistory:
    """Test conversation history chunking."""

    def test_empty_history(self):
        assert chunk_conversation_history([]) == []

    def test_single_chunk(self):
        history = [(i, f"msg{i}", f"resp{i}") for i in range(5)]
        chunks = chunk_conversation_history(history, chunk_size=10)
        assert len(chunks) == 1
        assert chunks[0] == history

    def test_exact_chunk_size(self):
        history = [(i, f"msg{i}", f"resp{i}") for i in range(10)]
        chunks = chunk_conversation_history(history, chunk_size=5)
        assert len(chunks) == 2
        assert len(chunks[0]) == 5
        assert len(chunks[1]) == 5

    def test_partial_last_chunk(self):
        history = [(i, f"msg{i}", f"resp{i}") for i in range(7)]
        chunks = chunk_conversation_history(history, chunk_size=3)
        assert len(chunks) == 3
        assert len(chunks[0]) == 3
        assert len(chunks[1]) == 3
        assert len(chunks[2]) == 1

    def test_chunk_size_one(self):
        history = [(1, "a", "b"), (2, "c", "d")]
        chunks = chunk_conversation_history(history, chunk_size=1)
        assert len(chunks) == 2


class TestFilterMessagesForApi:
    """Test system_summary filtering and injection logic."""

    def test_no_summary_messages(self):
        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        result = filter_messages_for_api(messages)
        assert len(result) == 3
        assert result[0]["role"] == "system"

    def test_summary_merged_into_existing_system(self):
        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "system_summary", "content": "Previous context: user discussed quitting"},
            {"role": "user", "content": "Hello"},
        ]
        result = filter_messages_for_api(messages)
        assert len(result) == 2
        assert result[0]["role"] == "system"
        assert "Previous context" in result[0]["content"]
        assert "You are helpful" in result[0]["content"]

    def test_summary_creates_system_if_missing(self):
        messages = [
            {"role": "system_summary", "content": "Summary info"},
            {"role": "user", "content": "Hello"},
        ]
        result = filter_messages_for_api(messages)
        assert len(result) == 2
        assert result[0]["role"] == "system"
        assert "Summary info" in result[0]["content"]

    def test_multiple_summaries_combined(self):
        messages = [
            {"role": "system", "content": "Base system"},
            {"role": "system_summary", "content": "Part 1"},
            {"role": "system_summary", "content": "Part 2"},
            {"role": "user", "content": "Hello"},
        ]
        result = filter_messages_for_api(messages)
        assert len(result) == 2
        assert "Part 1" in result[0]["content"]
        assert "Part 2" in result[0]["content"]

    def test_empty_summary_not_injected(self):
        messages = [
            {"role": "system", "content": "Base"},
            {"role": "system_summary", "content": "   "},
            {"role": "user", "content": "Hi"},
        ]
        result = filter_messages_for_api(messages)
        # Empty summary should not alter the system message
        assert result[0]["content"] == "Base"

    def test_preserves_message_order(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "resp1"},
            {"role": "user", "content": "msg2"},
        ]
        result = filter_messages_for_api(messages)
        roles = [m["role"] for m in result]
        assert roles == ["system", "user", "assistant", "user"]
