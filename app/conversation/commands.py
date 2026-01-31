"""
Command Handler module for the 'ใจดี' chatbot.
Routes and handles slash commands (/help, /status, /optimize, etc.)
"""

import logging
from typing import Optional, Callable, Dict, Any, Tuple
from dataclasses import dataclass
from enum import Enum


class CommandResult(Enum):
    """Result type for command execution."""
    HANDLED = "handled"
    NOT_A_COMMAND = "not_a_command"
    UNKNOWN_COMMAND = "unknown_command"
    ERROR = "error"


@dataclass
class CommandResponse:
    """Response from command execution."""
    result: CommandResult
    message: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class CommandHandler:
    """
    Handles slash command routing and execution.
    
    Responsibilities:
    - Parse and validate commands
    - Route to appropriate handlers
    - Return formatted responses
    """
    
    def __init__(
        self,
        redis_client: Any,
        db: Any,
        session_manager: Any,
        token_threshold: int,
    ):
        """
        Initialize the command handler.
        
        Args:
            redis_client: Redis client instance
            db: Database instance
            session_manager: Session manager module
            token_threshold: Token threshold for optimization
        """
        self.redis_client = redis_client
        self.db = db
        self.session_manager = session_manager
        self.token_threshold = token_threshold
        
        # Command registry
        self._commands: Dict[str, Callable] = {
            '/help': self._handle_help,
            '/status': self._handle_status,
            '/reset': self._handle_reset,
            '/optimize': self._handle_optimize,
            '/tokens': self._handle_tokens,
            '/followup': self._handle_followup,
            '/emergency': self._handle_emergency,
            '/progress': self._handle_progress,
            '/register': self._handle_register,
            '/context': self._handle_context,
            '/summary': self._handle_summary,
        }
    
    def is_command(self, message: str) -> bool:
        """Check if a message is a command."""
        return message.strip().startswith('/')
    
    def handle_command(
        self,
        user_id: str,
        message: str,
        get_user_context: Callable,
        generate_progress_report: Callable,
        is_user_registered: Callable,
        register_user_with_code: Callable,
    ) -> CommandResponse:
        """
        Handle a command message.
        
        Args:
            user_id: LINE User ID
            message: Command message (e.g., "/help", "/verify 123456")
            get_user_context: Function to get user context
            generate_progress_report: Function to generate progress report
            is_user_registered: Function to check registration
            register_user_with_code: Function to register user
            
        Returns:
            CommandResponse with result and message
        """
        if not self.is_command(message):
            return CommandResponse(result=CommandResult.NOT_A_COMMAND)
        
        normalized = message.strip().lower()
        
        # Handle /verify separately (has arguments)
        if normalized.startswith('/verify'):
            return self._handle_verify(
                user_id, 
                message, 
                is_user_registered, 
                register_user_with_code
            )
        
        # Get base command
        base_command = normalized.split()[0]
        
        # Look up handler
        handler = self._commands.get(base_command)
        if not handler:
            return CommandResponse(
                result=CommandResult.UNKNOWN_COMMAND,
                message="คำสั่งไม่ถูกต้องครับ ลองพิมพ์ /help เพื่อดูคำสั่งที่สามารถใช้ได้"
            )
        
        try:
            # Execute handler with dependencies
            return handler(
                user_id=user_id,
                get_user_context=get_user_context,
                generate_progress_report=generate_progress_report,
            )
        except Exception as e:
            logging.error(f"Command handler error for {base_command}: {e}")
            return CommandResponse(
                result=CommandResult.ERROR,
                error=str(e),
                message="เกิดข้อผิดพลาดในการประมวลผลคำสั่ง กรุณาลองใหม่อีกครั้ง"
            )
    
    def _handle_verify(
        self,
        user_id: str,
        message: str,
        is_user_registered: Callable,
        register_user_with_code: Callable,
    ) -> CommandResponse:
        """Handle /verify command."""
        if is_user_registered(user_id):
            return CommandResponse(
                result=CommandResult.HANDLED,
                message=(
                    "✅ คุณได้ลงทะเบียนและยืนยันตัวตนเรียบร้อยแล้ว\n"
                    "ไม่จำเป็นต้องยืนยันอีกครั้ง คุณสามารถใช้บริการของน้องใจดีได้ตามปกติ\n\n"
                    "พิมพ์ /help เพื่อดูคำสั่งและบริการที่มี"
                )
            )
        
        parts = message.strip().split()
        if len(parts) != 2:
            return CommandResponse(
                result=CommandResult.HANDLED,
                message='รูปแบบไม่ถูกต้อง กรุณาพิมพ์ "/verify" ตามด้วยรหัส 6 หลัก เช่น "/verify 123456"'
            )
        
        confirmation_code = parts[1].strip()
        success, result_message = register_user_with_code(user_id, confirmation_code)
        
        return CommandResponse(
            result=CommandResult.HANDLED,
            message=result_message,
            data={"success": success}
        )
    
    def _handle_help(self, **kwargs) -> CommandResponse:
        """Handle /help command."""
        return CommandResponse(
            result=CommandResult.HANDLED,
            message=(
                "สวัสดีครับ 👋 ฉันคือน้องใจดี ผู้ช่วยดูแลและให้คำปรึกษาสำหรับผู้ที่ต้องการเลิกใช้สารเสพติด"
                "💬 ฉันสามารถช่วยคุณได้ดังนี้:\n"
                "- พูดคุยและให้กำลังใจในการเลิกใช้สารเสพติด\n"
                "- ให้คำปรึกษาเกี่ยวกับวิธีรับมือความอยากและอาการถอน\n"
                "- ให้ข้อมูลเกี่ยวกับผลกระทบของสารเสพติดและการรักษา\n"
                "- ติดตามความก้าวหน้าและให้คำแนะนำที่เหมาะสมกับคุณ\n\n"
                "🛠️ คำสั่งที่มีให้ใช้:\n"
                "📥 /register - วิธีลงทะเบียนใช้งาน\n"
                "✅ /verify <รหัส> - ยืนยันตัวตนด้วยรหัส 6 หลัก\n"
                "🧠 /optimize - ปรับปรุงประวัติการสนทนาให้มีประสิทธิภาพ\n"
                "🪙 /tokens - ตรวจสอบการใช้งานโทเค็นในเซสชันปัจจุบัน\n"
                "📊 /status - ดูสรุปสถานะการสนทนาและการใช้โทเค็น\n"
                "📈 /progress - ดูรายงานความก้าวหน้าและแนวทางถัดไป\n"
                "📋 /context - ดูบริบทของคุณจากแบบประเมินที่กรอกไว้\n"
                "📝 /summary - ดูสรุปการสนทนาที่ผ่านมา\n"
                "🔔 /followup - ตรวจสอบกำหนดการติดตามของคุณ\n"
                "🚨 /emergency - ดูข้อมูลติดต่อฉุกเฉินและสายด่วน\n"
                "❓ /help - แสดงเมนูช่วยเหลือนี้\n\n"
                "💡 ตัวอย่างคำถามที่สามารถถามฉันได้:\n"
                '- "ช่วยประเมินการใช้สารเสพติดของฉันหน่อย"\n'
                '- "ผลกระทบของยาบ้าต่อร่างกายมีอะไรบ้าง"\n'
                '- "มีเทคนิคจัดการความอยากยาอย่างไร"\n'
                '- "ฉันควรทำอย่างไรเมื่อรู้สึกอยากกลับไปใช้สารอีก"\n\n'
                "📧 ติดต่อทีมงาน:\n"
                "หากพบข้อผิดพลาด (บัค) หรือมีข้อเสนอแนะ สามารถติดต่อได้ที่:\n"
                "🔧 ผู้พัฒนาระบบ: pahnkcn@gmail.com\n"
                "📖 ผู้วิจัย: Std6548097@pcm.ac.th\n\n"
                "เริ่มพูดคุยกับฉันได้เลยนะครับ ฉันพร้อมรับฟังและช่วยเหลือคุณ 💚"
            )
        )
    
    def _handle_status(self, user_id: str, **kwargs) -> CommandResponse:
        """Handle /status command."""
        history_count = self.db.get_user_history_count(user_id)
        important_count = self.db.get_important_message_count(user_id)
        last_interaction = self.db.get_last_interaction(user_id)
        current_session = self.redis_client.exists(f"chat_session:{user_id}") == 1
        total_db_tokens = self.db.get_total_tokens(user_id) or 0
        session_tokens = self.session_manager.get_session_token_count(user_id)
        
        return CommandResponse(
            result=CommandResult.HANDLED,
            message=(
                "📊 สถิติการสนทนาของคุณ\n"
                f"▫️ จำนวนการสนทนาที่บันทึก: {history_count} ครั้ง\n"
                f"▫️ ประเด็นสำคัญที่พูดคุย: {important_count} รายการ\n"
                f"▫️ สนทนาล่าสุดเมื่อ: {last_interaction}\n"
                f"▫️ สถานะเซสชันปัจจุบัน: {'🟢 กำลังสนทนาอยู่' if current_session else '🔴 ยังไม่เริ่มสนทนา'}\n\n"
                f"📝 สถิติโทเค็น\n"
                f"▫️ โทเค็นในเซสชันปัจจุบัน: {session_tokens:,}\n"
                f"▫️ โทเค็นในฐานข้อมูล: {total_db_tokens:,}\n"
                "  (ผลรวมของแต่ละข้อความที่บันทึก)\n\n"
                "💚 น้องใจดีพร้อมให้คำปรึกษาและสนับสนุนคุณตลอดเส้นทางการเลิกสารเสพติด\n"
                "💬 มีคำถามหรือต้องการความช่วยเหลือ เพียงพิมพ์บอกฉันได้เลยครับ\n\n"
                "ℹ️ เคล็ดลับ: ต้องการดูรายงานความก้าวหน้าของคุณ พิมพ์ /progress"
            ),
            data={
                "history_count": history_count,
                "important_count": important_count,
                "session_tokens": session_tokens,
                "total_db_tokens": total_db_tokens,
            }
        )
    
    def _handle_reset(self, user_id: str, **kwargs) -> CommandResponse:
        """Handle /reset command."""
        self.db.clear_user_history(user_id)
        self.redis_client.delete(f"chat_session:{user_id}")
        self.redis_client.delete(f"session_tokens:{user_id}")
        self.redis_client.zrem('follow_up_queue', user_id)
        self.redis_client.delete(f"last_follow_up:{user_id}")
        self.redis_client.delete(f"first_interaction:{user_id}")
        
        return CommandResponse(
            result=CommandResult.HANDLED,
            message=(
                "🔄 ล้างประวัติการสนทนาเรียบร้อยแล้วครับ\n\n"
                "เราสามารถเริ่มต้นการสนทนาใหม่ได้ทันที\n"
                "คุณต้องการพูดคุยเกี่ยวกับเรื่องอะไรดีครับ?"
            )
        )
    
    def _handle_optimize(self, user_id: str, **kwargs) -> CommandResponse:
        """Handle /optimize command."""
        token_count_before = self.session_manager.get_session_token_count(user_id)
        self.session_manager.hybrid_context_management(user_id, self.token_threshold)
        token_count_after = self.session_manager.get_session_token_count(user_id)
        
        return CommandResponse(
            result=CommandResult.HANDLED,
            message=(
                f"🔄 ปรับปรุงประวัติการสนทนาเรียบร้อยแล้วครับ\n\n"
                f"จำนวนโทเค็น: {token_count_before} → {token_count_after} ({token_count_before - token_count_after} ลดลง)\n\n"
                "ประวัติการสนทนาสำคัญยังคงถูกเก็บไว้ และบอทยังเข้าใจบริบทการสนทนาของเรา\n"
                "เราสามารถสนทนาต่อได้ตามปกติครับ"
            ),
            data={
                "tokens_before": token_count_before,
                "tokens_after": token_count_after,
                "tokens_saved": token_count_before - token_count_after,
            }
        )
    
    def _handle_tokens(self, user_id: str, **kwargs) -> CommandResponse:
        """Handle /tokens command."""
        token_count = self.session_manager.get_session_token_count(user_id)
        max_tokens = self.token_threshold
        percentage = (token_count / max_tokens) * 100 if max_tokens else 0
        
        status_msg = (
            '⚠️ ใกล้ถึงขีดจำกัด โปรดใช้ /optimize เพื่อปรับปรุงประวัติ' 
            if percentage > 80 
            else '✅ อยู่ในเกณฑ์ปกติ'
        )
        
        return CommandResponse(
            result=CommandResult.HANDLED,
            message=(
                f"📊 สถิติการใช้โทเค็น\n\n"
                f"โทเค็นในเซสชันปัจจุบัน: {token_count:,}\n"
                f"ขีดจำกัด: {max_tokens:,}\n"
                f"เปอร์เซ็นต์การใช้งาน: {percentage:.1f}%\n\n"
                f"{status_msg}"
            ),
            data={
                "token_count": token_count,
                "max_tokens": max_tokens,
                "percentage": percentage,
            }
        )
    
    def _handle_followup(self, user_id: str, **kwargs) -> CommandResponse:
        """Handle /followup command."""
        # Get follow-up status from session manager
        from .followups import FollowUpManager
        
        # Check if user is in follow-up queue
        score = self.redis_client.zscore('follow_up_queue', user_id)
        last_followup = self.redis_client.get(f"last_follow_up:{user_id}")
        
        if score:
            from datetime import datetime
            next_followup = datetime.fromtimestamp(score)
            message = (
                f"🔔 สถานะการติดตาม\n\n"
                f"▫️ กำหนดการติดตามครั้งถัดไป: {next_followup.strftime('%d/%m/%Y %H:%M')}\n"
            )
            if last_followup:
                message += f"▫️ ติดตามครั้งล่าสุด: {last_followup}\n"
            message += "\nน้องใจดีจะส่งข้อความติดตามตามกำหนดการนี้ครับ"
        else:
            message = (
                "🔔 สถานะการติดตาม\n\n"
                "ยังไม่มีกำหนดการติดตามในขณะนี้\n"
                "เมื่อเราพูดคุยกันมากขึ้น น้องใจดีจะตั้งกำหนดการติดตามให้อัตโนมัติครับ"
            )
        
        return CommandResponse(
            result=CommandResult.HANDLED,
            message=message
        )
    
    def _handle_emergency(self, **kwargs) -> CommandResponse:
        """Handle /emergency command."""
        return CommandResponse(
            result=CommandResult.HANDLED,
            message=(
                "🚨 บริการช่วยเหลือฉุกเฉิน 🚨\n\n"
                "หากคุณหรือคนใกล้ตัวกำลังประสบปัญหาต่อไปนี้:\n"
                "- ใช้สารเสพติดเกินขนาด (Overdose)\n"
                "- มีอาการชัก เลือดออก หมดสติ\n"
                "- มีความคิดทำร้ายตัวเอง\n"
                "- มีอาการถอนยารุนแรง\n\n"
                "📞 ติดต่อขอความช่วยเหลือด่วนได้ที่:\n"
                "🔸 สายด่วนกรมควบคุมโรค: 1422\n"
                "🔸 ศูนย์ปรึกษาปัญหายาเสพติด: 1165\n"
                "🔸 หน่วยกู้ชีพฉุกเฉิน: 1669\n"
                "🔸 สายด่วนสุขภาพจิต: 1323\n\n"
                "🌐 เว็บไซต์ช่วยเหลือ:\n"
                "https://www.pmnidat.go.th\n\n"
                "💚 การขอความช่วยเหลือคือก้าวแรกของการดูแลตัวเอง"
            )
        )
    
    def _handle_progress(
        self, 
        user_id: str, 
        generate_progress_report: Callable,
        **kwargs
    ) -> CommandResponse:
        """Handle /progress command."""
        report = generate_progress_report(user_id)
        
        if report:
            message = f"{report}\n\nℹ️ เคล็ดลับ: ต้องการดูสรุปสถานะการสนทนาปัจจุบัน พิมพ์ /status"
        else:
            message = (
                "📊 รายงานความก้าวหน้า\n\n"
                "ยังไม่มีข้อมูลความก้าวหน้าเพียงพอสำหรับการวิเคราะห์\n\n"
                "เมื่อเราพูดคุยกันมากขึ้น น้องใจดีจะสามารถติดตามและวิเคราะห์ความก้าวหน้าของคุณได้\n\n"
                "ℹ️ เคล็ดลับ: ดูสรุปสถานะล่าสุดด้วย /status"
            )
        
        return CommandResponse(
            result=CommandResult.HANDLED,
            message=message
        )
    
    def _handle_register(self, **kwargs) -> CommandResponse:
        """Handle /register command."""
        return CommandResponse(
            result=CommandResult.HANDLED,
            message=(
                "📝 การลงทะเบียนใช้งานน้องใจดี\n\n"
                "เพื่อเริ่มใช้งาน คุณจำเป็นต้องลงทะเบียนก่อน โดยทำตามขั้นตอนดังนี้:\n\n"
                "1. กรอกแบบฟอร์มที่ลิงก์นี้: https://forms.gle/KYU4JNWL72TL3PsG9\n"
                '2. หลังกรอกเสร็จ คุณจะได้รับรหัสยืนยัน 6 หลัก\n'
                '3. นำรหัสมาพิมพ์ที่นี่ด้วยคำสั่ง "/verify รหัส" เช่น "/verify 123456"\n\n'
                "หากมีปัญหาในการลงทะเบียน คุณสามารถติดต่อเจ้าหน้าที่ได้ที่ support@example.com"
            )
        )
    
    def _handle_context(
        self, 
        user_id: str, 
        get_user_context: Callable,
        **kwargs
    ) -> CommandResponse:
        """Handle /context command."""
        context = get_user_context(user_id)
        
        if context:
            message = (
                "📋 บริบทของคุณจากแบบประเมิน:\n\n"
                f"{context}\n\n"
                "ใจดีใช้ข้อมูลนี้เพื่อให้คำปรึกษาที่เหมาะสมกับคุณมากที่สุดครับ"
            )
        else:
            message = (
                "ไม่พบข้อมูลบริบทจากแบบประเมิน\n"
                "อาจเป็นเพราะคุณลงทะเบียนก่อนที่ระบบจะมีฟีเจอร์นี้"
            )
        
        return CommandResponse(
            result=CommandResult.HANDLED,
            message=message
        )
    
    def _handle_summary(self, user_id: str, **kwargs) -> CommandResponse:
        """Handle /summary command - shows conversation summary."""
        # Get current session
        session_history = self.session_manager.get_chat_session(user_id)
        
        if not session_history:
            return CommandResponse(
                result=CommandResult.HANDLED,
                message=(
                    "📝 สรุปการสนทนา\n\n"
                    "ยังไม่มีประวัติการสนทนาในเซสชันปัจจุบัน\n"
                    "เริ่มพูดคุยกับน้องใจดีได้เลยครับ"
                )
            )
        
        # Check for existing summary in session
        summary_content = None
        for msg in session_history:
            if msg.get('role') == 'system_summary':
                summary_content = msg.get('content', '')
                break
        
        # Count conversation stats
        user_messages = sum(1 for m in session_history if m.get('role') == 'user')
        assistant_messages = sum(1 for m in session_history if m.get('role') == 'assistant')
        
        if summary_content:
            message = (
                "📝 สรุปการสนทนา\n\n"
                f"{summary_content}\n\n"
                f"📊 สถิติเซสชันปัจจุบัน:\n"
                f"▫️ ข้อความจากคุณ: {user_messages} ข้อความ\n"
                f"▫️ ข้อความจากใจดี: {assistant_messages} ข้อความ"
            )
        else:
            message = (
                "📝 สรุปการสนทนา\n\n"
                f"📊 สถิติเซสชันปัจจุบัน:\n"
                f"▫️ ข้อความจากคุณ: {user_messages} ข้อความ\n"
                f"▫️ ข้อความจากใจดี: {assistant_messages} ข้อความ\n\n"
                "ยังไม่มีการสรุปการสนทนา\n"
                "เมื่อสนทนากันมากขึ้น ระบบจะสร้างสรุปให้อัตโนมัติครับ"
            )
        
        return CommandResponse(
            result=CommandResult.HANDLED,
            message=message,
            data={
                "user_messages": user_messages,
                "assistant_messages": assistant_messages,
                "has_summary": summary_content is not None,
            }
        )
