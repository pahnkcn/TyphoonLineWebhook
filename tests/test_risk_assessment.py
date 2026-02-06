"""Tests for the risk assessment module."""
import pytest
from app.risk_assessment import (
    assess_risk,
    normalize_risk_level,
    GENERAL_RISK_LEVEL,
    MEDIUM_RISK_THRESHOLD,
)


class TestAssessRisk:
    """Tests for the assess_risk function."""

    # --- High risk keywords ---
    @pytest.mark.parametrize("message", [
        "ฆ่าตัวตาย",
        "ผมอยากตาย",
        "ไม่อยากมีชีวิตอยู่แล้ว",
        "คิดสั้น",
        "overdose",
        "อยากจบชีวิต",
        "suicide",
        "kill myself",
        "หมดสติ",
    ])
    def test_high_risk_detected(self, message):
        level, keywords = assess_risk(message)
        assert level == "high"
        assert len(keywords) > 0

    # --- Medium risk keywords (single match only) ---
    @pytest.mark.parametrize("message", [
        "นอนไม่หลับเลย",
        "กังวล",
        "เหล้า",
    ])
    def test_medium_risk_single_keyword(self, message):
        level, keywords = assess_risk(message)
        assert level == "medium"
        assert len(keywords) >= 1

    # --- Medium keywords with substring overlap → escalated to high ---
    @pytest.mark.parametrize("message", [
        "เครียดมาก",    # matches 'เครียด' + 'เค' (ketamine slang)
        "ซึมเศร้า",     # matches 'ซึมเศร้า' + 'เศร้า'
    ])
    def test_medium_risk_substring_escalation(self, message):
        """Multiple medium substring matches escalate to high."""
        level, keywords = assess_risk(message)
        assert level == "high"
        assert len(keywords) >= MEDIUM_RISK_THRESHOLD

    # --- Medium risk escalation to high ---
    def test_medium_risk_escalation(self):
        """Multiple medium-risk keywords in one message should escalate to high."""
        # Combine enough medium-risk keywords to exceed MEDIUM_RISK_THRESHOLD
        message = "เครียดมาก นอนไม่หลับ ซึมเศร้า อยากใช้ยา"
        level, keywords = assess_risk(message)
        assert level == "high"
        assert len(keywords) >= MEDIUM_RISK_THRESHOLD

    # --- General risk (no keywords) ---
    @pytest.mark.parametrize("message", [
        "สวัสดีครับ",
        "วันนี้อากาศดีจัง",
        "ขอบคุณครับ",
        "hello",
    ])
    def test_general_risk(self, message):
        level, keywords = assess_risk(message)
        assert level == GENERAL_RISK_LEVEL
        assert len(keywords) == 0

    # --- Empty / edge cases ---
    def test_empty_message(self):
        level, keywords = assess_risk("")
        assert level == GENERAL_RISK_LEVEL
        assert keywords == []

    def test_case_insensitive(self):
        """assess_risk lowercases the input, so uppercase keywords should still match."""
        level, keywords = assess_risk("SUICIDE")
        assert level == "high"

    def test_emoji_risk_keywords(self):
        """Emoji-based risk keywords should be detected."""
        level, keywords = assess_risk("🔪")
        assert level == "high"
        assert "🔪" in keywords


class TestNormalizeRiskLevel:
    """Tests for the normalize_risk_level function."""

    def test_general(self):
        assert normalize_risk_level("general") == GENERAL_RISK_LEVEL

    def test_legacy_low(self):
        assert normalize_risk_level("low") == GENERAL_RISK_LEVEL

    def test_high(self):
        assert normalize_risk_level("high") == "high"

    def test_medium(self):
        assert normalize_risk_level("medium") == "medium"

    def test_none(self):
        assert normalize_risk_level(None) == "unknown"

    def test_empty_string(self):
        assert normalize_risk_level("") == "unknown"
