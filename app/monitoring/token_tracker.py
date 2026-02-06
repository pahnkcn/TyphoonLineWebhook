"""
Token Usage Tracking System
Monitors API token consumption and costs for Grok API calls
"""
import logging
import json
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, Any, List, Optional
import threading


class TokenUsageTracker:
    """
    Track token usage and costs for monitoring and optimization

    Features:
    - Per-user and global token tracking
    - Cost calculation based on model pricing
    - Daily/hourly statistics
    - Redis persistence
    - Alert thresholds
    """

    # Grok pricing (update with actual xAI pricing)
    PRICING = {
        "grok-4": {
            "input_per_1k": 0.0005,   # $0.0005 per 1K input tokens (example)
            "output_per_1k": 0.0015,  # $0.0015 per 1K output tokens (example)
        },
        "grok-2": {
            "input_per_1k": 0.0003,
            "output_per_1k": 0.0010,
        }
    }

    def __init__(self, redis_client=None):
        """
        Initialize token tracker

        Args:
            redis_client: Optional Redis client for persistence
        """
        self.redis = redis_client
        self.lock = threading.Lock()

        # In-memory statistics (reset daily)
        self.daily_stats = defaultdict(lambda: {
            'total_requests': 0,
            'total_input_tokens': 0,
            'total_output_tokens': 0,
            'total_cost': 0.0,
            'by_user': defaultdict(lambda: {
                'requests': 0,
                'input_tokens': 0,
                'output_tokens': 0,
                'cost': 0.0,
                'response_times': []
            })
        })

        # Alert thresholds
        self.alerts = {
            'user_daily_cost': 1.0,      # $1 per user per day
            'global_daily_cost': 100.0,  # $100 total per day
            'user_hourly_tokens': 100000,  # 100K tokens per user per hour
        }

        logging.info("TokenUsageTracker initialized")

    def track_api_call(
        self,
        user_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        response_time_ms: Optional[float] = None
    ):
        """
        Track a single API call

        Args:
            user_id: User identifier
            model: Model name (e.g., "grok-4")
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
            response_time_ms: Optional response time in milliseconds
        """
        today = datetime.now().strftime("%Y-%m-%d")

        # Calculate cost
        cost = self._calculate_cost(model, input_tokens, output_tokens)

        # Update in-memory stats (thread-safe)
        with self.lock:
            stats = self.daily_stats[today]
            stats['total_requests'] += 1
            stats['total_input_tokens'] += input_tokens
            stats['total_output_tokens'] += output_tokens
            stats['total_cost'] += cost

            # Update per-user stats
            user_stats = stats['by_user'][user_id]
            user_stats['requests'] += 1
            user_stats['input_tokens'] += input_tokens
            user_stats['output_tokens'] += output_tokens
            user_stats['cost'] += cost

            if response_time_ms:
                user_stats['response_times'].append(response_time_ms)

        # Persist to Redis if available
        if self.redis:
            self._persist_to_redis(today, user_id, {
                'input_tokens': input_tokens,
                'output_tokens': output_tokens,
                'cost': cost,
                'response_time_ms': response_time_ms,
                'model': model,
                'timestamp': datetime.now().isoformat()
            })

        # Check alert thresholds
        self._check_alerts(today, user_id, stats)

        # Log tracking
        logging.debug(
            f"Token usage tracked: user={user_id[:8]}..., "
            f"in={input_tokens}, out={output_tokens}, "
            f"cost=${cost:.4f}, model={model}"
        )

    def _calculate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Calculate API call cost based on token usage"""
        pricing = self.PRICING.get(model, self.PRICING.get("grok-4"))

        input_cost = (input_tokens / 1000) * pricing["input_per_1k"]
        output_cost = (output_tokens / 1000) * pricing["output_per_1k"]

        return input_cost + output_cost

    def _persist_to_redis(self, date: str, user_id: str, metrics: Dict[str, Any]):
        """Persist metrics to Redis for long-term storage"""
        try:
            # Increment daily totals
            pipe = self.redis.pipeline()
            if pipe is None:
                return

            pipe.hincrby(f"token_usage:{date}", "total_requests", 1)
            pipe.hincrby(f"token_usage:{date}", "total_input_tokens", metrics['input_tokens'])
            pipe.hincrby(f"token_usage:{date}", "total_output_tokens", metrics['output_tokens'])
            pipe.hincrbyfloat(f"token_usage:{date}", "total_cost", metrics['cost'])

            # Store per-user metrics
            user_key = f"token_usage:{date}:user:{user_id}"
            pipe.hincrby(user_key, "requests", 1)
            pipe.hincrby(user_key, "input_tokens", metrics['input_tokens'])
            pipe.hincrby(user_key, "output_tokens", metrics['output_tokens'])
            pipe.hincrbyfloat(user_key, "cost", metrics['cost'])

            # Store detailed event (for debugging)
            event_key = f"token_events:{date}:{user_id}"
            pipe.lpush(event_key, json.dumps(metrics))
            pipe.ltrim(event_key, 0, 99)  # Keep last 100 events per user

            # Set expiry (keep 30 days)
            ttl = 30 * 24 * 60 * 60
            pipe.expire(f"token_usage:{date}", ttl)
            pipe.expire(user_key, ttl)
            pipe.expire(event_key, ttl)

            pipe.execute()

        except Exception as e:
            logging.error(f"Failed to persist token usage to Redis: {e}")

    def _check_alerts(self, date: str, user_id: str, stats: Dict):
        """Check if any alert thresholds are exceeded"""
        user_stats = stats['by_user'][user_id]

        # Check user daily cost
        if user_stats['cost'] > self.alerts['user_daily_cost']:
            if user_stats['cost'] % self.alerts['user_daily_cost'] < 0.01:  # Alert once per threshold
                logging.warning(
                    f"ALERT: User {user_id[:8]}... exceeded daily cost threshold: "
                    f"${user_stats['cost']:.2f} > ${self.alerts['user_daily_cost']:.2f}"
                )

        # Check global daily cost
        if stats['total_cost'] > self.alerts['global_daily_cost']:
            if stats['total_cost'] % self.alerts['global_daily_cost'] < 0.01:
                logging.critical(
                    f"ALERT: Global daily cost threshold exceeded: "
                    f"${stats['total_cost']:.2f} > ${self.alerts['global_daily_cost']:.2f}"
                )

    def get_daily_report(self, date: Optional[str] = None) -> Dict[str, Any]:
        """
        Get usage report for a specific date

        Args:
            date: Date in YYYY-MM-DD format (default: today)

        Returns:
            Dictionary with usage statistics
        """
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        # Try Redis first
        if self.redis:
            try:
                stats_dict = self.redis.hgetall(f"token_usage:{date}")
                if stats_dict:
                    # Decode bytes keys and values
                    stats = {
                        k.decode() if isinstance(k, bytes) else k:
                        v.decode() if isinstance(v, bytes) else v
                        for k, v in stats_dict.items()
                    }

                    total_requests = int(stats.get('total_requests', 0))
                    total_cost = float(stats.get('total_cost', 0.0))

                    return {
                        "date": date,
                        "total_requests": total_requests,
                        "total_input_tokens": int(stats.get('total_input_tokens', 0)),
                        "total_output_tokens": int(stats.get('total_output_tokens', 0)),
                        "total_cost": total_cost,
                        "average_cost_per_request": (
                            total_cost / max(total_requests, 1)
                        )
                    }
            except Exception as e:
                logging.error(f"Failed to get daily report from Redis: {e}")

        # Fallback to in-memory stats
        with self.lock:
            if date in self.daily_stats:
                stats = self.daily_stats[date]
                return {
                    "date": date,
                    "total_requests": stats['total_requests'],
                    "total_input_tokens": stats['total_input_tokens'],
                    "total_output_tokens": stats['total_output_tokens'],
                    "total_cost": stats['total_cost'],
                    "average_cost_per_request": (
                        stats['total_cost'] / max(stats['total_requests'], 1)
                    )
                }

        return {"date": date, "no_data": True}

    def get_user_stats(self, user_id: str, date: Optional[str] = None) -> Dict[str, Any]:
        """
        Get usage statistics for a specific user

        Args:
            user_id: User identifier
            date: Date in YYYY-MM-DD format (default: today)

        Returns:
            Dictionary with user statistics
        """
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        # Try Redis first
        if self.redis:
            try:
                user_key = f"token_usage:{date}:user:{user_id}"
                stats_dict = self.redis.hgetall(user_key)

                if stats_dict:
                    stats = {
                        k.decode() if isinstance(k, bytes) else k:
                        v.decode() if isinstance(v, bytes) else v
                        for k, v in stats_dict.items()
                    }

                    return {
                        "user_id": user_id,
                        "date": date,
                        "requests": int(stats.get('requests', 0)),
                        "input_tokens": int(stats.get('input_tokens', 0)),
                        "output_tokens": int(stats.get('output_tokens', 0)),
                        "cost": float(stats.get('cost', 0.0))
                    }
            except Exception as e:
                logging.error(f"Failed to get user stats from Redis: {e}")

        # Fallback to in-memory
        with self.lock:
            if date in self.daily_stats:
                user_stats = self.daily_stats[date]['by_user'].get(user_id, {})
                if user_stats:
                    return {
                        "user_id": user_id,
                        "date": date,
                        "requests": user_stats['requests'],
                        "input_tokens": user_stats['input_tokens'],
                        "output_tokens": user_stats['output_tokens'],
                        "cost": user_stats['cost']
                    }

        return {"user_id": user_id, "date": date, "no_data": True}

    def get_top_users_by_cost(self, date: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get top users by cost for a specific date

        Args:
            date: Date in YYYY-MM-DD format (default: today)
            limit: Number of top users to return

        Returns:
            List of user statistics sorted by cost
        """
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        user_costs = []

        # Try Redis first
        if self.redis:
            try:
                pattern = f"token_usage:{date}:user:*"
                user_keys = self.redis.keys(pattern)

                for key in user_keys:
                    key_str = key.decode() if isinstance(key, bytes) else key
                    user_id = key_str.split(":")[-1]

                    stats_dict = self.redis.hgetall(key)
                    stats = {
                        k.decode() if isinstance(k, bytes) else k:
                        v.decode() if isinstance(v, bytes) else v
                        for k, v in stats_dict.items()
                    }

                    cost = float(stats.get('cost', 0.0))
                    requests = int(stats.get('requests', 0))

                    user_costs.append({
                        "user_id": user_id,
                        "cost": cost,
                        "requests": requests,
                        "avg_cost_per_request": cost / max(requests, 1)
                    })
            except Exception as e:
                logging.error(f"Failed to get top users from Redis: {e}")

        # Fallback to in-memory
        if not user_costs:
            with self.lock:
                if date in self.daily_stats:
                    for user_id, stats in self.daily_stats[date]['by_user'].items():
                        user_costs.append({
                            "user_id": user_id,
                            "cost": stats['cost'],
                            "requests": stats['requests'],
                            "avg_cost_per_request": stats['cost'] / max(stats['requests'], 1)
                        })

        # Sort by cost descending
        user_costs.sort(key=lambda x: x['cost'], reverse=True)
        return user_costs[:limit]

    def set_alert_threshold(self, alert_type: str, value: float):
        """
        Set custom alert threshold

        Args:
            alert_type: Type of alert (user_daily_cost, global_daily_cost, etc.)
            value: Threshold value
        """
        if alert_type in self.alerts:
            self.alerts[alert_type] = value
            logging.info(f"Alert threshold updated: {alert_type} = {value}")
        else:
            logging.warning(f"Unknown alert type: {alert_type}")


# Global tracker instance
_token_tracker: Optional[TokenUsageTracker] = None


def init_token_tracker(redis_client=None) -> TokenUsageTracker:
    """
    Initialize the global token tracker

    Args:
        redis_client: Optional Redis client for persistence

    Returns:
        TokenUsageTracker instance
    """
    global _token_tracker
    _token_tracker = TokenUsageTracker(redis_client)
    logging.info("Global token tracker initialized")
    return _token_tracker


def get_token_tracker() -> Optional[TokenUsageTracker]:
    """Get the global token tracker instance"""
    return _token_tracker


def track_grok_call(
    user_id: str,
    input_tokens: int,
    output_tokens: int,
    model: str = "grok-4",
    response_time_ms: Optional[float] = None
):
    """
    Convenience function to track a Grok API call

    Args:
        user_id: User identifier
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens
        model: Model name (default: "grok-4")
        response_time_ms: Optional response time in milliseconds
    """
    if _token_tracker:
        _token_tracker.track_api_call(
            user_id=user_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            response_time_ms=response_time_ms
        )
    else:
        logging.warning("Token tracker not initialized, skipping tracking")
