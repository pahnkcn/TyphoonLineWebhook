"""Proactive check-in service for inactive users.

Identifies users who haven't interacted recently and sends personalized
check-in messages, prioritizing high-risk users. Runs as a scheduled job
alongside the existing follow-up system.
"""
import json
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

_db_manager = None
_redis_client = None
_line_bot_api = None

# Configurable thresholds
INACTIVE_DAYS_HIGH_RISK = 3    # Check in after 3 days for high-risk users
INACTIVE_DAYS_NORMAL = 7       # Check in after 7 days for normal users
MAX_CHECKINS_PER_RUN = 10      # Don't overwhelm the system
CHECKIN_COOLDOWN_DAYS = 7      # Don't check in more than once per week


def init_proactive_checkin(db_manager, redis_client, line_bot_api):
    """Wire runtime dependencies."""
    global _db_manager, _redis_client, _line_bot_api
    _db_manager = db_manager
    _redis_client = redis_client
    _line_bot_api = line_bot_api


def _get_inactive_users(days: int, limit: int = 50) -> List[Tuple[str, datetime]]:
    """Find registered users whose last conversation was more than `days` ago."""
    try:
        cutoff = datetime.now() - timedelta(days=days)
        query = '''
            SELECT c.user_id, MAX(c.timestamp) as last_active
            FROM conversations c
            INNER JOIN registration_codes r ON r.user_id = c.user_id AND r.status = 'verified'
            GROUP BY c.user_id
            HAVING last_active < %s
            ORDER BY last_active ASC
            LIMIT %s
        '''
        rows = _db_manager.execute_query(query, (cutoff, limit)) or []
        return [(row[0], row[1]) for row in rows]
    except Exception as e:
        logging.error(f"Error finding inactive users: {e}")
        return []


def _get_user_risk_level(user_id: str) -> str:
    """Get the most recent risk level for a user from Redis progress data."""
    try:
        if _redis_client is None:
            return 'unknown'
        latest = _redis_client.lrange(f"progress:{user_id}", 0, 0)
        if latest:
            entry = json.loads(latest[0])
            return entry.get('risk_level', 'general')
    except Exception:
        pass
    return 'general'


def _was_recently_checked_in(user_id: str) -> bool:
    """Check if we already sent a proactive check-in recently."""
    try:
        if _redis_client is None:
            return False
        return bool(_redis_client.exists(f"proactive_checkin:{user_id}"))
    except Exception:
        return False


def _mark_checked_in(user_id: str) -> None:
    """Record that we sent a check-in, with cooldown TTL."""
    try:
        if _redis_client is not None:
            _redis_client.setex(
                f"proactive_checkin:{user_id}",
                CHECKIN_COOLDOWN_DAYS * 86400,
                datetime.now().isoformat(),
            )
    except Exception:
        pass


def _build_checkin_message(user_id: str, days_inactive: int, risk_level: str) -> str:
    """Build a personalized check-in message based on context."""
    if risk_level == 'high':
        return (
            "สวัสดีครับ 💚 น้องใจดีแวะมาทักทายครับ\n\n"
            f"ไม่ได้คุยกันมา {days_inactive} วันแล้ว "
            "ผมอยากรู้ว่าคุณเป็นอย่างไรบ้างครับ\n\n"
            "ถ้ากำลังเผชิญช่วงเวลาที่ยากลำบาก "
            "ผมพร้อมรับฟังและอยู่เคียงข้างคุณเสมอนะครับ\n\n"
            "📞 หากต้องการความช่วยเหลือเร่งด่วน พิมพ์ /emergency\n"
            "💬 หรือเริ่มพูดคุยกับผมได้เลยครับ"
        )
    elif risk_level == 'medium':
        return (
            "สวัสดีครับ 👋 น้องใจดีมาเช็คอินครับ\n\n"
            f"ไม่ได้คุยกันมา {days_inactive} วันแล้ว "
            "อยากรู้ว่าคุณสบายดีไหมครับ\n\n"
            "ถ้ามีอะไรอยากคุย หรือต้องการกำลังใจ "
            "ผมพร้อมอยู่ตรงนี้เสมอนะครับ 💪\n\n"
            "💡 พิมพ์ /progress เพื่อดูความก้าวหน้าของคุณ"
        )
    else:
        return (
            "สวัสดีครับ 😊 น้องใจดีมาทักทายครับ\n\n"
            f"ไม่ได้คุยกันมา {days_inactive} วันแล้ว "
            "วันนี้เป็นอย่างไรบ้างครับ\n\n"
            "ถ้าอยากคุย แชร์เรื่องราว หรือถามอะไร "
            "ผมยินดีรับฟังเสมอครับ 💚"
        )


def run_proactive_checkins() -> int:
    """Main scheduled job: find inactive users and send check-in messages.

    Returns the number of check-ins sent.
    """
    if _db_manager is None or _redis_client is None or _line_bot_api is None:
        logging.warning("Proactive check-in skipped: dependencies not initialized")
        return 0

    from linebot.models import TextSendMessage

    sent_count = 0
    logging.info("Running proactive check-in scan")

    try:
        # Phase 1: High-risk users (shorter inactivity threshold)
        high_risk_candidates = _get_inactive_users(INACTIVE_DAYS_HIGH_RISK, limit=MAX_CHECKINS_PER_RUN * 2)
        for user_id, last_active in high_risk_candidates:
            if sent_count >= MAX_CHECKINS_PER_RUN:
                break
            if _was_recently_checked_in(user_id):
                continue
            risk = _get_user_risk_level(user_id)
            if risk not in ('high', 'medium'):
                continue

            days_inactive = (datetime.now() - last_active).days
            message = _build_checkin_message(user_id, days_inactive, risk)
            try:
                _line_bot_api.push_message(user_id, TextSendMessage(text=message))
                _mark_checked_in(user_id)
                sent_count += 1
                logging.info(f"Proactive check-in sent to {user_id} (risk={risk}, inactive={days_inactive}d)")
            except Exception as e:
                logging.warning(f"Failed to send check-in to {user_id}: {e}")

        # Phase 2: Normal users (longer inactivity threshold)
        if sent_count < MAX_CHECKINS_PER_RUN:
            normal_candidates = _get_inactive_users(INACTIVE_DAYS_NORMAL, limit=MAX_CHECKINS_PER_RUN * 2)
            for user_id, last_active in normal_candidates:
                if sent_count >= MAX_CHECKINS_PER_RUN:
                    break
                if _was_recently_checked_in(user_id):
                    continue

                days_inactive = (datetime.now() - last_active).days
                risk = _get_user_risk_level(user_id)
                message = _build_checkin_message(user_id, days_inactive, risk)
                try:
                    _line_bot_api.push_message(user_id, TextSendMessage(text=message))
                    _mark_checked_in(user_id)
                    sent_count += 1
                    logging.info(f"Proactive check-in sent to {user_id} (risk={risk}, inactive={days_inactive}d)")
                except Exception as e:
                    logging.warning(f"Failed to send check-in to {user_id}: {e}")

    except Exception as e:
        logging.error(f"Error in proactive check-in run: {e}")

    logging.info(f"Proactive check-in complete: {sent_count} messages sent")
    return sent_count
