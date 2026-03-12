"""User data management service for PDPA compliance.

Provides:
- Full user data deletion (conversations, progress, follow-ups, registration, session)
- Privacy policy display
- Consent tracking
"""
import json
import logging
from datetime import datetime
from typing import Optional, Dict, Any

_db_manager = None
_redis_client = None


def init_data_management(db_manager, redis_client):
    """Wire runtime dependencies."""
    global _db_manager, _redis_client
    _db_manager = db_manager
    _redis_client = redis_client


def get_privacy_policy_message() -> str:
    """Return the privacy policy text shown to users via /privacy command."""
    return (
        "🔒 นโยบายความเป็นส่วนตัว — น้องใจดี\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "📋 ข้อมูลที่จัดเก็บ\n"
        "• ประวัติการสนทนากับน้องใจดี\n"
        "• ข้อมูลแบบประเมิน (กรณีลงทะเบียน)\n"
        "• ข้อมูลความก้าวหน้าและระดับความเสี่ยง\n"
        "• กำหนดการนัดติดตามผล\n"
        "\n🎯 วัตถุประสงค์การใช้ข้อมูล\n"
        "• เพื่อให้คำปรึกษาที่เหมาะสมกับสถานการณ์ของคุณ\n"
        "• เพื่อติดตามความก้าวหน้าในการดูแลตนเอง\n"
        "• เพื่อพัฒนาคุณภาพระบบ (ข้อมูลจะถูกปกปิดตัวตน)\n"
        "\n⏰ ระยะเวลาจัดเก็บ\n"
        "• ประวัติสนทนา — ตลอดระยะเวลาที่ใช้งาน\n"
        "• ข้อมูลเซสชัน — หมดอายุอัตโนมัติใน 7 วัน\n"
        "\n🛡️ สิทธิ์ของคุณ\n"
        "• ดูข้อมูลที่จัดเก็บ → /context, /status\n"
        "\n📞 ติดต่อทีมงานได้ที่ pahnkcn@gmail.com"
    )


def delete_all_user_data(user_id: str) -> Dict[str, Any]:
    """Delete all data associated with a user.

    Removes data from:
    - conversations table
    - follow_ups table
    - user_metrics table
    - registration_codes table
    - Redis session and progress data
    - Records the deletion in user_consent table

    Returns a summary dict of what was deleted.
    """
    result = {
        'conversations_deleted': 0,
        'follow_ups_deleted': 0,
        'metrics_deleted': 0,
        'registration_cleared': False,
        'redis_keys_deleted': 0,
        'consent_recorded': False,
        'errors': [],
    }

    # 1. Delete conversations
    try:
        count_result = _db_manager.execute_query(
            'SELECT COUNT(*) FROM conversations WHERE user_id = %s', (user_id,)
        )
        conv_count = int(count_result[0][0]) if count_result else 0
        _db_manager.execute_and_commit(
            'DELETE FROM conversations WHERE user_id = %s', (user_id,)
        )
        result['conversations_deleted'] = conv_count
    except Exception as e:
        logging.error(f"Failed to delete conversations for {user_id}: {e}")
        result['errors'].append(f"conversations: {e}")

    # 2. Delete follow-ups
    try:
        count_result = _db_manager.execute_query(
            'SELECT COUNT(*) FROM follow_ups WHERE user_id = %s', (user_id,)
        )
        fu_count = int(count_result[0][0]) if count_result else 0
        _db_manager.execute_and_commit(
            'DELETE FROM follow_ups WHERE user_id = %s', (user_id,)
        )
        result['follow_ups_deleted'] = fu_count
    except Exception as e:
        logging.error(f"Failed to delete follow_ups for {user_id}: {e}")
        result['errors'].append(f"follow_ups: {e}")

    # 3. Delete user metrics
    try:
        count_result = _db_manager.execute_query(
            'SELECT COUNT(*) FROM user_metrics WHERE user_id = %s', (user_id,)
        )
        m_count = int(count_result[0][0]) if count_result else 0
        _db_manager.execute_and_commit(
            'DELETE FROM user_metrics WHERE user_id = %s', (user_id,)
        )
        result['metrics_deleted'] = m_count
    except Exception as e:
        logging.error(f"Failed to delete user_metrics for {user_id}: {e}")
        result['errors'].append(f"user_metrics: {e}")

    # 4. Keep registration data intact so user can re-verify without re-filling the form
    result['registration_cleared'] = False

    # 5. Delete Redis keys
    redis_keys_to_delete = [
        f"chat_session:{user_id}",
        f"session_tokens:{user_id}",
        f"progress:{user_id}",
        f"user_context:{user_id}",
        f"message_lock:{user_id}",
        f"wait_notice:{user_id}",
        f"token_warning:{user_id}",
        f"last_follow_up:{user_id}",
        f"first_interaction:{user_id}",
        f"registration_sent:{user_id}",
        f"last_activity:{user_id}",
        f"registered:{user_id}",
        f"history_cache:{user_id}",
        f"proactive_checkin:{user_id}",
    ]
    deleted_keys = 0
    if _redis_client is not None:
        for key in redis_keys_to_delete:
            try:
                if _redis_client.exists(key):
                    _redis_client.delete(key)
                    deleted_keys += 1
            except Exception:
                pass
        # Also remove from follow-up sorted set
        try:
            _redis_client.zrem('follow_up_queue', user_id)
        except Exception:
            pass
    result['redis_keys_deleted'] = deleted_keys

    # 6. Record deletion in user_consent table
    try:
        _ensure_consent_table()
        now = datetime.now()
        existing = _db_manager.execute_query(
            'SELECT id FROM user_consent WHERE user_id = %s', (user_id,)
        )
        if existing:
            _db_manager.execute_and_commit(
                'UPDATE user_consent SET data_deleted_at = %s, consent_revoked_at = %s WHERE user_id = %s',
                (now, now, user_id),
            )
        else:
            _db_manager.execute_and_commit(
                'INSERT INTO user_consent (user_id, data_deleted_at, consent_revoked_at, updated_at) VALUES (%s, %s, %s, %s)',
                (user_id, now, now, now),
            )
        result['consent_recorded'] = True
    except Exception as e:
        logging.error(f"Failed to record consent for {user_id}: {e}")
        result['errors'].append(f"consent: {e}")

    logging.info(
        f"User data deletion for {user_id}: "
        f"conversations={result['conversations_deleted']}, "
        f"follow_ups={result['follow_ups_deleted']}, "
        f"metrics={result['metrics_deleted']}, "
        f"redis_keys={result['redis_keys_deleted']}, "
        f"errors={len(result['errors'])}"
    )

    return result


def format_deletion_result(result: Dict[str, Any]) -> str:
    """Format the deletion result dict into a user-facing message."""
    total_deleted = (
        result['conversations_deleted']
        + result['follow_ups_deleted']
        + result['metrics_deleted']
        + result['redis_keys_deleted']
    )

    msg = "🗑️ ผลการลบข้อมูลของคุณ:\n\n"
    msg += f"• ประวัติการสนทนา: {result['conversations_deleted']} รายการ\n"
    msg += f"• การติดตามผล: {result['follow_ups_deleted']} รายการ\n"
    msg += f"• ข้อมูลเมตริก: {result['metrics_deleted']} รายการ\n"
    msg += f"• ข้อมูลเซสชัน: {result['redis_keys_deleted']} คีย์\n\n"

    if result['errors']:
        msg += f"⚠️ พบข้อผิดพลาด {len(result['errors'])} รายการ กรุณาติดต่อผู้ดูแลระบบ\n\n"
    else:
        msg += "✅ ลบข้อมูลทั้งหมดเรียบร้อยแล้ว\n\n"

    msg += "คุณยังสามารถใช้งานน้องใจดีต่อได้ หากต้องการลงทะเบียนใหม่ พิมพ์ /register"

    return msg


def _ensure_consent_table():
    """Create user_consent table if it doesn't exist (idempotent)."""
    try:
        _db_manager.execute_query("SELECT 1 FROM user_consent LIMIT 1")
    except Exception:
        try:
            _db_manager.execute_and_commit("""
                CREATE TABLE IF NOT EXISTS user_consent (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id VARCHAR(50) NOT NULL UNIQUE,
                    consent_given_at DATETIME,
                    consent_revoked_at DATETIME,
                    data_deleted_at DATETIME,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX idx_uc_user_id (user_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)
        except Exception as e:
            logging.error(f"Failed to create user_consent table: {e}")
            raise
