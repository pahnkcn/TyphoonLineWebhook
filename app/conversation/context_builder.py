"""
Context Builder module for the 'ใจดี' chatbot.
Builds LLM-ready message lists with a single, deterministic system message.
"""

import logging
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field


@dataclass
class ContextConfig:
    """Configuration for context building."""
    max_context_tokens: int = 450000
    max_recent_messages: int = 100
    include_summary: bool = True
    include_user_context: bool = True
    include_crisis_policies: bool = True


@dataclass
class BuiltContext:
    """Result of context building operation."""
    messages: List[Dict[str, str]]
    system_message: Dict[str, str]
    token_count: int
    has_summary: bool = False
    has_user_context: bool = False
    truncated: bool = False


class ContextBuilder:
    """
    Builds LLM-ready message lists with exactly one system message.
    
    Responsibilities:
    - Compose a single system message from multiple sources
    - Manage conversation history within token limits
    - Include user context from forms
    - Include conversation summaries
    - Apply runtime policies (crisis rules, style constraints)
    """
    
    def __init__(
        self,
        base_system_prompt: Dict[str, str],
        token_counter: Any,
        config: Optional[ContextConfig] = None
    ):
        """
        Initialize the context builder.
        
        Args:
            base_system_prompt: The base persona system message (SYSTEM_MESSAGES)
            token_counter: TokenCounter instance for counting tokens
            config: Optional configuration overrides
        """
        self.base_system_prompt = base_system_prompt
        self.token_counter = token_counter
        self.config = config or ContextConfig()
    
    def build_context(
        self,
        conversation_history: List[Dict[str, str]],
        user_context: Optional[str] = None,
        summary: Optional[str] = None,
        crisis_override: Optional[str] = None,
        style_constraints: Optional[str] = None,
    ) -> BuiltContext:
        """
        Build the complete context for an LLM call.
        
        This method creates exactly ONE system message by combining:
        1. Base persona (SYSTEM_MESSAGES)
        2. User context from form (if present)
        3. Conversation summary (if present)
        4. Runtime policies (crisis rules, style constraints)
        
        Args:
            conversation_history: List of user/assistant message pairs
            user_context: User context from assessment form
            summary: Conversation summary from previous sessions
            crisis_override: Crisis-specific instructions (supersedes style)
            style_constraints: Style constraints for response
            
        Returns:
            BuiltContext with the assembled messages and metadata
        """
        # Build the unified system message
        system_content = self._build_system_content(
            user_context=user_context,
            summary=summary,
            crisis_override=crisis_override,
            style_constraints=style_constraints,
        )
        
        system_message = {"role": "system", "content": system_content}
        
        # Filter out any existing system messages and system_summary from history
        filtered_history = self._filter_history(conversation_history)
        
        # Build the final message list
        messages = [system_message] + filtered_history
        
        # Calculate token count
        token_count = self._count_tokens(messages)
        
        # Truncate if necessary
        truncated = False
        if token_count > self.config.max_context_tokens:
            messages, token_count = self._truncate_to_fit(
                system_message, 
                filtered_history,
                self.config.max_context_tokens
            )
            truncated = True
        
        return BuiltContext(
            messages=messages,
            system_message=system_message,
            token_count=token_count,
            has_summary=summary is not None and len(summary.strip()) > 0,
            has_user_context=user_context is not None and len(user_context.strip()) > 0,
            truncated=truncated,
        )
    
    def _build_system_content(
        self,
        user_context: Optional[str] = None,
        summary: Optional[str] = None,
        crisis_override: Optional[str] = None,
        style_constraints: Optional[str] = None,
    ) -> str:
        """
        Build a single unified system message content.
        
        The order of composition:
        1. Base persona
        2. User context (if available)
        3. Summary (if available)
        4. Crisis override OR style constraints
        """
        parts = []
        
        # 1. Base persona (always included)
        base_content = self.base_system_prompt.get("content", "")
        parts.append(base_content)
        
        # 2. User context from assessment form
        if user_context and self.config.include_user_context:
            parts.append(self._format_user_context(user_context))
        
        # 3. Conversation summary
        if summary and self.config.include_summary:
            parts.append(self._format_summary(summary))
        
        # 4. Runtime policies (crisis supersedes style)
        if crisis_override and self.config.include_crisis_policies:
            parts.append(self._format_crisis_override(crisis_override))
        elif style_constraints:
            parts.append(self._format_style_constraints(style_constraints))
        
        return "\n\n".join(parts)
    
    def _format_user_context(self, user_context: str) -> str:
        """Format user context section."""
        return (
            "---\n"
            "บริบทผู้ใช้จากแบบประเมิน:\n"
            f"{user_context}\n"
            "ใช้ข้อมูลนี้เพื่อให้คำปรึกษาที่เหมาะสมกับสถานการณ์ของผู้ใช้"
        )
    
    def _format_summary(self, summary: str) -> str:
        """Format conversation summary section."""
        return (
            "---\n"
            "สรุปการสนทนาก่อนหน้า (สำหรับ AI เท่านั้น):\n"
            f"{summary}"
        )
    
    def _format_crisis_override(self, crisis_override: str) -> str:
        """Format crisis override section."""
        return (
            "---\n"
            "⚠️ คำสั่งพิเศษสำหรับสถานการณ์ฉุกเฉิน:\n"
            f"{crisis_override}"
        )
    
    def _format_style_constraints(self, style_constraints: str) -> str:
        """Format style constraints section."""
        return (
            "---\n"
            "ข้อกำหนดรูปแบบการตอบ:\n"
            f"{style_constraints}"
        )
    
    def _filter_history(
        self, 
        conversation_history: List[Dict[str, str]]
    ) -> List[Dict[str, str]]:
        """
        Filter conversation history to remove system messages and summaries.
        
        Args:
            conversation_history: Raw conversation history
            
        Returns:
            Filtered history with only user/assistant messages
        """
        filtered = []
        for msg in conversation_history:
            role = msg.get("role", "")
            # Keep only user and assistant messages
            if role in ("user", "assistant"):
                filtered.append({
                    "role": role,
                    "content": msg.get("content", "")
                })
        return filtered
    
    def _count_tokens(self, messages: List[Dict[str, str]]) -> int:
        """Count tokens in the message list."""
        try:
            return self.token_counter.count_message_tokens(messages)
        except Exception as e:
            logging.warning(f"Token counting failed, using estimate: {e}")
            # Fallback: estimate ~4 chars per token
            total_chars = sum(len(m.get("content", "")) for m in messages)
            return total_chars // 4
    
    def _truncate_to_fit(
        self,
        system_message: Dict[str, str],
        history: List[Dict[str, str]],
        max_tokens: int,
    ) -> tuple:
        """
        Truncate history to fit within token limit while preserving recent messages.
        
        Strategy:
        1. Always keep system message
        2. Keep as many recent messages as possible
        3. Remove oldest messages first
        """
        system_tokens = self._count_tokens([system_message])
        available_tokens = max_tokens - system_tokens - 1000  # Buffer
        
        if available_tokens <= 0:
            logging.error("System message exceeds token limit")
            return [system_message], system_tokens
        
        # Start from most recent and work backwards
        kept_messages = []
        current_tokens = 0
        
        for msg in reversed(history):
            msg_tokens = self._count_tokens([msg])
            if current_tokens + msg_tokens <= available_tokens:
                kept_messages.insert(0, msg)
                current_tokens += msg_tokens
            else:
                break
        
        final_messages = [system_message] + kept_messages
        final_tokens = system_tokens + current_tokens
        
        logging.info(
            f"Truncated history from {len(history)} to {len(kept_messages)} messages "
            f"({final_tokens} tokens)"
        )
        
        return final_messages, final_tokens
    
    def add_user_message(
        self, 
        context: BuiltContext, 
        user_message: str
    ) -> BuiltContext:
        """
        Add a user message to an existing context.
        
        Args:
            context: Existing built context
            user_message: New user message to add
            
        Returns:
            Updated BuiltContext
        """
        new_messages = context.messages + [{"role": "user", "content": user_message}]
        new_token_count = self._count_tokens(new_messages)
        
        return BuiltContext(
            messages=new_messages,
            system_message=context.system_message,
            token_count=new_token_count,
            has_summary=context.has_summary,
            has_user_context=context.has_user_context,
            truncated=context.truncated,
        )
    
    def add_assistant_message(
        self, 
        context: BuiltContext, 
        assistant_message: str
    ) -> BuiltContext:
        """
        Add an assistant message to an existing context.
        
        Args:
            context: Existing built context
            assistant_message: New assistant message to add
            
        Returns:
            Updated BuiltContext
        """
        new_messages = context.messages + [{"role": "assistant", "content": assistant_message}]
        new_token_count = self._count_tokens(new_messages)
        
        return BuiltContext(
            messages=new_messages,
            system_message=context.system_message,
            token_count=new_token_count,
            has_summary=context.has_summary,
            has_user_context=context.has_user_context,
            truncated=context.truncated,
        )
    
    def extract_messages_for_api(self, context: BuiltContext) -> List[Dict[str, str]]:
        """
        Extract messages ready for API call.
        
        This is the final step before sending to the LLM API.
        The messages are already properly formatted with a single system message.
        
        Args:
            context: Built context
            
        Returns:
            List of messages ready for API call
        """
        return context.messages
