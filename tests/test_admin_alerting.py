"""Tests for services.admin_alerting — LINE Notify integration and cooldown logic."""
import time
import pytest
from unittest.mock import patch, MagicMock

from app.services.admin_alerting import (
    init_admin_alerting,
    send_alert,
    alert_high_risk_user,
    alert_critical_error,
    alert_circuit_breaker,
    alert_daily_cost_threshold,
    _is_cooled_down,
    _last_alerts,
)


@pytest.fixture(autouse=True)
def reset_alerting():
    """Reset alerting state before each test."""
    _last_alerts.clear()
    init_admin_alerting(token='', enabled=False, cooldown=0)
    yield
    _last_alerts.clear()


class TestInitAdminAlerting:
    def test_disabled_by_default(self):
        init_admin_alerting(token='tok', enabled=False)
        assert send_alert('test', 'msg') is False

    def test_enabled_without_token_logs_only(self):
        init_admin_alerting(token='', enabled=True, cooldown=0)
        result = send_alert('test', 'message')
        assert result is False  # No token → log only


class TestCooldown:
    def test_first_alert_passes(self):
        init_admin_alerting(token='tok', enabled=True, cooldown=60)
        assert _is_cooled_down('cat1') is True

    def test_duplicate_within_cooldown_suppressed(self):
        init_admin_alerting(token='tok', enabled=True, cooldown=60)
        assert _is_cooled_down('cat1') is True
        assert _is_cooled_down('cat1') is False

    def test_different_categories_independent(self):
        init_admin_alerting(token='tok', enabled=True, cooldown=60)
        assert _is_cooled_down('cat1') is True
        assert _is_cooled_down('cat2') is True

    def test_after_cooldown_passes(self):
        init_admin_alerting(token='tok', enabled=True, cooldown=0)
        assert _is_cooled_down('cat1') is True
        time.sleep(0.01)
        assert _is_cooled_down('cat1') is True


class TestSendAlert:
    @patch('app.services.admin_alerting._send_line_notify')
    def test_sends_when_enabled(self, mock_notify):
        mock_notify.return_value = True
        init_admin_alerting(token='tok', enabled=True, cooldown=0)
        result = send_alert('test_cat', 'Test message', severity='warning')
        assert result is True
        mock_notify.assert_called_once()
        call_msg = mock_notify.call_args[0][0]
        assert 'WARNING' in call_msg
        assert 'Test message' in call_msg

    @patch('app.services.admin_alerting._send_line_notify')
    def test_includes_extra_data(self, mock_notify):
        mock_notify.return_value = True
        init_admin_alerting(token='tok', enabled=True, cooldown=0)
        send_alert('cat', 'msg', extra={'key1': 'val1'})
        call_msg = mock_notify.call_args[0][0]
        assert 'key1' in call_msg
        assert 'val1' in call_msg

    @patch('app.services.admin_alerting._send_line_notify')
    def test_critical_severity_emoji(self, mock_notify):
        mock_notify.return_value = True
        init_admin_alerting(token='tok', enabled=True, cooldown=0)
        send_alert('cat', 'msg', severity='critical')
        call_msg = mock_notify.call_args[0][0]
        assert '🚨' in call_msg


class TestConvenienceFunctions:
    @patch('app.services.admin_alerting._send_line_notify')
    def test_alert_high_risk_user(self, mock_notify):
        mock_notify.return_value = True
        init_admin_alerting(token='tok', enabled=True, cooldown=0)
        result = alert_high_risk_user('user123', 'high', ['ฆ่าตัวตาย'])
        assert result is True

    @patch('app.services.admin_alerting._send_line_notify')
    def test_alert_critical_error(self, mock_notify):
        mock_notify.return_value = True
        init_admin_alerting(token='tok', enabled=True, cooldown=0)
        result = alert_critical_error('user123', 'hello', 'some error')
        assert result is True

    @patch('app.services.admin_alerting._send_line_notify')
    def test_alert_circuit_breaker(self, mock_notify):
        mock_notify.return_value = True
        init_admin_alerting(token='tok', enabled=True, cooldown=0)
        result = alert_circuit_breaker('xai_api', 'open', 5)
        assert result is True

    @patch('app.services.admin_alerting._send_line_notify')
    def test_alert_daily_cost(self, mock_notify):
        mock_notify.return_value = True
        init_admin_alerting(token='tok', enabled=True, cooldown=0)
        result = alert_daily_cost_threshold(15.50, 10.00, 'grok-4')
        assert result is True
