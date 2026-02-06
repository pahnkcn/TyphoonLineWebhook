"""
Consolidated context and summarization manager.

Provides a single entry point for conversation history optimization,
summarization, and context window management. Replaces the previously
scattered summarization logic across app_main.py and session_manager.py.
"""
import logging
from typing import List, Dict, Tuple, Optional, Set, Any

from ..config import (
    SYSTEM_MESSAGE_SUMMARY,
    SUMMARY_GENERATION_CONFIG,
    TOKEN_THRESHOLD,
)
from ..llm import grok_client
from ..session_manager import (
    get_chat_session,
    save_chat_session,
    get_session_token_count,
    is_important_message,
)


def chunk_conversation_history(history: List[Tuple], chunk_size: int = 10) -> List[List[Tuple]]:
    """แบ่งประวัติการสนทนาเป็นส่วนๆ (chunks) เพื่อการสรุปที่มีประสิทธิภาพ

    Args:
        history: ประวัติการสนทนา [(id, user_msg, bot_resp), ...]
        chunk_size: ขนาดของแต่ละส่วน

    Returns:
        รายการของส่วนประวัติการสนทนา
    """
    return [history[i:i + chunk_size] for i in range(0, len(history), chunk_size)]


def summarize_conversation_chunk(chunk: List[Tuple], config: Any) -> str:
    """สรุปส่วนของประวัติการสนทนา

    Args:
        chunk: ส่วนของประวัติการสนทนา [(id, user_msg, bot_resp), ...]
        config: Application config object (needs XAI_MODEL)

    Returns:
        ข้อความสรุป
    """
    if not chunk:
        return ""

    try:
        conversation_text = ""
        for _, msg, resp in chunk:
            conversation_text += f"ผู้ใช้: {msg}\nบอท: {resp}\n\n"

        summary_prompt = f"""
โปรดสรุปประวัติการสนทนาต่อไปนี้โดยเน้นประเด็นสำคัญตามหลัก Motivational Interviewing:

{conversation_text}

กรุณาสรุปโดยครอบคลุม:
1. **ปัญหาหลัก**: สารเสพติดที่ใช้ และปัญหาที่เกี่ยวข้อง
2. **ระยะของการเปลี่ยนแปลง**: Precontemplation / Contemplation / Preparation / Action / Maintenance
3. **Change Talk**: ความปรารถนา ความสามารถ เหตุผล ความจำเป็น ความมุ่งมั่น การลงมือ (DARN-CAT)
4. **อุปสรรคหลัก**: สิ่งที่ขัดขวางการเปลี่ยนแปลง
5. **ความคืบหน้า**: ความสำเร็จหรือการกลับไปเสพซ้ำ (ถ้ามี)

**ไม่ต้องมีคำนำหรือคำอธิบายวิธีการสรุป เริ่มต้นเนื้อหาสรุปเลยทันที**
"""

        text = grok_client.send_chat(
            messages=[
                SYSTEM_MESSAGE_SUMMARY,
                {"role": "user", "content": summary_prompt}
            ],
            model=config.XAI_MODEL,
            **SUMMARY_GENERATION_CONFIG,
        )

        return text
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดใน summarize_conversation_chunk: {str(e)}")
        return ""


def summarize_conversation_history(history: List[Tuple], config: Any) -> str:
    """สรุปประวัติการสนทนาให้กระชับ โดยมีการจัดการขนาด

    Args:
        history: รายการประวัติการสนทนา [(id, user_msg, bot_resp), ...]
        config: Application config object (needs XAI_MODEL)

    Returns:
        ข้อความสรุป
    """
    if not history:
        return ""

    try:
        if len(history) > 20:
            chunks = chunk_conversation_history(history, chunk_size=10)
            summaries = []

            for chunk in chunks:
                chunk_summary = summarize_conversation_chunk(chunk, config)
                if chunk_summary:
                    summaries.append(chunk_summary)

            if summaries:
                combined_summary = "\n".join([f"• {summary}" for summary in summaries])
                return combined_summary

        conversation_text = ""
        for _, msg, resp in history:
            conversation_text += f"ผู้ใช้: {msg}\nบอท: {resp}\n\n"

        summary_prompt = f"""
โปรดสรุปประวัติการสนทนาต่อไปนี้โดยเน้นประเด็นสำคัญตามหลัก Motivational Interviewing:

{conversation_text}

กรุณาสรุปโดยครอบคลุม:
1. **ปัญหาหลัก**: สารเสพติดที่ใช้ และปัญหาที่เกี่ยวข้อง
2. **ระยะของการเปลี่ยนแปลง**: Precontemplation / Contemplation / Preparation / Action / Maintenance
3. **Change Talk**: ความปรารถนา ความสามารถ เหตุผล ความจำเป็น ความมุ่งมั่น การลงมือ (DARN-CAT)
4. **อุปสรรคหลัก**: สิ่งที่ขัดขวางการเปลี่ยนแปลง
5. **ความคืบหน้า**: ความสำเร็จหรือการกลับไปเสพซ้ำ (ถ้ามี)

ให้สรุปแบบครอบคลุมประเด็นสำคัญทั้งหมด:
"""

        text = grok_client.send_chat(
            messages=[
                SYSTEM_MESSAGE_SUMMARY,
                {"role": "user", "content": summary_prompt}
            ],
            model=config.XAI_MODEL,
            **SUMMARY_GENERATION_CONFIG,
        )

        return text
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดใน summarize_conversation_history: {str(e)}")
        return ""


def summarize_by_topic(history: List[Tuple], config: Any) -> str:
    """สรุปประวัติการสนทนาแบ่งตามหัวข้อ

    Args:
        history: รายการประวัติการสนทนา [(id, user_msg, bot_resp), ...]
        config: Application config object (needs XAI_MODEL)

    Returns:
        ข้อความสรุปแบ่งตามหัวข้อ
    """
    if not history:
        return ""

    try:
        conversation_text = ""
        for _, msg, resp in history:
            conversation_text += f"ผู้ใช้: {msg}\nบอท: {resp}\n\n"

        topic_prompt = f"""
นี่คือประวัติการสนทนาระหว่างผู้ใช้และบอทเกี่ยวกับการเลิกสารเสพติด:

{conversation_text}

โปรดวิเคราะห์และแบ่งแยกหัวข้อสำคัญต่างๆ ในการสนทนานี้ พร้อมทั้งสรุปแต่ละหัวข้อ ตามรูปแบบนี้:
1. [ชื่อหัวข้อ 1]: [สรุป]
2. [ชื่อหัวข้อ 2]: [สรุป]
...

แต่ละหัวข้อควรครอบคลุมประเด็นสำคัญที่พูดถึงโดยมีใจความชัดเจน กระชับ และเก็บรายละเอียดสำคัญไว้
"""

        text = grok_client.send_chat(
            messages=[
                SYSTEM_MESSAGE_SUMMARY,
                {"role": "user", "content": topic_prompt}
            ],
            model=config.XAI_MODEL,
            temperature=0.2,
            max_tokens=1500,
        )

        return text
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดใน summarize_by_topic: {str(e)}")
        return ""


def filter_messages_for_api(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """กรองข้อความที่มี role เป็น 'system_summary' ออกจากการส่งไปยัง API
    แต่ยังคงไว้ในระบบเพื่อให้ AI เข้าใจบริบท

    Args:
        messages: รายการข้อความ

    Returns:
        ข้อความที่กรองแล้ว
    """
    filtered_messages = []
    summary_content = ""

    for message in messages:
        if message.get('role') == 'system_summary':
            summary_content += message.get('content', '') + "\n\n"
        else:
            filtered_messages.append(message)

    if summary_content.strip():
        system_msg_found = False
        for i, msg in enumerate(filtered_messages):
            if msg.get('role') == 'system':
                filtered_messages[i] = {
                    'role': 'system',
                    'content': msg.get('content', '') + "\n\nข้อมูลสำคัญเพิ่มเติม (สำหรับ AI เท่านั้น):\n" + summary_content.strip()
                }
                system_msg_found = True
                break

        if not system_msg_found:
            filtered_messages.insert(0, {
                'role': 'system',
                'content': "ข้อมูลสำคัญเพิ่มเติม (สำหรับ AI เท่านั้น):\n" + summary_content.strip()
            })

    return filtered_messages


def optimize_context(
    user_id: str,
    config: Any,
    db: Any,
    max_tokens: int = 450000,
    keep_recent: int = 30,
) -> List[Dict[str, str]]:
    """Single entry point for conversation context optimization.

    Checks current session token count, and if it exceeds the threshold,
    keeps important + recent messages and summarizes the rest.

    Args:
        user_id: LINE User ID
        config: Application config object
        db: ChatHistoryDB instance
        max_tokens: Maximum tokens to target
        keep_recent: Number of recent message pairs to always keep

    Returns:
        Optimized message list for the conversation
    """
    try:
        session_tokens = get_session_token_count(user_id)
        current_history = get_chat_session(user_id) or []

        if session_tokens < max_tokens:
            return current_history

        logging.info(
            f"เซสชันใกล้เต็ม context window ({session_tokens} tokens) สำหรับผู้ใช้ {user_id}, กำลังจัดการประวัติ..."
        )

        if len(current_history) <= keep_recent * 2:
            return current_history

        recent_messages = current_history[-keep_recent * 2:]
        older_messages = current_history[:-keep_recent * 2]

        # แยกข้อความสำคัญออกจากข้อความปกติ
        important_messages: List[Dict[str, str]] = []
        normal_pairs: List[Tuple] = []

        for i in range(0, len(older_messages), 2):
            if i + 1 < len(older_messages):
                user_msg = older_messages[i].get("content", "")
                bot_resp = older_messages[i + 1].get("content", "")
                if is_important_message(user_msg, bot_resp):
                    important_messages.append({"role": "user", "content": user_msg})
                    important_messages.append({"role": "assistant", "content": bot_resp})
                else:
                    normal_pairs.append((len(normal_pairs), user_msg, bot_resp))

        # สรุปข้อความปกติ
        summary = ""
        if normal_pairs:
            summary = summarize_conversation_history(normal_pairs, config)

        # รวมประวัติใหม่
        new_history: List[Dict[str, str]] = []
        if summary:
            new_history.append({
                "role": "system_summary",
                "content": f"สรุปการสนทนาก่อนหน้า: {summary}",
            })
        new_history.extend(important_messages)
        new_history.extend(recent_messages)

        save_chat_session(user_id, new_history)
        return new_history

    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการปรับปรุงประวัติ: {str(e)}")
        return get_chat_session(user_id) or []
