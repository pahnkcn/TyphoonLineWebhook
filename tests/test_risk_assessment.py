"""Tests for the risk assessment module."""
import pytest
from app.risk_assessment import (
    assess_risk,
    classify_risk_category,
    normalize_risk_level,
    _is_negated,
    _match_keyword,
    _find_keyword_pos,
    GENERAL_RISK_LEVEL,
    MEDIUM_RISK_THRESHOLD,
    RISK_KEYWORDS,
    _SHORT_EN_KEYWORDS,
)
from app.session_manager import is_important_message


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

    # --- Medium risk escalation to high ---
    def test_medium_risk_escalation(self):
        """Multiple medium-risk keywords in one message should escalate to high."""
        message = "นอนไม่หลับ ซึมเศร้า อยากใช้ยา"
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

    def test_emoji_high_risk(self):
        """Clearly dangerous emojis should still be high risk."""
        level, keywords = assess_risk("🔪")
        assert level == "high"
        assert "🔪" in keywords


class TestFalsePositiveFixes:
    """Tests that previously false-positive messages are now correctly classified."""

    # --- CRITICAL: "od" removed from high_risk ---
    @pytest.mark.parametrize("message,expected_level", [
        ("today is a good day", GENERAL_RISK_LEVEL),
        ("my body feels fine", GENERAL_RISK_LEVEL),
        ("let me check the code", GENERAL_RISK_LEVEL),
        ("what a good mood", GENERAL_RISK_LEVEL),
        ("the food was great", GENERAL_RISK_LEVEL),
        ("modern method applied", GENERAL_RISK_LEVEL),
    ])
    def test_od_false_positives_fixed(self, message, expected_level):
        """Messages containing 'od' as substring should NOT trigger high risk."""
        level, _ = assess_risk(message)
        assert level == expected_level

    # --- "overdose" should still work ---
    def test_overdose_still_detected(self):
        level, keywords = assess_risk("I think I took an overdose")
        assert level == "high"
        assert "overdose" in keywords

    # --- Short Thai keyword removals ---
    @pytest.mark.parametrize("message", [
        "วันนี้ก้าวหน้ามาก",          # "กาว" removed, should not match "ก้าว"
        "ไปสูบน้ำที่บ้าน",             # "สูบ" replaced with "สูบยา"
        "ฉีดวัคซีนเข็มที่สอง",        # "ฉีด" replaced with "ฉีดยา"
        "ผสมแป้งทำขนม",              # "ผสม" replaced with "ผสมยา"
        "เคยไปเที่ยวทะเล",           # "เค" removed
        "ดมกลิ่นดอกไม้",             # "ดม" replaced with "ดมยา"
        "บาร์โค้ดสแกนไม่ได้",         # "บาร์" removed
        "ไปคลับออกกำลังกาย",         # "คลับ" removed
        "ฟัง podcast สนุกดี",          # "pod" removed
        "this is a nice day",          # "ice" removed
        "วันนี้หลุดจากงาน",           # "หลุด" removed (keep "หลุดเคลน")
        "เมาเรื่อยเปล่า",             # "เมา" removed (keep "เมายา")
        "กัญญาสวยมาก",              # "กัญ" removed
    ])
    def test_short_keyword_false_positives_fixed(self, message):
        """Short/ambiguous keywords should no longer cause false positives."""
        level, _ = assess_risk(message)
        assert level == GENERAL_RISK_LEVEL

    # --- Compound keywords should still work ---
    @pytest.mark.parametrize("message,expected_keywords", [
        ("อยากสูบยาอีกแล้ว", ["สูบยา"]),
        ("แอบไปฉีดยา", ["ฉีดยา"]),
        ("ชวนกันไปดมกาว", ["ดมกาว"]),
        ("เสพยาทุกวัน", ["เสพยา"]),
        ("ไปดมยาที่บ้าน", ["ดมยา"]),
    ])
    def test_compound_keywords_still_detected(self, message, expected_keywords):
        """Compound forms of previously short keywords should still be detected."""
        level, keywords = assess_risk(message)
        assert level in ("medium", "high")
        for expected in expected_keywords:
            assert expected in keywords

    # --- "เครียดมาก" should now be medium, not high ---
    def test_เครียดมาก_no_longer_escalated(self):
        """'เครียดมาก' previously matched 'เครียด'+'เค' → high. Now 'เค' is removed."""
        level, keywords = assess_risk("เครียดมาก")
        assert level == "medium"
        assert "เครียด" in keywords


class TestCaseSensitivityFixes:
    """Tests that uppercase keywords now match correctly after lowercasing."""

    @pytest.mark.parametrize("message", [
        "ผมใช้ MDMA",
        "เพื่อนชวนเสพ LSD",
        "เอา GHB มาผสม",
    ])
    def test_uppercase_drug_names_detected(self, message):
        """Previously dead keywords (MDMA, LSD, GHB) should now match after lowercasing."""
        level, _ = assess_risk(message)
        assert level in ("medium", "high")


class TestEmojiReclassification:
    """Tests that casual emojis moved from high_risk to medium_risk."""

    @pytest.mark.parametrize("emoji", ["😭", "😞", "😖", "💊"])
    def test_casual_emojis_are_medium(self, emoji):
        """Commonly-used emojis should be medium risk, not high."""
        level, _ = assess_risk(emoji)
        assert level == "medium"

    @pytest.mark.parametrize("emoji", ["🔪", "🩸", "⚰️", "🪦"])
    def test_dangerous_emojis_remain_high(self, emoji):
        """Clearly dangerous emojis should remain high risk."""
        level, _ = assess_risk(emoji)
        assert level == "high"


class TestNegationHandling:
    """Tests for negation prefix detection."""

    def test_negated_high_becomes_medium(self):
        """'ไม่ได้อยากตาย' should become medium instead of high."""
        level, keywords = assess_risk("ไม่ได้อยากตาย")
        assert level == "medium"
        assert "อยากตาย" in keywords

    def test_negated_medium_becomes_general(self):
        """'ไม่ได้เครียด' should become general instead of medium."""
        level, _ = assess_risk("ไม่ได้เครียดนะ")
        assert level == GENERAL_RISK_LEVEL

    def test_non_negated_still_detected(self):
        """Keywords without negation should still be detected normally."""
        level, _ = assess_risk("อยากตาย")
        assert level == "high"

    def test_negation_window(self):
        """Negation prefix must be close to the keyword."""
        level, _ = assess_risk("ไม่ได้คิดว่าจะเป็นอะไร แต่ตอนนี้อยากตาย")
        assert level == "high"

    def test_is_negated_helper(self):
        """Direct test of _is_negated helper."""
        msg = "ไม่ได้อยากตาย"
        pos = msg.find("อยากตาย")
        assert _is_negated(msg, pos) is True

    def test_not_negated_helper(self):
        msg = "ผมอยากตาย"
        pos = msg.find("อยากตาย")
        assert _is_negated(msg, pos) is False


class TestWordBoundaryMatching:
    """Tests for short English keyword word boundary matching."""

    def test_kms_detected_standalone(self):
        """'kms' as standalone word should be detected."""
        level, keywords = assess_risk("i feel like kms")
        assert level == "high"
        assert "kms" in keywords

    def test_kms_not_in_bookmarks(self):
        """'kms' inside 'bookmarks' should NOT be detected."""
        level, _ = assess_risk("check my bookmarks")
        assert level == GENERAL_RISK_LEVEL

    def test_kys_detected_standalone(self):
        level, _ = assess_risk("someone told me kys")
        assert level == "high"

    def test_short_en_keywords_identified(self):
        """Verify that short English keywords are flagged for word boundary matching."""
        assert "kms" in _SHORT_EN_KEYWORDS
        assert "kys" in _SHORT_EN_KEYWORDS


class TestKeywordPreparation:
    """Tests that keywords are properly lowercased and deduplicated."""

    def test_all_keywords_lowercase(self):
        """Every keyword in RISK_KEYWORDS should be lowercase after _prepare_keywords."""
        for level_name, keywords in RISK_KEYWORDS.items():
            for kw in keywords:
                assert kw == kw.lower(), f"Keyword '{kw}' in {level_name} is not lowercase"

    def test_no_duplicate_keywords(self):
        """No duplicates within each risk level."""
        for level_name, keywords in RISK_KEYWORDS.items():
            assert len(keywords) == len(set(keywords)), f"Duplicates found in {level_name}"


class TestIsImportantMessage:
    """Tests for the consolidated is_important_message function."""

    def test_high_risk_is_important(self):
        assert is_important_message("test", "reply", risk_level="high") is True

    def test_medium_risk_is_important(self):
        assert is_important_message("test", "reply", risk_level="medium") is True

    def test_general_risk_not_important(self):
        assert is_important_message("test", "reply", risk_level="general") is False

    def test_long_user_message_with_risk(self):
        """Long message + general risk should still be important due to length."""
        long_msg = "a" * 301
        assert is_important_message(long_msg, "reply", risk_level="general") is True

    def test_long_bot_response_with_risk(self):
        long_resp = "b" * 501
        assert is_important_message("hi", long_resp, risk_level="general") is True

    def test_short_general_not_important(self):
        assert is_important_message("hi", "hello", risk_level="general") is False

    def test_no_risk_level_fallback(self):
        """When risk_level is not provided, only length check applies."""
        assert is_important_message("short", "short") is False
        assert is_important_message("a" * 301, "short") is True


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


class TestClassifyRiskCategory:
    """Tests for the classify_risk_category function."""

    def test_suicide_keywords(self):
        assert classify_risk_category(["ฆ่าตัวตาย"]) == "suicide"
        assert classify_risk_category(["อยากตาย"]) == "suicide"
        assert classify_risk_category(["suicide"]) == "suicide"
        assert classify_risk_category(["kms"]) == "suicide"

    def test_overdose_keywords(self):
        assert classify_risk_category(["overdose"]) == "overdose"
        assert classify_risk_category(["เกินขนาด"]) == "overdose"
        assert classify_risk_category(["หมดสติ"]) == "overdose"

    def test_substance_keywords(self):
        assert classify_risk_category(["ยาบ้า"]) == "substance"
        assert classify_risk_category(["เสพยา"]) == "substance"
        assert classify_risk_category(["ติดเหล้า"]) == "substance"

    def test_emotional_keywords(self):
        assert classify_risk_category(["เครียด"]) == "emotional"
        assert classify_risk_category(["เหงา"]) == "emotional"
        assert classify_risk_category(["นอนไม่หลับ"]) == "emotional"

    def test_suicide_takes_priority(self):
        """Suicide should take priority over overdose and substance."""
        assert classify_risk_category(["อยากตาย", "overdose"]) == "suicide"
        assert classify_risk_category(["ฆ่าตัวตาย", "ยาบ้า"]) == "suicide"

    def test_overdose_over_substance(self):
        """Overdose should take priority over substance."""
        assert classify_risk_category(["หมดสติ", "ยาบ้า"]) == "overdose"

    def test_empty_keywords(self):
        assert classify_risk_category([]) == "emotional"

    def test_integrated_with_assess_risk(self):
        """classify_risk_category should work with assess_risk output."""
        _, keywords = assess_risk("อยากฆ่าตัวตาย")
        assert classify_risk_category(keywords) == "suicide"

        _, keywords = assess_risk("กินยาเกินขนาด")
        assert classify_risk_category(keywords) == "overdose"


class TestFindKeywordPos:
    """Tests for the _find_keyword_pos helper."""

    def test_normal_keyword(self):
        assert _find_keyword_pos("อยากตาย", "ผมอยากตาย") == 2

    def test_short_en_keyword(self):
        pos = _find_keyword_pos("kms", "i feel like kms")
        assert pos == 12

    def test_keyword_not_found(self):
        assert _find_keyword_pos("อยากตาย", "สวัสดีครับ") == -1
