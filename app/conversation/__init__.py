"""
Conversation package for the 'ใจดี' chatbot.
Provides modular conversation handling with clear separation of concerns.
"""

from .orchestrator import ConversationOrchestrator, create_orchestrator
from .context_builder import ContextBuilder, ContextConfig, BuiltContext
from .commands import CommandHandler, CommandResult, CommandResponse
from .reply_sender import ReplySender, SendResult, SendResponse
from .followups import FollowUpManager, FollowUpStatus, FollowUpInfo
from .validation import (
    InputValidator,
    ValidationResult,
    ValidationInfo,
    validate_user_input,
    create_validation_callback,
)
from .llm_caller import (
    LLMCaller,
    LLMCallConfig,
    LLMCallResult,
    LLMCallResponse,
    RateLimitError,
    create_llm_caller,
)
from .integration import (
    init_conversation_system,
    get_orchestrator,
    process_user_message_v2,
    create_message_processor,
)

__all__ = [
    # Orchestrator
    'ConversationOrchestrator',
    'create_orchestrator',
    # Context Builder
    'ContextBuilder',
    'ContextConfig',
    'BuiltContext',
    # Commands
    'CommandHandler',
    'CommandResult',
    'CommandResponse',
    # Reply Sender
    'ReplySender',
    'SendResult',
    'SendResponse',
    # Follow-ups
    'FollowUpManager',
    'FollowUpStatus',
    'FollowUpInfo',
    # Validation
    'InputValidator',
    'ValidationResult',
    'ValidationInfo',
    'validate_user_input',
    'create_validation_callback',
    # LLM Caller
    'LLMCaller',
    'LLMCallConfig',
    'LLMCallResult',
    'LLMCallResponse',
    'RateLimitError',
    'create_llm_caller',
    # Integration
    'init_conversation_system',
    'get_orchestrator',
    'process_user_message_v2',
    'create_message_processor',
]
