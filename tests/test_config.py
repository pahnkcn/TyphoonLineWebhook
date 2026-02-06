"""Tests for the config module — verify new SYSTEM_MESSAGE_CORE/SUMMARY and generation configs."""
import pytest
from app.config import (
    SYSTEM_MESSAGES,
    SYSTEM_MESSAGE_CORE,
    SYSTEM_MESSAGE_SUMMARY,
    GENERATION_CONFIG,
    CRISIS_CONFIG,
    INFO_CONFIG,
    SUMMARY_GENERATION_CONFIG,
    get_dynamic_config,
)


class TestSystemMessages:
    """Verify all system message variants exist and have correct structure."""

    def test_system_messages_full_exists(self):
        assert SYSTEM_MESSAGES["role"] == "system"
        assert len(SYSTEM_MESSAGES["content"]) > 500  # Full prompt is very long

    def test_system_message_core_exists(self):
        assert SYSTEM_MESSAGE_CORE["role"] == "system"
        assert "น้องใจดี" in SYSTEM_MESSAGE_CORE["content"]
        # Core should be shorter than full
        assert len(SYSTEM_MESSAGE_CORE["content"]) < len(SYSTEM_MESSAGES["content"])

    def test_system_message_summary_exists(self):
        assert SYSTEM_MESSAGE_SUMMARY["role"] == "system"
        assert "สรุป" in SYSTEM_MESSAGE_SUMMARY["content"]
        # Summary should be much shorter than core
        assert len(SYSTEM_MESSAGE_SUMMARY["content"]) < len(SYSTEM_MESSAGE_CORE["content"])

    def test_core_covers_key_topics(self):
        content = SYSTEM_MESSAGE_CORE["content"]
        assert "Motivational Interviewing" in content
        assert "OARS" in content
        assert "1323" in content  # Crisis hotline
        assert "1165" in content  # Drug hotline


class TestGenerationConfigs:
    """Verify generation config values are reasonable."""

    def test_generation_config_max_tokens_reduced(self):
        assert GENERATION_CONFIG["max_tokens"] <= 2000

    def test_crisis_config_max_tokens_reduced(self):
        assert CRISIS_CONFIG["max_tokens"] <= 3000

    def test_info_config_max_tokens_reduced(self):
        assert INFO_CONFIG["max_tokens"] <= 4000

    def test_summary_config_max_tokens_reduced(self):
        assert SUMMARY_GENERATION_CONFIG["max_tokens"] <= 5000

    def test_crisis_has_frequency_penalty(self):
        assert "frequency_penalty" in CRISIS_CONFIG

    def test_info_has_frequency_penalty(self):
        assert "frequency_penalty" in INFO_CONFIG

    def test_generation_presence_penalty_increased(self):
        assert GENERATION_CONFIG["presence_penalty"] >= 0.5


class TestGetDynamicConfig:
    """Verify dynamic config selection logic."""

    def test_crisis_keywords_return_crisis_config(self):
        result = get_dynamic_config("ฆ่าตัวตาย")
        assert result == CRISIS_CONFIG

    def test_info_keywords_return_info_config(self):
        result = get_dynamic_config("ยาบ้าคืออะไร")
        assert result == INFO_CONFIG

    def test_normal_message_returns_generation_config(self):
        result = get_dynamic_config("สวัสดีครับ")
        assert result == GENERATION_CONFIG
