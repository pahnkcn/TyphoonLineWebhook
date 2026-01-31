"""
Input Validation integration for the conversation pipeline.
Wires the existing input_validation.py into the message flow.
"""

import logging
from typing import Tuple, Optional, Dict, Any
from dataclasses import dataclass
from enum import Enum

from ..input_validation import (
    UserMessageSchema,
    PromptInjectionDetector,
    SecurityValidationError,
)


class ValidationResult(Enum):
    """Result of input validation."""
    VALID = "valid"
    SANITIZED = "sanitized"
    BLOCKED = "blocked"
    ERROR = "error"


@dataclass
class ValidationInfo:
    """Information about validation result."""
    result: ValidationResult
    original_message: str
    sanitized_message: Optional[str] = None
    risk_level: str = "none"
    reason: Optional[str] = None
    security_risk: Optional[str] = None


class InputValidator:
    """
    Validates and sanitizes user input before processing.
    
    Integrates with the existing input_validation.py module to provide:
    - Prompt injection detection
    - XSS/HTML injection protection
    - Message sanitization
    """
    
    def __init__(self, block_high_risk: bool = True, sanitize_medium_risk: bool = True):
        """
        Initialize the input validator.
        
        Args:
            block_high_risk: Whether to block high-risk messages
            sanitize_medium_risk: Whether to sanitize medium-risk messages
        """
        self.block_high_risk = block_high_risk
        self.sanitize_medium_risk = sanitize_medium_risk
        self.schema = UserMessageSchema()
    
    def validate_message(
        self,
        user_id: str,
        message: str,
    ) -> ValidationInfo:
        """
        Validate a user message.
        
        Args:
            user_id: LINE User ID
            message: User's message text
            
        Returns:
            ValidationInfo with result and details
        """
        original_message = message
        
        try:
            # 1. Check for prompt injection
            is_injection, risk_level, reason = PromptInjectionDetector.detect_injection(message)
            
            if is_injection:
                if risk_level == "high" and self.block_high_risk:
                    logging.warning(
                        f"SECURITY: Blocked high-risk injection from user {user_id[:8]}...: {reason}"
                    )
                    return ValidationInfo(
                        result=ValidationResult.BLOCKED,
                        original_message=original_message,
                        risk_level=risk_level,
                        reason=reason,
                        security_risk=f"prompt_injection_{reason}",
                    )
                
                elif risk_level == "medium" and self.sanitize_medium_risk:
                    logging.info(
                        f"SECURITY: Sanitizing medium-risk message from user {user_id[:8]}...: {reason}"
                    )
                    sanitized = PromptInjectionDetector.sanitize_for_llm(message)
                    return ValidationInfo(
                        result=ValidationResult.SANITIZED,
                        original_message=original_message,
                        sanitized_message=sanitized,
                        risk_level=risk_level,
                        reason=reason,
                    )
            
            # 2. Validate with schema (catches XSS, length issues, etc.)
            try:
                validated_data = self.schema.load({
                    'user_id': user_id,
                    'message': message,
                })
                # Schema may have sanitized the message
                validated_message = validated_data.get('message', message)
                
                if validated_message != message:
                    return ValidationInfo(
                        result=ValidationResult.SANITIZED,
                        original_message=original_message,
                        sanitized_message=validated_message,
                        risk_level="low",
                        reason="schema_sanitization",
                    )
                    
            except SecurityValidationError as e:
                logging.warning(
                    f"SECURITY: Schema validation blocked message from user {user_id[:8]}...: {e}"
                )
                return ValidationInfo(
                    result=ValidationResult.BLOCKED,
                    original_message=original_message,
                    risk_level="high",
                    reason=str(e),
                    security_risk=getattr(e, 'security_risk', 'validation_error'),
                )
            
            # 3. Message is valid
            return ValidationInfo(
                result=ValidationResult.VALID,
                original_message=original_message,
                sanitized_message=message,
                risk_level="none",
            )
            
        except Exception as e:
            logging.error(f"Validation error for user {user_id[:8]}...: {e}")
            return ValidationInfo(
                result=ValidationResult.ERROR,
                original_message=original_message,
                reason=str(e),
            )
    
    def is_valid(self, info: ValidationInfo) -> bool:
        """Check if validation result allows processing."""
        return info.result in (ValidationResult.VALID, ValidationResult.SANITIZED)
    
    def get_message(self, info: ValidationInfo) -> str:
        """Get the message to use (sanitized if applicable)."""
        if info.sanitized_message is not None:
            return info.sanitized_message
        return info.original_message


def validate_user_input(
    user_id: str,
    message: str,
    block_high_risk: bool = True,
    sanitize_medium_risk: bool = True,
) -> Tuple[bool, str, ValidationInfo]:
    """
    Convenience function to validate user input.
    
    This is the main entry point for input validation in the conversation pipeline.
    
    Args:
        user_id: LINE User ID
        message: User's message text
        block_high_risk: Whether to block high-risk messages
        sanitize_medium_risk: Whether to sanitize medium-risk messages
        
    Returns:
        Tuple of (is_valid, message_to_use, validation_info)
    """
    validator = InputValidator(
        block_high_risk=block_high_risk,
        sanitize_medium_risk=sanitize_medium_risk,
    )
    
    info = validator.validate_message(user_id, message)
    is_valid = validator.is_valid(info)
    message_to_use = validator.get_message(info) if is_valid else message
    
    return is_valid, message_to_use, info


def create_validation_callback() -> callable:
    """
    Create a validation callback function for use with the orchestrator.
    
    Returns:
        Callback function with signature (user_id, message) -> (is_valid, message, info)
    """
    def callback(user_id: str, message: str) -> Tuple[bool, str, ValidationInfo]:
        return validate_user_input(user_id, message)
    
    return callback
