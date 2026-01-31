"""
Integration module for bridging the new conversation package with app_main.py.
Provides backward-compatible functions that use the new orchestrator internally.
"""

import logging
from typing import Optional, Any, Callable

from .orchestrator import ConversationOrchestrator, ProcessingResult, create_orchestrator
from .validation import validate_user_input, create_validation_callback


# Global orchestrator instance (initialized by init_conversation_system)
_orchestrator: Optional[ConversationOrchestrator] = None


def init_conversation_system(
    redis_client: Any,
    db: Any,
    line_bot_api: Any,
    grok_client: Any,
    token_counter: Any,
    system_prompt: dict,
    app_config: Any,
    token_threshold: int,
) -> ConversationOrchestrator:
    """
    Initialize the conversation system.
    
    Call this during app startup after all dependencies are initialized.
    
    Args:
        redis_client: Redis client instance
        db: Database instance (ChatHistoryDB)
        line_bot_api: LINE Bot API instance
        grok_client: Grok LLM client
        token_counter: Token counter instance
        system_prompt: Base system prompt (SYSTEM_MESSAGES)
        app_config: Application configuration
        token_threshold: Token threshold for context management
        
    Returns:
        Configured ConversationOrchestrator instance
    """
    global _orchestrator
    
    _orchestrator = create_orchestrator(
        redis_client=redis_client,
        db=db,
        line_bot_api=line_bot_api,
        grok_client=grok_client,
        token_counter=token_counter,
        system_prompt=system_prompt,
        app_config=app_config,
        token_threshold=token_threshold,
    )
    
    logging.info("Conversation system initialized successfully")
    return _orchestrator


def get_orchestrator() -> Optional[ConversationOrchestrator]:
    """Get the global orchestrator instance."""
    return _orchestrator


def process_user_message_v2(
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
) -> ProcessingResult:
    """
    Process a user message using the new orchestrator.
    
    This is the new entry point for message processing.
    Can be used as a drop-in replacement for process_user_message.
    
    Args:
        user_id: LINE User ID
        user_message: User's message text
        reply_token: Optional LINE reply token
        ... (callback functions from app_main)
        
    Returns:
        ProcessingResult enum value
    """
    if _orchestrator is None:
        logging.error("Conversation system not initialized. Call init_conversation_system first.")
        raise RuntimeError("Conversation system not initialized")
    
    # Create validation callback
    validation_callback = create_validation_callback()
    
    result, metrics = _orchestrator.process_message(
        user_id=user_id,
        user_message=user_message,
        reply_token=reply_token,
        get_user_context=get_user_context,
        generate_progress_report=generate_progress_report,
        is_user_registered=is_user_registered,
        register_user_with_code=register_user_with_code,
        check_session_timeout=check_session_timeout,
        update_last_activity=update_last_activity,
        check_hospital_inquiry=check_hospital_inquiry,
        get_hospital_information_message=get_hospital_information_message,
        start_loading_animation=start_loading_animation,
        assess_risk=assess_risk,
        save_progress_data=save_progress_data,
        clean_ai_response=clean_ai_response,
        get_dynamic_config=get_dynamic_config,
        validate_user_input=validation_callback,
    )
    
    return result


def create_message_processor(
    # Dependencies that don't change per-message
    get_user_context: Callable,
    generate_progress_report: Callable,
    is_user_registered: Callable,
    register_user_with_code: Callable,
    check_session_timeout: Callable,
    update_last_activity: Callable,
    check_hospital_inquiry: Callable,
    get_hospital_information_message: Callable,
    start_loading_animation: Callable,
    assess_risk: Callable,
    save_progress_data: Callable,
    clean_ai_response: Callable,
    get_dynamic_config: Callable,
) -> Callable:
    """
    Create a message processor function with pre-bound dependencies.
    
    This creates a simpler interface for processing messages where
    all the callback functions are already bound.
    
    Args:
        ... (callback functions from app_main)
        
    Returns:
        Function with signature (user_id, user_message, reply_token) -> ProcessingResult
    """
    def processor(
        user_id: str,
        user_message: str,
        reply_token: Optional[str] = None,
    ) -> ProcessingResult:
        return process_user_message_v2(
            user_id=user_id,
            user_message=user_message,
            reply_token=reply_token,
            get_user_context=get_user_context,
            generate_progress_report=generate_progress_report,
            is_user_registered=is_user_registered,
            register_user_with_code=register_user_with_code,
            check_session_timeout=check_session_timeout,
            update_last_activity=update_last_activity,
            check_hospital_inquiry=check_hospital_inquiry,
            get_hospital_information_message=get_hospital_information_message,
            start_loading_animation=start_loading_animation,
            assess_risk=assess_risk,
            save_progress_data=save_progress_data,
            clean_ai_response=clean_ai_response,
            get_dynamic_config=get_dynamic_config,
        )
    
    return processor
