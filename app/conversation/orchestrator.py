"""
Conversation Orchestrator module for the 'ใจดี' chatbot.
Main flow controller for every user message.
"""

import logging
import time
import uuid
from typing import Optional, Dict, Any, Callable, List, Tuple
from dataclasses import dataclass, field
from enum import Enum

from .context_builder import ContextBuilder, ContextConfig, BuiltContext
from .commands import CommandHandler, CommandResult, CommandResponse
from .reply_sender import ReplySender, SendResult
from .followups import FollowUpManager


class ProcessingResult(Enum):
    """Result type for message processing."""
    SUCCESS = "success"
    COMMAND_HANDLED = "command_handled"
    HOSPITAL_INQUIRY = "hospital_inquiry"
    SESSION_TIMEOUT = "session_timeout"
    VALIDATION_BLOCKED = "validation_blocked"
    AI_ERROR = "ai_error"
    SEND_ERROR = "send_error"
    UNKNOWN_ERROR = "unknown_error"


@dataclass
class ProcessingMetrics:
    """Metrics for a single message processing."""
    correlation_id: str
    user_id: str
    start_time: float
    end_time: Optional[float] = None
    total_duration: Optional[float] = None
    ai_call_duration: Optional[float] = None
    token_count_input: int = 0
    token_count_output: int = 0
    used_fallback: bool = False
    had_error: bool = False
    result: ProcessingResult = ProcessingResult.SUCCESS


@dataclass
class OrchestratorConfig:
    """Configuration for the orchestrator."""
    max_retries: int = 3
    base_timeout: int = 30
    max_timeout: int = 120
    enable_input_validation: bool = True
    enable_crisis_detection: bool = True
    enable_follow_ups: bool = True


class ConversationOrchestrator:
    """
    Main flow controller for every user message.
    
    This is the single entry point for processing user messages.
    It coordinates all components:
    - Input validation
    - Command routing
    - Context building
    - LLM calls
    - Response post-processing
    - Reply sending
    - Follow-up scheduling
    - Observability
    """
    
    def __init__(
        self,
        # Core dependencies
        redis_client: Any,
        db: Any,
        line_bot_api: Any,
        grok_client: Any,
        token_counter: Any,
        # Configuration
        system_prompt: Dict[str, str],
        app_config: Any,
        token_threshold: int,
        # Optional config
        config: Optional[OrchestratorConfig] = None,
    ):
        """
        Initialize the orchestrator.
        
        Args:
            redis_client: Redis client instance
            db: Database instance
            line_bot_api: LINE Bot API instance
            grok_client: Grok LLM client
            token_counter: Token counter instance
            system_prompt: Base system prompt (SYSTEM_MESSAGES)
            app_config: Application configuration
            token_threshold: Token threshold for context management
            config: Optional orchestrator configuration
        """
        self.redis_client = redis_client
        self.db = db
        self.line_bot_api = line_bot_api
        self.grok_client = grok_client
        self.token_counter = token_counter
        self.system_prompt = system_prompt
        self.app_config = app_config
        self.token_threshold = token_threshold
        self.config = config or OrchestratorConfig()
        
        # Initialize sub-components
        self._init_components()
    
    def _init_components(self):
        """Initialize sub-components."""
        # Import session_manager functions
        from ..session_manager import (
            get_chat_session,
            save_chat_session,
            get_session_token_count,
            hybrid_context_management,
        )
        
        # Create a session manager interface
        class SessionManagerInterface:
            def __init__(self, redis_client):
                self.redis_client = redis_client
            
            def get_chat_session(self, user_id):
                return get_chat_session(user_id)
            
            def save_chat_session(self, user_id, messages):
                return save_chat_session(user_id, messages)
            
            def get_session_token_count(self, user_id):
                return get_session_token_count(user_id)
            
            def hybrid_context_management(self, user_id, threshold):
                return hybrid_context_management(user_id, threshold)
        
        self.session_manager = SessionManagerInterface(self.redis_client)
        
        # Context builder
        self.context_builder = ContextBuilder(
            base_system_prompt=self.system_prompt,
            token_counter=self.token_counter,
            config=ContextConfig(max_context_tokens=self.token_threshold),
        )
        
        # Command handler
        self.command_handler = CommandHandler(
            redis_client=self.redis_client,
            db=self.db,
            session_manager=self.session_manager,
            token_threshold=self.token_threshold,
        )
        
        # Reply sender
        self.reply_sender = ReplySender(line_bot_api=self.line_bot_api)
        
        # Follow-up manager
        self.follow_up_manager = FollowUpManager(
            redis_client=self.redis_client,
            db=self.db,
            line_bot_api=self.line_bot_api,
            config=self.app_config,
        )
    
    def process_message(
        self,
        user_id: str,
        user_message: str,
        reply_token: Optional[str] = None,
        # Callback functions from app_main
        get_user_context: Optional[Callable] = None,
        generate_progress_report: Optional[Callable] = None,
        is_user_registered: Optional[Callable] = None,
        register_user_with_code: Optional[Callable] = None,
        check_session_timeout: Optional[Callable] = None,
        update_last_activity: Optional[Callable] = None,
        check_hospital_inquiry: Optional[Callable] = None,
        get_hospital_information_message: Optional[Callable] = None,
        start_loading_animation: Optional[Callable] = None,
        assess_risk: Optional[Callable] = None,
        save_progress_data: Optional[Callable] = None,
        clean_ai_response: Optional[Callable] = None,
        get_dynamic_config: Optional[Callable] = None,
        validate_user_input: Optional[Callable] = None,
    ) -> Tuple[ProcessingResult, ProcessingMetrics]:
        """
        Process a user message through the complete pipeline.
        
        This is the main entry point for message processing.
        
        Args:
            user_id: LINE User ID
            user_message: User's message text
            reply_token: Optional LINE reply token
            get_user_context: Function to get user context from form
            generate_progress_report: Function to generate progress report
            is_user_registered: Function to check if user is registered
            register_user_with_code: Function to register user with code
            check_session_timeout: Function to check session timeout
            update_last_activity: Function to update last activity
            check_hospital_inquiry: Function to check for hospital inquiry
            get_hospital_information_message: Function to get hospital info
            start_loading_animation: Function to start loading animation
            assess_risk: Function to assess risk level
            save_progress_data: Function to save progress data
            clean_ai_response: Function to clean AI response
            get_dynamic_config: Function to get dynamic LLM config
            validate_user_input: Function to validate user input
            
        Returns:
            Tuple of (ProcessingResult, ProcessingMetrics)
        """
        # Initialize metrics
        correlation_id = str(uuid.uuid4())[:8]
        metrics = ProcessingMetrics(
            correlation_id=correlation_id,
            user_id=user_id,
            start_time=time.time(),
        )
        
        logging.info(f"[{correlation_id}] Processing message for user {user_id[:8]}...")
        
        try:
            # Clear any wait notice
            self.redis_client.delete(f"wait_notice:{user_id}")
            
            # 1. Check session timeout
            if check_session_timeout and check_session_timeout(user_id):
                self._send_session_timeout_message(user_id, reply_token)
                metrics.result = ProcessingResult.SESSION_TIMEOUT
                return self._finalize_metrics(metrics)
            
            # 2. Update last activity
            if update_last_activity:
                update_last_activity(user_id)
            
            # 3. Input validation (if enabled)
            if self.config.enable_input_validation and validate_user_input:
                is_valid, validated_message, risk_info = validate_user_input(
                    user_id, user_message
                )
                if not is_valid:
                    self._send_validation_blocked_message(user_id, reply_token)
                    metrics.result = ProcessingResult.VALIDATION_BLOCKED
                    return self._finalize_metrics(metrics)
                user_message = validated_message
            
            # 4. Command handling
            if self.command_handler.is_command(user_message):
                cmd_response = self.command_handler.handle_command(
                    user_id=user_id,
                    message=user_message,
                    get_user_context=get_user_context or (lambda uid: None),
                    generate_progress_report=generate_progress_report or (lambda uid: None),
                    is_user_registered=is_user_registered or (lambda uid: True),
                    register_user_with_code=register_user_with_code or (lambda uid, code: (False, "")),
                )
                
                if cmd_response.result in (CommandResult.HANDLED, CommandResult.UNKNOWN_COMMAND):
                    self.reply_sender.send_response(
                        user_id, 
                        cmd_response.message, 
                        reply_token
                    )
                    metrics.result = ProcessingResult.COMMAND_HANDLED
                    return self._finalize_metrics(metrics)
            
            # 5. Hospital inquiry check
            if check_hospital_inquiry and check_hospital_inquiry(user_message):
                hospital_response = get_hospital_information_message() if get_hospital_information_message else ""
                self.reply_sender.send_response(user_id, hospital_response, reply_token)
                metrics.result = ProcessingResult.HOSPITAL_INQUIRY
                return self._finalize_metrics(metrics)
            
            # 6. Start loading animation
            animation_success = False
            if start_loading_animation:
                animation_success, _ = start_loading_animation(user_id)
                if not animation_success and reply_token:
                    # Send processing status and consume reply token
                    from ..config import PROCESSING_MESSAGES
                    if hasattr(self, '_processing_messages'):
                        processing_messages = self._processing_messages
                    else:
                        processing_messages = [
                            "⌛ กำลังคิดอยู่ครับ...",
                            "🤔 กำลังประมวลผลข้อความของคุณ...",
                            "📝 กำลังเรียบเรียงคำตอบ...",
                            "🔄 รอสักครู่นะครับ..."
                        ]
                    if self.reply_sender.send_processing_status(
                        user_id, reply_token, processing_messages
                    ):
                        reply_token = None
            
            # 7. Process AI response
            result, bot_response = self._process_ai_response(
                user_id=user_id,
                user_message=user_message,
                metrics=metrics,
                get_user_context=get_user_context,
                clean_ai_response=clean_ai_response,
                get_dynamic_config=get_dynamic_config,
                assess_risk=assess_risk,
                save_progress_data=save_progress_data,
            )
            
            if result != ProcessingResult.SUCCESS:
                metrics.result = result
                # Send error message
                self._send_error_message(user_id, reply_token, result)
                return self._finalize_metrics(metrics)
            
            # 8. Handle response timing
            self._handle_response_timing(metrics.start_time, animation_success)
            
            # 9. Send response
            send_result = self.reply_sender.send_response(
                user_id, 
                bot_response, 
                reply_token
            )
            
            if send_result.result == SendResult.FAILED:
                metrics.result = ProcessingResult.SEND_ERROR
                metrics.had_error = True
                logging.error(f"[{correlation_id}] Failed to send response to user {user_id[:8]}")
            
            # 10. Schedule follow-up (if enabled)
            if self.config.enable_follow_ups:
                self.follow_up_manager.reset_follow_up_schedule(user_id)
            
            return self._finalize_metrics(metrics)
            
        except Exception as e:
            logging.error(
                f"[{correlation_id}] Unexpected error processing message: {e}",
                exc_info=True
            )
            metrics.result = ProcessingResult.UNKNOWN_ERROR
            metrics.had_error = True
            self._send_error_message(user_id, reply_token, ProcessingResult.UNKNOWN_ERROR)
            return self._finalize_metrics(metrics)
    
    def _process_ai_response(
        self,
        user_id: str,
        user_message: str,
        metrics: ProcessingMetrics,
        get_user_context: Optional[Callable] = None,
        clean_ai_response: Optional[Callable] = None,
        get_dynamic_config: Optional[Callable] = None,
        assess_risk: Optional[Callable] = None,
        save_progress_data: Optional[Callable] = None,
    ) -> Tuple[ProcessingResult, str]:
        """
        Process AI response generation.
        
        Args:
            user_id: LINE User ID
            user_message: User's message
            metrics: Processing metrics to update
            get_user_context: Function to get user context
            clean_ai_response: Function to clean AI response
            get_dynamic_config: Function to get dynamic config
            assess_risk: Function to assess risk
            save_progress_data: Function to save progress data
            
        Returns:
            Tuple of (ProcessingResult, bot_response)
        """
        correlation_id = metrics.correlation_id
        
        # 1. Get user context
        user_context = None
        if get_user_context:
            try:
                user_context = get_user_context(user_id)
                if user_context:
                    logging.info(f"[{correlation_id}] Loaded user context")
            except Exception as e:
                logging.warning(f"[{correlation_id}] Failed to load user context: {e}")
        
        # 2. Get conversation history
        try:
            conversation_history = self.session_manager.get_chat_session(user_id)
        except Exception as e:
            logging.warning(f"[{correlation_id}] Failed to get session: {e}")
            conversation_history = []
        
        # 3. Check for existing summary
        summary = None
        for msg in conversation_history:
            if msg.get('role') == 'system_summary':
                summary = msg.get('content', '')
                break
        
        # 4. Build context
        try:
            context = self.context_builder.build_context(
                conversation_history=conversation_history,
                user_context=user_context,
                summary=summary,
            )
            
            # Add user message
            context = self.context_builder.add_user_message(context, user_message)
            metrics.token_count_input = context.token_count
            
        except Exception as e:
            logging.error(f"[{correlation_id}] Context building failed: {e}")
            # Create minimal context
            context = self.context_builder.build_context(
                conversation_history=[],
                user_context=user_context,
            )
            context = self.context_builder.add_user_message(context, user_message)
        
        # 5. Call AI with retries
        bot_response = None
        ai_start_time = time.time()
        
        for attempt in range(self.config.max_retries):
            try:
                # Get messages for API
                messages = self.context_builder.extract_messages_for_api(context)
                
                # Get dynamic config
                dynamic_config = {}
                if get_dynamic_config:
                    dynamic_config = get_dynamic_config(user_message, messages)
                
                # Call Grok API
                response_text = self.grok_client.send_chat(
                    messages=messages,
                    model=self.app_config.XAI_MODEL,
                    **dynamic_config,
                )
                
                if not response_text:
                    raise ValueError("Empty AI response")
                
                # Clean response
                if clean_ai_response:
                    bot_response = clean_ai_response(response_text)
                else:
                    bot_response = response_text.strip()
                
                if not bot_response:
                    raise ValueError("Empty cleaned response")
                
                metrics.ai_call_duration = time.time() - ai_start_time
                break
                
            except Exception as e:
                logging.warning(
                    f"[{correlation_id}] AI call attempt {attempt + 1} failed: {e}"
                )
                if attempt < self.config.max_retries - 1:
                    time.sleep(2 ** (attempt + 1))  # Exponential backoff
                else:
                    logging.error(f"[{correlation_id}] All AI call attempts failed")
                    metrics.had_error = True
        
        # 6. Use fallback if needed
        if not bot_response:
            bot_response = self._generate_fallback_response(user_message, user_context)
            metrics.used_fallback = True
        
        # 7. Save to history
        try:
            # Add assistant response to context
            context = self.context_builder.add_assistant_message(context, bot_response)
            
            # Save session (need to reconstruct with user/assistant messages only)
            messages_to_save = []
            for msg in context.messages:
                if msg.get('role') in ('user', 'assistant'):
                    messages_to_save.append(msg)
                elif msg.get('role') == 'system_summary':
                    # Preserve summary
                    messages_to_save.append(msg)
            
            self.session_manager.save_chat_session(user_id, messages_to_save)
            
            # Save to database
            self.db.save_conversation(
                user_id=user_id,
                user_message=user_message,
                bot_response=bot_response,
                token_count=context.token_count,
            )
            
            # Assess risk and save progress
            if assess_risk and save_progress_data:
                risk_level, keywords = assess_risk(user_message)
                save_progress_data(user_id, risk_level, keywords)
                
        except Exception as e:
            logging.error(f"[{correlation_id}] Failed to save conversation: {e}")
            metrics.had_error = True
        
        return ProcessingResult.SUCCESS, bot_response
    
    def _generate_fallback_response(
        self, 
        user_message: str, 
        user_context: Optional[str]
    ) -> str:
        """Generate fallback response when AI fails."""
        message_lower = user_message.lower()
        
        # Crisis keywords
        crisis_keywords = ['ฆ่าตัวตาย', 'ทำร้ายตัวเอง', 'อยากตาย']
        if any(kw in message_lower for kw in crisis_keywords):
            return (
                "ใจดีเข้าใจว่าคุณกำลังผ่านช่วงเวลาที่ยากลำบาก\n\n"
                "⚠️ กรุณาติดต่อสายด่วนสุขภาพจิต 1323 ทันที\n"
                "หรือโทร 1669 หากต้องการความช่วยเหลือฉุกเฉิน\n\n"
                "คุณไม่ได้อยู่คนเดียว มีคนพร้อมช่วยเหลือคุณตลอด 24 ชั่วโมง"
            )
        
        # General fallback
        return (
            "ขออภัยครับ ระบบกำลังประสบปัญหาชั่วคราว\n\n"
            "ใจดียังคงอยู่ที่นี่และพร้อมรับฟังคุณ "
            "กรุณาลองพูดคุยกับใจดีอีกครั้งในอีกสักครู่นะครับ\n\n"
            "หากต้องการความช่วยเหลือเร่งด่วน:\n"
            "📞 สายด่วนยาเสพติด: 1165\n"
            "📞 สายด่วนสุขภาพจิต: 1323"
        )
    
    def _send_session_timeout_message(
        self, 
        user_id: str, 
        reply_token: Optional[str]
    ):
        """Send session timeout message."""
        message = (
            "สวัสดีครับ ยินดีต้อนรับกลับมา 👋\n\n"
            "เซสชันก่อนหน้าของเราหมดอายุแล้ว เราสามารถเริ่มการสนทนาใหม่ได้ทันที\n\n"
            "💡 ต้องการดูประวัติการสนทนาก่อนหน้า พิมพ์: /status\n"
            "💡 ต้องการดูรายงานความก้าวหน้า พิมพ์: /progress\n"
            "💡 ต้องการคำแนะนำเพิ่มเติม พิมพ์: /help\n\n"
            "คุณต้องการพูดคุยเกี่ยวกับเรื่องอะไรดีครับวันนี้?"
        )
        self.reply_sender.send_response(user_id, message, reply_token)
    
    def _send_validation_blocked_message(
        self, 
        user_id: str, 
        reply_token: Optional[str]
    ):
        """Send message when input validation blocks the message."""
        message = (
            "ขออภัยครับ ข้อความของคุณมีเนื้อหาที่ระบบไม่สามารถประมวลผลได้\n\n"
            "กรุณาลองส่งข้อความใหม่อีกครั้งนะครับ\n\n"
            "หากต้องการความช่วยเหลือ พิมพ์ /help"
        )
        self.reply_sender.send_response(user_id, message, reply_token)
    
    def _send_error_message(
        self, 
        user_id: str, 
        reply_token: Optional[str],
        result: ProcessingResult
    ):
        """Send appropriate error message based on result."""
        if result == ProcessingResult.AI_ERROR:
            message = (
                "ขออภัยครับ ระบบ AI กำลังประสบปัญหาชั่วคราว\n\n"
                "กรุณาลองส่งข้อความอีกครั้งในอีกสักครู่\n\n"
                "หากต้องการความช่วยเหลือเร่งด่วน:\n"
                "📞 สายด่วนยาเสพติด: 1165"
            )
        else:
            message = (
                "ขออภัยครับ เกิดข้อผิดพลาดในระบบ\n\n"
                "กรุณาลองส่งข้อความอีกครั้ง\n\n"
                "หากปัญหายังคงอยู่ กรุณาติดต่อ support@example.com"
            )
        
        self.reply_sender.send_response(user_id, message, reply_token)
    
    def _handle_response_timing(self, start_time: float, animation_success: bool):
        """Handle response timing for better UX."""
        elapsed_time = time.time() - start_time
        
        # If animation was shown and response came back quickly,
        # add small delay so user sees animation for reasonable time
        if animation_success and elapsed_time < 5:
            time.sleep(5 - elapsed_time)
    
    def _finalize_metrics(self, metrics: ProcessingMetrics) -> Tuple[ProcessingResult, ProcessingMetrics]:
        """Finalize metrics and log."""
        metrics.end_time = time.time()
        metrics.total_duration = metrics.end_time - metrics.start_time
        
        logging.info(
            f"[{metrics.correlation_id}] Completed processing for user {metrics.user_id[:8]}: "
            f"result={metrics.result.value}, duration={metrics.total_duration:.2f}s, "
            f"fallback={metrics.used_fallback}, error={metrics.had_error}"
        )
        
        return metrics.result, metrics


def create_orchestrator(
    redis_client: Any,
    db: Any,
    line_bot_api: Any,
    grok_client: Any,
    token_counter: Any,
    system_prompt: Dict[str, str],
    app_config: Any,
    token_threshold: int,
) -> ConversationOrchestrator:
    """
    Factory function to create a ConversationOrchestrator.
    
    Args:
        redis_client: Redis client instance
        db: Database instance
        line_bot_api: LINE Bot API instance
        grok_client: Grok LLM client
        token_counter: Token counter instance
        system_prompt: Base system prompt
        app_config: Application configuration
        token_threshold: Token threshold
        
    Returns:
        Configured ConversationOrchestrator instance
    """
    return ConversationOrchestrator(
        redis_client=redis_client,
        db=db,
        line_bot_api=line_bot_api,
        grok_client=grok_client,
        token_counter=token_counter,
        system_prompt=system_prompt,
        app_config=app_config,
        token_threshold=token_threshold,
    )
