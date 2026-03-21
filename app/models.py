"""Shared data models for the Jai Dee chatbot.

Provides typed dataclasses for structured return values, replacing raw
dicts and tuples throughout the codebase for better IDE support and
fewer bugs.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class RiskAssessmentResult:
    """Result of a risk assessment on a user message.

    Supports tuple destructuring for backward compatibility:
        level, keywords = assess_risk(message)
    """
    level: str
    keywords: List[str] = field(default_factory=list)

    def __iter__(self):
        """Allow tuple-style unpacking: level, keywords = result."""
        yield self.level
        yield self.keywords

    @property
    def is_high_risk(self) -> bool:
        return self.level == 'high'

    @property
    def is_medium_risk(self) -> bool:
        return self.level == 'medium'


@dataclass
class ConversationEntry:
    """A single conversation exchange from the database.

    Replaces raw Tuple[int, str, str] from get_user_history().
    Supports tuple destructuring for backward compatibility:
        entry_id, user_msg, bot_resp = entry
    """
    id: int
    user_message: str
    bot_response: str
    timestamp: Optional[datetime] = None
    token_count: Optional[int] = None
    is_important: bool = False

    def __iter__(self):
        """Allow tuple-style unpacking: id, user_msg, bot_resp = entry."""
        yield self.id
        yield self.user_message
        yield self.bot_response


@dataclass
class ProcessingMetrics:
    """Metrics recorded after processing a user message."""
    user_id: str
    processing_time: float
    used_fallback: bool = False
    had_error: bool = False
    timestamp: Optional[str] = None


@dataclass
class HealthStatus:
    """Health check result for a service dependency."""
    name: str
    healthy: bool
    details: Optional[str] = None
    latency_ms: Optional[float] = None


@dataclass
class DashboardOverview:
    """Aggregated dashboard overview stats."""
    total_conversations: int = 0
    unique_users: int = 0
    important_messages: int = 0
    active_follow_ups: int = 0


@dataclass
class UserSummary:
    """Per-user summary for dashboard display."""
    user_id: str
    total_messages: int = 0
    important_messages: int = 0
    total_tokens: int = 0
    last_interaction: Optional[str] = None
    important_ratio: float = 0.0
    recent_risk_events: List[Dict[str, Any]] = field(default_factory=list)
