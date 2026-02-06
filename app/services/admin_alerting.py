"""Admin alerting service via LINE Notify.

Sends alerts for critical events:
- High-risk user detected
- Critical application errors
- Circuit breaker state changes
- Daily cost threshold exceeded

Configuration via environment variables:
- LINE_NOTIFY_TOKEN: LINE Notify access token (obtain from https://notify-bot.line.me/)
- ADMIN_ALERT_ENABLED: "true" to enable (default: disabled)
- ADMIN_ALERT_COOLDOWN: Seconds between duplicate alerts (default: 300)
"""
import os
import logging
import time
import threading
from datetime import datetime
from typing import Optional, Dict, Any

import requests


_LINE_NOTIFY_URL = 'https://notify-api.line.me/api/notify'
_notify_token: Optional[str] = None
_enabled: bool = False
_cooldown: int = 300
_last_alerts: Dict[str, float] = {}
_lock = threading.Lock()


def init_admin_alerting(
    token: Optional[str] = None,
    enabled: Optional[bool] = None,
    cooldown: Optional[int] = None,
):
    """Initialize the admin alerting system.

    Args:
        token: LINE Notify token. Falls back to LINE_NOTIFY_TOKEN env var.
        enabled: Whether alerting is active. Falls back to ADMIN_ALERT_ENABLED env var.
        cooldown: Seconds between duplicate alerts per category.
    """
    global _notify_token, _enabled, _cooldown

    _notify_token = token or os.getenv('LINE_NOTIFY_TOKEN', '')
    if enabled is not None:
        _enabled = enabled
    else:
        _enabled = os.getenv('ADMIN_ALERT_ENABLED', 'false').lower() == 'true'
    if cooldown is not None:
        _cooldown = cooldown
    else:
        _cooldown = int(os.getenv('ADMIN_ALERT_COOLDOWN', '300'))

    if _enabled and not _notify_token:
        logging.warning("Admin alerting enabled but LINE_NOTIFY_TOKEN is not set — alerts will be logged only")

    status = "enabled" if _enabled else "disabled"
    logging.info(f"Admin alerting initialized ({status})")


def _is_cooled_down(category: str) -> bool:
    """Check if enough time has passed since the last alert of this category."""
    now = time.time()
    with _lock:
        last = _last_alerts.get(category, 0.0)
        if now - last < _cooldown:
            return False
        _last_alerts[category] = now
        return True


def _send_line_notify(message: str) -> bool:
    """Send a message via LINE Notify API.

    Returns True on success, False on failure.
    """
    if not _notify_token:
        logging.info(f"[ALERT-LOG] {message}")
        return False

    try:
        headers = {'Authorization': f'Bearer {_notify_token}'}
        data = {'message': message}
        response = requests.post(_LINE_NOTIFY_URL, headers=headers, data=data, timeout=10)
        if response.status_code == 200:
            logging.debug("LINE Notify alert sent successfully")
            return True
        else:
            logging.warning(f"LINE Notify failed ({response.status_code}): {response.text}")
            return False
    except Exception as e:
        logging.error(f"LINE Notify error: {e}")
        return False


def send_alert(
    category: str,
    message: str,
    severity: str = 'warning',
    extra: Optional[Dict[str, Any]] = None,
) -> bool:
    """Send an admin alert if enabled and not rate-limited.

    Args:
        category: Alert category for cooldown grouping (e.g. 'high_risk', 'critical_error').
        message: Human-readable alert message.
        severity: One of 'info', 'warning', 'critical'.
        extra: Additional key-value data to include.

    Returns:
        True if alert was sent, False if suppressed or failed.
    """
    if not _enabled:
        return False

    if not _is_cooled_down(category):
        logging.debug(f"Alert suppressed (cooldown): {category}")
        return False

    severity_emoji = {'info': 'ℹ️', 'warning': '⚠️', 'critical': '🚨'}.get(severity, '📢')
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    formatted = f"\n{severity_emoji} [{severity.upper()}] {category}\n⏰ {timestamp}\n\n{message}"

    if extra:
        details = "\n".join(f"• {k}: {v}" for k, v in extra.items())
        formatted += f"\n\n📋 Details:\n{details}"

    return _send_line_notify(formatted)


# ── Convenience functions for common alert types ────────────────────

def alert_high_risk_user(user_id: str, risk_level: str, keywords: list):
    """Alert when a high-risk user is detected."""
    return send_alert(
        category=f'high_risk:{user_id}',
        message=f"ผู้ใช้ {user_id[:8]}... แสดงสัญญาณความเสี่ยงสูง",
        severity='critical',
        extra={
            'risk_level': risk_level,
            'keywords': ', '.join(keywords[:5]),
            'user_id': user_id,
        },
    )


def alert_critical_error(user_id: str, user_message: str, error: str):
    """Alert on critical application errors."""
    return send_alert(
        category='critical_error',
        message=f"Critical error for user {user_id[:8]}...",
        severity='critical',
        extra={
            'user_id': user_id,
            'user_message': user_message[:100],
            'error': error[:200],
        },
    )


def alert_circuit_breaker(name: str, state: str, failure_count: int):
    """Alert when a circuit breaker changes state."""
    return send_alert(
        category=f'circuit_breaker:{name}',
        message=f"Circuit breaker '{name}' → {state}",
        severity='warning' if state == 'open' else 'info',
        extra={
            'breaker_name': name,
            'new_state': state,
            'failure_count': failure_count,
        },
    )


def alert_daily_cost_threshold(current_cost: float, threshold: float, model: str):
    """Alert when daily API cost exceeds threshold."""
    return send_alert(
        category='cost_threshold',
        message=f"ค่าใช้จ่าย API วันนี้ (${current_cost:.2f}) เกินเกณฑ์ (${threshold:.2f})",
        severity='warning',
        extra={
            'current_cost': f"${current_cost:.2f}",
            'threshold': f"${threshold:.2f}",
            'model': model,
        },
    )
