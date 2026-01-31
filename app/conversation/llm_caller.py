"""
Unified LLM Caller module for the 'ใจดี' chatbot.
Single source of truth for Grok API calls with retry, timeout, and rate-limit handling.
"""

import logging
import time
import math
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass
from enum import Enum
import concurrent.futures


class LLMCallResult(Enum):
    """Result type for LLM calls."""
    SUCCESS = "success"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    API_ERROR = "api_error"
    EMPTY_RESPONSE = "empty_response"


@dataclass
class LLMCallConfig:
    """Configuration for LLM calls."""
    base_timeout: int = 30
    max_timeout: int = 120
    max_retries: int = 3
    retry_base_delay: float = 2.0
    rate_limit_default_wait: int = 60
    adaptive_timeout: bool = True


@dataclass
class LLMCallResponse:
    """Response from LLM call."""
    result: LLMCallResult
    text: Optional[str] = None
    error: Optional[str] = None
    retries_used: int = 0
    total_duration: float = 0.0
    rate_limit_wait: Optional[int] = None


class LLMCaller:
    """
    Unified wrapper for Grok API calls.
    
    Provides:
    - Single retry policy
    - Adaptive timeout based on context size
    - Rate limit handling with user notification
    - Consistent error handling
    """
    
    def __init__(
        self,
        grok_client: Any,
        model: str,
        token_counter: Optional[Any] = None,
        config: Optional[LLMCallConfig] = None,
        on_rate_limit: Optional[Callable[[str, int], None]] = None,
    ):
        """
        Initialize the LLM caller.
        
        Args:
            grok_client: Grok client instance
            model: Model name to use
            token_counter: Optional token counter for adaptive timeout
            config: Optional configuration
            on_rate_limit: Optional callback when rate limited (user_id, wait_time)
        """
        self.grok_client = grok_client
        self.model = model
        self.token_counter = token_counter
        self.config = config or LLMCallConfig()
        self.on_rate_limit = on_rate_limit
    
    def call(
        self,
        messages: List[Dict[str, str]],
        user_id: Optional[str] = None,
        dynamic_config: Optional[Dict[str, Any]] = None,
    ) -> LLMCallResponse:
        """
        Make an LLM call with retry and timeout handling.
        
        Args:
            messages: List of messages for the API
            user_id: Optional user ID for rate limit notifications
            dynamic_config: Optional dynamic configuration (temperature, etc.)
            
        Returns:
            LLMCallResponse with result and text
        """
        start_time = time.time()
        retries_used = 0
        last_error = None
        
        # Calculate adaptive timeout
        timeout = self._calculate_timeout(messages)
        
        for attempt in range(self.config.max_retries):
            retries_used = attempt
            
            try:
                # Make the API call with timeout
                response_text = self._call_with_timeout(
                    messages=messages,
                    timeout=timeout,
                    dynamic_config=dynamic_config or {},
                )
                
                if not response_text:
                    raise ValueError("Empty response from API")
                
                return LLMCallResponse(
                    result=LLMCallResult.SUCCESS,
                    text=response_text,
                    retries_used=retries_used,
                    total_duration=time.time() - start_time,
                )
                
            except concurrent.futures.TimeoutError:
                last_error = f"Timeout after {timeout}s"
                logging.warning(
                    f"LLM call timeout (attempt {attempt + 1}/{self.config.max_retries})"
                )
                
                if attempt < self.config.max_retries - 1:
                    # Increase timeout for next attempt
                    timeout = min(timeout * 1.5, self.config.max_timeout)
                    time.sleep(self.config.retry_base_delay * (attempt + 1))
                    
            except RateLimitError as e:
                wait_time = getattr(e, 'retry_after', self.config.rate_limit_default_wait)
                logging.warning(f"Rate limited, waiting {wait_time}s")
                
                # Notify user if callback provided
                if self.on_rate_limit and user_id:
                    self.on_rate_limit(user_id, wait_time)
                
                # Wait and retry
                time.sleep(wait_time)
                retries_used = attempt
                
            except Exception as e:
                last_error = str(e)
                logging.warning(
                    f"LLM call error (attempt {attempt + 1}/{self.config.max_retries}): {e}"
                )
                
                if attempt < self.config.max_retries - 1:
                    delay = self.config.retry_base_delay ** (attempt + 1)
                    time.sleep(delay)
        
        # All retries exhausted
        total_duration = time.time() - start_time
        
        if "timeout" in str(last_error).lower():
            return LLMCallResponse(
                result=LLMCallResult.TIMEOUT,
                error=last_error,
                retries_used=retries_used,
                total_duration=total_duration,
            )
        else:
            return LLMCallResponse(
                result=LLMCallResult.API_ERROR,
                error=last_error,
                retries_used=retries_used,
                total_duration=total_duration,
            )
    
    def _call_with_timeout(
        self,
        messages: List[Dict[str, str]],
        timeout: int,
        dynamic_config: Dict[str, Any],
    ) -> str:
        """
        Make API call with timeout using ThreadPoolExecutor.
        
        Args:
            messages: Messages for the API
            timeout: Timeout in seconds
            dynamic_config: Dynamic configuration
            
        Returns:
            Response text
            
        Raises:
            TimeoutError: If call times out
            Exception: If API call fails
        """
        def _call() -> str:
            return self.grok_client.send_chat(
                messages=messages,
                model=self.model,
                **dynamic_config,
            )
        
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(_call)
            try:
                return future.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                raise concurrent.futures.TimeoutError(
                    f"LLM call timed out after {timeout} seconds"
                )
    
    def _calculate_timeout(self, messages: List[Dict[str, str]]) -> int:
        """
        Calculate adaptive timeout based on message size.
        
        Args:
            messages: Messages to send
            
        Returns:
            Timeout in seconds
        """
        if not self.config.adaptive_timeout:
            return self.config.base_timeout
        
        base_timeout = self.config.base_timeout
        max_timeout = self.config.max_timeout
        
        token_count = 0
        char_count = 0
        
        # Try to count tokens
        if self.token_counter:
            try:
                token_count = self.token_counter.count_message_tokens(messages)
            except Exception as e:
                logging.debug(f"Token counting failed: {e}")
        
        # Count characters as fallback
        try:
            char_count = sum(len(m.get('content', '')) for m in messages)
        except Exception:
            pass
        
        # Calculate adaptive timeout
        adaptive_timeout = base_timeout
        
        # Add time for large token counts
        if token_count > 1000:
            extra_units = math.ceil((token_count - 1000) / 400)
            adaptive_timeout += extra_units * 5
        
        # Add time for large character counts
        if char_count > 4000:
            extra_units = math.ceil((char_count - 4000) / 2000)
            adaptive_timeout += extra_units * 5
        
        # Clamp to range
        adaptive_timeout = max(base_timeout, min(adaptive_timeout, max_timeout))
        
        logging.debug(
            f"Adaptive timeout: base={base_timeout}s, tokens={token_count}, "
            f"chars={char_count}, result={adaptive_timeout}s"
        )
        
        return adaptive_timeout


class RateLimitError(Exception):
    """Exception for rate limit errors."""
    
    def __init__(self, message: str, retry_after: Optional[int] = None):
        super().__init__(message)
        self.retry_after = retry_after


def create_llm_caller(
    grok_client: Any,
    model: str,
    token_counter: Optional[Any] = None,
    on_rate_limit: Optional[Callable[[str, int], None]] = None,
) -> LLMCaller:
    """
    Factory function to create an LLMCaller.
    
    Args:
        grok_client: Grok client instance
        model: Model name
        token_counter: Optional token counter
        on_rate_limit: Optional rate limit callback
        
    Returns:
        Configured LLMCaller instance
    """
    return LLMCaller(
        grok_client=grok_client,
        model=model,
        token_counter=token_counter,
        on_rate_limit=on_rate_limit,
    )
