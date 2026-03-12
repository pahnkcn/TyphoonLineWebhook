"""Tests for services.context_manager — summarization helpers and filter_messages_for_api."""
from unittest.mock import MagicMock, patch
import pytest
import app.session_manager as session_manager
from app.services.context_manager import (
    chunk_conversation_history,
    filter_messages_for_api,
    optimize_context,
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


class TestHybridContextManagement:
    def test_delegates_to_optimize_context(self):
        mock_config = MagicMock()
        expected = [{"role": "system_summary", "content": "summary"}]

        with patch.object(session_manager, "_config", mock_config), \
             patch("app.session_manager.get_chat_session", return_value=[{"role": "user", "content": "hello"}]), \
             patch("app.services.context_manager.optimize_context", return_value=expected) as mock_optimize:
            result = session_manager.hybrid_context_management("user-1", 123)

        assert result == expected
        mock_optimize.assert_called_once_with("user-1", mock_config, None, max_tokens=123)


class TestOptimizeContext:
    def test_preserves_system_messages_and_important_turns(self):
        mock_config = MagicMock()
        history = [
            {"role": "system", "content": "base system"},
            {"role": "system", "content": "บริบทผู้ใช้จากแบบประเมิน:\nuser context"},
            {"role": "user", "content": "สวัสดี"},
            {"role": "assistant", "content": "สวัสดีครับ"},
            {"role": "user", "content": "ช่วงนี้เครียดมาก"},
            {"role": "assistant", "content": "ขอบคุณที่เล่าให้ฟังนะครับ"},
            {"role": "user", "content": "ช่วยผมวางแผนหน่อย"},
            {"role": "assistant", "content": "ได้ครับ เรามาค่อยๆ วางแผนกัน"},
        ]

        with patch("app.services.context_manager.get_session_token_count", return_value=999999), \
             patch("app.services.context_manager.get_chat_session", return_value=history), \
             patch("app.services.context_manager.summarize_conversation_history", return_value="summary") as mock_summary, \
             patch("app.services.context_manager.save_chat_session") as mock_save:
            result = optimize_context("user-1", mock_config, None, max_tokens=100, keep_recent=1)

        assert result[0] == {"role": "system", "content": "base system"}
        assert result[1]["content"].startswith("บริบทผู้ใช้จากแบบประเมิน:")
        assert any(msg["role"] == "system_summary" and "summary" in msg["content"] for msg in result)
        assert {"role": "user", "content": "ช่วงนี้เครียดมาก"} in result
        assert {"role": "assistant", "content": "ขอบคุณที่เล่าให้ฟังนะครับ"} in result
        assert {"role": "user", "content": "ช่วยผมวางแผนหน่อย"} in result
        assert {"role": "assistant", "content": "ได้ครับ เรามาค่อยๆ วางแผนกัน"} in result
        assert {"role": "user", "content": "สวัสดี"} not in result
        mock_summary.assert_called_once()
        mock_save.assert_called_once_with("user-1", result)

    def test_keeps_existing_summary_when_no_new_summary_is_needed(self):
        mock_config = MagicMock()
        history = [
            {"role": "system", "content": "base system"},
            {"role": "system_summary", "content": "existing summary"},
            {"role": "user", "content": "ช่วงนี้เครียดมาก"},
            {"role": "assistant", "content": "ขอบคุณที่เล่าให้ฟังนะครับ"},
            {"role": "user", "content": "ผมกังวลมากเรื่องการกลับไปใช้ยา"},
            {"role": "assistant", "content": "ได้ครับ"},
            {"role": "user", "content": "ตกลงครับ"},
            {"role": "assistant", "content": "ผมอยู่ตรงนี้เสมอ"},
        ]

        with patch("app.services.context_manager.get_session_token_count", return_value=999999), \
             patch("app.services.context_manager.get_chat_session", return_value=history), \
             patch("app.services.context_manager.summarize_conversation_history") as mock_summary, \
             patch("app.services.context_manager.save_chat_session"):
            result = optimize_context("user-1", mock_config, None, max_tokens=100, keep_recent=1)

        assert {"role": "system_summary", "content": "existing summary"} in result
        mock_summary.assert_not_called()
