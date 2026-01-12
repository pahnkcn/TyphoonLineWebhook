"""
Comprehensive input validation and sanitization system for TyphoonLineWebhook
Uses marshmallow schemas with security-focused validation and sanitization
"""
import re
import html
import bleach
import logging
from datetime import datetime, date
from typing import Dict, Any, Optional, List, Union, Callable
from marshmallow import Schema, fields, validate, ValidationError, pre_load, post_load
from marshmallow.decorators import validates_schema
from functools import wraps
import unicodedata

class SecurityValidationError(ValidationError):
    """Custom validation error for security-related validation failures"""
    
    def __init__(self, message: str, field_name: Optional[str] = None, security_risk: str = "unknown"):
        super().__init__(message)
        self.field_name = field_name
        self.security_risk = security_risk
        self.timestamp = datetime.now()

class SanitizedString(fields.String):
    """
    String field with automatic sanitization capabilities
    """
    
    def __init__(self, 
                 sanitize_html: bool = True,
                 allow_unicode: bool = True,
                 normalize_whitespace: bool = True,
                 max_length: Optional[int] = None,
                 **kwargs):
        """
        Initialize sanitized string field
        
        Args:
            sanitize_html: Remove/escape HTML tags
            allow_unicode: Allow unicode characters
            normalize_whitespace: Normalize whitespace characters
            max_length: Maximum allowed length
        """
        super().__init__(**kwargs)
        self.sanitize_html = sanitize_html
        self.allow_unicode = allow_unicode
        self.normalize_whitespace = normalize_whitespace
        if max_length:
            self.validators.append(validate.Length(max=max_length))
    
    def _deserialize(self, value: Any, attr: str, data: Optional[Dict[str, Any]], **kwargs):
        """Deserialize and sanitize the input value"""
        # First, get the base string value
        value = super()._deserialize(value, attr, data, **kwargs)
        
        if value is None:
            return value
        
        # Apply sanitization
        sanitized_value = self._sanitize_string(value)
        
        return sanitized_value
    
    def _sanitize_string(self, value: str) -> str:
        """Apply sanitization rules to string"""
        if not isinstance(value, str):
            return value
        
        # Normalize unicode if needed
        if self.allow_unicode:
            value = unicodedata.normalize('NFKC', value)
        else:
            # Remove non-ASCII characters
            value = value.encode('ascii', 'ignore').decode('ascii')
        
        # Normalize whitespace
        if self.normalize_whitespace:
            # Replace multiple whitespace with single space
            value = re.sub(r'\s+', ' ', value)
            value = value.strip()
        
        # HTML sanitization
        if self.sanitize_html:
            # Allow only safe HTML tags and attributes
            allowed_tags = ['b', 'i', 'u', 'strong', 'em']
            allowed_attributes = {}
            value = bleach.clean(value, tags=allowed_tags, attributes=allowed_attributes, strip=True)
            # Also escape any remaining HTML entities
            value = html.escape(value, quote=False)
        
        return value

class ThaiTextString(SanitizedString):
    """
    String field specifically for Thai text with appropriate validation
    """
    
    def __init__(self, **kwargs):
        super().__init__(allow_unicode=True, **kwargs)
        # Thai Unicode range validation
        self.thai_pattern = re.compile(r'^[\u0E00-\u0E7F\s\d\w.,!?()[\]{}:;\'\"@#$%^&*+=<>/-]*$')
    
    def _deserialize(self, value: Any, attr: str, data: Optional[Dict[str, Any]], **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        
        if value and not self.thai_pattern.match(value):
            raise SecurityValidationError(
                "Text contains invalid characters for Thai content",
                field_name=attr,
                security_risk="invalid_characters"
            )
        
        return value

class SecureEmail(fields.Email):
    """
    Enhanced email field with additional security validation
    """
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Add length validation
        self.validators.append(validate.Length(max=254))  # RFC 5321 limit
        
        # Dangerous email patterns
        self.dangerous_patterns = [
            r'javascript:',
            r'data:',
            r'vbscript:',
            r'<script',
            r'onclick',
            r'onerror'
        ]
    
    def _deserialize(self, value: Any, attr: str, data: Optional[Dict[str, Any]], **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        
        if value:
            # Check for dangerous patterns
            value_lower = value.lower()
            for pattern in self.dangerous_patterns:
                if re.search(pattern, value_lower):
                    raise SecurityValidationError(
                        "Email contains potentially dangerous content",
                        field_name=attr,
                        security_risk="dangerous_content"
                    )
        
        return value

class SecureURL(fields.Url):
    """
    Enhanced URL field with security validation
    """
    
    def __init__(self, allowed_schemes: Optional[List[str]] = None, **kwargs):
        super().__init__(**kwargs)
        self.allowed_schemes = allowed_schemes or ['http', 'https']
        
        # Dangerous URL patterns
        self.dangerous_patterns = [
            r'javascript:',
            r'data:',
            r'vbscript:',
            r'file:',
            r'ftp:',
            r'<script',
            r'%3Cscript'
        ]
    
    def _deserialize(self, value: Any, attr: str, data: Optional[Dict[str, Any]], **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        
        if value:
            # Check scheme
            scheme = value.split(':', 1)[0].lower()
            if scheme not in self.allowed_schemes:
                raise SecurityValidationError(
                    f"URL scheme '{scheme}' not allowed",
                    field_name=attr,
                    security_risk="disallowed_scheme"
                )
            
            # Check for dangerous patterns
            value_lower = value.lower()
            for pattern in self.dangerous_patterns:
                if re.search(pattern, value_lower):
                    raise SecurityValidationError(
                        "URL contains potentially dangerous content",
                        field_name=attr,
                        security_risk="dangerous_content"
                    )
        
        return value

class LineUserIdField(fields.String):
    """
    Field for validating LINE User IDs
    """
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # LINE User ID pattern: U + 32 hexadecimal characters
        self.line_id_pattern = re.compile(r'^U[0-9a-f]{32}$')
        self.validators.append(validate.Length(equal=33))
    
    def _deserialize(self, value: Any, attr: str, data: Optional[Dict[str, Any]], **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        
        if value and not self.line_id_pattern.match(value):
            raise SecurityValidationError(
                "Invalid LINE User ID format",
                field_name=attr,
                security_risk="invalid_format"
            )
        
        return value

class RegistrationCodeField(fields.String):
    """
    Field for validating registration codes
    """
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Registration code: 8 alphanumeric characters (no confusing chars)
        self.code_pattern = re.compile(r'^[123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz]{8}$')
        self.validators.append(validate.Length(equal=8))
    
    def _deserialize(self, value: Any, attr: str, data: Optional[Dict[str, Any]], **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        
        if value and not self.code_pattern.match(value):
            raise SecurityValidationError(
                "Invalid registration code format",
                field_name=attr,
                security_risk="invalid_format"
            )
        
        return value

# Schema Definitions

class PromptInjectionDetector:
    """
    Detect and block LLM prompt injection attempts

    Protects against:
    - Instruction overrides
    - Role manipulation
    - System command injection
    - Jailbreak attempts
    - Delimiter attacks
    """

    # Suspicious patterns for LLM prompt injection
    INJECTION_PATTERNS = [
        # Instruction overrides (Thai + English)
        r'(?i)(ignore|forget|discard|override|ลืม|ละเลย|เพิกเฉย).{0,20}(previous|above|earlier|prior|ก่อนหน้า|ข้างบน|ก่อน).{0,20}(instruction|prompt|rule|command|คำสั่ง|กฎ)',
        r'(?i)(new|different|updated|ใหม่|เปลี่ยน|อัพเดท).{0,20}(instruction|prompt|role|personality|คำสั่ง|บทบาท|บุคลิก)',

        # Role manipulation (Thai + English)
        r'(?i)(you are now|ตอนนี้คุณคือ|คุณกลายเป็น|เปลี่ยนเป็น|ให้คุณเป็น).{0,30}(AI|assistant|chatbot|system|admin|ผู้ช่วย|บอท)',
        r'(?i)system\s*[:：]\s*(you|คุณ)',
        r'(?i)(act as|แสดงเป็น|ทำตัวเป็น|เล่นบทเป็น).{0,20}(AI|model|system|DAN)',

        # System command injection
        r'###\s*(system|admin|root|override)',
        r'</?(system|assistant|user|human)>',  # Chat markup injection
        r'\[(SYSTEM|ADMIN|OVERRIDE|ROOT)\]',
        r'<\|.*?\|>',  # Special tokens

        # Jailbreak attempts
        r'(?i)(DAN|jailbreak|bypass|unlock|jail\s*break)',
        r'(?i)(act as|pretend to be|simulate).{0,20}(without|ignore|bypass).{0,20}(restriction|limit|rule|filter|ข้อจำกัด|กฎ)',
        r'(?i)developer\s*mode',

        # Delimiter attacks
        r'---+\s*(end|stop|finish)\s*of\s*(prompt|instruction|คำสั่ง)',
        r'```.*?(system|instruction|prompt|คำสั่ง).*?```',

        # Role confusion
        r'(?i)(i|me|my|ฉัน|ผม|หนู|เรา)\s+(am|is|เป็น|คือ)\s+(AI|model|assistant|chatbot|grok|ผู้ช่วย)',

        # Meta prompting
        r'(?i)(summarize|translate|analyze|วิเคราะห์|สรุป|แปล)\s+(this\s+)?(prompt|instruction|system|คำสั่ง)',
        r'(?i)what\s+(is|are)\s+your\s+(instruction|prompt|rule|system)',
    ]

    # Suspicious keywords (context-dependent)
    SUSPICIOUS_KEYWORDS = {
        'high_risk': [
            'ignore instructions', 'ลืมคำสั่ง', 'bypass filter',
            'system override', 'new personality', 'admin mode',
            'forget everything', 'ลืมทุกอย่าง', 'reset personality',
            'sudo mode', 'god mode'
        ],
        'medium_risk': [
            'pretend to be', 'แกล้งทำเป็น', 'roleplay as',
            'simulate', 'imagine you are', 'ลองจินตนาการว่า'
        ]
    }

    @staticmethod
    def detect_injection(text: str) -> tuple:
        """
        Detect prompt injection attempts

        Args:
            text: User input text to validate

        Returns:
            Tuple of (is_injection, risk_level, reason)
            risk_level: "none", "medium", "high"
        """
        if not text or len(text.strip()) == 0:
            return False, "none", ""

        text_lower = text.lower()

        # Check high-risk keywords first
        for keyword in PromptInjectionDetector.SUSPICIOUS_KEYWORDS['high_risk']:
            if keyword.lower() in text_lower:
                return True, "high", f"high_risk_keyword: {keyword}"

        # Check regex patterns
        for pattern in PromptInjectionDetector.INJECTION_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
            if match:
                return True, "high", f"pattern_match: {pattern[:50]}..."

        # Check medium-risk keywords
        for keyword in PromptInjectionDetector.SUSPICIOUS_KEYWORDS['medium_risk']:
            if keyword.lower() in text_lower:
                return True, "medium", f"medium_risk_keyword: {keyword}"

        # Check for unusual character patterns
        if len(text) > 50:
            # Too many special characters (excluding Thai)
            # Count non-alphanumeric, non-Thai, non-whitespace characters
            special_chars = sum(
                1 for c in text
                if not c.isalnum() and not ('\u0E00' <= c <= '\u0E7F') and not c.isspace()
            )
            special_char_ratio = special_chars / len(text)

            if special_char_ratio > 0.3:
                return True, "medium", f"high_special_char_ratio: {special_char_ratio:.2f}"

            # Too many newlines (delimiter attacks)
            newline_count = text.count('\n')
            newline_ratio = newline_count / len(text)

            if newline_ratio > 0.15:
                return True, "medium", f"excessive_newlines: {newline_count}"

        # Check for prompt leakage attempts
        prompt_leak_patterns = [
            r'(?i)show\s+(me\s+)?(your|the)\s+(prompt|instruction|system\s+message)',
            r'(?i)what\s+(is|are)\s+your\s+(initial|original|system)\s+(instruction|prompt)',
        ]

        for pattern in prompt_leak_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return True, "medium", "prompt_leakage_attempt"

        return False, "none", ""

    @staticmethod
    def sanitize_for_llm(text: str) -> str:
        """
        Sanitize text to prevent injection while preserving meaning

        Args:
            text: Text to sanitize

        Returns:
            Sanitized text safe for LLM consumption
        """
        # Remove potential delimiters
        text = re.sub(r'---+', ' ', text)
        text = re.sub(r'===+', ' ', text)
        text = re.sub(r'###', ' ', text)

        # Remove markdown code blocks
        text = re.sub(r'```.*?```', '[โค้ดถูกลบ]', text, flags=re.DOTALL)

        # Remove chat markup
        text = re.sub(r'</(system|user|assistant|human)>', '', text, flags=re.IGNORECASE)
        text = re.sub(r'<(system|user|assistant|human)>', '', text, flags=re.IGNORECASE)

        # Remove special tokens
        text = re.sub(r'<\|.*?\|>', '', text)

        # Normalize excessive whitespace/newlines
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r'\s{3,}', '  ', text)

        return text.strip()


class UserMessageSchema(Schema):
    """Schema for validating user messages with prompt injection protection"""

    user_id = LineUserIdField(required=True)
    message = ThaiTextString(required=True, validate=validate.Length(min=1, max=2000))
    timestamp = fields.DateTime(missing=datetime.now)
    message_type = fields.String(validate=validate.OneOf(['text', 'sticker', 'image', 'audio']))

    @validates_schema
    def validate_message_content(self, data, **kwargs):
        """Comprehensive message validation including injection detection"""
        message = data.get('message', '')

        # 1. Check for prompt injection (CRITICAL SECURITY)
        is_injection, risk_level, reason = PromptInjectionDetector.detect_injection(message)

        if is_injection:
            if risk_level == "high":
                # Block high-risk injections
                logging.error(
                    f"SECURITY: Prompt injection detected - {reason}\n"
                    f"User: {data.get('user_id', 'unknown')[:8]}...\n"
                    f"Message: {message[:100]}..."
                )
                raise SecurityValidationError(
                    "ข้อความมีเนื้อหาที่อาจเป็นอันตราย กรุณาส่งข้อความใหม่",
                    field_name='message',
                    security_risk=f"prompt_injection_{reason}"
                )
            elif risk_level == "medium":
                # Sanitize medium-risk injections but allow with warning
                logging.warning(
                    f"SECURITY: Possible injection attempt - {reason}\n"
                    f"User: {data.get('user_id', 'unknown')[:8]}...\n"
                    f"Message: {message[:100]}..."
                )
                # Sanitize the message
                data['message'] = PromptInjectionDetector.sanitize_for_llm(message)

        # 2. Check for XSS/HTML injection
        dangerous_patterns = [
            r'<script[^>]*>.*?</script>',
            r'javascript:',
            r'on\w+\s*=',
            r'<iframe',
            r'<object',
            r'<embed'
        ]

        for pattern in dangerous_patterns:
            if re.search(pattern, message, re.IGNORECASE):
                raise SecurityValidationError(
                    "Message contains potentially dangerous content",
                    security_risk="script_injection"
                )

class RegistrationSchema(Schema):
    """Schema for user registration"""
    
    user_id = LineUserIdField(required=True)
    registration_code = RegistrationCodeField(required=True)
    ip_address = fields.String(validate=validate.Length(max=45))  # IPv6 max length
    user_agent = SanitizedString(validate=validate.Length(max=500))
    
    @validates_schema
    def validate_registration_data(self, data, **kwargs):
        """Validate registration data"""
        # Additional security checks can be added here
        pass

class UserProfileSchema(Schema):
    """Schema for user profile data"""
    
    user_id = LineUserIdField(required=True)
    display_name = SanitizedString(validate=validate.Length(max=100))
    email = SecureEmail(allow_none=True)
    phone = fields.String(
        validate=validate.Regexp(r'^\+?[1-9]\d{1,14}$'),  # E.164 format
        allow_none=True
    )
    age = fields.Integer(validate=validate.Range(min=13, max=120))
    gender = fields.String(
        validate=validate.OneOf(['male', 'female', 'other', 'prefer_not_to_say']),
        allow_none=True
    )
    
    @pre_load
    def preprocess_data(self, data, **kwargs):
        """Preprocess data before validation"""
        # Normalize phone number
        if 'phone' in data and data['phone']:
            phone = re.sub(r'[^\d+]', '', data['phone'])
            data['phone'] = phone
        
        return data

class ConversationSchema(Schema):
    """Schema for conversation data"""
    
    user_id = LineUserIdField(required=True)
    user_message = ThaiTextString(required=True, validate=validate.Length(max=2000))
    bot_response = ThaiTextString(required=True, validate=validate.Length(max=4000))
    timestamp = fields.DateTime(required=True)
    token_count = fields.Integer(validate=validate.Range(min=0, max=10000))
    important_flag = fields.Boolean(missing=False)
    risk_level = fields.String(validate=validate.OneOf(['general', 'medium', 'high', 'low']))

class HealthCheckSchema(Schema):
    """Schema for health check requests"""
    
    component = fields.String(
        validate=validate.OneOf(['database', 'redis', 'external_api', 'all']),
        missing='all'
    )
    detailed = fields.Boolean(missing=False)

class SystemConfigSchema(Schema):
    """Schema for system configuration"""
    
    max_message_length = fields.Integer(validate=validate.Range(min=100, max=5000))
    session_timeout = fields.Integer(validate=validate.Range(min=300, max=86400))  # 5 min to 24 hours
    rate_limit_per_hour = fields.Integer(validate=validate.Range(min=10, max=1000))
    debug_mode = fields.Boolean(missing=False)

# Validation Decorator and Helper Functions

def validate_input(schema_class: Schema, error_handler: Optional[Callable] = None):
    """
    Decorator for automatic input validation using marshmallow schema
    
    Args:
        schema_class: Marshmallow schema class
        error_handler: Optional custom error handler function
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Extract data from request or function arguments
            if 'request' in kwargs:
                # Flask request object
                data = kwargs['request'].get_json() or {}
            elif args and isinstance(args[0], dict):
                # First argument is data dict
                data = args[0]
            else:
                # Try to extract from kwargs
                data = kwargs
            
            # Validate data
            schema = schema_class()
            try:
                validated_data = schema.load(data)
                
                # Replace original data with validated data
                if 'request' in kwargs:
                    kwargs['validated_data'] = validated_data
                elif args and isinstance(args[0], dict):
                    args = (validated_data,) + args[1:]
                else:
                    kwargs.update(validated_data)
                
                return func(*args, **kwargs)
                
            except ValidationError as e:
                if error_handler:
                    return error_handler(e)
                else:
                    # Default error handling
                    logging.warning(f"Validation error in {func.__name__}: {e.messages}")
                    raise SecurityValidationError(
                        f"Input validation failed: {e.messages}",
                        security_risk="validation_error"
                    )
            except SecurityValidationError as e:
                logging.error(f"Security validation error in {func.__name__}: {e}")
                if error_handler:
                    return error_handler(e)
                else:
                    raise
        return wrapper
    return decorator

def sanitize_html_content(content: str, allowed_tags: List[str] = None) -> str:
    """
    Sanitize HTML content using bleach
    
    Args:
        content: HTML content to sanitize
        allowed_tags: List of allowed HTML tags
        
    Returns:
        Sanitized HTML content
    """
    if allowed_tags is None:
        allowed_tags = ['b', 'i', 'u', 'strong', 'em', 'p', 'br']
    
    allowed_attributes = {
        '*': ['class'],
        'a': ['href', 'title'],
    }
    
    # Clean HTML
    clean_content = bleach.clean(
        content,
        tags=allowed_tags,
        attributes=allowed_attributes,
        strip=True
    )
    
    return clean_content

def validate_and_sanitize_user_input(
    data: Dict[str, Any],
    schema_class: Schema,
    sanitize: bool = True
) -> Tuple[bool, Union[Dict[str, Any], Dict[str, str]]]:
    """
    Validate and optionally sanitize user input
    
    Args:
        data: Input data to validate
        schema_class: Marshmallow schema class
        sanitize: Whether to apply sanitization
        
    Returns:
        Tuple of (success, validated_data_or_errors)
    """
    try:
        schema = schema_class()
        validated_data = schema.load(data)
        
        # Apply additional sanitization if requested
        if sanitize:
            for key, value in validated_data.items():
                if isinstance(value, str):
                    validated_data[key] = sanitize_html_content(value)
        
        return True, validated_data
        
    except ValidationError as e:
        return False, e.messages
    except SecurityValidationError as e:
        return False, {'security_error': str(e)}

def create_validation_middleware():
    """
    Create middleware for automatic request validation
    """
    def validation_middleware(app):
        @app.before_request
        def validate_request():
            # Skip validation for certain endpoints
            skip_endpoints = ['/health', '/favicon.ico']
            if any(request.path.startswith(endpoint) for endpoint in skip_endpoints):
                return
            
            # Validate common security headers
            user_agent = request.headers.get('User-Agent', '')
            if len(user_agent) > 1000:  # Suspiciously long user agent
                logging.warning(f"Suspicious User-Agent length: {len(user_agent)}")
                return jsonify({'error': 'Invalid request'}), 400
            
            # Check for suspicious patterns in headers
            suspicious_patterns = [
                r'<script',
                r'javascript:',
                r'data:',
                r'vbscript:'
            ]
            
            for header_name, header_value in request.headers:
                if isinstance(header_value, str):
                    for pattern in suspicious_patterns:
                        if re.search(pattern, header_value, re.IGNORECASE):
                            logging.warning(f"Suspicious pattern in header {header_name}: {pattern}")
                            return jsonify({'error': 'Invalid request'}), 400
        
        return app
    
    return validation_middleware

# Rate Limiting Schema
class RateLimitSchema(Schema):
    """Schema for rate limiting configuration"""
    
    requests_per_minute = fields.Integer(validate=validate.Range(min=1, max=1000))
    requests_per_hour = fields.Integer(validate=validate.Range(min=1, max=10000))
    burst_limit = fields.Integer(validate=validate.Range(min=1, max=100))

# Export commonly used schemas and functions
__all__ = [
    'UserMessageSchema',
    'RegistrationSchema',
    'UserProfileSchema',
    'ConversationSchema',
    'HealthCheckSchema',
    'SystemConfigSchema',
    'RateLimitSchema',
    'validate_input',
    'sanitize_html_content',
    'validate_and_sanitize_user_input',
    'create_validation_middleware',
    'SecurityValidationError',
    'SanitizedString',
    'ThaiTextString',
    'SecureEmail',
    'SecureURL',
    'LineUserIdField',
    'RegistrationCodeField'
]