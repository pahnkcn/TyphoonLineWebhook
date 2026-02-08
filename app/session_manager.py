"""Session management utilities for the Jai Dee chatbot."""
import json
import logging
from datetime import datetime
from typing import List, Dict, Tuple
from linebot.models import TextSendMessage

redis_client = None
line_bot_api = None
token_counter = None
_config = None
SESSION_TIMEOUT = 604800

def init_session_manager(redis_instance, line_api, token_counter_instance, session_timeout: int = 604800, config=None):
    """Initialize session manager dependencies."""
    global redis_client, line_bot_api, token_counter, SESSION_TIMEOUT, _config
    redis_client = redis_instance
    line_bot_api = line_api
    token_counter = token_counter_instance
    SESSION_TIMEOUT = session_timeout
    _config = config


def get_chat_session(user_id: str) -> List[Dict[str, str]]:
    """Retrieve chat session history from Redis."""
    try:
        history = redis_client.get(f"chat_session:{user_id}")
        if history:
            loaded_history = json.loads(history)
            return [
                {"role": msg_data["role"], "content": msg_data["content"]}
                for msg_data in loaded_history
            ]
        return []
    except Exception as e:  # redis.RedisError or others
        logging.error(f"Redis error in get_chat_session: {str(e)}")
        return []


def save_chat_session(user_id: str, messages: List[Dict[str, str]]) -> None:
    """
    Save chat session history to Redis with atomic token count update.

    Uses Redis pipeline to ensure token count and session data are updated atomically.

    Args:
        user_id: LINE User ID
        messages: List of message dictionaries with 'role' and 'content'
    """
    try:
        max_messages = 100
        serialized_history = [
            {"role": msg["role"], "content": msg["content"]}
            for msg in messages[-max_messages:]
        ]

        # Calculate token count ONCE before Redis operations
        token_count = token_counter.count_message_tokens(serialized_history)

        ttl_seconds = max(int(SESSION_TIMEOUT or 0), 60)

        # Use Redis pipeline for atomic updates (CRITICAL FIX)
        # This ensures token count and session are always in sync
        pipe = redis_client.pipeline()

        if pipe is None:
            # Redis unavailable — fall back to individual (non-atomic) calls
            redis_client.setex(f"chat_session:{user_id}", ttl_seconds, json.dumps(serialized_history))
            redis_client.setex(f"session_tokens:{user_id}", ttl_seconds, str(token_count))
        else:
            pipe.setex(
                f"chat_session:{user_id}",
                ttl_seconds,
                json.dumps(serialized_history),
            )

            pipe.setex(
                f"session_tokens:{user_id}",
                ttl_seconds,
                str(token_count),
            )

            # Execute all commands atomically
            pipe.execute()

        logging.debug(
            f"บันทึกเซสชัน: {len(serialized_history)} ข้อความ, {token_count} โทเค็น "
            f"สำหรับผู้ใช้ {user_id} (atomic update)"
        )

    except Exception as e:
        logging.error(f"Redis error in save_chat_session: {str(e)}")


def check_session_timeout(user_id: str) -> bool:
    """Check whether the session has timed out."""
    try:
        last_activity = redis_client.get(f"last_activity:{user_id}")
        if last_activity:
            if isinstance(last_activity, bytes):
                last_activity = last_activity.decode("utf-8")
            last_activity_time = float(last_activity)
            if (datetime.now().timestamp() - last_activity_time) > SESSION_TIMEOUT:
                redis_client.delete(f"chat_session:{user_id}")
                return True
        return False
    except Exception as e:
        logging.error(
            f"เกิดข้อผิดพลาดในการตรวจสอบ session timeout สำหรับผู้ใช้ {user_id}: {str(e)}"
        )
        return False


def update_last_activity(user_id: str) -> None:
    """Update last activity timestamp and send timeout warnings."""
    try:
        current_time = datetime.now().timestamp()
        last_activity = redis_client.get(f"last_activity:{user_id}")
        warning_sent = redis_client.get(f"timeout_warning:{user_id}")

        if isinstance(last_activity, bytes):
            last_activity = last_activity.decode("utf-8")
        if isinstance(warning_sent, bytes):
            warning_sent = warning_sent.decode("utf-8")

        if last_activity:
            time_passed = current_time - float(last_activity)
            if time_passed > (SESSION_TIMEOUT - 86400) and not warning_sent:
                warning_message = (
                    "⚠️ เซสชันของคุณจะหมดอายุในอีก 1 วัน\n"
                    "หากต้องการคุยต่อ กรุณาพิมพ์ข้อความใดๆ เพื่อต่ออายุเซสชัน"
                )
                line_bot_api.push_message(user_id, TextSendMessage(text=warning_message))
                redis_client.setex(
                    f"timeout_warning:{user_id}",
                    86400,
                    "1",
                )
                logging.info(f"ส่งการแจ้งเตือนหมดเวลาเซสชันไปยังผู้ใช้: {user_id}")

        redis_client.setex(
            f"last_activity:{user_id}",
            SESSION_TIMEOUT,
            str(current_time),
        )
    except Exception as e:
        logging.error(
            f"เกิดข้อผิดพลาดในการอัพเดทเวลาใช้งานล่าสุดสำหรับผู้ใช้ {user_id}: {str(e)}"
        )


def get_session_token_count(user_id: str) -> int:
    """
    Get token usage for the current session with cache fallback.

    Improved version with proper TTL synchronization and error handling.

    Args:
        user_id: LINE User ID

    Returns:
        Token count for the session (0 if error or no session)
    """
    try:
        # Try cached count first (fast path)
        cached_count = redis_client.get(f"session_tokens:{user_id}")
        if cached_count:
            if isinstance(cached_count, bytes):
                cached_count = cached_count.decode("utf-8")
            return int(cached_count)

        # Cache miss - recalculate from session data
        session_data = redis_client.get(f"chat_session:{user_id}")
        if not session_data:
            return 0

        if isinstance(session_data, bytes):
            session_data = session_data.decode("utf-8")

        messages = json.loads(session_data)
        token_count = token_counter.count_message_tokens(messages)

        # Update cache with SAME TTL as session (CRITICAL: synchronization)
        ttl = redis_client.ttl(f"chat_session:{user_id}")
        if ttl > 0:
            # Session exists and has valid TTL
            redis_client.setex(
                f"session_tokens:{user_id}",
                ttl,  # Use session's remaining TTL
                str(token_count)
            )
        else:
            # Fallback to default TTL if session TTL couldn't be retrieved
            ttl_seconds = max(int(SESSION_TIMEOUT or 0), 60)
            redis_client.setex(
                f"session_tokens:{user_id}",
                ttl_seconds,
                str(token_count)
            )

        logging.debug(
            f"Token count recalculated for {user_id}: {token_count} tokens, TTL={ttl}s"
        )

        return token_count

    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการคำนวณโทเค็นของเซสชัน: {str(e)}")
        return 0


def is_important_message(user_message: str, bot_response: str, risk_level: str = None) -> bool:
    """Determine if a message pair is important.

    ใช้ risk_level จาก assess_risk() เป็นตัวตัดสินหลัก:
    - risk_level == 'high' หรือ 'medium' → สำคัญ (ยกเว้น general)
    - ข้อความยาวผิดปกติ → สำคัญ (บ่งบอกว่าผู้ใช้เปิดเผยเรื่องสำคัญ)

    เมื่อ risk_level ถูกส่งมา จะไม่ใช้ keyword list แยกอีกต่อไป
    (keyword list เดิมถูกรวมเข้า RISK_KEYWORDS ใน risk_assessment.py แล้ว)
    """
    if risk_level is not None:
        if risk_level in ('high', 'medium'):
            return True
        if len(user_message) > 300 or len(bot_response) > 500:
            return True
        return False
    if len(user_message) > 300 or len(bot_response) > 500:
        return True
    return False


def hybrid_context_management(user_id: str, token_threshold: int) -> List[Dict[str, str]]:
    """Manage conversation history to fit within the context window."""
    try:
        current_history = get_chat_session(user_id)
        if not current_history:
            return []
        current_tokens = get_session_token_count(user_id)
        if current_tokens < token_threshold:
            return current_history
        logging.info(
            f"เซสชันใกล้เต็ม context window ({current_tokens} tokens) สำหรับผู้ใช้ {user_id}, กำลังจัดการประวัติ..."
        )
        keep_recent = 30
        if len(current_history) <= keep_recent * 2:
            return current_history
        recent_messages = current_history[-keep_recent*2:]
        older_messages = current_history[:-keep_recent*2]
        if older_messages:
            important_pairs = []
            normal_pairs = []
            for i in range(0, len(older_messages), 2):
                if i+1 < len(older_messages):
                    user_msg = older_messages[i].get("content", "")
                    bot_resp = older_messages[i+1].get("content", "")
                    if is_important_message(user_msg, bot_resp):
                        important_pairs.append((user_msg, bot_resp))
                    else:
                        normal_pairs.append((user_msg, bot_resp))
            important_messages = []
            for user_msg, bot_resp in important_pairs:
                important_messages.append({"role": "user", "content": user_msg})
                important_messages.append({"role": "assistant", "content": bot_resp})
            formatted_normal = []
            for i, (user_msg, bot_resp) in enumerate(normal_pairs):
                formatted_normal.append((i, user_msg, bot_resp))
            summary = ""
            if formatted_normal:
                from .services.context_manager import summarize_conversation_history as _summarize
                summary = _summarize(formatted_normal, _config)
            new_history = []
            if summary:
                # ใช้ role พิเศษสำหรับการสรุปที่ไม่แสดงให้ผู้ใช้เห็น
                new_history.append({"role": "system_summary", "content": f"สรุปการสนทนาก่อนหน้า: {summary}"})
            new_history.extend(important_messages)
            new_history.extend(recent_messages)
            save_chat_session(user_id, new_history)
            return new_history
        return current_history
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการจัดการประวัติ: {str(e)}")
        return current_history


def generate_contextual_followup_message(user_id: str, db, config):
    """สร้างข้อความติดตามที่เป็นไปตามบริบทของการสนทนาล่าสุดโดยใช้ xAI Grok"""
    from .utils import safe_api_call, clean_ai_response
    from .llm import grok_client
    
    try:
        # ดึงประวัติการสนทนาล่าสุด โดยใช้ max_tokens แทน limit
        # ปรับให้สมดุลระหว่างบริบทและประสิทธิภาพ
        recent_history = db.get_user_history(user_id, max_tokens=100000)
        
        # ถ้าไม่มีประวัติการสนทนา ใช้ข้อความติดตามทั่วไป
        if not recent_history:
            return get_default_followup_message()
        
        # จำกัดให้แสดงเฉพาะ 20 ข้อความล่าสุด
        # เนื่องจาก get_user_history เรียงจากใหม่ไปเก่า เราต้องจัดเรียงใหม่
        if len(recent_history) > 20:
            recent_history = recent_history[:20]
        
        # เรียงจากเก่าไปใหม่เพื่อให้ AI เข้าใจบริบทที่ถูกต้อง
        recent_history = list(reversed(recent_history))
        
        # สร้างบริบทการสนทนาสำหรับ Grok แบบมีโครงสร้าง
        conversation_context = ""
        total_messages = len(recent_history)
        
        for i, (_, user_msg, bot_resp) in enumerate(recent_history):
            # เพิ่มหมายเลขลำดับเพื่อให้ AI เข้าใจความต่อเนื่อง
            msg_number = i + 1
            conversation_context += f"[{msg_number}/{total_messages}] ผู้ใช้: {user_msg}\n[{msg_number}/{total_messages}] ใจดี: {bot_resp}\n\n"
        
        # สร้าง prompt พร้อมบริบทที่ดีขึ้นสำหรับ Grok
        followup_prompt = f"""
ต่อไปนี้คือประวัติการสนทนาล่าสุดระหว่างผู้ใช้และแชทบอท "ใจดี" ที่ช่วยเหลือคนเลิกสารเสพติด (รวม {total_messages} คู่ข้อความ):

{conversation_context}

จากประวัติการสนทนาข้างต้น โปรดสร้างข้อความติดตามผลที่:
1. อ้างอิงถึงหัวข้อ ปัญหา หรือความคืบหน้าที่เราพูดคุยกันล่าสุด
2. แสดงความห่วงใยและเป็นการติดตามอย่างต่อเนื่อง
3. ใช้ข้อมูลจากการสนทนาเพื่อสร้างความเชื่อมโยงที่เป็นธรรมชาติ
4. ถามถึงสถานการณ์ปัจจุบันหรือความรู้สึกในช่วงที่ผ่านมา
5. มีความยาวประมาณ 2-4 ประโยค
6. ใช้ภาษาไทยที่อบอุ่นและเข้าใจง่าย
7. เริ่มต้นด้วยคำทักทายที่เหมาะสม

โปรดสร้างข้อความติดตามที่แสดงให้เห็นว่าคุณจำและเข้าใจบริบทของการสนทนาก่อนหน้า:
"""

        # เรียกใช้ xAI Grok API ด้วยการตั้งค่าที่เหมาะสม
        text = grok_client.send_chat(
            messages=[
                {"role": "system", "content": "คุณคือแชทบอท 'ใจดี' ที่ช่วยเหลือคนเลิกสารเสพติดด้วยความเข้าใจและเป็นมิตร คุณสามารถจำและอ้างอิงถึงการสนทนาก่อนหน้าได้"},
                {"role": "user", "content": followup_prompt}
            ],
            model=config.XAI_MODEL,
            temperature=0.75,  # เพิ่มจาก 0.6 → ความเป็นธรรมชาติมากขึ้น
            max_tokens=800,    # เพิ่มจาก 400 → พื้นที่เพียงพอ
            top_p=0.9,         # เพิ่มจาก 0.85 → หลากหลายมากขึ้น
        )
        
        # ตรวจสอบและทำความสะอาดผลลัพธ์
        if text:
            followup_message = text.strip()
            
            # ทำความสะอาดข้อความ
            followup_message = clean_ai_response(followup_message)
            
            # ตรวจสอบความยาวและเนื้อหา
            if len(followup_message) > 30 and len(followup_message) < 600:
                logging.info(f"Successfully generated contextual follow-up message for user {user_id}: {len(followup_message)} characters")
                return followup_message
            else:
                logging.warning(f"AI generated follow-up message length is outside acceptable range: {len(followup_message)} characters")
        
        # ถ้า AI response ไม่เหมาะสม ใช้ข้อความทั่วไป
        return get_fallback_followup_message()
            
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการสร้างข้อความติดตามด้วย AI: {str(e)}")
        # ถ้าเกิดข้อผิดพลาด ใช้ข้อความติดตามทั่วไป
        return get_default_followup_message()


def get_default_followup_message():
    """ข้อความติดตามทั่วไปสำหรับผู้ใช้ที่ไม่มีประวัติการสนทนา"""
    return (
        "สวัสดีครับ ใจดีมาติดตามผลการเลิกใช้สารเสพติดของคุณ\n"
        "คุณสามารถเล่าให้ฟังได้ว่าช่วงที่ผ่านมาเป็นอย่างไรบ้าง?"
    )


def get_fallback_followup_message():
    """ข้อความติดตามสำรองเมื่อ AI ตอบไม่เหมาะสม"""
    return (
        "สวัสดีครับ ใจดีมาติดตามความเป็นไปนะครับ 😊\n\n"
        "ช่วงที่ผ่านมาคุณเป็นอย่างไรบ้างครับ? มีอะไรที่อยากแชร์ให้ฟังไหม?\n"
        "ใจดีพร้อมฟังและให้กำลังใจคุณเสมอนะครับ 💚"
    )
