"""
แชทบอท 'ใจดี' - แอปพลิเคชันหลัก
โค้ดหลักสำหรับการจัดการข้อความจาก LINE API และการตอบกลับด้วย xAI Grok API
"""
import os
import json
import logging
from logging.handlers import RotatingFileHandler
import requests
import time
import threading
import re
from datetime import datetime, timedelta
from flask import Flask, request, abort, jsonify, render_template
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError, LineBotApiError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FollowEvent
import redis
from random import choice
from collections import Counter
import signal
import atexit
import math
from waitress import serve
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import SchedulerNotRunningError

# นำเข้าโมดูลภายในโปรเจค
from .middleware.rate_limiter import init_limiter
from .config import (
    load_config,
    SYSTEM_MESSAGES,
    SYSTEM_MESSAGE_CORE,
    SYSTEM_MESSAGE_SUMMARY,
    GENERATION_CONFIG,
    SUMMARY_GENERATION_CONFIG,
    TOKEN_THRESHOLD,
    MAX_CONTEXT_WINDOW,
    CRISIS_CONFIG,
    INFO_CONFIG,
    get_dynamic_config
)
from .utils import safe_db_operation, safe_api_call, clean_ai_response, check_hospital_inquiry, get_hospital_information_message, handle_grok_api_error
from .llm import grok_client
from .chat_history_db import ChatHistoryDB
from .token_counter import TokenCounter
from .session_manager import (
    init_session_manager,
    get_chat_session,
    save_chat_session,
    check_session_timeout,
    update_last_activity,
    hybrid_context_management,
    is_important_message,
    get_session_token_count,
    generate_contextual_followup_message
)
from .risk_assessment import (
    init_risk_assessment,
    assess_risk,
    save_progress_data,
    generate_progress_report,
    RISK_KEYWORDS,
    GENERAL_RISK_LEVEL,
    normalize_risk_level,
)
from .database_init import initialize_database
from .database_manager import DatabaseManager
from .error_handling import (
    ChatbotError,
    ErrorCategory,
    ErrorSeverity,
    get_error_handler
)
import hmac
import traceback
import concurrent.futures
from typing import Optional, List, Dict, Tuple, Any, Set
from enum import Enum

# Shared ThreadPoolExecutor for AI API calls — avoids creating/destroying per request
_AI_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4)

# Dashboard API authentication helper
def _require_dashboard_auth():
    """Validate dashboard API key from Authorization header or query param.
    Returns (True, None) if valid, or (False, error_response) if invalid."""
    dashboard_key = os.getenv('DASHBOARD_API_KEY', '')
    if not dashboard_key:
        # If no key configured, allow access (backward compatibility) but log warning
        logging.warning("DASHBOARD_API_KEY not set — dashboard endpoints are unprotected")
        return True, None
    # Check Authorization: Bearer <key> header first
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        token = auth_header[7:]
        if hmac.compare_digest(token, dashboard_key):
            return True, None
    # Fallback: check query parameter
    token = request.args.get('api_key', '')
    if token and hmac.compare_digest(token, dashboard_key):
        return True, None
    return False, (jsonify({"error": "Unauthorized"}), 401)

# ค่าคงที่ส่วนของการแอพลิเคชัน
FOLLOW_UP_INTERVALS = [1, 3, 7, 14, 30]  # จำนวนวันในการติดตาม
SESSION_TIMEOUT = 604800  # 7 วัน (7 * 24 * 60 * 60 วินาที)
MESSAGE_LOCK_TIMEOUT = 30  # ระยะเวลาล็อค (วินาที)
DB_RESTORE_MESSAGE_PAIRS = 40  # จำนวนคู่ข้อความล่าสุดที่ใช้ในการกู้คืนจากฐานข้อมูล
PROCESSING_MESSAGES = [
    "⌛ กำลังคิดอยู่ครับ...",
    "🤔 กำลังประมวลผลข้อความของคุณ...",
    "📝 กำลังเรียบเรียงคำตอบ...",
    "🔄 รอสักครู่นะครับ..."
]
HIGH_RISK_KEYWORDS = {kw.lower() for kw in RISK_KEYWORDS.get('high_risk', [])}
MEDIUM_RISK_KEYWORDS = {kw.lower() for kw in RISK_KEYWORDS.get('medium_risk', [])}

# Legacy error types for backward compatibility - will be migrated to new system
class ErrorType(Enum):
    """Legacy error types - use ErrorCategory instead"""
    CONTEXT_LOAD_ERROR = "context_load_error"
    TOKEN_MANAGEMENT_ERROR = "token_management_error"
    AI_API_ERROR = "ai_api_error"
    MESSAGE_SEND_ERROR = "message_send_error"
    DATABASE_ERROR = "database_error"
    UNKNOWN_ERROR = "unknown_error"

# สร้างอินสแตนซ์แอป Flask
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
TEMPLATE_DIR = os.path.join(BASE_DIR, 'templates')
STATIC_DIR = os.path.join(BASE_DIR, 'static')

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)

# ตั้งค่าการบันทึกข้อมูลและหมุนไฟล์เมื่อขนาดเกิน 5MB
os.makedirs('logs', exist_ok=True)
logging.basicConfig(
    level=getattr(logging, os.getenv('LOG_LEVEL', 'INFO')),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler('logs/app.log', maxBytes=5 * 1024 * 1024, backupCount=3),
        logging.StreamHandler()
    ]
)

# โหลดการตั้งค่าและตัวแปรสภาพแวดล้อม
config = load_config()

# เริ่มต้นเซอร์วิสภายนอก
try:
    # เริ่มต้น Redis
    redis_client = redis.Redis(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        db=config.REDIS_DB,
        decode_responses=True,
        socket_timeout=5,
        socket_connect_timeout=5
    )
    redis_client.ping()  # ตรวจสอบการเชื่อมต่อ

    # เริ่มต้น Line API
    line_bot_api = LineBotApi(config.LINE_CHANNEL_ACCESS_TOKEN)
    handler = WebhookHandler(config.LINE_CHANNEL_SECRET)

    # ใช้ Grok client ผ่านโมดูลรวมศูนย์ app/llm/grok_client.py

    # เริ่มต้นตัวนับโทเค็นที่ปรับปรุงแล้ว
    token_counter = TokenCounter(cache_size=5000)

    # เริ่มต้น DatabaseManager สำหรับการจัดการฐานข้อมูล
    db_config = {
        'MYSQL_HOST': config.MYSQL_HOST,
        'MYSQL_PORT': config.MYSQL_PORT,
        'MYSQL_USER': config.MYSQL_USER,
        'MYSQL_PASSWORD': config.MYSQL_PASSWORD,
        'MYSQL_DB': config.MYSQL_DB
    }

    # สร้าง DatabaseManager ด้วยการตั้งค่าที่เหมาะสม with retry logic for container startup
    max_db_retries = 5
    db_retry_count = 0
    db_manager = None
    
    while db_retry_count < max_db_retries:
        try:
            db_manager = DatabaseManager(db_config, pool_size=32)
            break  # Success, exit retry loop
        except Exception as e:
            db_retry_count += 1
            if db_retry_count >= max_db_retries:
                logging.critical(f"เกิดข้อผิดพลาดในการเริ่มต้นแอพพลิเคชัน: {str(e)}")
                logging.critical(f"เกิดข้อผิดพลาดร้ายแรงในการเริ่มต้นแอพพลิเคชัน: {str(e)}")
                raise
            else:
                wait_time = 5 * db_retry_count
                logging.warning(f"Database initialization failed (attempt {db_retry_count}/{max_db_retries}), retrying in {wait_time} seconds: {str(e)}")
                time.sleep(wait_time)
    
    # Ensure db_manager is not None before proceeding
    if db_manager is None:
        raise RuntimeError("Failed to initialize database manager after all retries")

    # เสร็จสิ้นการตรวจสอบและเริ่มต้นฐานข้อมูล
    initialize_database(db_config)
    logging.info("เสร็จสิ้นการเริ่มต้นและตรวจสอบฐานข้อมูล")
    
    # Apply database optimizations
    try:
        from .database_optimization import optimize_database
        optimization_result = optimize_database(db_config)
        if optimization_result:
            logging.info("การปรับปรุงประสิทธิภาพฐานข้อมูลสำเร็จ")
        else:
            logging.warning("การปรับปรุงประสิทธิภาพฐานข้อมูลเสร็จสิ้นแต่มีปัญหาบางส่วน")
    except Exception as e:
        logging.error(f"ไม่สามารถรันการปรับปรุงฐานข้อมูลได้: {str(e)}")

    # เริ่มต้น ChatHistoryDB ด้วย DatabaseManager
    db = ChatHistoryDB(db_manager)

    # ตั้งค่าโมดูลจัดการเซสชันและประเมินความเสี่ยง
    init_session_manager(redis_client, line_bot_api, token_counter, SESSION_TIMEOUT)
    init_risk_assessment(redis_client)

except Exception as e:
    logging.critical(f"เกิดข้อผิดพลาดในการเริ่มต้นแอปพลิเคชัน: {str(e)}")
    raise

# เน€เธฃเธดเนเธกเธ•เนเธ rate limiter
limiter = init_limiter(app)

@limiter.exempt
@app.route('/dashboard', methods=['GET'])
def dashboard_page():
    return render_template('dashboard.html')






def _parse_progress_timestamp(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            if value.endswith('Z'):
                try:
                    return datetime.fromisoformat(value.replace('Z', '+00:00'))
                except ValueError:
                    return None
    return None


def _classify_keyword_risk(keyword: str) -> str:
    key_lower = keyword.lower()
    if key_lower in HIGH_RISK_KEYWORDS:
        return 'high'
    if key_lower in MEDIUM_RISK_KEYWORDS:
        return 'medium'
    return 'contextual'


def _collect_dashboard_progress_metrics(
    lookback_days: int = 30,
    per_user_limit: int = 5,
    keyword_limit: int = 10,
) -> Dict[str, Any]:
    keyword_counter: Counter[str] = Counter()
    display_lookup: Dict[str, str] = {}
    risk_counter: Counter[str] = Counter()
    user_progress: Dict[str, List[Dict[str, Any]]] = {}
    daily_risk: Dict[str, Counter] = {}  # date_str -> Counter of risk levels
    if redis_client is None:
        return {
            'top_keywords': [],
            'risk_summary': {'high': 0, 'medium': 0, 'general': 0, 'unknown': 0},
            'user_progress': {},
            'risk_trend': [],
        }

    cutoff = datetime.now() - timedelta(days=max(1, lookback_days))
    try:
        for key in redis_client.scan_iter('progress:*'):
            user_id = key.split(':', 1)[1] if ':' in key else key
            entries = redis_client.lrange(key, 0, -1)
            limit_per_user = max(1, per_user_limit)
            recent_events: List[Dict[str, Any]] = []
            for raw in entries:
                try:
                    entry = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    continue

                timestamp = _parse_progress_timestamp(entry.get('timestamp'))
                raw_level = entry.get('risk_level')
                risk_level = normalize_risk_level(raw_level)
                keywords = entry.get('keywords') or []
                risk_counter[risk_level] += 1

                if timestamp and timestamp >= cutoff:
                    day_key = timestamp.date().isoformat()
                    if day_key not in daily_risk:
                        daily_risk[day_key] = Counter()
                    daily_risk[day_key][risk_level] += 1

                if risk_level in ('high', 'medium') and len(recent_events) < limit_per_user:
                    recent_events.append({
                        'timestamp': timestamp.isoformat() if timestamp else None,
                        'risk_level': risk_level,
                        'keywords': keywords,
                    })

                if timestamp and timestamp >= cutoff:
                    for keyword in keywords:
                        normalized = keyword.strip()
                        if not normalized:
                            continue
                        lowered = normalized.lower()
                        keyword_counter[lowered] += 1
                        display_lookup.setdefault(lowered, normalized)

            if recent_events:
                user_progress[user_id] = recent_events
    except Exception as exc:
        logging.warning('Failed to collect progress metrics: %s', exc)

    top_keywords: List[Dict[str, Any]] = []
    for lowered, count in keyword_counter.most_common(max(1, keyword_limit)):
        label = display_lookup.get(lowered, lowered)
        top_keywords.append({
            'keyword': label,
            'count': int(count),
            'risk_level': _classify_keyword_risk(lowered),
        })

    risk_summary = {
        'high': int(risk_counter.get('high', 0)),
        'medium': int(risk_counter.get('medium', 0)),
        'general': int(risk_counter.get(GENERAL_RISK_LEVEL, 0)),
    }
    unknown_total = sum(
        count for level, count in risk_counter.items()
        if level not in risk_summary
    )
    risk_summary['unknown'] = int(unknown_total)

    risk_trend: List[Dict[str, Any]] = []
    for day_key in sorted(daily_risk.keys()):
        counts = daily_risk[day_key]
        risk_trend.append({
            'date': day_key,
            'high': int(counts.get('high', 0)),
            'medium': int(counts.get('medium', 0)),
            'general': int(counts.get(GENERAL_RISK_LEVEL, 0)),
        })

    return {
        'top_keywords': top_keywords,
        'risk_summary': risk_summary,
        'user_progress': user_progress,
        'risk_trend': risk_trend,
    }


@limiter.limit('20 per minute')
@app.route('/api/dashboard/insights', methods=['GET'])
def get_dashboard_insights():
    """Summarise conversation and risk insights for care teams."""
    auth_ok, auth_error = _require_dashboard_auth()
    if not auth_ok:
        return auth_error
    try:
        user_limit = request.args.get('limit', default=10, type=int) or 10
        lookback_days = request.args.get('lookback_days', default=30, type=int) or 30
        keyword_limit = request.args.get('keyword_limit', default=10, type=int) or 10

        user_limit = max(1, min(user_limit, 100))
        lookback_days = max(1, min(lookback_days, 180))
        keyword_limit = max(1, min(keyword_limit, 50))

        progress_metrics = _collect_dashboard_progress_metrics(
            lookback_days=lookback_days,
            per_user_limit=5,
            keyword_limit=keyword_limit,
        )

        overview_raw = db.get_dashboard_overview() or {}
        overview = {
            'total_conversations': int(overview_raw.get('total_conversations', 0) or 0),
            'unique_users': int(overview_raw.get('unique_users', 0) or 0),
            'important_messages': int(overview_raw.get('important_messages', 0) or 0),
        }

        try:
            followup_result = db_manager.execute_query(
                'SELECT COUNT(*) FROM follow_ups WHERE status != %s',
                ('completed',),
            )
            active_followups = int(followup_result[0][0]) if followup_result else 0
        except Exception as exc:
            logging.warning('Could not fetch follow-up metrics: %s', exc)
            active_followups = 0
        overview['active_follow_ups'] = active_followups

        user_summaries = db.get_recent_user_summaries(limit=user_limit) or []
        user_progress_map = progress_metrics.get('user_progress', {})
        formatted_users: List[Dict[str, Any]] = []

        for summary in user_summaries:
            formatted = dict(summary)
            parsed = _parse_progress_timestamp(formatted.get('last_interaction'))
            if parsed:
                formatted['last_interaction'] = parsed.isoformat()

            total_messages = int(formatted.get('total_messages') or 0)
            important_messages = int(formatted.get('important_messages') or 0)
            total_tokens = int(formatted.get('total_tokens') or 0)

            formatted['total_messages'] = total_messages
            formatted['important_messages'] = important_messages
            formatted['total_tokens'] = total_tokens
            formatted['important_ratio'] = (
                round(important_messages / total_messages, 3)
                if total_messages else 0.0
            )
            formatted['recent_risk_events'] = user_progress_map.get(
                formatted.get('user_id'), []
            )

            formatted_users.append(formatted)

        total_users = len(formatted_users)
        total_messages_all = sum(user['total_messages'] for user in formatted_users)
        important_messages_all = sum(user['important_messages'] for user in formatted_users)
        total_tokens_all = sum(user['total_tokens'] for user in formatted_users)
        high_focus_users = sum(
            1 for user in formatted_users
            if user.get('important_ratio', 0) >= 0.4
        )
        growth_watch_users = sum(
            1 for user in formatted_users
            if 0.15 <= user.get('important_ratio', 0) < 0.4
        )
        monitor_users = max(total_users - high_focus_users - growth_watch_users, 0)
        returning_users = sum(1 for user in formatted_users if user['total_messages'] >= 10)
        deep_conversation_users = sum(
            1 for user in formatted_users
            if user['total_tokens'] >= 2000
        )
        avg_messages_per_user = (
            round(total_messages_all / total_users, 1)
            if total_users
            else 0.0
        )
        avg_tokens_per_message = (
            round(total_tokens_all / total_messages_all, 2)
            if total_messages_all
            else 0.0
        )
        important_share = (
            round(important_messages_all / total_messages_all, 3)
            if total_messages_all
            else 0.0
        )

        trend_window = min(max(lookback_days, 7), 30)
        daily_totals = db.get_recent_daily_message_totals(days=trend_window) or []

        retention_buckets = db.get_retention_buckets() or []
        conversation_depth = db.get_conversation_depth_trend(days=trend_window) or []

        infographic = {
            'engagement': {
                'active_users': total_users,
                'returning_users': returning_users,
                'avg_messages_per_user': avg_messages_per_user,
                'high_focus_users': high_focus_users,
                'growth_watch_users': growth_watch_users,
                'monitor_users': monitor_users,
            },
            'quality': {
                'important_message_share': important_share,
                'avg_tokens_per_message': avg_tokens_per_message,
                'deep_conversation_users': deep_conversation_users,
                'active_follow_ups': overview.get('active_follow_ups', 0),
            },
            'message_trend': daily_totals,
        }

        risk_summary = progress_metrics.get('risk_summary', {
            'high': 0,
            'medium': 0,
            'general': 0,
            'unknown': 0,
        })

        response_payload = {
            'generated_at': datetime.now().isoformat(),
            'parameters': {
                'user_limit': user_limit,
                'lookback_days': lookback_days,
                'keyword_limit': keyword_limit,
            },
            'overview': overview,
            'risk_summary': risk_summary,
            'risk_trend': progress_metrics.get('risk_trend', []),
            'top_keywords': progress_metrics.get('top_keywords', []),
            'infographic': infographic,
            'retention_buckets': retention_buckets,
            'conversation_depth': conversation_depth,
            'users': formatted_users,
        }
        return jsonify(response_payload)

    except Exception as exc:
        logging.error('Error generating dashboard insights: %s', exc, exc_info=True)
        return jsonify({
            'error': 'dashboard_generation_failed',
            'message': 'Dashboard insights are unavailable at the moment.',
        }), 500

@limiter.limit('30 per minute')
@app.route('/api/dashboard/users/<user_id>/history', methods=['GET'])
def get_dashboard_user_history(user_id: str):
    """Return conversation transcript and risk highlights for a dashboard drill-down."""
    auth_ok, auth_error = _require_dashboard_auth()
    if not auth_ok:
        return auth_error
    if not user_id:
        return jsonify({'error': 'missing_user_id'}), 400

    limit = request.args.get('limit', default=50, type=int) or 50
    limit = max(10, min(limit, 200))

    try:
        history = db.get_user_conversation_feed(user_id, limit=limit) or []
        summary = db.get_user_snapshot(user_id) or {
            'user_id': user_id,
            'total_messages': 0,
            'important_messages': 0,
            'total_tokens': 0,
            'important_ratio': 0.0,
            'last_interaction': None,
            'first_interaction': None,
        }
        if summary.get('user_id') is None:
            summary['user_id'] = user_id

        risk_events: List[Dict[str, Any]] = []
        if redis_client is not None:
            raw_events = redis_client.lrange(f"progress:{user_id}", 0, limit - 1)
            for raw in raw_events:
                try:
                    event = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    continue

                timestamp = _parse_progress_timestamp(event.get('timestamp'))
                normalized_level = normalize_risk_level(event.get('risk_level'))
                risk_events.append({
                    'timestamp': timestamp.isoformat() if timestamp else event.get('timestamp'),
                    'risk_level': normalized_level,
                    'keywords': event.get('keywords') or [],
                })

        response_payload = {
            'generated_at': datetime.now().isoformat(),
            'user_id': user_id,
            'summary': summary,
            'history': history,
            'risk_events': risk_events,
            'limit': limit,
        }
        return jsonify(response_payload)
    except Exception as exc:
        logging.error('Error retrieving dashboard user history for %s: %s', user_id, exc, exc_info=True)
        return jsonify({
            'error': 'user_history_unavailable',
            'message': 'ไม่สามารถดึงประวัติการสนทนาได้ในขณะนี้',
        }), 500


# Health check endpoint
@app.route('/health', methods=['GET'])
def health_check():
    """
    Health check endpoint for monitoring
    """
    try:
        health_status = {
            'status': 'healthy',
            'timestamp': datetime.now().isoformat(),
            'services': {
                'database': 'unknown',
                'redis': 'unknown',
                'xai_api': 'unknown'
            }
        }
        
        # Check database
        try:
            if db_manager and db_manager.check_connection():
                health_status['services']['database'] = 'healthy'
            else:
                health_status['services']['database'] = 'unhealthy'
                health_status['status'] = 'degraded'
        except Exception as e:
            health_status['services']['database'] = f'error: {str(e)[:50]}'
            health_status['status'] = 'degraded'
        
        # Check Redis
        try:
            redis_client.ping()
            health_status['services']['redis'] = 'healthy'
        except Exception as e:
            health_status['services']['redis'] = f'error: {str(e)[:50]}'
            health_status['status'] = 'degraded'
        
        # Check xAI API (simple check)
        try:
            # This is a lightweight check - we don't actually call the API
            if config.XAI_API_KEY:
                health_status['services']['xai_api'] = 'configured'
            else:
                health_status['services']['xai_api'] = 'not_configured'
        except Exception as e:
            health_status['services']['xai_api'] = f'error: {str(e)[:50]}'
        
        # Determine overall status code
        if health_status['status'] == 'healthy':
            return jsonify(health_status), 200
        else:
            return jsonify(health_status), 503
            
    except Exception as e:
        return jsonify({
            'status': 'error',
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

def chunk_conversation_history(history, chunk_size=10):
    """
    แบ่งประวัติการสนทนาเป็นส่วนๆ (chunks) เพื่อการสรุปที่มีประสิทธิภาพ

    Args:
        history (list): ประวัติการสนทนา [(id, user_msg, bot_resp), ...]
        chunk_size (int): ขนาดของแต่ละส่วน

    Returns:
        list: รายการของส่วนประวัติการสนทนา
    """
    return [history[i:i + chunk_size] for i in range(0, len(history), chunk_size)]

def summarize_conversation_chunk(chunk):
    """
    สรุปส่วนของประวัติการสนทนา

    Args:
        chunk (list): ส่วนของประวัติการสนทนา [(id, user_msg, bot_resp), ...]

    Returns:
        str: ข้อความสรุป
    """
    if not chunk:
        return ""

    try:
        # สร้างข้อความสนทนา
        conversation_text = ""
        for _, msg, resp in chunk:
            conversation_text += f"ผู้ใช้: {msg}\nบอท: {resp}\n\n"

        summary_prompt = f"""
โปรดสรุปประวัติการสนทนาต่อไปนี้โดยเน้นประเด็นสำคัญตามหลัก Motivational Interviewing:

{conversation_text}

กรุณาสรุปโดยครอบคลุม:
1. **ปัญหาหลัก**: สารเสพติดที่ใช้ และปัญหาที่เกี่ยวข้อง
2. **ระยะของการเปลี่ยนแปลง**: Precontemplation / Contemplation / Preparation / Action / Maintenance
3. **Change Talk**: ความปรารถนา ความสามารถ เหตุผล ความจำเป็น ความมุ่งมั่น การลงมือ (DARN-CAT)
4. **อุปสรรคหลัก**: สิ่งที่ขัดขวางการเปลี่ยนแปลง
5. **ความคืบหน้า**: ความสำเร็จหรือการกลับไปเสพซ้ำ (ถ้ามี)

**ไม่ต้องมีคำนำหรือคำอธิบายวิธีการสรุป เริ่มต้นเนื้อหาสรุปเลยทันที**
"""

        text = grok_client.send_chat(
            messages=[
                SYSTEM_MESSAGE_SUMMARY,
                {"role": "user", "content": summary_prompt}
            ],
            model=config.XAI_MODEL,
            **SUMMARY_GENERATION_CONFIG,
        )

        return text
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดใน summarize_conversation_chunk: {str(e)}")
        return ""

def process_and_optimize_history(user_id, max_tokens=450000):
    """
    ประมวลผลและปรับปรุงประวัติการสนทนาให้เหมาะสมที่สุด
    รวมการสรุปเป็นชั้นๆ และการจัดลำดับความสำคัญ

    Args:
        user_id (str): LINE User ID
        max_tokens (int): จำนวนโทเค็นสูงสุดที่ต้องการใช้ (90% ของ TOKEN_THRESHOLD)

    Returns:
        list: ประวัติการสนทนาที่ปรับปรุงแล้ว
    """
    try:
        # 1. ตรวจสอบโทเค็นในเซสชันปัจจุบัน
        session_tokens = get_session_token_count(user_id)
        if session_tokens < max_tokens:
            # ถ้ายังอยู่ในเกณฑ์ ส่งคืนประวัติทั้งหมด
            return get_chat_session(user_id)

        # 2. ดึงประวัติจากฐานข้อมูลและเซสชัน
        db_history = db.get_user_history(user_id, max_tokens=max_tokens)
        session_history = get_chat_session(user_id)

        # 3. ระบุข้อความสำคัญ
        important_messages = []

        # แยกข้อความสำคัญจาก session history
        for i in range(0, len(session_history), 2):
            if i+1 < len(session_history):
                user_msg = session_history[i].get("content", "")
                bot_resp = session_history[i+1].get("content", "")

                if is_important_message(user_msg, bot_resp):
                    important_messages.append({"role": "user", "content": user_msg})
                    important_messages.append({"role": "assistant", "content": bot_resp})

        # 4. เก็บข้อความล่าสุด
        recent_count = min(20, len(session_history) // 2)  # จำนวนการโต้ตอบล่าสุด (ไม่เกิน 20)
        recent_messages = session_history[-recent_count*2:]  # *2 เพราะแต่ละการโต้ตอบมี 2 ข้อความ

        # 5. สรุปข้อความที่เหลือจาก db_history
        # แบ่งเป็นส่วนๆ เพื่อประสิทธิภาพในการสรุป
        chunks = chunk_conversation_history(db_history, chunk_size=10)
        summaries = []

        for chunk in chunks:
            summary = summarize_conversation_chunk(chunk)
            if summary:
                summaries.append(summary)

        # 6. รวมประวัติทั้งหมด
        optimized_history = []

        # เพิ่มสรุปทั้งหมด
        if summaries:
            combined_summary = "\n\n".join(summaries)
            # ใช้ role พิเศษสำหรับการสรุปที่ไม่แสดงให้ผู้ใช้เห็น
            optimized_history.append({"role": "system_summary", "content": f"สรุปการสนทนาก่อนหน้า: {combined_summary}"})

        # เพิ่มข้อความสำคัญ
        optimized_history.extend(important_messages)

        # เพิ่มข้อความล่าสุด
        for msg in recent_messages:
            # ตรวจสอบว่าไม่ซ้ำกับข้อความสำคัญ
            if msg not in important_messages:
                optimized_history.append(msg)

        # 7. บันทึกประวัติที่ปรับปรุงแล้ว
        save_chat_session(user_id, optimized_history)

        return optimized_history

    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการปรับปรุงประวัติ: {str(e)}")
        return get_chat_session(user_id)  # ส่งคืนประวัติปกติในกรณีที่มีข้อผิดพลาด

def filter_messages_for_api(messages):
    """
    กรองข้อความที่มี role เป็น 'system_summary' ออกจากการส่งไปยัง API
    แต่ยังคงไว้ในระบบเพื่อให้ AI เข้าใจบริบท
    
    Args:
        messages (list): รายการข้อความ
        
    Returns:
        list: ข้อความที่กรองแล้ว
    """
    filtered_messages = []
    summary_content = ""
    
    for message in messages:
        if message.get('role') == 'system_summary':
            # เก็บเนื้อหาสรุปแต่ไม่ส่งไปยัง API
            summary_content += message.get('content', '') + "\n\n"
        else:
            filtered_messages.append(message)
    
    # ถ้ามีการสรุป ให้รวมเข้ากับ system message เพื่อให้ AI เข้าใจบริบท
    if summary_content.strip():
        # ค้นหา system message ที่มีอยู่แล้ว
        system_msg_found = False
        for i, msg in enumerate(filtered_messages):
            if msg.get('role') == 'system':
                # เพิ่มการสรุปเข้าใน system message ที่มีอยู่
                filtered_messages[i] = {
                    'role': 'system',
                    'content': msg.get('content', '') + "\n\nข้อมูลสำคัญเพิ่มเติม (สำหรับ AI เท่านั้น):\n" + summary_content.strip()
                }
                system_msg_found = True
                break
        
        # ถ้าไม่มี system message ให้เพิ่มใหม่
        if not system_msg_found:
            filtered_messages.insert(0, {
                'role': 'system',
                'content': "ข้อมูลสำคัญเพิ่มเติม (สำหรับ AI เท่านั้น):\n" + summary_content.strip()
            })
    
    return filtered_messages

def summarize_conversation_history(history):
    """
    สรุปประวัติการสนทนาให้กระชับ โดยมีการจัดการขนาด

    Args:
        history (list): รายการประวัติการสนทนา [(id, user_msg, bot_resp), ...]

    Returns:
        str: ข้อความสรุป
    """
    if not history:
        return ""

    try:
        # แบ่งประวัติเป็นส่วนๆ หากมีขนาดใหญ่
        if len(history) > 20:
            # แบ่งเป็นชิ้นและสรุปแต่ละชิ้น
            chunks = chunk_conversation_history(history, chunk_size=10)
            summaries = []

            for chunk in chunks:
                chunk_summary = summarize_conversation_chunk(chunk)
                if chunk_summary:
                    summaries.append(chunk_summary)

            # รวมสรุปทั้งหมด
            if summaries:
                combined_summary = "\n".join([f"• {summary}" for summary in summaries])
                return combined_summary

        # หากมีขนาดเล็ก ใช้วิธีสรุปแบบปกติ
        conversation_text = ""
        for _, msg, resp in history:
            conversation_text += f"ผู้ใช้: {msg}\nบอท: {resp}\n\n"

        summary_prompt = f"""
โปรดสรุปประวัติการสนทนาต่อไปนี้โดยเน้นประเด็นสำคัญตามหลัก Motivational Interviewing:

{conversation_text}

กรุณาสรุปโดยครอบคลุม:
1. **ปัญหาหลัก**: สารเสพติดที่ใช้ และปัญหาที่เกี่ยวข้อง
2. **ระยะของการเปลี่ยนแปลง**: Precontemplation / Contemplation / Preparation / Action / Maintenance
3. **Change Talk**: ความปรารถนา ความสามารถ เหตุผล ความจำเป็น ความมุ่งมั่น การลงมือ (DARN-CAT)
4. **อุปสรรคหลัก**: สิ่งที่ขัดขวางการเปลี่ยนแปลง
5. **ความคืบหน้า**: ความสำเร็จหรือการกลับไปเสพซ้ำ (ถ้ามี)

ให้สรุปแบบครอบคลุมประเด็นสำคัญทั้งหมด:
"""

        text = grok_client.send_chat(
            messages=[
                SYSTEM_MESSAGE_SUMMARY,
                {"role": "user", "content": summary_prompt}
            ],
            model=config.XAI_MODEL,
            **SUMMARY_GENERATION_CONFIG,
        )

        return text
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดใน summarize_conversation_history: {str(e)}")
        return ""

def summarize_by_topic(history):
    """
    สรุปประวัติการสนทนาแบ่งตามหัวข้อ
    เหมาะสำหรับการสนทนาที่มีหลายหัวข้อคละกัน

    Args:
        history (list): รายการประวัติการสนทนา [(id, user_msg, bot_resp), ...]

    Returns:
        str: ข้อความสรุปแบ่งตามหัวข้อ
    """
    if not history:
        return ""

    try:
        # สร้างข้อความเพื่อให้ AI แบ่งหัวข้อและสรุป
        topic_prompt = """
นี่คือประวัติการสนทนาระหว่างผู้ใช้และบอทเกี่ยวกับการเลิกสารเสพติด:

{conversation}

โปรดวิเคราะห์และแบ่งแยกหัวข้อสำคัญต่างๆ ในการสนทนานี้ พร้อมทั้งสรุปแต่ละหัวข้อ ตามรูปแบบนี้:
1. [ชื่อหัวข้อ 1]: [สรุป]
2. [ชื่อหัวข้อ 2]: [สรุป]
...

แต่ละหัวข้อควรครอบคลุมประเด็นสำคัญที่พูดถึงโดยมีใจความชัดเจน กระชับ และเก็บรายละเอียดสำคัญไว้
"""

        # สร้างเนื้อหาการสนทนาสำหรับใส่ใน prompt
        conversation_text = ""
        for _, msg, resp in history:
            conversation_text += f"ผู้ใช้: {msg}\nบอท: {resp}\n\n"

        # นำเนื้อหาการสนทนาใส่ใน prompt
        topic_prompt = topic_prompt.format(conversation=conversation_text)

        # ส่งไปให้ AI ประมวลผล
        text = grok_client.send_chat(
            messages=[
                SYSTEM_MESSAGE_SUMMARY,
                {"role": "user", "content": topic_prompt}
            ],
            model=config.XAI_MODEL,
            temperature=0.2,
            max_tokens=1500,
        )

        return text
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดใน summarize_by_topic: {str(e)}")
        return ""


def is_user_registered(user_id):
    """ตรวจสอบว่าผู้ใช้ลงทะเบียนแล้วหรือไม่"""
    try:
        query = 'SELECT EXISTS(SELECT 1 FROM registration_codes WHERE user_id = %s AND status = %s)'
        result = db_manager.execute_query(query, (user_id, 'verified'))
        return bool(result[0][0]) if result else False
    except Exception as e:
        logging.error(f"Error checking user registration: {str(e)}")
        return False

def register_user_with_code(user_id, code):
    """ยืนยันการลงทะเบียนด้วยรหัสยืนยันและโหลดบริบทผู้ใช้"""
    try:
        # ตรวจสอบว่ารหัสมีอยู่และยังไม่หมดอายุ
        query = 'SELECT code, form_data FROM registration_codes WHERE code = %s AND status = %s'
        result = db_manager.execute_query(query, (code, 'pending'), dictionary=True)

        if not result:
            return False, "รหัสยืนยันไม่ถูกต้องหรือหมดอายุแล้ว"

        # ดึงข้อมูล form และสรุป
        form_data_json = result[0].get('form_data', '{}')
        form_data = json.loads(form_data_json) if form_data_json else {}

        # ตรวจสอบว่า AI summary พร้อมหรือยัง
        ai_summary = form_data.get('ai_summary', '')
        processed_at_str = form_data.get('processed_at')

        # ถ้ายังไม่มี AI summary และยังไม่ผ่าน timeout
        if not ai_summary and processed_at_str:
            try:
                processed_at = datetime.fromisoformat(processed_at_str)
                time_elapsed = (datetime.now() - processed_at).total_seconds()

                # ถ้ายังไม่ถึง 5 นาที ให้บอกผู้ใช้รอ
                if time_elapsed < 300:  # 5 minutes
                    minutes_left = int((300 - time_elapsed) / 60) + 1
                    return False, (
                        "⏳ ระบบกำลังประมวลผลข้อมูลของคุณเพื่อสร้างบริบทที่เหมาะสม\n\n"
                        f"กรุณารอสักครู่ประมาณ {minutes_left} นาที แล้วลองใช้คำสั่ง /verify อีกครั้ง\n\n"
                        "การรอจะช่วยให้น้องใจดีเข้าใจบริบทและสถานการณ์ของคุณได้ดีขึ้น "
                        "และสามารถให้คำปรึกษาที่เหมาะสมกับคุณมากที่สุดครับ 💚"
                    )

                # ถ้าเกิน 3 นาทีแล้ว ให้ลงทะเบียนได้แต่เตือนว่าไม่มี AI summary
                logging.warning(f"AI summary timeout สำหรับรหัส {code} (ใช้เวลา {time_elapsed:.1f} วินาที)")

            except (ValueError, TypeError) as e:
                logging.warning(f"ไม่สามารถแปลงวันที่ processed_at: {str(e)}")

        # อัพเดทรหัสให้เชื่อมกับผู้ใช้และสถานะเป็น verified
        update_query = 'UPDATE registration_codes SET user_id = %s, status = %s, verified_at = %s WHERE code = %s'
        db_manager.execute_and_commit(update_query, (user_id, 'verified', datetime.now(), code))

        # บันทึกบริบทเริ่มต้นของผู้ใช้
        if form_data and ai_summary:
            save_user_initial_context(user_id, ai_summary)
            logging.info(f"บันทึกบริบทสำหรับผู้ใช้ {user_id} สำเร็จ (AI summary พร้อม)")
        else:
            logging.warning(f"ลงทะเบียนผู้ใช้ {user_id} โดยไม่มี AI summary")

        # ส่งข้อความต้อนรับพร้อมบริบท
        welcome_message = create_personalized_welcome_message(form_data)

        # ถ้าไม่มี AI summary ให้เพิ่มข้อความแจ้งเตือน
        if not ai_summary:
            welcome_message += (
                "\n\n⚠️ หมายเหตุ: ระบบยังไม่สามารถประมวลผลข้อมูลของคุณเสร็จสมบูรณ์ "
                "แต่คุณสามารถใช้บริการได้ตามปกติ น้องใจดีพร้อมช่วยเหลือคุณครับ 💚"
            )

        return True, welcome_message

    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการลงทะเบียน: {str(e)}")
        return False, "เกิดข้อผิดพลาดในการลงทะเบียน กรุณาลองอีกครั้ง"
    
def save_user_initial_context(user_id, ai_summary):
    """บันทึกบริบทเริ่มต้นของผู้ใช้ใน Redis"""
    try:
        # บันทึกบริบทใน Redis โดยไม่มีเวลาหมดอายุ
        context_key = f"user_context:{user_id}"
        redis_client.set(context_key, ai_summary)
        
        # บันทึกเวลาที่สร้างบริบท
        redis_client.set(f"context_created:{user_id}", datetime.now().timestamp())
        
        logging.info(f"บันทึกบริบทเริ่มต้นสำหรับผู้ใช้: {user_id}")
        
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการบันทึกบริบท: {str(e)}")


def get_user_context(user_id):
    """ดึงบริบทของผู้ใช้จาก Redis"""
    try:
        context_key = f"user_context:{user_id}"
        context = redis_client.get(context_key)
        
        if context:
            if isinstance(context, bytes):
                context = context.decode('utf-8')
            return context
            
        # ถ้าไม่มีบริบทใน Redis ลองดึงจากฐานข้อมูล
        query = '''
            SELECT form_data 
            FROM registration_codes 
            WHERE user_id = %s AND status = 'verified'
            ORDER BY verified_at DESC
            LIMIT 1
        '''
        result = db_manager.execute_query(query, (user_id,))
        
        if result and result[0][0]:
            form_data = json.loads(result[0][0])
            if 'ai_summary' in form_data:
                # บันทึกกลับใน Redis สำหรับการใช้ครั้งถัดไป
                save_user_initial_context(user_id, form_data['ai_summary'])
                return form_data['ai_summary']
                
        return None
        
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการดึงบริบทผู้ใช้: {str(e)}")
        return None


def create_personalized_welcome_message(form_data):
    """สร้างข้อความต้อนรับแบบเฉพาะบุคคลตามข้อมูลจาก form"""
    base_message = "✅ ลงทะเบียนเรียบร้อยแล้ว! ยินดีต้อนรับสู่แชทบอทน้องใจดีครับ\n\n"
    
    if not form_data or 'ai_summary' not in form_data:
        return base_message + "ใจดีพร้อมเป็นเพื่อนคุยและช่วยเหลือคุณในเส้นทางการเลิกสารเสพติด มีอะไรอยากคุยเป็นพิเศษไหมครับ?"
    
    # ถ้ามีข้อมูลจาก form ให้สร้างข้อความเฉพาะบุคคล
    personalized_message = base_message
    
    personalized_message += "จากข้อมูลที่คุณให้มา ใจดีพร้อมที่จะช่วยเหลือคุณแบบเฉพาะบุคคล "
    personalized_message += "คุณสามารถพูดคุยเรื่องใดก็ได้ที่คุณสบายใจ หรือถามคำถามที่อยากรู้ได้เลยครับ\n\n"
    personalized_message += "💚 พิมพ์ /help เพื่อดูคำสั่งทั้งหมด"
    
    return personalized_message

def send_registration_message(user_id):
    """ส่งข้อความแนะนำการลงทะเบียน"""
    register_message = (
        "สวัสดีครับ! ยินดีต้อนรับสู่แชทบอท 'ใจดี'\n\n"
        "เพื่อเริ่มใช้งาน คุณจำเป็นต้องลงทะเบียนก่อน โดยทำตามขั้นตอนดังนี้:\n\n"
        "1. กรอกแบบฟอร์มที่ลิงก์นี้: https://forms.gle/KYU4JNWL72TL3PsG9\n"
        "2. หลังกรอกเสร็จ คุณจะได้รับรหัสยืนยัน 6 หลัก\n"
        "3. นำรหัสมาพิมพ์ที่นี่ด้วยคำสั่ง \"/verify รหัส\" เช่น \"/verify 123456\"\n\n"
        "หากมีข้อสงสัย พิมพ์ /help เพื่อดูคำแนะนำ\n\n"
        "📧 ติดต่อสอบถาม:\n"
        "• ปัญหาทางเทคนิค: pahnkcn@gmail.com\n"
        "• คำถามเกี่ยวกับการวิจัย: Std6548097@pcm.ac.th"
    )

    line_bot_api.push_message(
        user_id,
        TextSendMessage(text=register_message)
    )

# ฟังก์ชันที่เกี่ยวข้องกับการล็อคข้อความ
def is_user_locked(user_id):
    """ตรวจสอบว่าผู้ใช้ถูกล็อคอยู่หรือไม่"""
    return redis_client.exists(f"message_lock:{user_id}")

def lock_user(user_id):
    """ล็อคผู้ใช้"""
    redis_client.setex(f"message_lock:{user_id}", MESSAGE_LOCK_TIMEOUT, "1")

def unlock_user(user_id):
    """ปลดล็อคผู้ใช้"""
    redis_client.delete(f"message_lock:{user_id}")

# ฟังก์ชันเกี่ยวกับการติดตามผู้ใช้
def schedule_follow_up(user_id, interaction_date=None):
    """
    จัดการการติดตามผู้ใช้ โดยอ้างอิงจากข้อความแรกสุด
    ไม่รีเซ็ตเวลาหลังจากส่งข้อความใหม่

    Args:
        user_id (str): LINE User ID
        interaction_date (datetime, optional): วันที่ปฏิสัมพันธ์ (ถ้าไม่ระบุจะหาจากฐานข้อมูล)
    """
    try:
        # หาวันที่ของข้อความแรกสุด (ถ้าไม่ได้ระบุมา)
        if interaction_date is None:
            # ตรวจสอบว่ามีการเก็บเวลาเริ่มต้นไว้ใน Redis หรือไม่
            first_interaction_time = redis_client.get(f"first_interaction:{user_id}")

            if first_interaction_time:
                try:
                    # แปลงจาก string หรือ bytes เป็น float และจาก float เป็น datetime
                    if isinstance(first_interaction_time, bytes):
                        first_interaction_time = first_interaction_time.decode('utf-8')
                    interaction_date = datetime.fromtimestamp(float(first_interaction_time))
                except (ValueError, TypeError) as e:
                    logging.warning(f"ข้อมูลเวลาเริ่มต้นใน Redis ไม่ถูกต้อง: {str(e)}")
                    interaction_date = None

            # ถ้ายังไม่มีเวลาเริ่มต้นที่ถูกต้อง ให้ดึงจากฐานข้อมูล
            if interaction_date is None:
                try:
                    # ใช้ DatabaseManager เพื่อดึงข้อมูล
                    query = 'SELECT MIN(timestamp) FROM conversations WHERE user_id = %s'
                    result = db_manager.execute_query(query, (user_id,))
                    first_timestamp = result[0][0] if result and result[0] else None

                    if first_timestamp:
                        interaction_date = first_timestamp
                        # เก็บเวลาเริ่มต้นลง Redis เพื่อใช้อ้างอิงในอนาคต (ไม่มีเวลาหมดอายุ)
                        redis_client.set(
                            f"first_interaction:{user_id}",
                            interaction_date.timestamp()
                        )
                    else:
                        # ถ้าไม่มีข้อมูลในฐานข้อมูล ใช้เวลาปัจจุบัน
                        interaction_date = datetime.now()
                        # เก็บเวลาเริ่มต้นลง Redis
                        redis_client.set(
                            f"first_interaction:{user_id}",
                            interaction_date.timestamp()
                        )
                except Exception as db_error:
                    logging.error(f"เกิดข้อผิดพลาดในการดึงข้อมูลจากฐานข้อมูล: {str(db_error)}")
                    interaction_date = datetime.now()

        # ตรวจสอบว่า interaction_date เป็นประเภท datetime
        if not isinstance(interaction_date, datetime):
            logging.warning(f"ค่า interaction_date ไม่ใช่ประเภท datetime ใช้เวลาปัจจุบันแทน")
            interaction_date = datetime.now()

        # บันทึกข้อมูลวันที่เริ่มต้นลงใน Redis (ถ้ายังไม่มี)
        redis_client.setnx(f"first_interaction:{user_id}", interaction_date.timestamp())

        # ถ้ามีการกำหนดการติดตามไว้แล้วและยังไม่ถึงกำหนด ให้ใช้อันเดิม
        existing_ts = redis_client.zscore('follow_up_queue', user_id)
        if existing_ts:
            try:
                existing_dt = datetime.fromtimestamp(float(existing_ts))
                if existing_dt > datetime.now():
                    logging.info(
                        f"มีการกำหนดการติดตามไว้แล้วสำหรับผู้ใช้ {user_id} ในวันที่ {existing_dt.strftime('%Y-%m-%d')}"
                    )
                    return
            except (ValueError, TypeError) as e:
                logging.warning(f"ข้อมูลกำหนดการติดตามไม่ถูกต้อง: {str(e)}")

        # ดึงข้อมูลการติดตามล่าสุด (ถ้ามี)
        last_follow_up = redis_client.get(f"last_follow_up:{user_id}")
        next_follow_idx = 0

        if last_follow_up:
            # แปลงจาก bytes เป็น string ถ้าจำเป็น
            if isinstance(last_follow_up, bytes):
                last_follow_up = last_follow_up.decode('utf-8')

            # หาดัชนีถัดไปใน FOLLOW_UP_INTERVALS
            try:
                last_idx = FOLLOW_UP_INTERVALS.index(int(last_follow_up))
                next_follow_idx = last_idx + 1
                # ถ้าเกินขอบเขต ให้ใช้วันสุดท้าย
                if next_follow_idx >= len(FOLLOW_UP_INTERVALS):
                    next_follow_idx = len(FOLLOW_UP_INTERVALS) - 1
            except (ValueError, IndexError):
                # ถ้าไม่พบค่าใน FOLLOW_UP_INTERVALS หรือเกิดข้อผิดพลาด ให้เริ่มจาก 0
                next_follow_idx = 0

        # กำหนดการติดตามตามช่วงเวลาที่กำหนด
        current_date = datetime.now()
        scheduled = False

        # ลูปเริ่มจากดัชนีที่คำนวณได้ (ไม่ใช่ตั้งแต่ดัชนี 0 เสมอ)
        for i in range(next_follow_idx, len(FOLLOW_UP_INTERVALS)):
            days = FOLLOW_UP_INTERVALS[i]
            follow_up_date = interaction_date + timedelta(days=days)

            # กำหนดการติดตามสำหรับวันที่ในอนาคตเท่านั้น
            if follow_up_date > current_date:
                redis_client.zadd(
                    'follow_up_queue',
                    {user_id: follow_up_date.timestamp()}
                )
                # บันทึกว่าการติดตามล่าสุดคือวันที่เท่าไร
                redis_client.set(f"last_follow_up:{user_id}", str(days))

                logging.info(f"กำหนดการติดตามผู้ใช้ {user_id} ในวันที่ {follow_up_date.strftime('%Y-%m-%d')} (+{days} วัน จากวันแรก)")
                scheduled = True
                break

        if not scheduled:
            logging.info(f"ไม่ได้กำหนดการติดตามสำหรับผู้ใช้ {user_id} เนื่องจากไม่มีวันที่ในอนาคตที่เข้าเกณฑ์")

    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการกำหนดการติดตามผล: {str(e)}")

def get_follow_up_status(user_id):
    """คืนค่าข้อมูลกำหนดการติดตามของผู้ใช้"""
    try:
        timestamp = redis_client.zscore('follow_up_queue', user_id)
        if timestamp:
            next_dt = datetime.fromtimestamp(float(timestamp))
            date_text = next_dt.strftime("%d/%m/%Y %H:%M")
            delta = next_dt - datetime.now()
            if delta.total_seconds() < 0:
                delta = timedelta(0)
            days = delta.days
            hours, rem = divmod(delta.seconds, 3600)
            minutes = rem // 60
            time_text = f"อีก {days} วัน {hours} ชั่วโมง {minutes} นาที"
        else:
            time_text = "ยังไม่ได้กำหนดการติดตามครั้งถัดไป"
            date_text = "-"

        last_follow = redis_client.get(f"last_follow_up:{user_id}")
        start_idx = 0
        if last_follow:
            try:
                start_idx = FOLLOW_UP_INTERVALS.index(int(last_follow)) + 1
            except ValueError:
                start_idx = 0
        remaining = FOLLOW_UP_INTERVALS[start_idx:]
        remaining_text = ",".join(str(d) for d in remaining) if remaining else "หมดแล้ว"

        return (
            f"📆 กำหนดการติดตามครั้งถัดไป: {date_text}\n"
            f"⏰ การติดตามครั้งถัดไปจะเริ่มใน {time_text}\n"
            f"📅 รอบติดตามที่เหลือ: {remaining_text}"
        )
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการดึงสถานะการติดตามผล: {str(e)}")
        return "ไม่สามารถดึงข้อมูลการติดตามได้ในขณะนี้"


def check_and_send_follow_ups():
    """ตรวจสอบและส่งการติดตามที่ถึงกำหนด พร้อมกำหนดการติดตามครั้งถัดไป"""
    logging.info("กำลังรันการตรวจสอบการติดตามผลตามกำหนดเวลา")
    try:
        current_time = datetime.now().timestamp()
        # ดึงรายการติดตามที่ถึงกำหนด
        due_follow_ups = redis_client.zrangebyscore(
            'follow_up_queue',
            0,
            current_time
        )

        for user_id in due_follow_ups:
            # แปลง bytes เป็ string ถ้าจำเป็น
            if isinstance(user_id, bytes):
                user_id = user_id.decode('utf-8')

            # สร้างข้อความติดตามที่เป็นไปตามบริบทของการสนทนา
            follow_up_message = generate_contextual_followup_message(user_id, db, config)
            try:
                line_bot_api.push_message(
                    user_id,
                    TextSendMessage(text=follow_up_message)
                )
                # ลบรายการติดตามที่ส่งแล้ว
                redis_client.zrem('follow_up_queue', user_id)
                # บันทึกการติดตามลงในฐานข้อมูล
                db.update_follow_up_status(user_id, 'sent', datetime.now())
                logging.info(f"ส่งการติดตามไปยังผู้ใช้: {user_id}")

                # กำหนดการติดตามครั้งถัดไปโดยอัตโนมัติ
                # ส่งค่า None เพื่อให้ใช้วันที่เริ่มต้นจาก Redis
                schedule_follow_up(user_id, None)

            except Exception as e:
                logging.error(f"เกิดข้อผิดพลาดในการส่งการติดตามไปยัง {user_id}: {str(e)}")

    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดใน check_and_send_follow_ups: {str(e)}")

# ฟังก์ชันที่เกี่ยวข้องกับการแสดงสถานะการประมวลผล
def send_processing_status(user_id, reply_token):
    """ส่งข้อความแจ้งสถานะกำลังประมวลผล"""
    try:
        # ส่งข้อความว่ากำลังประมวลผลทันที
        processing_message = choice(PROCESSING_MESSAGES)
        line_bot_api.reply_message(
            reply_token,
            TextSendMessage(text=processing_message)
        )
        return True
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการส่งสถานะประมวลผล: {str(e)}")
        return False

def send_final_response(user_id, bot_response, reply_token=None):
    """ส่งคำตอบสุดท้ายหลังประมวลผลเสร็จ พร้อมรองรับการ reply"""
    try:
        text = bot_response or ""
        segments = [
            seg.strip() for seg in re.split(r"\n{2,}|•", text) if seg.strip()
        ]

        if segments:
            messages = [TextSendMessage(text=segment) for segment in segments]
        else:
            messages = [TextSendMessage(text=text)]

        to_push = messages

        if reply_token:
            reply_batch = messages[:5]
            try:
                if reply_batch:
                    payload = reply_batch if len(reply_batch) > 1 else reply_batch[0]
                    line_bot_api.reply_message(reply_token, payload)
                    to_push = messages[5:]
            except LineBotApiError as exc:
                logging.warning(f"Reply message failed for user {user_id}: {exc}")
                to_push = messages

        for index in range(0, len(to_push), 5):
            batch = to_push[index:index + 5]
            if not batch:
                continue
            payload = batch if len(batch) > 1 else batch[0]
            line_bot_api.push_message(user_id, payload)
            if index + 5 < len(to_push):
                time.sleep(0.5)

        return True
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการส่งคำตอบสุดท้าย: {str(e)}")
        return False

def start_loading_animation(user_id, duration=60):
    """แสดงภาพเคลื่อนไหวการโหลดของ LINE ให้กับผู้ใช้

    Args:
        user_id (str): LINE user ID
        duration (int): ระยะเวลาเป็นวินาที (ต้องอยู่ในช่วง 5-60 และเป็นจำนวนเท่าของ 5)

    Returns:
        bool: True หากสำเร็จ, False หากไม่สำเร็จ
    """
    try:
        # ใช้ 60 วินาทีเสมอ (ระยะเวลาสูงสุดที่อนุญาตโดย LINE API)
        duration = 60

        # ดึงโทเค็นการเข้าถึงจากตัวแปรสภาพแวดล้อม
        access_token = config.LINE_CHANNEL_ACCESS_TOKEN

        # สร้างคำขอ
        url = 'https://api.line.me/v2/bot/chat/loading/start'
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {access_token}'
        }
        payload = {
            'chatId': user_id,
            'loadingSeconds': duration
        }

        # ส่งคำขอ
        response = requests.post(url, headers=headers, json=payload)

        # ตรวจสอบการตอบกลับ - ทั้ง 200 และ 202 ถือว่าสำเร็จ
        # 202 หมายถึง "Accepted" ใน HTTP ซึ่งเหมาะสำหรับการดำเนินการแบบอะซิงโครนัส
        if response.status_code in [200, 202]:
            logging.info(f"เริ่มภาพเคลื่อนไหวการโหลดสำหรับผู้ใช้ {user_id} เป็นเวลา {duration} วินาที (สถานะ: {response.status_code})")
            return True, duration
        else:
            logging.error(f"ไม่สามารถเริ่มภาพเคลื่อนไหวการโหลด: {response.status_code} {response.text}")
            return False, 0
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการเริ่มภาพเคลื่อนไหวการโหลด: {str(e)}")
        return False, 0

@safe_api_call
def summarize_form_data(form_data):
    """
    สรุปข้อมูลจาก Google Form โดยใช้ xAI Grok
    
    Args:
        form_data (dict): ข้อมูลจาก Google Form
        
    Returns:
        str: ข้อความสรุปข้อมูลผู้ใช้
    """
    try:
        # สร้าง prompt สำหรับการสรุป
        prompt = """
จากข้อมูลแบบประเมินต่อไปนี้ กรุณาสรุปข้อมูลสำคัญของผู้ใช้ในรูปแบบที่จะช่วยให้แชทบอทเข้าใจบริบทและให้คำปรึกษาได้อย่างเหมาะสม:

ข้อมูลการตอบแบบสอบถาม:
"""
        
        # เพิ่มคำถาม-คำตอบทั้งหมด
        for item in form_data.get('responses', []):
            prompt += f"\nคำถาม: {item['question']}\nคำตอบ: {item['answer']}\n"
        
        # เพิ่มข้อมูล ASSIST scores
        if 'assistScores' in form_data:
            prompt += "\n\nผลการประเมิน ASSIST:\n"
            for substance, score in form_data['assistScores'].items():
                prompt += f"- {substance}: {score} คะแนน\n"
        
        # เพิ่มการประเมินความเสี่ยง
        if 'riskAssessment' in form_data:
            risk_data = form_data['riskAssessment']
            prompt += f"\n\nระดับความเสี่ยงโดยรวม: {risk_data.get('overallRisk', 'ไม่ระบุ')}\n"

        # เพิ่มข้อมูล Readiness และ Confidence (Readiness Ruler)
        readiness_info = []
        for item in form_data.get('responses', []):
            question_lower = item.get('question', '').lower()
            # ตรวจหาคำถามเกี่ยวกับความพร้อม
            if any(keyword in question_lower for keyword in ['พร้อม', 'ready', 'readiness', 'ความพร้อม']):
                readiness_info.append(f"ความพร้อม: {item.get('answer', 'ไม่ระบุ')}")
            # ตรวจหาคำถามเกี่ยวกับความมั่นใจ
            elif any(keyword in question_lower for keyword in ['มั่นใจ', 'confident', 'confidence', 'ความมั่นใจ']):
                readiness_info.append(f"ความมั่นใจ: {item.get('answer', 'ไม่ระบุ')}")

        if readiness_info:
            prompt += "\n\nระดับความพร้อมและความมั่นใจในการเปลี่ยนแปลง (Readiness Ruler):\n"
            for info in readiness_info:
                prompt += f"- {info}\n"

        prompt += """
กรุณาสรุปข้อมูลในหัวข้อต่อไปนี้ โดยใช้หลักการ Motivational Interviewing:

1. **ประวัติการใช้สารเสพติด**

2. **ระดับความเสี่ยงและผลกระทบ (ASSIST Scores)**

3. **ขั้นตอนของการเปลี่ยนแปลง (Stages of Change)** - **สำคัญมาก**:
   จากข้อมูลทั้งหมด กรุณาระบุอย่างชัดเจนว่าผู้ใช้อยู่ในขั้นตอนใด:
   - **Precontemplation**: ปฏิเสธปัญหา ไม่เห็นความจำเป็นต้องเปลี่ยน ยังไม่คิดจะเปลี่ยนแปลง
   - **Contemplation**: เห็นปัญหาบ้างแล้ว มีความลังเลสองใจ (ambivalence) อยากจะเปลี่ยนแต่ยังไม่แน่ใจ
   - **Preparation**: ตัดสินใจเปลี่ยนแล้ว มีแผนการเปลี่ยน พร้อมที่จะเริ่ม
   - **Action**: กำลังดำเนินการเปลี่ยนแปลงอย่างจริงจัง (เริ่มเลิกหรือลดแล้ว)
   - **Maintenance**: เลิกได้แล้วเกิน 6 เดือน กำลังรักษาพฤติกรรมใหม่

   **ระบุว่าผู้ใช้น่าจะอยู่ในขั้นตอนใด พร้อมเหตุผล** (เช่น "Contemplation - เพราะแสดงความลังเลระหว่างอยากเลิกและกังวลว่าจะทำไม่ได้")

4. **แรงจูงใจภายใน (Intrinsic Motivation)**

5. **Change Talk และ Sustain Talk**:
   - Change Talk: ประโยค/ความคิดที่แสดงถึงความต้องการเปลี่ยน (DARN: Desire-ความปรารถนา, Ability-ความสามารถ, Reason-เหตุผล, Need-ความจำเป็น)
   - Sustain Talk: ประโยค/ความคิดที่แสดงการต่อต้านการเปลี่ยน
   - Ambivalence: ความขัดแย้งภายในระหว่างต้องการเปลี่ยนกับต้องการคงเดิม

6. **จุดแข็งและทรัพยากร (Strengths & Resources)**

7. **อุปสรรคและความท้าทาย**

8. **ประสบการณ์การเลิกในอดีต** (ถ้ามี)

**คำแนะนำสำคัญ**:
- ใช้ภาษาที่ไม่ตัดสิน เห็นใจ และให้กำลังใจ
- เน้นจุดแข็งและความเชื่อมั่นในตนเอง (Self-efficacy)
- มองหาและระบุ Change Talk ที่ซ่อนอยู่
- ชี้ให้เห็น Ambivalence เพื่อใช้ในการสนทนา MI
- สรุปให้กระชับแต่ครอบคลุมประเด็นสำคัญ
- **ไม่ต้องมีคำนำหรือคำอธิบายวิธีการสรุป เริ่มต้นเนื้อหาสรุปเลยทันที** - **สำคัญมาก**
"""

        # เรียก xAI Grok API
        summary = grok_client.send_chat(
            messages=[
                {
                    "role": "system",
                    "content": "คุณคือผู้เชี่ยวชาญด้าน Motivational Interviewing (MI) และการบำบัดสารเสพติด สรุปข้อมูลผู้ใช้โดยเน้นหลัก MI: แรงจูงใจภายใน, ความพร้อมในการเปลี่ยนแปลง (Stages of Change), จุดแข็ง และทรัพยากรที่มีอยู่ ใช้ภาษาที่ไม่ตัดสิน (non-judgmental) และเน้นการสร้างความเชื่อมั่นในตนเอง (self-efficacy)",
                },
                {"role": "user", "content": prompt},
            ],
            model=config.XAI_MODEL,
            temperature=0.3,
            max_tokens=2000,
        )
        return clean_ai_response(summary)
        
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการสรุปข้อมูล form: {str(e)}")
        # ถ้าสรุปไม่ได้ ให้สร้างสรุปพื้นฐาน
        return create_basic_summary(form_data)


def create_basic_summary(form_data):
    """สร้างสรุปพื้นฐานถ้า AI ไม่สามารถสรุปได้"""
    summary = "ข้อมูลพื้นฐานจากแบบประเมิน:\n\n"
    
    if 'assistScores' in form_data:
        summary += "สารเสพติดที่ใช้:\n"
        for substance, score in form_data['assistScores'].items():
            risk_level = get_risk_level_from_score(substance, score)
            summary += f"- {substance}: {score} คะแนน ({risk_level})\n"
    
    if 'riskAssessment' in form_data:
        risk = form_data['riskAssessment'].get('overallRisk', 'ไม่ระบุ')
        summary += f"\nระดับความเสี่ยงโดยรวม: {risk}\n"
    
    return summary


def get_risk_level_from_score(substance, score):
    """คำนวณระดับความเสี่ยงจากคะแนน"""
    if substance == 'เครื่องดื่มแอลกอฮอล์':
        if score <= 10:
            return 'ความเสี่ยงต่ำ'
        elif score <= 26:
            return 'ความเสี่ยงปานกลาง'
        else:
            return 'ความเสี่ยงสูง'
    else:
        if score <= 3:
            return 'ความเสี่ยงต่ำ'
        elif score <= 26:
            return 'ความเสี่ยงปานกลาง'
        else:
            return 'ความเสี่ยงสูง'

def process_conversation_data(user_id, user_message, bot_response, messages):
    """
    ประมวลผลและบันทึกข้อมูลการสนทนา พร้อมกับตรวจสอบความเสี่ยง

    Args:
        user_id (str): LINE User ID
        user_message (str): ข้อความของผู้ใช้
        bot_response (str): การตอบกลับของบอท
        messages (list): ข้อความทั้งหมดในเซสชัน
    """
    # นับโทเค็นสำหรับการสนทนาคู่นี้
    message_token_count = token_counter.count_tokens(user_message + bot_response)

    # ประเมินความเสี่ยง
    risk_level, keywords = assess_risk(user_message)
    save_progress_data(user_id, risk_level, keywords)

    # ตรวจสอบว่าข้อความนี้สำคัญหรือไม่
    is_important = is_important_message(user_message, bot_response)
    if risk_level == GENERAL_RISK_LEVEL:
        is_important = False

    # บันทึกการสนทนาและกำหนดการติดตาม
    save_chat_session(user_id, messages)
    db.save_conversation(
        user_id=user_id,
        user_message=user_message,
        bot_response=bot_response,
        token_count=message_token_count,  # บันทึกเฉพาะโทเค็นของข้อความคู่นี้
        important=is_important
    )

    # กำหนดการติดตามโดยยึดวันแรกที่ผู้ใช้เริ่มสนทนา
    # ถ้ามีการกำหนดการติดตามค้างอยู่จะไม่ถูกปรับใหม่
    schedule_follow_up(user_id, None)

    # ส่งการแจ้งเตือนถ้าพบความเสี่ยงสูง
    if risk_level == 'high':
        emergency_message = (
            "⚠️ น้องใจดีกังวลว่าคุณอาจกำลังเผชิญกับภาวะเสี่ยง\n\n"
            "ขอแนะนำให้ติดต่อผู้เชี่ยวชาญเพื่อรับความช่วยเหลือโดยเร็วที่สุด:\n"
            "📞 สายด่วนสุขภาพจิต: 1323\n"
            "📞 สายด่วนยาเสพติด: 1165\n"
            "📞 หน่วยกู้ชีพฉุกเฉิน: 1669\n\n"
            "คุณไม่จำเป็นต้องเผชิญกับสิ่งนี้เพียงลำพัง การขอความช่วยเหลือคือความกล้าหาญ"
        )
        send_final_response(user_id, emergency_message)

    # ตรวจสอบโทเค็นและแจ้งเตือนถ้าเข้าใกล้ขีดจำกัด
    session_token_count = get_session_token_count(user_id)
    token_threshold_warning = TOKEN_THRESHOLD * 0.70  # แจ้งเตือนที่ 70% ของขีดจำกัด

    if session_token_count > token_threshold_warning and not redis_client.exists(f"token_warning:{user_id}"):
        # ส่งการแจ้งเตือนเรื่องโทเค็น
        warning_message = (
            "📊 ข้อควรทราบ: ประวัติการสนทนาของเรากำลังเติบโต ระบบอาจจะต้องสรุปบางส่วน"
            "ในการสนทนาต่อไปเพื่อรักษาประสิทธิภาพ\n\n"
            f"• โทเค็นในเซสชันปัจจุบัน: {session_token_count:,} จาก {TOKEN_THRESHOLD:,} ({(session_token_count/TOKEN_THRESHOLD*100):.1f}%)\n"
            "• คุณสามารถใช้คำสั่ง /optimize เพื่อปรับปรุงประวัติการสนทนาได้ทุกเมื่อ"
        )

        # ตั้งค่าเวลาหมดอายุของการแจ้งเตือน (30 นาที)
        redis_client.setex(f"token_warning:{user_id}", 1800, "1")

        # ส่งข้อความแจ้งเตือนหลังจากการตอบกลับปกติเล็กน้อย
        def send_delayed_warning():
            time.sleep(3)  # รอ 3 วินาทีหลังจากส่งการตอบกลับปกติ
            send_final_response(user_id, warning_message)

        # เริ่ม thread ใหม่เพื่อส่งการแจ้งเตือนแบบหน่วงเวลา
        warning_thread = threading.Thread(target=send_delayed_warning)
        warning_thread.daemon = True
        warning_thread.start()

# ฟังก์ชันสำหรับการจัดการข้อความที่ถูกล็อค

def handle_locked_user(user_id):
    """จัดการกรณีผู้ใช้ถูกล็อค"""
    wait_notice_sent = redis_client.exists(f"wait_notice:{user_id}")

    if not wait_notice_sent:
        line_bot_api.push_message(
            user_id,
            TextSendMessage(text="กรุณารอระบบประมวลผลข้อความก่อนหน้าให้เสร็จสิ้นก่อนครับ")
        )
        redis_client.setex(f"wait_notice:{user_id}", 10, "1")

# ฟังก์ชันสำหรับประมวลผลข้อความของผู้ใช้
def process_user_message(user_id, user_message, reply_token):
    """ประมวลผลข้อความผู้ใช้พร้อมจัดการสถานะและการตอบกลับ"""
    start_time = time.time()
    redis_client.delete(f"wait_notice:{user_id}")

    if check_session_timeout(user_id):
        send_session_timeout_message(user_id, reply_token=reply_token)
        return

    update_last_activity(user_id)

    if user_message.startswith('/'):
        if handle_command_with_processing(user_id, user_message, reply_token=reply_token):
            return

    if check_hospital_inquiry(user_message):
        hospital_response = get_hospital_information_message()
        send_final_response(user_id, hospital_response, reply_token=reply_token)
        return

    animation_success, _ = start_loading_animation(user_id)
    if not animation_success and reply_token:
        if send_processing_status(user_id, reply_token):
            reply_token = None

    process_ai_response_with_context(
        user_id,
        user_message,
        start_time,
        animation_success,
        reply_token,
    )

def process_ai_response_with_context(user_id: str, user_message: str, start_time: float, animation_success: bool, reply_token: Optional[str]):
    """
    สร้างการตอบกลับ AI โดยใช้บริบทจาก form พร้อมการจัดการข้อผิดพลาดที่ดีขึ้น
    """
    # ตัวแปรสำหรับเก็บสถานะและข้อมูลสำคัญ
    user_context = None
    messages = []
    bot_response = None
    error_occurred = False
    fallback_response = None
    
    try:
        # 1. ดึงบริบทผู้ใช้ (ไม่ critical - สามารถทำงานต่อได้แม้ไม่มีบริบท)
        try:
            user_context = get_user_context(user_id)
            if user_context:
                logging.info(f"โหลดบริบทผู้ใช้สำเร็จ: {user_id}")
        except Exception as e:
            logging.warning(f"ไม่สามารถโหลดบริบทผู้ใช้ {user_id}: {str(e)}")
            # ไม่ throw error - ให้ทำงานต่อแบบไม่มีบริบท
            user_context = None
        
        # 2. จัดการประวัติการสนทนาและโทเค็น
        try:
            messages = prepare_conversation_messages(user_id, user_context)
        except TokenThresholdExceeded:
            # ถ้าโทเค็นเกิน ใช้การจัดการแบบพิเศษ
            logging.info(f"โทเค็นเกินขีดจำกัดสำหรับผู้ใช้ {user_id}, ใช้การจัดการแบบไฮบริด")
            try:
                messages = hybrid_context_management(user_id, TOKEN_THRESHOLD)
                # เพิ่มบริบทกลับเข้าไปถ้ามี
                if user_context:
                    add_context_to_messages(messages, user_context)
            except Exception as hybrid_error:
                logging.error(f"การจัดการแบบไฮบริดล้มเหลว: {str(hybrid_error)}")
                # Fallback: ใช้เซสชันว่าง
                messages = create_minimal_session(user_context)
        except Exception as e:
            logging.error(f"เกิดข้อผิดพลาดในการเตรียมข้อความ: {str(e)}")
            messages = create_minimal_session(user_context)
        
        # 3. เพิ่มข้อความของผู้ใช้
        messages.append({"role": "user", "content": user_message})
        
        # 4. เรียก AI API พร้อม retry mechanism (2 retries + grok_client built-in retry = 4 max)
        bot_response = None
        max_retries = 2
        retry_count = 0
        
        while retry_count < max_retries and bot_response is None:
            try:
                response_text = generate_ai_response_with_timeout(messages, timeout=30)
                
                if not response_text:
                    raise ValueError("Empty AI response")
                
                bot_response = clean_ai_response(response_text)
                
                if not bot_response or len(bot_response.strip()) == 0:
                    raise ValueError("Empty response from AI")
                    
                break  # สำเร็จ
                
            except requests.exceptions.Timeout as timeout_error:
                retry_count += 1
                if retry_count < max_retries:
                    logging.warning(f"AI API timeout (attempt {retry_count}/{max_retries})")
                    time.sleep(2 ** retry_count)  # Exponential backoff
                else:
                    raise create_legacy_chatbot_error(
                        ErrorType.AI_API_ERROR,
                        "AI API timeout after all retries",
                        timeout_error
                    )
                    
            except RateLimitError as e:
                # จัดการ rate limit แบบพิเศษ
                wait_time = e.retry_after if hasattr(e, 'retry_after') else 60
                logging.warning(f"Rate limited, waiting {wait_time} seconds")
                
                # ส่งข้อความแจ้งผู้ใช้
                send_rate_limit_notification(user_id, wait_time)
                
                # รอแล้วลองใหม่
                time.sleep(wait_time)
                retry_count += 1
                
            except Exception as e:
                logging.error(f"AI API error (attempt {retry_count + 1}): {str(e)}")
                retry_count += 1
                if retry_count >= max_retries:
                    raise create_legacy_chatbot_error(
                        ErrorType.AI_API_ERROR,
                        f"AI API error after {max_retries} attempts",
                        e
                    )
        
        # 5. ถ้ายังไม่มี response ให้ใช้ fallback
        if not bot_response:
            bot_response = generate_fallback_response(user_message, user_context)
            fallback_response = bot_response  # บันทึกว่าใช้ fallback
        
        # 6. เพิ่มข้อความตอบกลับลงในประวัติ
        messages.append({"role": "assistant", "content": bot_response})
        
        # 7. จัดการจังหวะเวลา (ก่อนส่ง เพื่อให้ animation แสดงครบ)
        handle_response_timing(start_time, animation_success)
        
        # 8. ส่งการตอบกลับทันที — ลด latency โดยส่งก่อนบันทึกข้อมูล
        try:
            success = send_final_response(user_id, bot_response, reply_token=reply_token)
            if not success:
                raise create_legacy_chatbot_error(
                    ErrorType.MESSAGE_SEND_ERROR,
                    "Failed to send response to user"
                )
        except Exception as e:
            logging.critical(f"ไม่สามารถส่งข้อความให้ผู้ใช้ {user_id}: {str(e)}")
            notify_admin_critical_error(user_id, user_message, str(e))
        
        # 9. ย้ายงาน post-response ไป background thread (ไม่บล็อกผู้ใช้)
        _post_response_args = {
            'user_id': user_id,
            'user_message': user_message,
            'bot_response': bot_response,
            'messages': messages.copy(),  # copy เพื่อ thread-safety
            'start_time': start_time,
            'fallback_response': fallback_response,
            'error_occurred': error_occurred,
        }
        threading.Thread(
            target=_post_response_background,
            kwargs=_post_response_args,
            daemon=True,
        ).start()
        
    except ChatbotError as e:
        # จัดการ custom errors
        handle_chatbot_error(e, user_id, user_message, reply_token=reply_token)
        
    except Exception as e:
        # จัดการ unexpected errors
        logging.critical(f"Unexpected error in process_ai_response: {str(e)}", exc_info=True)
        handle_unexpected_error(e, user_id, user_message, reply_token=reply_token)



def _post_response_background(
    user_id: str,
    user_message: str,
    bot_response: str,
    messages: List[Dict[str, str]],
    start_time: float,
    fallback_response: Optional[str],
    error_occurred: bool,
):
    """Background thread: save conversation data, metrics, and send notifications.

    Runs after the response has already been sent to the user so that
    DB/Redis writes and follow-up scheduling don't add to perceived latency.
    """
    try:
        # บันทึกข้อมูลการสนทนา
        try:
            process_conversation_data_safely(user_id, user_message, bot_response, messages)
        except Exception as e:
            logging.error(f"[background] เกิดข้อผิดพลาดในการบันทึกข้อมูล: {e}")
            error_occurred = True

        # แจ้งเตือนถ้าใช้ fallback หรือมี error
        if fallback_response or error_occurred:
            send_system_notification(user_id, fallback_response is not None, error_occurred)

        # บันทึกเวลาประมวลผลและ metrics
        total_time = time.time() - start_time
        logging.info(f"เวลาประมวลผลทั้งหมดสำหรับผู้ใช้ {user_id}: {total_time:.2f} วินาที")
        record_processing_metrics(user_id, total_time, fallback_response is not None, error_occurred)

    except Exception as e:
        logging.error(f"[background] Unexpected error in post-response processing for {user_id}: {e}")


def history_to_messages(history: List[Tuple], max_pairs: int = DB_RESTORE_MESSAGE_PAIRS) -> Tuple[List[Dict[str, str]], Set[int]]:
    """Convert database conversation rows into chronological chat messages."""
    if not history:
        return [], set()

    history_sorted = sorted(history, key=lambda item: item[0])
    trimmed_history = history_sorted[-max_pairs:] if max_pairs else history_sorted
    used_ids: Set[int] = {entry[0] for entry in trimmed_history if entry and len(entry) > 0}

    messages: List[Dict[str, str]] = []
    for _, user_msg, bot_resp in trimmed_history:
        if user_msg:
            messages.append({"role": "user", "content": user_msg})
        if bot_resp:
            messages.append({"role": "assistant", "content": bot_resp})

    return messages, used_ids

def prepare_conversation_messages(user_id: str, user_context: Optional[str]) -> List[Dict[str, str]]:
    """เตรียมข้อความสำหรับการสนทนา พร้อมจัดการข้อผิดพลาด"""
    try:
        session_token_count = get_session_token_count(user_id)
        logging.info(f"จำนวนโทเค็นปัจจุบัน: {session_token_count} (ผู้ใช้: {user_id})")

        if session_token_count > TOKEN_THRESHOLD:
            raise TokenThresholdExceeded(f"Token count {session_token_count} exceeds threshold")

        messages = get_chat_session(user_id) or []
        used_history_ids: Set[int] = set()
        history_for_summary: List[Tuple] = []

        history_token_limit = 100000 if not messages else 50000
        try:
            history_for_summary = db.get_user_history(user_id, max_tokens=history_token_limit) or []
        except Exception as e:
            logging.warning(f"ไม่สามารถโหลดประวัติจากฐานข้อมูล: {str(e)}")
            history_for_summary = []

        if not messages and history_for_summary:
            restored_messages, used_history_ids = history_to_messages(history_for_summary, max_pairs=DB_RESTORE_MESSAGE_PAIRS)
            if restored_messages:
                messages = restored_messages
                try:
                    save_chat_session(user_id, messages)
                    logging.info(f"กู้คืนประวัติการสนทนาจากฐานข้อมูลสำหรับผู้ใช้ {user_id}: {len(messages)} ข้อความ")
                except Exception as store_error:
                    logging.warning(f"ไม่สามารถบันทึกเซสชันที่กู้คืนสำหรับผู้ใช้ {user_id}: {store_error}")
        elif history_for_summary:
            _, used_history_ids = history_to_messages(history_for_summary, max_pairs=DB_RESTORE_MESSAGE_PAIRS)

        if history_for_summary:
            prepare_conversation_context(messages, history_for_summary, used_history_ids)

        if user_context:
            add_context_to_messages(messages, user_context)

        return messages

    except Exception as e:
        logging.error(f"Error in prepare_conversation_messages: {str(e)}")
        raise


def add_context_to_messages(messages: List[Dict[str, str]], user_context: str):
    """เพิ่มบริบทผู้ใช้ลงในข้อความ"""
    # ตรวจสอบว่ายังไม่มีบริบทอยู่แล้ว
    if not any(msg.get('content', '').startswith('บริบทผู้ใช้จากแบบประเมิน:') for msg in messages):
        context_message = {
            "role": "system",
            "content": f"บริบทผู้ใช้จากแบบประเมิน:\n{user_context}\n\nใช้ข้อมูลนี้เพื่อให้คำปรึกษาที่เหมาะสมกับสถานการณ์ของผู้ใช้"
        }
        # แทรกหลัง system message หลัก
        if messages and messages[0].get('role') == 'system':
            messages.insert(1, context_message)
        else:
            messages.insert(0, context_message)


def create_minimal_session(user_context: Optional[str]) -> List[Dict[str, str]]:
    """สร้างเซสชันขั้นต่ำเมื่อไม่สามารถโหลดประวัติได้"""
    messages = [SYSTEM_MESSAGES]
    
    if user_context:
        add_context_to_messages(messages, user_context)
        
    return messages


def generate_ai_response_with_timeout(messages: List[Dict[str, str]], timeout: int = 30) -> str:
    """เรียก xAI Grok API พร้อม timeout และคืนข้อความตอบกลับ"""
    filtered_messages = filter_messages_for_api(messages)
    effective_timeout = _calculate_adaptive_timeout(filtered_messages, base_timeout=timeout)

    # ดึงข้อความล่าสุดจากผู้ใช้เพื่อเลือก config ที่เหมาะสม
    user_message = ""
    for msg in reversed(filtered_messages):
        if msg.get("role") == "user":
            user_message = msg.get("content", "")
            break

    # เลือก config แบบ dynamic ตามบริบท
    dynamic_config = get_dynamic_config(user_message, filtered_messages)

    # ใช้ SYSTEM_MESSAGE_CORE แทน SYSTEM_MESSAGES เต็ม เพื่อลด token overhead
    # ตรวจสอบว่า filtered_messages มี system message อยู่แล้วหรือไม่ เพื่อไม่ให้ซ้ำซ้อน
    has_system_msg = any(msg.get("role") == "system" for msg in filtered_messages)
    if has_system_msg:
        api_messages = filtered_messages
    else:
        api_messages = [SYSTEM_MESSAGE_CORE] + filtered_messages

    def _call() -> str:
        return grok_client.send_chat(
            messages=api_messages,
            model=config.XAI_MODEL,
            **dynamic_config,
        )

    future = _AI_EXECUTOR.submit(_call)
    try:
        return future.result(timeout=effective_timeout)
    except concurrent.futures.TimeoutError:
        raise requests.exceptions.Timeout(f"AI API timeout after {effective_timeout} seconds")


def _calculate_adaptive_timeout(filtered_messages: List[Dict[str, str]], base_timeout: int = 30) -> int:
    """คำนวณ timeout ตามขนาดข้อความเพื่อรองรับบริบทที่ยาวขึ้น"""
    max_timeout = max(base_timeout, 120)

    token_count = 0
    char_count = 0

    try:
        payload = [SYSTEM_MESSAGE_CORE] + filtered_messages
        if token_counter is not None:
            token_count = token_counter.count_message_tokens(payload)
    except Exception as token_error:
        logging.debug(f"Adaptive timeout token count failed: {token_error}")

    try:
        char_count = sum(len(message.get('content', '')) for message in filtered_messages)
    except Exception as length_error:
        logging.debug(f"Adaptive timeout length calculation failed: {length_error}")

    adaptive_timeout = base_timeout

    if token_count > 1000:
        extra_token_units = math.ceil((token_count - 1000) / 400)
        adaptive_timeout += extra_token_units * 5

    if char_count > 4000:
        extra_length_units = math.ceil((char_count - 4000) / 2000)
        adaptive_timeout += extra_length_units * 5

    adaptive_timeout = max(base_timeout, min(adaptive_timeout, max_timeout))

    logging.debug(
        f"Adaptive timeout computed: base={base_timeout}s, tokens={token_count}, "
        f"chars={char_count}, result={adaptive_timeout}s"
    )

    return adaptive_timeout


def generate_fallback_response(user_message: str, user_context: Optional[str]) -> str:
    """สร้างคำตอบสำรองเมื่อ AI API ไม่ทำงาน"""
    # ตรวจสอบประเภทของคำถาม
    message_lower = user_message.lower()
    
    # คำตอบสำหรับกรณีฉุกเฉิน
    if any(word in message_lower for word in ['ฆ่าตัวตาย', 'ทำร้ายตัวเอง', 'อยากตาย']):
        return (
            "ใจดีเข้าใจว่าคุณกำลังผ่านช่วงเวลาที่ยากลำบาก\n\n"
            "⚠️ กรุณาติดต่อสายด่วนสุขภาพจิต 1323 ทันที\n"
            "หรือโทร 1669 หากต้องการความช่วยเหลือฉุกเฉิน\n\n"
            "คุณไม่ได้อยู่คนเดียว มีคนพร้อมช่วยเหลือคุณตลอด 24 ชั่วโมง"
        )
    
    # คำตอบทั่วไป
    return (
        "ขออภัยครับ ระบบกำลังประสบปัญหาชั่วคราว\n\n"
        "ใจดียังคงอยู่ที่นี่และพร้อมรับฟังคุณ "
        "กรุณาลองพูดคุยกับใจดีอีกครั้งในอีกสักครู่นะครับ\n\n"
        "หากต้องการความช่วยเหลือเร่งด่วน:\n"
        "📞 สายด่วนยาเสพติด: 1165\n"
        "📞 สายด่วนสุขภาพจิต: 1323"
    )


def process_conversation_data_safely(user_id: str, user_message: str, bot_response: str, messages: List[Dict[str, str]]):
    """บันทึกข้อมูลการสนทนาแบบปลอดภัย"""
    try:
        # ใช้ transaction-like approach
        temp_data = {
            'user_id': user_id,
            'user_message': user_message,
            'bot_response': bot_response,
            'timestamp': datetime.now(),
            'messages': messages.copy()
        }
        
        # บันทึกลง Redis ก่อน (fast, ถ้าล้มเหลวยังมีข้อมูลใน memory)
        try:
            save_chat_session(user_id, messages)
        except Exception as e:
            logging.error(f"Failed to save to Redis: {str(e)}")
            # เก็บใน queue สำหรับ retry ภายหลัง
            queue_for_retry('redis_save', temp_data)
        
        # บันทึกลงฐานข้อมูล
        try:
            message_token_count = token_counter.count_tokens(user_message + bot_response)
            risk_level, keywords = assess_risk(user_message)
            is_important = is_important_message(user_message, bot_response)
            if risk_level == GENERAL_RISK_LEVEL:
                is_important = False

            db.save_conversation(
                user_id=user_id,
                user_message=user_message,
                bot_response=bot_response,
                token_count=message_token_count,
                important=is_important
            )
            
            save_progress_data(user_id, risk_level, keywords)
            
        except Exception as e:
            logging.error(f"Failed to save to database: {str(e)}")
            queue_for_retry('db_save', temp_data)
        
        # กำหนดการติดตาม
        try:
            schedule_follow_up(user_id, None)
        except Exception as e:
            logging.error(f"Failed to schedule follow-up: {str(e)}")
            # ไม่ critical - ไม่ต้อง retry
            
    except Exception as e:
        logging.error(f"Unexpected error in process_conversation_data_safely: {str(e)}")
        raise


def send_system_notification(user_id: str, used_fallback: bool, had_error: bool):
    """ส่งการแจ้งเตือนระบบให้ผู้ใช้"""
    if used_fallback:
        message = "💡 หมายเหตุ: ระบบใช้การตอบกลับสำรองเนื่องจากมีปัญหาชั่วคราว"
    elif had_error:
        message = "⚠️ มีข้อผิดพลาดบางอย่างในการบันทึกข้อมูล แต่การสนทนายังดำเนินต่อได้"
    else:
        return
        
    # ส่งแบบ delayed เพื่อไม่รบกวน flow หลัก
    def send_delayed():
        time.sleep(2)
        try:
            line_bot_api.push_message(user_id, TextSendMessage(text=message))
        except Exception as e:
            logging.debug(f"Failed to send system notification to {user_id}: {e}")
            
    threading.Thread(target=send_delayed, daemon=True).start()


# Helper function to create legacy-compatible ChatbotError
def create_legacy_chatbot_error(error_type: ErrorType, message: str, original_error: Optional[Exception] = None):
    """Create ChatbotError compatible with legacy system"""
    # Create a simple object that mimics the old ChatbotError for backward compatibility
    class LegacyChatbotError(Exception):
        def __init__(self, error_type: ErrorType, message: str, original_error: Optional[Exception] = None):
            self.error_type = error_type
            self.message = message
            self.original_error = original_error
            super().__init__(self.message)
    
    return LegacyChatbotError(error_type, message, original_error)


def handle_chatbot_error(error: ChatbotError, user_id: str, user_message: str, reply_token: Optional[str] = None):
    """จัดการข้อผิดพลาดที่คาดการณ์ได้ - compatible with both legacy and new error systems"""

    if hasattr(error, 'error_type'):
        logging.error(f"ChatbotError [Legacy-{error.error_type.value}]: {error.message}")

        error_messages = {
            ErrorType.AI_API_ERROR: "ขออภัยครับ ระบบ AI กำลังมีปัญหา กรุณาลองใหม่อีกครั้ง",
            ErrorType.TOKEN_MANAGEMENT_ERROR: "กำลังจัดระเบียบข้อมูล กรุณารอสักครู่",
            ErrorType.DATABASE_ERROR: "มีปัญหาในการบันทึกข้อมูล แต่เรายังคุยกันต่อได้ครับ",
            ErrorType.MESSAGE_SEND_ERROR: "ไม่สามารถส่งข้อความได้ กรุณาตรวจสอบการเชื่อมต่อ",
        }

        message = error_messages.get(
            error.error_type,
            "ขออภัยครับ เกิดข้อผิดพลาด กรุณาลองใหม่",
        )
    else:
        logging.error(f"ChatbotError [{error.category.value}-{error.severity.value}]: {error.message}")
        message = error.user_message or "ขออภัยครับ เกิดข้อผิดพลาด กรุณาลองใหม่"

    try:
        send_final_response(user_id, message, reply_token=reply_token)
    except Exception:
        logging.critical(f"Cannot send error message to user {user_id}")


def handle_unexpected_error(error: Exception, user_id: str, user_message: str, reply_token: Optional[str] = None):
    """จัดการข้อผิดพลาดที่ไม่คาดคิด"""
    error_id = f"ERR_{datetime.now().strftime('%Y%m%d%H%M%S')}_{user_id[:8]}"

    logging.critical(
        f"Unexpected error {error_id}:\n"
        f"User: {user_id}\n"
        f"Message: {user_message}\n"
        f"Error: {str(error)}\n"
        f"Traceback: {traceback.format_exc()}"
    )

    save_error_for_analysis(error_id, user_id, user_message, error)

    try:
        message = (
            "ขออภัยครับ เกิดข้อผิดพลาดที่ไม่คาดคิด\n"
            f"รหัสข้อผิดพลาด: {error_id}\n\n"
            "กรุณาลองใหม่อีกครั้ง หรือติดต่อผู้ดูแลระบบ"
        )
        send_final_response(user_id, message, reply_token=reply_token)
    except Exception:
        pass


def queue_for_retry(operation_type: str, data: dict):
    """เก็บข้อมูลไว้สำหรับ retry ภายหลัง"""
    try:
        retry_key = f"retry_queue:{operation_type}"
        redis_client.lpush(retry_key, json.dumps({
            'data': data,
            'timestamp': datetime.now().isoformat(),
            'attempts': 0
        }))
        # ตั้ง expiry 24 ชั่วโมง
        redis_client.expire(retry_key, 86400)
    except Exception as e:
        logging.warning(f"Failed to queue for retry ({operation_type}): {e}")
        # ถ้า Redis ไม่ทำงาน บันทึกลงไฟล์
        try:
            with open(f"retry_{operation_type}_{datetime.now().strftime('%Y%m%d')}.log", 'a') as f:
                f.write(json.dumps(data) + '\n')
        except Exception:
            logging.error(f"Failed to write retry log for {operation_type}")


def record_processing_metrics(user_id: str, processing_time: float, used_fallback: bool, had_error: bool):
    """บันทึก metrics สำหรับการ monitoring"""
    try:
        metrics = {
            'user_id': user_id,
            'processing_time': processing_time,
            'used_fallback': used_fallback,
            'had_error': had_error,
            'timestamp': datetime.now().isoformat()
        }
        
        # บันทึกลง Redis สำหรับ real-time monitoring
        redis_client.lpush('processing_metrics', json.dumps(metrics))
        redis_client.ltrim('processing_metrics', 0, 9999)  # เก็บแค่ 10,000 รายการล่าสุด
        
        # Update aggregated metrics
        if processing_time > 10:  # Slow response
            redis_client.incr('metrics:slow_responses')
        if used_fallback:
            redis_client.incr('metrics:fallback_used')
        if had_error:
            redis_client.incr('metrics:errors_occurred')
            
    except Exception as e:
        logging.debug(f"Failed to record processing metrics: {e}")  # Metrics เป็น nice-to-have


def notify_admin_critical_error(user_id: str, user_message: str, error: str):
    """แจ้งเตือน admin เมื่อมี critical error"""
    # ใช้วิธีที่เหมาะสมกับระบบ เช่น
    # - ส่งอีเมล
    # - ส่ง LINE notify
    # - บันทึกลง monitoring system
    pass


def save_error_for_analysis(error_id: str, user_id: str, user_message: str, error: Exception):
    """บันทึกข้อผิดพลาดสำหรับการวิเคราะห์"""
    try:
        error_data = {
            'error_id': error_id,
            'user_id': user_id,
            'user_message': user_message[:500],  # จำกัดความยาว
            'error_type': type(error).__name__,
            'error_message': str(error),
            'traceback': traceback.format_exc(),
            'timestamp': datetime.now().isoformat()
        }
        
        # บันทึกลง Redis
        redis_client.hset(f"errors:{datetime.now().strftime('%Y%m%d')}", error_id, json.dumps(error_data))
        redis_client.expire(f"errors:{datetime.now().strftime('%Y%m%d')}", 604800)  # 7 วัน
        
    except:
        # ถ้าบันทึกไม่ได้ ก็ไม่ต้องทำอะไร
        pass


# Custom Exceptions
class TokenThresholdExceeded(Exception):
    """เกิดขึ้นเมื่อโทเค็นเกินขีดจำกัด"""
    pass

class RateLimitError(Exception):
    """เกิดขึ้นเมื่อถูก rate limit"""
    def __init__(self, message: str, retry_after: int = 60):
        self.retry_after = retry_after
        super().__init__(message)


def send_rate_limit_notification(user_id: str, wait_time: int):
    """แจ้งผู้ใช้เมื่อถูก rate limit"""
    try:
        message = (
            f"⏳ ขออภัยครับ ระบบกำลังประมวลผลหนัก\n"
            f"กรุณารอประมาณ {wait_time} วินาที แล้วลองใหม่อีกครั้ง\n\n"
            "ใจดีจะรีบกลับมาคุยกับคุณโดยเร็วที่สุดนะครับ 💚"
        )
        line_bot_api.push_message(user_id, TextSendMessage(text=message))
    except Exception as e:
        logging.warning(f"Failed to send rate limit notification to {user_id}: {e}")


def prepare_conversation_context(messages, optimized_history, used_history_ids: Optional[Set[int]] = None):
    """Prepare conversation context by using stored history."""
    optimized_history = optimized_history or []
    used_history_ids = used_history_ids or set()

    if not optimized_history:
        return

    history_sorted = sorted(optimized_history, key=lambda item: item[0])
    if used_history_ids:
        history_for_summary = [entry for entry in history_sorted if entry[0] not in used_history_ids]
    else:
        history_for_summary = history_sorted[5:]

    if not history_for_summary:
        return

    summary = summarize_conversation_history(history_for_summary)
    if summary:
        messages[:] = [msg for msg in messages if msg.get('role') != 'system_summary']
        messages.append({"role": "system_summary", "content": "สรุปการสนทนาก่อนหน้า: " + summary})


def send_session_timeout_message(user_id, reply_token=None):
    """ส่งข้อความเซสชันหมดอายุ"""
    welcome_back = (
        "สวัสดีครับ ยินดีต้อนรับกลับมา 👋\n\n"
        "เซสชันก่อนหน้าของเราหมดอายุแล้ว เราสามารถเริ่มการสนทนาใหม่ได้ทันที\n\n"
        "💡 ต้องการดูประวัติการสนทนาก่อนหน้า พิมพ์: /status\n"
        "💡 ต้องการดูรายงานความก้าวหน้า พิมพ์: /progress\n"
        "💡 ต้องการคำแนะนำเพิ่มเติม พิมพ์: /help\n\n"
        "คุณต้องการพูดคุยเกี่ยวกับเรื่องอะไรดีครับวันนี้?"
    )
    send_final_response(user_id, welcome_back, reply_token=reply_token)


def handle_command_with_processing(user_id, command, reply_token=None):
    """จัดการคำสั่งและส่งผลลัพธ์กลับไปยังผู้ใช้ หากจัดการได้จะคืน True"""
    normalized = command.strip()

    if normalized.startswith('/verify'):
        if is_user_registered(user_id):
            send_final_response(
                user_id,
                "✅ คุณได้ลงทะเบียนและยืนยันตัวตนเรียบร้อยแล้ว\n"
                "ไม่จำเป็นต้องยืนยันอีกครั้ง คุณสามารถใช้บริการของน้องใจดีได้ตามปกติ\n\n"
                "พิมพ์ /help เพื่อดูคำสั่งและบริการที่มี",
                reply_token=reply_token,
            )
            return True

        parts = normalized.split()
        if len(parts) != 2:
            send_final_response(
                user_id,
                "รูปแบบไม่ถูกต้อง กรุณาพิมพ์ \"/verify\" ตามด้วยรหัส 6 หลัก เช่น \"/verify 123456\"",
                reply_token=reply_token,
            )
            return True

        confirmation_code = parts[1].strip()
        success, message = register_user_with_code(user_id, confirmation_code)
        send_final_response(user_id, message, reply_token=reply_token)
        return True

    response_text = None

    if normalized == '/reset':
        db.clear_user_history(user_id)
        redis_client.delete(f"chat_session:{user_id}")
        redis_client.delete(f"session_tokens:{user_id}")
        redis_client.zrem('follow_up_queue', user_id)
        redis_client.delete(f"last_follow_up:{user_id}")
        redis_client.delete(f"first_interaction:{user_id}")
        response_text = (
            "🔄 ล้างประวัติการสนทนาเรียบร้อยแล้วครับ\n\n"
            "เราสามารถเริ่มต้นการสนทนาใหม่ได้ทันที\n"
            "คุณต้องการพูดคุยเกี่ยวกับเรื่องอะไรดีครับ?"
        )

    elif normalized == '/optimize':
        token_count_before = get_session_token_count(user_id)
        hybrid_context_management(user_id, TOKEN_THRESHOLD)
        token_count_after = get_session_token_count(user_id)

        response_text = (
            f"🔄 ปรับปรุงประวัติการสนทนาเรียบร้อยแล้วครับ\n\n"
            f"จำนวนโทเค็น: {token_count_before} → {token_count_after} ({(token_count_before - token_count_after)} ลดลง)\n\n"
            "ประวัติการสนทนาสำคัญยังคงถูกเก็บไว้ และบอทยังเข้าใจบริบทการสนทนาของเรา\n"
            "เราสามารถสนทนาต่อได้ตามปกติครับ"
        )

    elif normalized == '/tokens':
        token_count = get_session_token_count(user_id)
        max_tokens = TOKEN_THRESHOLD
        percentage = (token_count / max_tokens) * 100 if max_tokens else 0

        response_text = (
            f"📊 สถิติการใช้โทเค็น\n\n"
            f"โทเค็นในเซสชันปัจจุบัน: {token_count:,}\n"
            f"ขีดจำกัด: {max_tokens:,}\n"
            f"เปอร์เซ็นต์การใช้งาน: {percentage:.1f}%\n\n"
            f"{'⚠️ ใกล้ถึงขีดจำกัด โปรดใช้ /optimize เพื่อปรับปรุงประวัติ' if percentage > 80 else '✅ อยู่ในเกณฑ์ปกติ'}"
        )

    elif normalized == '/followup':
        response_text = get_follow_up_status(user_id)

    elif normalized == '/help':
        response_text = (
            "สวัสดีครับ 👋 ฉันคือน้องใจดี ผู้ช่วยดูแลและให้คำปรึกษาสำหรับผู้ที่ต้องการเลิกใช้สารเสพติด"
            "💬 ฉันสามารถช่วยคุณได้ดังนี้:\n"
            "- พูดคุยและให้กำลังใจในการเลิกใช้สารเสพติด\n"
            "- ให้คำปรึกษาเกี่ยวกับวิธีรับมือความอยากและอาการถอน\n"
            "- ให้ข้อมูลเกี่ยวกับผลกระทบของสารเสพติดและการรักษา\n"
            "- ติดตามความก้าวหน้าและให้คำแนะนำที่เหมาะสมกับคุณ\n\n"
            "🛠️ คำสั่งที่มีให้ใช้:\n"
            "📥 /register - วิธีลงทะเบียนใช้งาน\n"
            "✅ /verify <รหัส> - ยืนยันตัวตนด้วยรหัส 6 หลัก\n"
            "🧠 /optimize - ปรับปรุงประวัติการสนทนาให้มีประสิทธิภาพ\n"
            "🪙 /tokens - ตรวจสอบการใช้งานโทเค็นในเซสชันปัจจุบัน\n"
            "📊 /status - ดูสรุปสถานะการสนทนาและการใช้โทเค็น\n"
            "📈 /progress - ดูรายงานความก้าวหน้าและแนวทางถัดไป\n"
            "📋 /context - ดูบริบทของคุณจากแบบประเมินที่กรอกไว้\n"
            "🔔 /followup - ตรวจสอบกำหนดการติดตามของคุณ\n"
            "🚨 /emergency - ดูข้อมูลติดต่อฉุกเฉินและสายด่วน\n"
            "❓ /help - แสดงเมนูช่วยเหลือนี้\n\n"
            "💡 ตัวอย่างคำถามที่สามารถถามฉันได้:\n"
            "- \"ช่วยประเมินการใช้สารเสพติดของฉันหน่อย\"\n"
            "- \"ผลกระทบของยาบ้าต่อร่างกายมีอะไรบ้าง\"\n"
            "- \"มีเทคนิคจัดการความอยากยาอย่างไร\"\n"
            "- \"ฉันควรทำอย่างไรเมื่อรู้สึกอยากกลับไปใช้สารอีก\"\n\n"
            "📧 ติดต่อทีมงาน:\n"
            "หากพบข้อผิดพลาด (บัค) หรือมีข้อเสนอแนะ สามารถติดต่อได้ที่:\n"
            "🔧 ผู้พัฒนาระบบ: pahnkcn@gmail.com\n"
            "📖 ผู้วิจัย: Std6548097@pcm.ac.th\n\n"
            "เริ่มพูดคุยกับฉันได้เลยนะครับ ฉันพร้อมรับฟังและช่วยเหลือคุณ 💚"
        )

    elif normalized == '/status':
        history_count = db.get_user_history_count(user_id)
        important_count = db.get_important_message_count(user_id)
        last_interaction = db.get_last_interaction(user_id)
        current_session = redis_client.exists(f"chat_session:{user_id}") == 1
        total_db_tokens = db.get_total_tokens(user_id) or 0
        session_tokens = get_session_token_count(user_id)

        response_text = (
            "📊 สถิติการสนทนาของคุณ\n"
            f"▫️ จำนวนการสนทนาที่บันทึก: {history_count} ครั้ง\n"
            f"▫️ ประเด็นสำคัญที่พูดคุย: {important_count} รายการ\n"
            f"▫️ สนทนาล่าสุดเมื่อ: {last_interaction}\n"
            f"▫️ สถานะเซสชันปัจจุบัน: {'🟢 กำลังสนทนาอยู่' if current_session else '🔴 ยังไม่เริ่มสนทนา'}\n\n"
            f"📝 สถิติโทเค็น\n"
            f"▫️ โทเค็นในเซสชันปัจจุบัน: {session_tokens:,}\n"
            f"▫️ โทเค็นในฐานข้อมูล: {total_db_tokens:,}\n"
            "  (ผลรวมของแต่ละข้อความที่บันทึก)\n\n"
            "💚 น้องใจดีพร้อมให้คำปรึกษาและสนับสนุนคุณตลอดเส้นทางการเลิกสารเสพติด\n"
            "💬 มีคำถามหรือต้องการความช่วยเหลือ เพียงพิมพ์บอกฉันได้เลยครับ\n\n"
            "ℹ️ เคล็ดลับ: ต้องการดูรายงานความก้าวหน้าของคุณ พิมพ์ /progress"
        )

    elif normalized == '/emergency':
        response_text = (
            "🚨 บริการช่วยเหลือฉุกเฉิน 🚨\n\n"
            "หากคุณหรือคนใกล้ตัวกำลังประสบปัญหาต่อไปนี้:\n"
            "- ใช้สารเสพติดเกินขนาด (Overdose)\n"
            "- มีอาการชัก เลือดออก หมดสติ\n"
            "- มีความคิดทำร้ายตัวเอง\n"
            "- มีอาการถอนยารุนแรง\n\n"
            "📞 ติดต่อขอความช่วยเหลือด่วนได้ที่:\n"
            "🔸 สายด่วนกรมควบคุมโรค: 1422\n"
            "🔸 ศูนย์ปรึกษาปัญหายาเสพติด: 1165\n"
            "🔸 หน่วยกู้ชีพฉุกเฉิน: 1669\n"
            "🔸 สายด่วนสุขภาพจิต: 1323\n\n"
            "🌐 เว็บไซต์ช่วยเหลือ:\n"
            "https://www.pmnidat.go.th\n\n"
            "💚 การขอความช่วยเหลือคือก้าวแรกของการดูแลตัวเอง"
        )

    elif normalized == '/progress':
        report = generate_progress_report(user_id)
        response_text = (
            f"{report}\n\nℹ️ เคล็ดลับ: ต้องการดูสรุปสถานะการสนทนาปัจจุบัน พิมพ์ /status"
            if report else
            (
                "📊 รายงานความก้าวหน้า\n\n"
                "ยังไม่มีข้อมูลความก้าวหน้าเพียงพอสำหรับการวิเคราะห์\n\n"
                "เมื่อเราพูดคุยกันมากขึ้น น้องใจดีจะสามารถติดตามและวิเคราะห์ความก้าวหน้าของคุณได้\n\n"
                "ℹ️ เคล็ดลับ: ดูสรุปสถานะล่าสุดด้วย /status"
            )
        )

    elif normalized == '/register':
        response_text = (
            "📝 การลงทะเบียนใช้งานน้องใจดี\n\n"
            "เพื่อเริ่มใช้งาน คุณจำเป็นต้องลงทะเบียนก่อน โดยทำตามขั้นตอนดังนี้:\n\n"
            "1. กรอกแบบฟอร์มที่ลิงก์นี้: https://forms.gle/KYU4JNWL72TL3PsG9\n"
            "2. หลังกรอกเสร็จ คุณจะได้รับรหัสยืนยัน 6 หลัก\n"
            "3. นำรหัสมาพิมพ์ที่นี่ด้วยคำสั่ง \"/verify รหัส\" เช่น \"/verify 123456\"\n\n"
            "หากมีปัญหาในการลงทะเบียน คุณสามารถติดต่อเจ้าหน้าที่ได้ที่ support@example.com"
        )

    elif normalized == '/context':
        context = get_user_context(user_id)
        if context:
            response_text = (
                "📋 บริบทของคุณจากแบบประเมิน:\n\n"
                f"{context}\n\n"
                "ใจดีใช้ข้อมูลนี้เพื่อให้คำปรึกษาที่เหมาะสมกับคุณมากที่สุดครับ"
            )
        else:
            response_text = (
                "ไม่พบข้อมูลบริบทจากแบบประเมิน\n"
                "อาจเป็นเพราะคุณลงทะเบียนก่อนที่ระบบจะมีฟีเจอร์นี้"
            )
        send_final_response(user_id, response_text, reply_token=reply_token)
        return True

    else:
        send_final_response(
            user_id,
            "คำสั่งไม่ถูกต้องครับ ลองพิมพ์ /help เพื่อดูคำสั่งที่สามารถใช้ได้",
            reply_token=reply_token,
        )
        return True

    if response_text:
        send_final_response(user_id, response_text, reply_token=reply_token)
        return True

    return False

def handle_response_timing(start_time, animation_success):
    """จัดการเวลาในการตอบสนองเพื่อประสบการณ์ผู้ใช้ที่ดีขึ้น"""
    # คำนวณเวลาที่ผ่านไป
    elapsed_time = time.time() - start_time

    # ถ้าเรามีการเคลื่อนไหวที่สำเร็จและการตอบสนอง API กลับมาอย่างรวดเร็ว
    # เพิ่มการหน่วงเวลาเล็กน้อยเพื่อให้แน่ใจว่าผู้ใช้เห็นภาพเคลื่อนไหวเป็นระยะเวลาที่เหมาะสม
    # แต่ไม่นานเกินไปที่จะทำให้เกิดความหงุดหงิด (ขั้นต่ำ 5 วินาที สูงสุด 15 วินาที)
    if animation_success and elapsed_time < 2:
        # หน่วงเล็กน้อยเพื่อให้ animation แสดงสักครู่ แต่ไม่นานเกินไป
        time.sleep(2 - elapsed_time)

# เส้นทาง Flask
@app.route("/callback", methods=['POST'])
@limiter.limit("10/minute")
def callback():
    # รับค่า X-Line-Signature header
    signature = request.headers['X-Line-Signature']

    # รับเนื้อหาคำขอเป็นข้อความ
    body = request.get_data(as_text=True)
    app.logger.debug("Request body received (length: %d)", len(body))

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return 'OK'

def process_ai_summary_async(code, full_form_data):
    """
    ประมวลผล AI summary ในพื้นหลัง (background thread)

    Args:
        code (str): รหัสยืนยัน
        full_form_data (dict): ข้อมูล form ที่ต้องการสรุป
    """
    try:
        logging.info(f"เริ่มสรุปข้อมูล form ในพื้นหลังสำหรับรหัส: {code}")

        # สรุปข้อมูลด้วย AI
        ai_summary = summarize_form_data(full_form_data)

        # ดึงข้อมูลเดิมออกมาก่อน
        select_query = 'SELECT form_data FROM registration_codes WHERE code = %s'
        result = db_manager.execute_query(select_query, (code,))

        if result and result[0]:
            existing_data = json.loads(result[0][0])
            # อัปเดต AI summary
            existing_data['ai_summary'] = ai_summary
            existing_data['ai_processed_at'] = datetime.now().isoformat()

            # บันทึกกลับลงฐานข้อมูล
            update_query = '''
                UPDATE registration_codes
                SET form_data = %s
                WHERE code = %s
            '''
            db_manager.execute_and_commit(
                update_query,
                (json.dumps(existing_data), code)
            )

            logging.info(f"อัปเดต AI summary สำเร็จสำหรับรหัส: {code}")
        else:
            logging.warning(f"ไม่พบรหัส {code} ในฐานข้อมูล ไม่สามารถอัปเดต AI summary ได้")

    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการสรุปข้อมูล form แบบ async สำหรับรหัส {code}: {str(e)}")


@app.route("/api/add-verification-code", methods=['POST'])
@limiter.exempt
def add_verification_code():
    """API endpoint รับรหัสยืนยันและข้อมูล form จาก Google Apps Script"""

    # ตรวจสอบการรับรอง API key (ใช้ hmac.compare_digest ป้องกัน timing attack)
    expected_key = os.getenv('FORM_WEBHOOK_KEY', '')
    if not expected_key:
        logging.error("FORM_WEBHOOK_KEY environment variable is not set")
        return jsonify({"success": False, "error": "Server configuration error"}), 500
    api_key = request.json.get('api_key', '')
    if not hmac.compare_digest(api_key, expected_key):
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    # รับข้อมูลจาก request
    code = request.json.get('code', '')
    full_form_data = request.json.get('full_form_data', {})

    if not code or not code.isdigit() or len(code) != 6:
        return jsonify({"success": False, "error": "Invalid verification code"}), 400

    try:
        # ตรวจสอบว่ารหัสมีอยู่แล้วหรือไม่
        check_query = 'SELECT code FROM registration_codes WHERE code = %s'
        result = db_manager.execute_query(check_query, (code,))

        if result and result[0]:
            return jsonify({"success": False, "error": "Code already exists"}), 409

        # เตรียมข้อมูลสำหรับบันทึก (โดยยังไม่มี AI summary)
        form_data_json = {
            "full_data": full_form_data,
            "ai_summary": "",  # จะถูกอัปเดทภายหลังโดย background thread
            "processed_at": datetime.now().isoformat(),
            "ai_processed_at": None
        }

        # บันทึกรหัสใหม่พร้อมข้อมูล form (โดยยังไม่มี AI summary)
        insert_query = '''
            INSERT INTO registration_codes
            (code, created_at, status, form_data)
            VALUES (%s, %s, %s, %s)
        '''
        db_manager.execute_and_commit(
            insert_query,
            (code, datetime.now(), 'pending', json.dumps(form_data_json))
        )

        logging.info(f"บันทึกรหัสยืนยันและข้อมูล form สำเร็จ: {code}")

        # เริ่ม background thread เพื่อสรุปข้อมูลด้วย AI
        if full_form_data:
            summary_thread = threading.Thread(
                target=process_ai_summary_async,
                args=(code, full_form_data),
                daemon=True
            )
            summary_thread.start()
            logging.info(f"เริ่ม background thread เพื่อสรุปข้อมูล form สำหรับรหัส: {code}")

        # ตอบกลับทันทีโดยไม่ต้องรอ AI summary
        return jsonify({
            "success": True,
            "message": "Verification code and form data saved successfully",
            "summary_processing": bool(full_form_data)
        }), 201

    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการบันทึกรหัสยืนยัน: {str(e)}")
        return jsonify({"success": False, "error": "Internal server error"}), 500

def check_redis_health():
    """ตรวจสอบการเชื่อมต่อ Redis"""
    try:
        return redis_client.ping()
    except Exception:
        return False

def check_mysql_health():
    """ตรวจสอบการเชื่อมต่อ MySQL"""
    try:
        return db_manager.check_connection()
    except Exception as e:
        logging.error(f"MySQL health check failed: {str(e)}")
        return False

def check_line_api_health():
    """ตรวจสอบการเชื่อมต่อ LINE API"""
    try:
        # ตรวจสอบแบบพื้นฐานว่า API พร้อมใช้งาน
        bot_info = line_bot_api.get_bot_info()
        return bool(bot_info.display_name)
    except Exception:
        return False

def check_grok_api_health():
    """ตรวจสอบการเชื่อมต่อ xAI Grok API"""
    try:
        _ = grok_client.send_chat(
            messages=[{"role": "user", "content": "ping"}],
            model=config.XAI_MODEL,
            max_tokens=1,
        )
        return True
    except Exception as e:
        logging.debug(f"xAI Grok API health check failed: {str(e)}")
        return False

def get_uptime():
    """ดึงเวลาการทำงานของแอปพลิเคชัน"""
    try:
        with open('/proc/uptime', 'r') as f:
            uptime_seconds = float(f.readline().split()[0])

        days, remainder = divmod(uptime_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)

        if days > 0:
            return f"{int(days)}d {int(hours)}h {int(minutes)}m"
        elif hours > 0:
            return f"{int(hours)}h {int(minutes)}m {int(seconds)}s"
        else:
            return f"{int(minutes)}m {int(seconds)}s"
    except Exception:
        return "unknown"

def get_memory_usage():
    """ดึงข้อมูลการใช้หน่วยความจำ"""
    try:
        # ใช้ /proc/self/status แทน psutil
        with open('/proc/self/status', 'r') as f:
            for line in f:
                if 'VmRSS:' in line:
                    # แปลงจาก kB เป็น MB
                    memory_kb = int(line.split()[1])
                    return f"{memory_kb / 1024:.2f} MB"
        return "unknown"
    except Exception:
        return "unknown"

# ตัวจัดการเหตุการณ์
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_message = event.message.text

    # ตรวจสอบว่าเป็นการยืนยันรหัสด้วย /verify หรือไม่
    if user_message.lower().startswith("/verify"):
        # ตรวจสอบว่าผู้ใช้ลงทะเบียนแล้วหรือไม่
        if is_user_registered(user_id):
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="✅ คุณได้ลงทะเบียนและยืนยันตัวตนเรียบร้อยแล้ว\n"
                                    "ไม่จำเป็นต้องยืนยันอีกครั้ง คุณสามารถใช้บริการของน้องใจดีได้ตามปกติ")
            )
            return

        # ดำเนินการต่อสำหรับผู้ที่ยังไม่ได้ลงทะเบียน
        try:
            # แยกรหัสยืนยันออกจากข้อความ
            parts = user_message.split()
            if len(parts) != 2:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text="รูปแบบไม่ถูกต้อง กรุณาพิมพ์ \"/verify\" ตามด้วยรหัส 6 หลัก เช่น \"/verify 123456\"")
                )
                return

            confirmation_code = parts[1].strip()
            _, message = register_user_with_code(user_id, confirmation_code)

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=message)
            )
            return
        except (IndexError, ValueError):
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="รูปแบบไม่ถูกต้อง กรุณาพิมพ์ \"/verify\" ตามด้วยรหัส 6 หลัก เช่น \"/verify 123456\"")
            )
            return

    # คำสั่งขอลิงก์ลงทะเบียนใหม่
    if user_message.lower() == "/register":
        send_registration_message(user_id)
        return

    # ตรวจสอบการลงทะเบียนก่อนประมวลผลข้อความปกติ
    if not is_user_registered(user_id):
        # ตรวจสอบว่าเคยส่งข้อความลงทะเบียนแล้วหรือไม่
        registration_sent = redis_client.exists(f"registration_sent:{user_id}")

        if not registration_sent:
            send_registration_message(user_id)
            # เก็บสถานะว่าส่งข้อความลงทะเบียนแล้ว (หมดอายุใน 1 วัน)
            redis_client.setex(f"registration_sent:{user_id}", 86400, "1")
        else:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="คุณยังไม่ได้ลงทะเบียน กรุณาลงทะเบียนก่อนใช้งาน พิมพ์ /register เพื่อดูวิธีลงทะเบียน")
            )
        return

    # ถ้าลงทะเบียนแล้ว ดำเนินการปกติ
    if is_user_locked(user_id):
        handle_locked_user(user_id)
        return

    # ล็อคผู้ใช้และประมวลผลข้อความ
    lock_user(user_id)
    try:
        process_user_message(user_id, user_message, event.reply_token)
    finally:
        unlock_user(user_id)

@handler.add(FollowEvent)
def handle_follow(event):
    user_id = event.source.user_id

    # ส่งข้อความต้อนรับและขอให้ลงทะเบียน
    welcome_message = (
        "ขอบคุณที่เพิ่มน้องใจดีเป็นเพื่อน! 👋\n\n"
        "น้องใจดีพร้อมเป็นเพื่อนคุยและช่วยเหลือคุณในเรื่องการเลิกสารเสพติด\n\n"
        "👉 ก่อนเริ่มต้นใช้งาน กรุณาลงทะเบียนตามขั้นตอนง่ายๆ"
    )

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=welcome_message)
    )

    # ส่งข้อความลงทะเบียนแบบ push message เพื่อให้แน่ใจว่าผู้ใช้ได้รับ
    send_registration_message(user_id)

# เริ่มต้นตัวกำหนดการ
scheduler = BackgroundScheduler()

def shutdown_scheduler(wait=True, reason="unknown"):
    """ปิดตัวกำหนดการของ APScheduler อย่างปลอดภัย"""
    if not scheduler.running:
        logging.debug(f"ข้ามการปิดตัวกำหนดการ ({reason}): ยังไม่ได้เริ่มทำงาน")
        return
    try:
        scheduler.shutdown(wait=wait)
        logging.info(f"ปิดตัวกำหนดการเรียบร้อย ({reason})")
    except SchedulerNotRunningError:
        logging.debug(f"ตัวกำหนดการถูกปิดแล้ว ({reason})")
    except Exception as exc:
        logging.error(f"เกิดข้อผิดพลาดในการปิดตัวกำหนดการ ({reason}): {exc}")

# เพิ่มงานตัวกำหนดการ
_scheduler_initialized = False

def init_scheduler():
    global _scheduler_initialized
    if _scheduler_initialized:
        logging.debug("Scheduler already initialized, skipping")
        return
    _scheduler_initialized = True
    scheduler.add_job(check_and_send_follow_ups, 'interval', minutes=30)
    scheduler.start()
    logging.info("ตัวกำหนดการเริ่มต้นแล้ว ตรวจสอบการติดตามทุก 30 นาที")

    # การจัดการการปิดอย่างถูกต้อง
    atexit.register(lambda: shutdown_scheduler(reason='atexit'))

# ตัวจัดการการปิดอย่างสง่างาม
def handle_shutdown(sig=None, frame=None):
    logging.info("กำลังปิดแอปพลิเคชัน...")

    # ปิดตัวกำหนดการ
    try:
        scheduler.shutdown()
        logging.info("ปิดตัวกำหนดการเรียบร้อย")
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการปิดตัวกำหนดการ: {str(e)}")

    # ปิดการเชื่อมต่อ Redis
    try:
        redis_client.close()
        logging.info("ปิดการเชื่อมต่อ Redis เรียบร้อย")
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการปิดการเชื่อมต่อ Redis: {str(e)}")

    # ปิดการเชื่อมต่อ xAI Grok API (ไม่มีการเชื่อมต่อถาวรในปัจจุบัน)
    try:
        logging.info("ปิดการเชื่อมต่อ xAI Grok API เรียบร้อย")
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการปิดการเชื่อมต่อ xAI Grok API: {str(e)}")

    logging.info("ปิดแอปพลิเคชันเรียบร้อย")
    exit(0)

signal.signal(signal.SIGTERM, handle_shutdown)
signal.signal(signal.SIGINT, handle_shutdown)

if __name__ == "__main__":
    # เริ่มต้นตัวกำหนดการก่อนเริ่มเซิร์ฟเวอร์
    init_scheduler()
    # เริ่มเซิร์ฟเวอร์
    serve(app, host='0.0.0.0', port=5000)
