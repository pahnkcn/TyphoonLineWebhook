"""Tests for app.models — typed dataclasses."""
import pytest
from app.models import (
    RiskAssessmentResult,
    ConversationEntry,
    ProcessingMetrics,
    HealthStatus,
    DashboardOverview,
    UserSummary,
)


class TestRiskAssessmentResult:
    def test_tuple_unpacking(self):
        result = RiskAssessmentResult("high", ["ฆ่าตัวตาย"])
        level, keywords = result
        assert level == "high"
        assert keywords == ["ฆ่าตัวตาย"]

    def test_is_high_risk(self):
        assert RiskAssessmentResult("high", []).is_high_risk is True
        assert RiskAssessmentResult("medium", []).is_high_risk is False

    def test_is_medium_risk(self):
        assert RiskAssessmentResult("medium", ["เครียด"]).is_medium_risk is True
        assert RiskAssessmentResult("general", []).is_medium_risk is False

    def test_frozen(self):
        result = RiskAssessmentResult("high", [])
        with pytest.raises(AttributeError):
            result.level = "low"

    def test_default_keywords(self):
        result = RiskAssessmentResult("general")
        assert result.keywords == []


class TestConversationEntry:
    def test_tuple_unpacking(self):
        entry = ConversationEntry(id=1, user_message="hello", bot_response="hi")
        eid, umsg, bresp = entry
        assert eid == 1
        assert umsg == "hello"
        assert bresp == "hi"

    def test_optional_fields(self):
        entry = ConversationEntry(id=1, user_message="a", bot_response="b")
        assert entry.timestamp is None
        assert entry.token_count is None
        assert entry.is_important is False

    def test_with_all_fields(self):
        from datetime import datetime
        now = datetime.now()
        entry = ConversationEntry(
            id=5, user_message="msg", bot_response="resp",
            timestamp=now, token_count=100, is_important=True,
        )
        assert entry.timestamp == now
        assert entry.token_count == 100
        assert entry.is_important is True


class TestProcessingMetrics:
    def test_basic(self):
        m = ProcessingMetrics(user_id="u1", processing_time=1.5)
        assert m.user_id == "u1"
        assert m.used_fallback is False
        assert m.had_error is False


class TestHealthStatus:
    def test_healthy(self):
        h = HealthStatus(name="redis", healthy=True)
        assert h.healthy is True
        assert h.details is None

    def test_unhealthy_with_details(self):
        h = HealthStatus(name="mysql", healthy=False, details="conn refused")
        assert h.healthy is False
        assert "conn refused" in h.details


class TestDashboardOverview:
    def test_defaults(self):
        d = DashboardOverview()
        assert d.total_conversations == 0
        assert d.active_follow_ups == 0


class TestUserSummary:
    def test_defaults(self):
        u = UserSummary(user_id="u1")
        assert u.total_messages == 0
        assert u.recent_risk_events == []
