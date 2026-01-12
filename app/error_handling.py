"""
Centralized error handling system for TyphoonLineWebhook chatbot
Provides consistent error management, circuit breaker pattern, and monitoring
"""
import logging
import time
import json
import traceback
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Callable, List, Union
from enum import Enum
from functools import wraps
from collections import defaultdict, deque
import threading

class ErrorSeverity(Enum):
    """Error severity levels"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class ErrorCategory(Enum):
    """Error categories for better classification"""
    DATABASE = "database"
    EXTERNAL_API = "external_api"
    AUTHENTICATION = "authentication"
    VALIDATION = "validation"
    NETWORK = "network"
    SYSTEM = "system"
    USER_INPUT = "user_input"
    BUSINESS_LOGIC = "business_logic"

class CircuitState(Enum):
    """Circuit breaker states"""
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

class ChatbotError(Exception):
    """Enhanced custom exception for chatbot with better error context"""
    
    def __init__(
        self,
        message: str,
        category: ErrorCategory,
        severity: ErrorSeverity = ErrorSeverity.MEDIUM,
        user_message: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        original_error: Optional[Exception] = None,
        retry_able: bool = True
    ):
        super().__init__(message)
        self.message = message
        self.category = category
        self.severity = severity
        self.user_message = user_message or self._get_default_user_message()
        self.context = context or {}
        self.original_error = original_error
        self.retry_able = retry_able
        self.timestamp = datetime.now()
    
    def _get_default_user_message(self) -> str:
        """Get default user-friendly message based on category and severity"""
        if self.severity == ErrorSeverity.CRITICAL:
            return "ขออภัยครับ เกิดข้อผิดพลาดร้ายแรง กรุณาติดต่อผู้ดูแลระบบ"
        
        category_messages = {
            ErrorCategory.DATABASE: "ขออภัยครับ เกิดปัญหาในการเข้าถึงข้อมูล กรุณาลองใหม่อีกครั้ง",
            ErrorCategory.EXTERNAL_API: "ขออภัยครับ ระบบกำลังมีการใช้งานสูง กรุณารอสักครู่และลองใหม่",
            ErrorCategory.NETWORK: "ขออภัยครับ เกิดปัญหาการเชื่อมต่อ กรุณาตรวจสอบการเชื่อมต่ออินเทอร์เน็ต",
            ErrorCategory.VALIDATION: "ขออภัยครับ ข้อมูลที่ส่งมาไม่ถูกต้อง กรุณาตรวจสอบและลองใหม่",
            ErrorCategory.AUTHENTICATION: "ขออภัยครับ เกิดปัญหาในการยืนยันตัวตน กรุณาลองใหม่อีกครั้ง"
        }
        
        return category_messages.get(
            self.category,
            "ขออภัยครับ เกิดข้อผิดพลาดในการประมวลผล กรุณาลองใหม่ในอีกสักครู่"
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert error to dictionary for logging/monitoring"""
        return {
            'message': self.message,
            'category': self.category.value,
            'severity': self.severity.value,
            'user_message': self.user_message,
            'context': self.context,
            'retry_able': self.retry_able,
            'timestamp': self.timestamp.isoformat(),
            'original_error': str(self.original_error) if self.original_error else None
        }

class CircuitBreaker:
    """
    Circuit breaker implementation for external service calls
    """
    
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        timeout: int = 60,
        expected_exception: tuple = (Exception,)
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.expected_exception = expected_exception
        
        self.failure_count = 0
        self.last_failure_time = None
        self.state = CircuitState.CLOSED
        self.success_count = 0
        self.total_calls = 0
        
        self.lock = threading.Lock()
        
        logging.info(f"Circuit breaker '{name}' initialized with threshold: {failure_threshold}")
    
    def __call__(self, func: Callable) -> Callable:
        """Decorator to apply circuit breaker to a function"""
        @wraps(func)
        def wrapper(*args, **kwargs):
            return self.call(func, *args, **kwargs)
        return wrapper
    
    def call(self, func: Callable, *args, **kwargs) -> Any:
        """Execute function with circuit breaker protection"""
        with self.lock:
            self.total_calls += 1
            
            if self.state == CircuitState.OPEN:
                if self._should_attempt_reset():
                    self.state = CircuitState.HALF_OPEN
                    logging.info(f"Circuit breaker '{self.name}' moved to HALF_OPEN state")
                else:
                    raise ChatbotError(
                        f"Circuit breaker '{self.name}' is OPEN",
                        ErrorCategory.EXTERNAL_API,
                        ErrorSeverity.HIGH,
                        retry_able=False
                    )
            
            try:
                result = func(*args, **kwargs)
                self._on_success()
                return result
                
            except self.expected_exception as e:
                self._on_failure()
                raise ChatbotError(
                    f"Circuit breaker '{self.name}' caught exception: {str(e)}",
                    ErrorCategory.EXTERNAL_API,
                    ErrorSeverity.MEDIUM,
                    original_error=e,
                    context={'circuit_state': self.state.value, 'failure_count': self.failure_count}
                )
    
    def _should_attempt_reset(self) -> bool:
        """Check if circuit should attempt to reset"""
        return (
            self.last_failure_time and 
            time.time() - self.last_failure_time >= self.timeout
        )
    
    def _on_success(self) -> None:
        """Handle successful call"""
        self.failure_count = 0
        self.success_count += 1
        
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.CLOSED
            logging.info(f"Circuit breaker '{self.name}' reset to CLOSED state")
    
    def _on_failure(self) -> None:
        """Handle failed call"""
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logging.warning(f"Circuit breaker '{self.name}' OPENED after {self.failure_count} failures")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get circuit breaker statistics"""
        return {
            'name': self.name,
            'state': self.state.value,
            'failure_count': self.failure_count,
            'success_count': self.success_count,
            'total_calls': self.total_calls,
            'failure_threshold': self.failure_threshold,
            'timeout': self.timeout,
            'last_failure_time': self.last_failure_time
        }

class ErrorHandler:
    """
    Centralized error handler with monitoring and alerting capabilities
    """
    
    def __init__(self, max_error_history: int = 1000):
        self.max_error_history = max_error_history
        self.error_history = deque(maxlen=max_error_history)
        self.error_stats = defaultdict(int)
        self.alert_thresholds = {
            ErrorSeverity.CRITICAL: 1,  # Alert on first critical error
            ErrorSeverity.HIGH: 3,      # Alert after 3 high severity errors
            ErrorSeverity.MEDIUM: 10,   # Alert after 10 medium severity errors
            ErrorSeverity.LOW: 50       # Alert after 50 low severity errors
        }
        self.circuit_breakers = {}
        self.lock = threading.Lock()
        
        # Initialize circuit breakers for common services
        self._initialize_circuit_breakers()
    
    def _initialize_circuit_breakers(self) -> None:
        """Initialize circuit breakers for external services"""
        # xAI Grok API circuit breaker
        self.circuit_breakers['xai_api'] = CircuitBreaker(
            name='xai_api',
            failure_threshold=5,
            timeout=300,  # 5 minutes
            expected_exception=(Exception,)
        )
        
        # LINE API circuit breaker
        self.circuit_breakers['line_api'] = CircuitBreaker(
            name='line_api',
            failure_threshold=3,
            timeout=180,  # 3 minutes
            expected_exception=(Exception,)
        )
        
        logging.info("Circuit breakers initialized for external services")
    
    def handle_error(
        self,
        error: Union[Exception, ChatbotError],
        context: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None
    ) -> ChatbotError:
        """
        Handle and process errors with comprehensive logging and monitoring
        
        Args:
            error: Exception or ChatbotError instance
            context: Additional context information
            user_id: User ID for personalized error tracking
            
        Returns:
            ChatbotError: Processed error with user-friendly message
        """
        # Convert regular exceptions to ChatbotError
        if not isinstance(error, ChatbotError):
            chatbot_error = self._convert_to_chatbot_error(error, context)
        else:
            chatbot_error = error
        
        # Add user context if provided
        if user_id:
            chatbot_error.context['user_id'] = user_id
        
        # Record error for monitoring
        self._record_error(chatbot_error)
        
        # Log error with appropriate level
        self._log_error(chatbot_error)
        
        # Check for alert conditions
        self._check_alert_conditions(chatbot_error)
        
        return chatbot_error
    
    def _convert_to_chatbot_error(
        self,
        error: Exception,
        context: Optional[Dict[str, Any]] = None
    ) -> ChatbotError:
        """Convert regular exception to ChatbotError"""
        error_str = str(error).lower()
        
        # Determine category based on error type and message
        if 'database' in error_str or 'mysql' in error_str:
            category = ErrorCategory.DATABASE
            severity = ErrorSeverity.HIGH
        elif 'network' in error_str or 'connection' in error_str or 'timeout' in error_str:
            category = ErrorCategory.NETWORK
            severity = ErrorSeverity.MEDIUM
        elif 'api' in error_str or 'http' in error_str:
            category = ErrorCategory.EXTERNAL_API
            severity = ErrorSeverity.MEDIUM
        elif 'auth' in error_str or 'permission' in error_str:
            category = ErrorCategory.AUTHENTICATION
            severity = ErrorSeverity.HIGH
        elif 'validation' in error_str or 'invalid' in error_str:
            category = ErrorCategory.VALIDATION
            severity = ErrorSeverity.LOW
        else:
            category = ErrorCategory.SYSTEM
            severity = ErrorSeverity.MEDIUM
        
        return ChatbotError(
            message=str(error),
            category=category,
            severity=severity,
            context=context,
            original_error=error
        )
    
    def _record_error(self, error: ChatbotError) -> None:
        """Record error for monitoring and statistics"""
        with self.lock:
            self.error_history.append(error)
            
            # Update statistics
            self.error_stats[f"{error.category.value}_{error.severity.value}"] += 1
            self.error_stats[error.category.value] += 1
            self.error_stats[error.severity.value] += 1
            self.error_stats['total'] += 1
    
    def _log_error(self, error: ChatbotError) -> None:
        """Log error with appropriate level and formatting"""
        log_message = f"[{error.category.value.upper()}] {error.message}"
        
        if error.context:
            log_message += f" | Context: {json.dumps(error.context)}"
        
        if error.severity == ErrorSeverity.CRITICAL:
            logging.critical(log_message, exc_info=error.original_error)
        elif error.severity == ErrorSeverity.HIGH:
            logging.error(log_message, exc_info=error.original_error)
        elif error.severity == ErrorSeverity.MEDIUM:
            logging.warning(log_message)
        else:
            logging.info(log_message)
    
    def _check_alert_conditions(self, error: ChatbotError) -> None:
        """Check if alert conditions are met and trigger alerts"""
        threshold = self.alert_thresholds.get(error.severity, 999)
        current_count = self.error_stats.get(error.severity.value, 0)
        
        if current_count >= threshold and current_count % threshold == 0:
            self._trigger_alert(error, current_count)
    
    def _trigger_alert(self, error: ChatbotError, count: int) -> None:
        """Trigger alert for high error rates"""
        alert_message = (
            f"ALERT: {error.severity.value.upper()} error threshold reached. "
            f"Category: {error.category.value}, Count: {count}, "
            f"Recent error: {error.message}"
        )
        
        logging.critical(alert_message)
        
        # Here you would integrate with external alerting systems
        # such as Slack, email, SMS, etc.
    
    def get_error_summary(self, hours: int = 24) -> Dict[str, Any]:
        """Get error summary for the specified time period"""
        with self.lock:
            cutoff_time = datetime.now() - timedelta(hours=hours)
            recent_errors = [
                error for error in self.error_history 
                if error.timestamp > cutoff_time
            ]
            
            # Calculate statistics
            category_stats = defaultdict(int)
            severity_stats = defaultdict(int)
            
            for error in recent_errors:
                category_stats[error.category.value] += 1
                severity_stats[error.severity.value] += 1
            
            return {
                'period_hours': hours,
                'total_errors': len(recent_errors),
                'category_breakdown': dict(category_stats),
                'severity_breakdown': dict(severity_stats),
                'circuit_breaker_stats': {
                    name: cb.get_stats() 
                    for name, cb in self.circuit_breakers.items()
                },
                'alert_thresholds': {
                    severity.value: threshold 
                    for severity, threshold in self.alert_thresholds.items()
                }
            }
    
    def get_circuit_breaker(self, name: str) -> Optional[CircuitBreaker]:
        """Get circuit breaker by name"""
        return self.circuit_breakers.get(name)
    
    def add_circuit_breaker(
        self,
        name: str,
        failure_threshold: int = 5,
        timeout: int = 60,
        expected_exception: tuple = (Exception,)
    ) -> CircuitBreaker:
        """Add a new circuit breaker"""
        self.circuit_breakers[name] = CircuitBreaker(
            name=name,
            failure_threshold=failure_threshold,
            timeout=timeout,
            expected_exception=expected_exception
        )
        return self.circuit_breakers[name]
    
    def clear_error_history(self) -> None:
        """Clear error history (for testing or maintenance)"""
        with self.lock:
            self.error_history.clear()
            self.error_stats.clear()
        logging.info("Error history cleared")

# Global error handler instance
_error_handler = None

def get_error_handler() -> ErrorHandler:
    """Get the global error handler instance"""
    global _error_handler
    if _error_handler is None:
        _error_handler = ErrorHandler()
    return _error_handler

def handle_error(
    error: Union[Exception, ChatbotError],
    context: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None
) -> ChatbotError:
    """Convenience function to handle errors using global handler"""
    return get_error_handler().handle_error(error, context, user_id)

# Decorator for automatic error handling
def with_error_handling(
    category: ErrorCategory = ErrorCategory.SYSTEM,
    severity: ErrorSeverity = ErrorSeverity.MEDIUM,
    user_message: Optional[str] = None,
    retry_able: bool = True
):
    """
    Decorator for automatic error handling
    
    Args:
        category: Error category
        severity: Error severity
        user_message: Custom user message
        retry_able: Whether the operation can be retried
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except ChatbotError:
                # Re-raise ChatbotError as-is
                raise
            except Exception as e:
                # Convert to ChatbotError and handle
                chatbot_error = ChatbotError(
                    message=str(e),
                    category=category,
                    severity=severity,
                    user_message=user_message,
                    original_error=e,
                    retry_able=retry_able,
                    context={'function': func.__name__}
                )
                raise handle_error(chatbot_error)
        return wrapper
    return decorator
