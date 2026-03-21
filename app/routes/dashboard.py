"""Dashboard routes -- extracted from app_main.py as a Flask Blueprint."""
import csv
import hashlib
import io
import os
import json
import hmac
import logging
from datetime import datetime, timedelta
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Blueprint, request, jsonify, render_template, Response
from flask_cors import CORS

from ..risk_assessment import normalize_risk_level, RISK_KEYWORDS

dashboard_bp = Blueprint('dashboard', __name__)

# CORS for dashboard API endpoints only (not the whole app)
_dashboard_cors_origins = [o.strip() for o in os.getenv('DASHBOARD_CORS_ORIGINS', '').split(',') if o.strip()]
if _dashboard_cors_origins:
    CORS(
        dashboard_bp,
        resources={
            r"/api/dashboard/*": {"origins": _dashboard_cors_origins, "methods": ["GET", "OPTIONS"]},
            r"/api/knowledge/*": {"origins": _dashboard_cors_origins, "methods": ["GET", "POST", "OPTIONS"]},
        },
    )


@dashboard_bp.after_request
def add_security_headers(response):
    """Add security headers to all dashboard responses."""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Cache-Control'] = 'no-store'
    return response

# Module-level references set by init_dashboard()
_db = None          # ChatHistoryDB
_db_manager = None  # DatabaseManager
_redis_client = None
_DASHBOARD_API_KEY: str = ''
_HIGH_RISK_KEYWORDS = set()
_MEDIUM_RISK_KEYWORDS = set()
_GENERAL_RISK_LEVEL = 'general'
_knowledge_base = None


def init_dashboard(db, db_manager, redis_client, general_risk_level='general', knowledge_base=None):
    """Wire runtime dependencies into the dashboard module."""
    global _db, _db_manager, _redis_client, _DASHBOARD_API_KEY, _knowledge_base
    global _HIGH_RISK_KEYWORDS, _MEDIUM_RISK_KEYWORDS, _GENERAL_RISK_LEVEL
    _db = db
    _db_manager = db_manager
    _redis_client = redis_client
    _knowledge_base = knowledge_base
    _DASHBOARD_API_KEY = os.getenv('DASHBOARD_API_KEY', '')
    _HIGH_RISK_KEYWORDS = {kw.lower() for kw in RISK_KEYWORDS.get('high_risk', [])}
    _MEDIUM_RISK_KEYWORDS = {kw.lower() for kw in RISK_KEYWORDS.get('medium_risk', [])}
    _GENERAL_RISK_LEVEL = general_risk_level


def _sanitize_filename(filename: str) -> str:
    cleaned = os.path.basename(filename or '').strip()
    if not cleaned:
        return ''
    return ''.join(ch for ch in cleaned if ch.isalnum() or ch in {'.', '_', '-'})


def _ensure_knowledge_base():
    if _knowledge_base is None:
        return False, (jsonify({'error': 'rag_unavailable', 'message': 'RAG knowledge base is disabled or unavailable'}), 503)
    return True, None


def _require_dashboard_auth():
    """Validate dashboard API key from Authorization header or query param."""
    if not _DASHBOARD_API_KEY:
        return True, None
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        token = auth_header[7:]
        if hmac.compare_digest(token, _DASHBOARD_API_KEY):
            return True, None
    return False, (jsonify({"error": "Unauthorized"}), 401)


def _clamp_lookback_days(value: int) -> int:
    return max(1, min(int(value or 0), 180))


def _lookback_window_start(lookback_days: int, now: Optional[datetime] = None) -> datetime:
    reference = now or datetime.now()
    days = _clamp_lookback_days(lookback_days)
    start_day = (reference - timedelta(days=days - 1)).date()
    return datetime.combine(start_day, datetime.min.time())


def _coerce_requested_datetime(
    value: Optional[str],
    *,
    default: datetime,
    end_of_day: bool = False,
) -> datetime:
    if not value:
        return default

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        if value.endswith('Z'):
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        else:
            raise

    if 'T' not in value and ' ' not in value:
        parsed = datetime.combine(parsed.date(), datetime.max.time() if end_of_day else datetime.min.time())

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)

    return parsed


def _parse_progress_timestamp(value: Any) -> Optional[datetime]:
    if value is None:
        return None

    parsed: Optional[datetime] = None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            if value.endswith('Z'):
                try:
                    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
                except ValueError:
                    return None
            else:
                return None
    else:
        return None

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)

    return parsed

def _classify_keyword_risk(keyword: str) -> str:
    key_lower = keyword.lower()
    if key_lower in _HIGH_RISK_KEYWORDS:
        return 'high'
    if key_lower in _MEDIUM_RISK_KEYWORDS:
        return 'medium'
    return 'contextual'


def _collect_dashboard_progress_metrics(
    cutoff: Optional[datetime] = None,
    lookback_days: int = 30,
    per_user_limit: int = 5,
    keyword_limit: int = 10,
) -> Dict[str, Any]:
    keyword_counter: Counter = Counter()
    display_lookup: Dict[str, str] = {}
    risk_counter: Counter = Counter()
    user_progress: Dict[str, List[Dict[str, Any]]] = {}
    daily_risk: Dict[str, Counter] = {}
    if _redis_client is None:
        return {
            'top_keywords': [],
            'risk_summary': {'high': 0, 'medium': 0, 'general': 0, 'unknown': 0},
            'user_progress': {},
            'risk_trend': [],
        }

    effective_cutoff = cutoff or _lookback_window_start(lookback_days)
    limit_per_user = max(1, per_user_limit)
    try:
        for key in _redis_client.scan_iter('progress:*'):
            if isinstance(key, bytes):
                key = key.decode('utf-8', errors='ignore')
            user_id = key.split(':', 1)[1] if ':' in key else key
            entries = _redis_client.lrange(key, 0, -1)
            recent_events: List[Dict[str, Any]] = []
            for raw in entries:
                try:
                    entry = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    continue

                timestamp = _parse_progress_timestamp(entry.get('timestamp'))
                if timestamp is None or timestamp < effective_cutoff:
                    continue

                risk_level = normalize_risk_level(entry.get('risk_level'))
                keywords = entry.get('keywords') or []
                risk_counter[risk_level] += 1

                day_key = timestamp.date().isoformat()
                if day_key not in daily_risk:
                    daily_risk[day_key] = Counter()
                daily_risk[day_key][risk_level] += 1

                if risk_level in ('high', 'medium') and len(recent_events) < limit_per_user:
                    recent_events.append({
                        'timestamp': timestamp.isoformat(),
                        'risk_level': risk_level,
                        'keywords': keywords,
                    })

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
        'general': int(risk_counter.get(_GENERAL_RISK_LEVEL, 0)),
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
            'general': int(counts.get(_GENERAL_RISK_LEVEL, 0)),
        })

    return {
        'top_keywords': top_keywords,
        'risk_summary': risk_summary,
        'user_progress': user_progress,
        'risk_trend': risk_trend,
    }

@dashboard_bp.route('/dashboard', methods=['GET'])
def dashboard_page():
    return render_template('dashboard.html', dashboard_auth_required=bool(_DASHBOARD_API_KEY))


@dashboard_bp.route('/api/dashboard/insights', methods=['GET'])
def get_dashboard_insights():
    """Summarise conversation and risk insights for care teams."""
    auth_ok, auth_error = _require_dashboard_auth()
    if not auth_ok:
        return auth_error
    try:
        user_limit = request.args.get('limit', default=10, type=int) or 10
        lookback_days = _clamp_lookback_days(request.args.get('lookback_days', default=30, type=int) or 30)
        keyword_limit = request.args.get('keyword_limit', default=10, type=int) or 10

        user_limit = max(1, min(user_limit, 100))
        keyword_limit = max(1, min(keyword_limit, 50))
        generated_at = datetime.now()
        cutoff = _lookback_window_start(lookback_days, now=generated_at)

        progress_metrics = _collect_dashboard_progress_metrics(
            cutoff=cutoff,
            per_user_limit=5,
            keyword_limit=keyword_limit,
        )

        overview_raw = _db.get_dashboard_overview(cutoff=cutoff) or {}
        overview = {
            'total_conversations': int(overview_raw.get('total_conversations', 0) or 0),
            'unique_users': int(overview_raw.get('unique_users', 0) or 0),
            'important_messages': int(overview_raw.get('important_messages', 0) or 0),
        }

        try:
            followup_result = _db_manager.execute_query(
                'SELECT COUNT(*) FROM follow_ups WHERE status != %s',
                ('completed',),
            )
            if followup_result:
                active_row = followup_result[0]
                active_followups = int(active_row[0] if not isinstance(active_row, dict) else next(iter(active_row.values())))
            else:
                active_followups = 0
        except Exception as exc:
            logging.warning('Could not fetch follow-up metrics: %s', exc)
            active_followups = 0
        overview['active_follow_ups'] = active_followups

        user_summaries = _db.get_recent_user_summaries(limit=None, cutoff=cutoff) or []
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

        displayed_users = formatted_users[:user_limit]
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

        daily_totals = _db.get_recent_daily_message_totals(days=lookback_days, cutoff=cutoff) or []
        retention_buckets = _db.get_retention_buckets(cutoff=cutoff) or []
        conversation_depth = _db.get_conversation_depth_trend(days=lookback_days, cutoff=cutoff) or []

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
            'generated_at': generated_at.isoformat(),
            'window': {
                'start': cutoff.isoformat(),
                'end': generated_at.isoformat(),
            },
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
            'users': displayed_users,
            'users_meta': {
                'displayed': len(displayed_users),
                'total': total_users,
            },
        }
        return jsonify(response_payload)

    except Exception as exc:
        logging.error('Error generating dashboard insights: %s', exc, exc_info=True)
        return jsonify({
            'error': 'dashboard_generation_failed',
            'message': 'Dashboard insights are unavailable at the moment.',
        }), 500

@dashboard_bp.route('/api/dashboard/users/<user_id>/history', methods=['GET'])
def get_dashboard_user_history(user_id: str):
    """Return conversation transcript and risk highlights for a dashboard drill-down."""
    auth_ok, auth_error = _require_dashboard_auth()
    if not auth_ok:
        return auth_error
    if not user_id:
        return jsonify({'error': 'missing_user_id'}), 400

    limit = request.args.get('limit', default=50, type=int) or 50
    limit = max(10, min(limit, 200))
    lookback_days = request.args.get('lookback_days', type=int)
    if lookback_days:
        lookback_days = _clamp_lookback_days(lookback_days)
        cutoff = _lookback_window_start(lookback_days)
    else:
        cutoff = None

    try:
        history = _db.get_user_conversation_feed(user_id, limit=limit, cutoff=cutoff) or []
        summary = _db.get_user_snapshot(user_id, cutoff=cutoff) or {
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
        if _redis_client is not None:
            raw_events = _redis_client.lrange(f"progress:{user_id}", 0, -1)
            for raw in raw_events:
                try:
                    event = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    continue

                timestamp = _parse_progress_timestamp(event.get('timestamp'))
                if cutoff is not None and (timestamp is None or timestamp < cutoff):
                    continue

                normalized_level = normalize_risk_level(event.get('risk_level'))
                risk_events.append({
                    'timestamp': timestamp.isoformat() if timestamp else event.get('timestamp'),
                    'risk_level': normalized_level,
                    'keywords': event.get('keywords') or [],
                })
                if len(risk_events) >= limit:
                    break

        generated_at = datetime.now()
        response_payload = {
            'generated_at': generated_at.isoformat(),
            'user_id': user_id,
            'summary': summary,
            'history': history,
            'risk_events': risk_events,
            'limit': limit,
            'parameters': {
                'lookback_days': lookback_days,
            },
        }
        if cutoff is not None:
            response_payload['window'] = {'start': cutoff.isoformat(), 'end': generated_at.isoformat()}
        return jsonify(response_payload)
    except Exception as exc:
        logging.error('Error retrieving dashboard user history for %s: %s', user_id, exc, exc_info=True)
        return jsonify({
            'error': 'user_history_unavailable',
            'message': 'à¹„à¸¡à¹ˆà¸ªà¸²à¸¡à¸²à¸£à¸–à¸"à¸¶à¸‡à¸›à¸£à¸°à¸§à¸±à¸•à¸´à¸à¸²à¸£à¸ªà¸™à¸—à¸™à¸²à¹„à¸"à¹‰à¹ƒà¸™à¸‚à¸"à¸°à¸™à¸µà¹‰',
        }), 500

@dashboard_bp.route('/api/dashboard/multi-ai-stats', methods=['GET'])
def get_multi_ai_stats():
    """Return aggregated Multi-AI Consensus statistics for dashboard visualization."""
    auth_ok, auth_error = _require_dashboard_auth()
    if not auth_ok:
        return auth_error

    lookback_days = _clamp_lookback_days(request.args.get('lookback_days', default=30, type=int) or 30)
    generated_at = datetime.now()
    cutoff = _lookback_window_start(lookback_days, now=generated_at)

    try:
        # --- Summary ---
        summary_row = _db_manager.execute_query(
            '''SELECT COUNT(*) AS cnt,
                      AVG(total_time_ms) AS avg_time,
                      AVG(best_score) AS avg_score
               FROM multi_ai_logs WHERE timestamp >= %s''',
            (cutoff,),
        )
        total_evals = int(summary_row[0][0]) if summary_row and summary_row[0][0] else 0
        avg_total_time = float(summary_row[0][1]) if summary_row and summary_row[0][1] else 0.0
        avg_best_score = float(summary_row[0][2]) if summary_row and summary_row[0][2] else 0.0

        if total_evals == 0:
            return jsonify({
                'generated_at': generated_at.isoformat(),
                'window': {'start': cutoff.isoformat(), 'end': generated_at.isoformat()},
                'lookback_days': lookback_days,
                'summary': {'total_evaluations': 0, 'avg_total_time_ms': 0, 'avg_best_score': 0},
                'provider_wins': [],
                'provider_avg_scores': [],
                'provider_tokens': [],
                'provider_response_times': [],
                'daily_trend': [],
                'provider_avg_times': [],
                'processing_times': [],
            })

        # --- Provider wins (best_provider frequency) ---
        wins_rows = _db_manager.execute_query(
            '''SELECT best_provider, COUNT(*) AS wins
               FROM multi_ai_logs WHERE timestamp >= %s
               GROUP BY best_provider ORDER BY wins DESC''',
            (cutoff,),
        ) or []
        provider_wins = []
        for row in wins_rows:
            provider_wins.append({
                'provider': row[0],
                'wins': int(row[1]),
                'pct': round(int(row[1]) / total_evals, 3) if total_evals else 0,
            })

        # --- Provider avg scores (from all_scores JSON) ---
        all_logs = _db_manager.execute_query(
            '''SELECT all_scores, token_usage, generation_time_ms, evaluation_time_ms, provider_times
               FROM multi_ai_logs WHERE timestamp >= %s''',
            (cutoff,),
        ) or []

        score_accum: Dict[str, List[float]] = {}
        token_accum: Dict[str, Dict[str, int]] = {}
        gen_time_accum: Dict[str, List[float]] = {}
        eval_time_accum: Dict[str, List[float]] = {}

        for row in all_logs:
            # Parse all_scores
            try:
                scores = json.loads(row[0]) if isinstance(row[0], str) else (row[0] or {})
            except (json.JSONDecodeError, TypeError):
                scores = {}
            for provider, score in scores.items():
                score_accum.setdefault(provider, []).append(float(score))

            # Parse token_usage
            try:
                tokens = json.loads(row[1]) if isinstance(row[1], str) else (row[1] or {})
            except (json.JSONDecodeError, TypeError):
                tokens = {}
            for provider, usage in tokens.items():
                if provider not in token_accum:
                    token_accum[provider] = {'prompt': 0, 'completion': 0, 'total': 0}
                if isinstance(usage, dict):
                    token_accum[provider]['prompt'] += int(usage.get('prompt', 0))
                    token_accum[provider]['completion'] += int(usage.get('completion', 0))
                    token_accum[provider]['total'] += int(usage.get('total', 0))

            # Parse provider_times
            try:
                ptimes = json.loads(row[4]) if isinstance(row[4], str) else (row[4] or {})
            except (json.JSONDecodeError, TypeError):
                ptimes = {}
            for provider, t in ptimes.items():
                if isinstance(t, dict):
                    gen_ms = float(t.get('gen_ms', 0))
                    eval_ms = float(t.get('eval_ms', 0))
                    if gen_ms > 0:
                        gen_time_accum.setdefault(provider, []).append(gen_ms)
                    if eval_ms > 0:
                        eval_time_accum.setdefault(provider, []).append(eval_ms)

        provider_avg_scores = []
        for provider, scores_list in sorted(score_accum.items(), key=lambda x: -(sum(x[1]) / len(x[1]))):
            provider_avg_scores.append({
                'provider': provider,
                'avg_score': round(sum(scores_list) / len(scores_list), 1),
                'evaluations': len(scores_list),
            })

        provider_tokens = []
        for provider, usage in sorted(token_accum.items(), key=lambda x: -x[1]['total']):
            provider_tokens.append({
                'provider': provider,
                'total_prompt': usage['prompt'],
                'total_completion': usage['completion'],
                'total_tokens': usage['total'],
            })

        # --- Per-model average gen/eval times ---
        all_providers_set = set(gen_time_accum.keys()) | set(eval_time_accum.keys())
        provider_avg_times = []
        for provider in sorted(all_providers_set):
            gen_list = gen_time_accum.get(provider, [])
            eval_list = eval_time_accum.get(provider, [])
            provider_avg_times.append({
                'provider': provider,
                'avg_gen_ms': round(sum(gen_list) / len(gen_list), 0) if gen_list else 0,
                'avg_eval_ms': round(sum(eval_list) / len(eval_list), 0) if eval_list else 0,
                'gen_count': len(gen_list),
                'eval_count': len(eval_list),
            })
        provider_avg_times.sort(key=lambda x: -(x['avg_gen_ms'] + x['avg_eval_ms']))

        # --- Phase-level aggregate response times (kept for stat cards) ---
        avg_gen_time = 0.0
        avg_eval_time = 0.0
        gen_times = [float(r[2]) for r in all_logs if r[2] is not None]
        eval_times = [float(r[3]) for r in all_logs if r[3] is not None]
        if gen_times:
            avg_gen_time = sum(gen_times) / len(gen_times)
        if eval_times:
            avg_eval_time = sum(eval_times) / len(eval_times)

        provider_response_times = [{
            'phase': 'generation',
            'avg_ms': round(avg_gen_time, 0),
            'count': len(gen_times),
        }, {
            'phase': 'evaluation',
            'avg_ms': round(avg_eval_time, 0),
            'count': len(eval_times),
        }]

        # --- Daily trend ---
        trend_rows = _db_manager.execute_query(
            '''SELECT DATE(timestamp) AS d, COUNT(*) AS cnt,
                      AVG(best_score) AS avg_sc, best_provider
               FROM multi_ai_logs WHERE timestamp >= %s
               GROUP BY d, best_provider ORDER BY d ASC''',
            (cutoff,),
        ) or []

        daily_map: Dict[str, Dict[str, Any]] = {}
        for row in trend_rows:
            day = row[0].isoformat() if hasattr(row[0], 'isoformat') else str(row[0])
            if day not in daily_map:
                daily_map[day] = {'date': day, 'evaluations': 0, 'avg_score': 0.0, 'score_sum': 0.0, 'best_provider_counts': {}}
            daily_map[day]['evaluations'] += int(row[1])
            daily_map[day]['score_sum'] += float(row[2] or 0) * int(row[1])
            daily_map[day]['best_provider_counts'][row[3]] = int(row[1])

        daily_trend = []
        for day_data in sorted(daily_map.values(), key=lambda x: x['date']):
            total = day_data['evaluations']
            daily_trend.append({
                'date': day_data['date'],
                'evaluations': total,
                'avg_score': round(day_data['score_sum'] / total, 1) if total else 0,
                'best_provider_counts': day_data['best_provider_counts'],
            })

        # --- Per-request processing times (recent, for timeline chart) ---
        time_rows = _db_manager.execute_query(
            '''SELECT timestamp, generation_time_ms, evaluation_time_ms,
                      total_time_ms, best_provider
               FROM multi_ai_logs WHERE timestamp >= %s
               ORDER BY timestamp ASC LIMIT 200''',
            (cutoff,),
        ) or []
        processing_times = []
        for row in time_rows:
            ts = row[0].isoformat() if hasattr(row[0], 'isoformat') else str(row[0])
            processing_times.append({
                'timestamp': ts,
                'gen_ms': round(float(row[1]), 0) if row[1] is not None else 0,
                'eval_ms': round(float(row[2]), 0) if row[2] is not None else 0,
                'total_ms': round(float(row[3]), 0) if row[3] is not None else 0,
                'best_provider': row[4],
            })

        return jsonify({
            'generated_at': generated_at.isoformat(),
            'window': {'start': cutoff.isoformat(), 'end': generated_at.isoformat()},
            'lookback_days': lookback_days,
            'summary': {
                'total_evaluations': total_evals,
                'avg_total_time_ms': round(avg_total_time, 0),
                'avg_best_score': round(avg_best_score, 1),
            },
            'provider_wins': provider_wins,
            'provider_avg_scores': provider_avg_scores,
            'provider_tokens': provider_tokens,
            'provider_response_times': provider_response_times,
            'provider_avg_times': provider_avg_times,
            'daily_trend': daily_trend,
            'processing_times': processing_times,
        })

    except Exception as exc:
        logging.error('Error generating multi-AI stats: %s', exc, exc_info=True)
        return jsonify({
            'error': 'multi_ai_stats_failed',
            'message': 'Multi-AI statistics are unavailable at the moment.',
        }), 500


@dashboard_bp.route('/api/knowledge/stats', methods=['GET'])
def get_knowledge_stats():
    auth_ok, auth_error = _require_dashboard_auth()
    if not auth_ok:
        return auth_error

    kb_ok, kb_error = _ensure_knowledge_base()
    if not kb_ok:
        return kb_error

    try:
        return jsonify(_knowledge_base.get_stats())
    except Exception as exc:
        logging.error('Error fetching knowledge stats: %s', exc, exc_info=True)
        return jsonify({'error': 'knowledge_stats_failed'}), 500


@dashboard_bp.route('/api/knowledge/reindex', methods=['POST'])
def reindex_knowledge_docs():
    auth_ok, auth_error = _require_dashboard_auth()
    if not auth_ok:
        return auth_error

    kb_ok, kb_error = _ensure_knowledge_base()
    if not kb_ok:
        return kb_error

    try:
        result = _knowledge_base.ingest_directory(force_reindex=True)
        return jsonify(result)
    except Exception as exc:
        logging.error('Knowledge reindex failed: %s', exc, exc_info=True)
        return jsonify({'error': 'knowledge_reindex_failed'}), 500


@dashboard_bp.route('/api/knowledge/upload', methods=['POST'])
def upload_knowledge_file():
    auth_ok, auth_error = _require_dashboard_auth()
    if not auth_ok:
        return auth_error

    kb_ok, kb_error = _ensure_knowledge_base()
    if not kb_ok:
        return kb_error

    if 'file' not in request.files:
        return jsonify({'error': 'missing_file'}), 400

    uploaded_file = request.files['file']
    safe_name = _sanitize_filename(uploaded_file.filename)
    if not safe_name:
        return jsonify({'error': 'invalid_filename'}), 400

    suffix = Path(safe_name).suffix.lower()
    if suffix not in {'.pdf', '.docx', '.txt', '.md'}:
        return jsonify({'error': 'unsupported_file_type'}), 400

    docs_dir = Path(getattr(_knowledge_base, 'docs_dir', 'knowledge_docs'))
    docs_dir.mkdir(parents=True, exist_ok=True)
    dest_path = docs_dir / safe_name

    try:
        uploaded_file.save(str(dest_path))
        result = _knowledge_base.ingest_file(str(dest_path), force_reindex=True)
        return jsonify(result)
    except Exception as exc:
        logging.error('Knowledge upload failed for %s: %s', safe_name, exc, exc_info=True)
        return jsonify({'error': 'knowledge_upload_failed'}), 500


def _anonymize_user_id(user_id: str, salt: str = '') -> str:
    """Hash a user ID for anonymized research export."""
    return hashlib.sha256(f"{salt}{user_id}".encode()).hexdigest()[:12]


@dashboard_bp.route('/api/dashboard/export', methods=['GET'])
def export_conversations():
    """Export anonymized conversation data for research.

    Query params:
        format: 'csv' or 'json' (default: json)
        start_date: ISO date string (default: 30 days ago)
        end_date: ISO date string (default: today)
        limit: max rows (default: 5000, max: 50000)
        anonymize: 'true'/'false' (default: true)
    """
    auth_ok, auth_error = _require_dashboard_auth()
    if not auth_ok:
        return auth_error

    export_format = request.args.get('format', 'json').lower()
    if export_format not in ('csv', 'json'):
        return jsonify({'error': 'invalid_format', 'message': "format must be 'csv' or 'json'"}), 400

    now = datetime.now()
    end_date = request.args.get('end_date')
    start_date = request.args.get('start_date')
    try:
        end_dt = _coerce_requested_datetime(end_date, default=now, end_of_day=True)
        start_dt = _coerce_requested_datetime(
            start_date,
            default=_lookback_window_start(30, now=end_dt),
            end_of_day=False,
        )
    except ValueError:
        return jsonify({'error': 'invalid_date', 'message': 'Dates must be ISO format (YYYY-MM-DD)'}), 400

    if start_dt > end_dt:
        return jsonify({'error': 'invalid_date_range', 'message': 'start_date must be before end_date'}), 400

    limit = request.args.get('limit', default=5000, type=int)
    limit = max(1, min(limit, 50000))
    anonymize = request.args.get('anonymize', 'true').lower() != 'false'
    salt = os.getenv('EXPORT_ANONYMIZE_SALT', 'jaidee-research-2026')

    try:
        query = '''
            SELECT c.user_id, c.user_message, c.bot_response,
                   c.timestamp, c.token_count, c.important_flag
            FROM conversations c
            WHERE c.timestamp BETWEEN %s AND %s
            ORDER BY c.timestamp ASC
            LIMIT %s
        '''
        rows = _db_manager.execute_query(query, (start_dt, end_dt, limit)) or []

        records: List[Dict[str, Any]] = []
        for row in rows:
            if isinstance(row, dict):
                user_id_raw = row.get('user_id') or ''
                user_message = row.get('user_message') or ''
                bot_response = row.get('bot_response') or ''
                timestamp_value = row.get('timestamp')
                token_count = row.get('token_count') or 0
                important_flag = row.get('important_flag')
            else:
                user_id_raw = row[0] if row[0] else ''
                user_message = row[1] or ''
                bot_response = row[2] or ''
                timestamp_value = row[3]
                token_count = row[4] or 0
                important_flag = row[5]

            record = {
                'participant_id': _anonymize_user_id(user_id_raw, salt) if anonymize else user_id_raw,
                'user_message': user_message,
                'bot_response': bot_response,
                'timestamp': timestamp_value.isoformat() if hasattr(timestamp_value, 'isoformat') else str(timestamp_value) if timestamp_value else '',
                'token_count': int(token_count) if token_count else 0,
                'is_important': bool(important_flag) if important_flag is not None else False,
            }
            records.append(record)

        filename = f"jaidee_export_{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}.{export_format}"
        if export_format == 'csv':
            output = io.StringIO()
            if records:
                writer = csv.DictWriter(output, fieldnames=records[0].keys())
                writer.writeheader()
                writer.writerows(records)
            csv_data = '\ufeff' + output.getvalue()
            return Response(
                csv_data,
                mimetype='text/csv',
                headers={'Content-Disposition': f'attachment; filename="{filename}"'},
            )

        payload = {
            'exported_at': now.isoformat(),
            'parameters': {
                'start_date': start_dt.isoformat(),
                'end_date': end_dt.isoformat(),
                'limit': limit,
                'anonymized': anonymize,
                'format': export_format,
            },
            'total_records': len(records),
            'data': records,
        }
        return Response(
            json.dumps(payload, ensure_ascii=False, indent=2),
            mimetype='application/json',
            headers={'Content-Disposition': f'attachment; filename="{filename}"'},
        )

    except Exception as exc:
        logging.error('Error exporting conversations: %s', exc, exc_info=True)
        return jsonify({
            'error': 'export_failed',
            'message': 'à¹„à¸¡à¹ˆà¸ªà¸²à¸¡à¸²à¸£à¸–à¸ªà¹ˆà¸‡à¸­à¸­à¸à¸‚à¹‰à¸­à¸¡à¸¹à¸¥à¹„à¸"à¹‰à¹ƒà¸™à¸‚à¸"à¸°à¸™à¸µà¹‰',
        }), 500

