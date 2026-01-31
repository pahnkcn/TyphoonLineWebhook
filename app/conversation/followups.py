"""
Follow-up Manager module for the 'ใจดี' chatbot.
Handles follow-up scheduling and generation.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass
from enum import Enum


class FollowUpStatus(Enum):
    """Status of follow-up operations."""
    SCHEDULED = "scheduled"
    SENT = "sent"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class FollowUpInfo:
    """Information about a scheduled follow-up."""
    user_id: str
    scheduled_time: datetime
    interval_days: int
    status: FollowUpStatus
    last_sent: Optional[datetime] = None


# Default follow-up intervals in days
DEFAULT_FOLLOW_UP_INTERVALS = [1, 3, 7, 14, 30]


class FollowUpManager:
    """
    Manages follow-up scheduling and message generation.
    
    Responsibilities:
    - Schedule follow-ups based on user activity
    - Generate contextual follow-up messages
    - Track follow-up history
    - Manage follow-up queue
    """
    
    def __init__(
        self,
        redis_client: Any,
        db: Any,
        line_bot_api: Any,
        config: Any,
        intervals: Optional[List[int]] = None,
    ):
        """
        Initialize the follow-up manager.
        
        Args:
            redis_client: Redis client instance
            db: Database instance
            line_bot_api: LINE Bot API instance
            config: Application config
            intervals: Follow-up intervals in days
        """
        self.redis_client = redis_client
        self.db = db
        self.line_bot_api = line_bot_api
        self.config = config
        self.intervals = intervals or DEFAULT_FOLLOW_UP_INTERVALS
    
    def schedule_follow_up(
        self,
        user_id: str,
        interval_index: int = 0,
    ) -> Optional[FollowUpInfo]:
        """
        Schedule a follow-up for a user.
        
        Args:
            user_id: LINE User ID
            interval_index: Index into the intervals list
            
        Returns:
            FollowUpInfo if scheduled, None if failed
        """
        try:
            if interval_index >= len(self.intervals):
                logging.info(f"No more follow-up intervals for user {user_id}")
                return None
            
            interval_days = self.intervals[interval_index]
            scheduled_time = datetime.now() + timedelta(days=interval_days)
            
            # Store in Redis sorted set (score = timestamp)
            self.redis_client.zadd(
                'follow_up_queue',
                {user_id: scheduled_time.timestamp()}
            )
            
            # Store interval index for next scheduling
            self.redis_client.set(
                f"follow_up_interval:{user_id}",
                str(interval_index)
            )
            
            logging.info(
                f"Scheduled follow-up for user {user_id} "
                f"in {interval_days} days at {scheduled_time}"
            )
            
            return FollowUpInfo(
                user_id=user_id,
                scheduled_time=scheduled_time,
                interval_days=interval_days,
                status=FollowUpStatus.SCHEDULED,
            )
            
        except Exception as e:
            logging.error(f"Failed to schedule follow-up for {user_id}: {e}")
            return None
    
    def cancel_follow_up(self, user_id: str) -> bool:
        """
        Cancel a scheduled follow-up for a user.
        
        Args:
            user_id: LINE User ID
            
        Returns:
            True if cancelled, False if not found or failed
        """
        try:
            removed = self.redis_client.zrem('follow_up_queue', user_id)
            if removed:
                logging.info(f"Cancelled follow-up for user {user_id}")
            return removed > 0
        except Exception as e:
            logging.error(f"Failed to cancel follow-up for {user_id}: {e}")
            return False
    
    def reschedule_follow_up(self, user_id: str) -> Optional[FollowUpInfo]:
        """
        Reschedule follow-up to next interval after user activity.
        
        Args:
            user_id: LINE User ID
            
        Returns:
            FollowUpInfo if rescheduled, None if failed
        """
        try:
            # Get current interval index
            interval_str = self.redis_client.get(f"follow_up_interval:{user_id}")
            current_index = int(interval_str) if interval_str else 0
            
            # Move to next interval
            next_index = min(current_index + 1, len(self.intervals) - 1)
            
            return self.schedule_follow_up(user_id, next_index)
            
        except Exception as e:
            logging.error(f"Failed to reschedule follow-up for {user_id}: {e}")
            return None
    
    def reset_follow_up_schedule(self, user_id: str) -> Optional[FollowUpInfo]:
        """
        Reset follow-up schedule to first interval (after significant activity).
        
        Args:
            user_id: LINE User ID
            
        Returns:
            FollowUpInfo if reset, None if failed
        """
        return self.schedule_follow_up(user_id, interval_index=0)
    
    def get_follow_up_status(self, user_id: str) -> Optional[FollowUpInfo]:
        """
        Get the current follow-up status for a user.
        
        Args:
            user_id: LINE User ID
            
        Returns:
            FollowUpInfo if found, None otherwise
        """
        try:
            score = self.redis_client.zscore('follow_up_queue', user_id)
            if score is None:
                return None
            
            scheduled_time = datetime.fromtimestamp(score)
            
            interval_str = self.redis_client.get(f"follow_up_interval:{user_id}")
            interval_index = int(interval_str) if interval_str else 0
            interval_days = self.intervals[interval_index] if interval_index < len(self.intervals) else 0
            
            last_sent_str = self.redis_client.get(f"last_follow_up:{user_id}")
            last_sent = datetime.fromisoformat(last_sent_str) if last_sent_str else None
            
            return FollowUpInfo(
                user_id=user_id,
                scheduled_time=scheduled_time,
                interval_days=interval_days,
                status=FollowUpStatus.SCHEDULED,
                last_sent=last_sent,
            )
            
        except Exception as e:
            logging.error(f"Failed to get follow-up status for {user_id}: {e}")
            return None
    
    def get_due_follow_ups(self, limit: int = 100) -> List[str]:
        """
        Get list of user IDs with follow-ups due now.
        
        Args:
            limit: Maximum number of users to return
            
        Returns:
            List of user IDs with due follow-ups
        """
        try:
            now = datetime.now().timestamp()
            
            # Get users with scheduled time <= now
            due_users = self.redis_client.zrangebyscore(
                'follow_up_queue',
                '-inf',
                now,
                start=0,
                num=limit
            )
            
            return due_users or []
            
        except Exception as e:
            logging.error(f"Failed to get due follow-ups: {e}")
            return []
    
    def generate_follow_up_message(
        self,
        user_id: str,
        grok_client: Any,
        clean_ai_response: Callable,
    ) -> str:
        """
        Generate a contextual follow-up message for a user.
        
        Args:
            user_id: LINE User ID
            grok_client: Grok LLM client
            clean_ai_response: Function to clean AI response
            
        Returns:
            Generated follow-up message
        """
        try:
            # Get recent conversation history
            recent_history = self.db.get_user_history(user_id, max_tokens=100000)
            
            if not recent_history:
                return self._get_default_followup_message()
            
            # Limit to 20 most recent messages
            if len(recent_history) > 20:
                recent_history = recent_history[:20]
            
            # Reverse to chronological order
            recent_history = list(reversed(recent_history))
            
            # Build conversation context
            conversation_context = ""
            total_messages = len(recent_history)
            
            for i, (_, user_msg, bot_resp) in enumerate(recent_history):
                msg_number = i + 1
                conversation_context += (
                    f"[{msg_number}/{total_messages}] ผู้ใช้: {user_msg}\n"
                    f"[{msg_number}/{total_messages}] ใจดี: {bot_resp}\n\n"
                )
            
            # Build prompt
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

            # Call Grok API
            text = grok_client.send_chat(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "คุณคือแชทบอท 'ใจดี' ที่ช่วยเหลือคนเลิกสารเสพติด"
                            "ด้วยความเข้าใจและเป็นมิตร "
                            "คุณสามารถจำและอ้างอิงถึงการสนทนาก่อนหน้าได้"
                        )
                    },
                    {"role": "user", "content": followup_prompt}
                ],
                model=self.config.XAI_MODEL,
                temperature=0.75,
                max_tokens=800,
                top_p=0.9,
            )
            
            if text:
                followup_message = text.strip()
                followup_message = clean_ai_response(followup_message)
                
                # Validate length
                if 30 < len(followup_message) < 600:
                    logging.info(
                        f"Generated contextual follow-up for user {user_id}: "
                        f"{len(followup_message)} characters"
                    )
                    return followup_message
                else:
                    logging.warning(
                        f"Follow-up message length outside range: "
                        f"{len(followup_message)} characters"
                    )
            
            return self._get_fallback_followup_message()
            
        except Exception as e:
            logging.error(f"Failed to generate follow-up message: {e}")
            return self._get_default_followup_message()
    
    def send_follow_up(
        self,
        user_id: str,
        grok_client: Any,
        clean_ai_response: Callable,
    ) -> bool:
        """
        Send a follow-up message to a user.
        
        Args:
            user_id: LINE User ID
            grok_client: Grok LLM client
            clean_ai_response: Function to clean AI response
            
        Returns:
            True if sent successfully, False otherwise
        """
        from linebot.models import TextSendMessage
        
        try:
            # Generate message
            message = self.generate_follow_up_message(
                user_id, 
                grok_client, 
                clean_ai_response
            )
            
            # Send via LINE
            self.line_bot_api.push_message(
                user_id,
                TextSendMessage(text=message)
            )
            
            # Update tracking
            self.redis_client.set(
                f"last_follow_up:{user_id}",
                datetime.now().isoformat()
            )
            
            # Remove from queue
            self.redis_client.zrem('follow_up_queue', user_id)
            
            # Schedule next follow-up
            self.reschedule_follow_up(user_id)
            
            logging.info(f"Sent follow-up to user {user_id}")
            return True
            
        except Exception as e:
            logging.error(f"Failed to send follow-up to {user_id}: {e}")
            return False
    
    def process_due_follow_ups(
        self,
        grok_client: Any,
        clean_ai_response: Callable,
        batch_size: int = 10,
    ) -> Dict[str, bool]:
        """
        Process all due follow-ups.
        
        Args:
            grok_client: Grok LLM client
            clean_ai_response: Function to clean AI response
            batch_size: Number of follow-ups to process at once
            
        Returns:
            Dict mapping user_id to success status
        """
        results = {}
        
        due_users = self.get_due_follow_ups(limit=batch_size)
        
        for user_id in due_users:
            success = self.send_follow_up(user_id, grok_client, clean_ai_response)
            results[user_id] = success
        
        return results
    
    def _get_default_followup_message(self) -> str:
        """Get default follow-up message when no history available."""
        return (
            "สวัสดีครับ 👋 น้องใจดีมาทักทายครับ\n\n"
            "ช่วงนี้เป็นอย่างไรบ้างครับ? "
            "ถ้ามีอะไรอยากพูดคุยหรือต้องการคำปรึกษา "
            "สามารถพิมพ์บอกได้เลยนะครับ\n\n"
            "💚 ใจดีพร้อมรับฟังและช่วยเหลือคุณเสมอครับ"
        )
    
    def _get_fallback_followup_message(self) -> str:
        """Get fallback follow-up message when AI generation fails."""
        return (
            "สวัสดีครับ 👋\n\n"
            "น้องใจดีนึกถึงคุณครับ อยากถามว่าช่วงนี้เป็นอย่างไรบ้าง?\n\n"
            "ถ้ามีเรื่องอะไรอยากเล่าหรือต้องการคำปรึกษา "
            "ใจดีพร้อมรับฟังเสมอนะครับ 💚"
        )
    
    def get_formatted_status(self, user_id: str) -> str:
        """
        Get formatted follow-up status message for display.
        
        Args:
            user_id: LINE User ID
            
        Returns:
            Formatted status message
        """
        info = self.get_follow_up_status(user_id)
        
        if info:
            message = (
                f"🔔 สถานะการติดตาม\n\n"
                f"▫️ กำหนดการติดตามครั้งถัดไป: "
                f"{info.scheduled_time.strftime('%d/%m/%Y %H:%M')}\n"
            )
            if info.last_sent:
                message += (
                    f"▫️ ติดตามครั้งล่าสุด: "
                    f"{info.last_sent.strftime('%d/%m/%Y %H:%M')}\n"
                )
            message += "\nน้องใจดีจะส่งข้อความติดตามตามกำหนดการนี้ครับ"
        else:
            message = (
                "🔔 สถานะการติดตาม\n\n"
                "ยังไม่มีกำหนดการติดตามในขณะนี้\n"
                "เมื่อเราพูดคุยกันมากขึ้น น้องใจดีจะตั้งกำหนดการติดตามให้อัตโนมัติครับ"
            )
        
        return message
