"""Dashboard route regression tests for range integrity, auth, and export."""
import csv
import io
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from flask import Flask

from app.routes import dashboard as dashboard_module


class FakeRedis:
    def __init__(self, events_by_user):
        self.events_by_user = events_by_user

    def scan_iter(self, pattern):
        for user_id in self.events_by_user:
            yield f"progress:{user_id}"

    def lrange(self, key, start, end):
        user_id = key.split(':', 1)[1] if ':' in key else key
        return [json.dumps(event) for event in self.events_by_user.get(user_id, [])]


class FakeDashboardDB:
    def __init__(self, conversations):
        self.conversations = sorted(conversations, key=lambda row: row['timestamp'])

    def _rows(self, cutoff=None, user_id=None):
        rows = self.conversations
        if user_id is not None:
            rows = [row for row in rows if row['user_id'] == user_id]
        if cutoff is not None:
            rows = [row for row in rows if row['timestamp'] >= cutoff]
        return rows

    def get_dashboard_overview(self, cutoff=None):
        rows = self._rows(cutoff=cutoff)
        return {
            'total_conversations': len(rows),
            'unique_users': len({row['user_id'] for row in rows}),
            'important_messages': sum(1 for row in rows if row['important_flag']),
        }

    def get_recent_user_summaries(self, limit=20, cutoff=None):
        grouped = defaultdict(list)
        for row in self._rows(cutoff=cutoff):
            grouped[row['user_id']].append(row)

        summaries = []
        for user_id, rows in grouped.items():
            rows = sorted(rows, key=lambda row: row['timestamp'])
            total_messages = len(rows)
            important_messages = sum(1 for row in rows if row['important_flag'])
            summaries.append({
                'user_id': user_id,
                'display_name': user_id.upper(),
                'total_messages': total_messages,
                'important_messages': important_messages,
                'last_interaction': rows[-1]['timestamp'],
                'total_tokens': sum(int(row['token_count'] or 0) for row in rows),
            })

        summaries.sort(key=lambda row: row['last_interaction'], reverse=True)
        if limit is not None:
            summaries = summaries[:limit]
        return summaries

    def get_recent_daily_message_totals(self, days=14, cutoff=None):
        grouped = defaultdict(lambda: {'total_messages': 0, 'important_messages': 0, 'total_tokens': 0})
        for row in self._rows(cutoff=cutoff):
            key = row['timestamp'].date().isoformat()
            grouped[key]['total_messages'] += 1
            grouped[key]['important_messages'] += int(bool(row['important_flag']))
            grouped[key]['total_tokens'] += int(row['token_count'] or 0)

        return [
            {
                'date': day,
                'total_messages': values['total_messages'],
                'important_messages': values['important_messages'],
                'total_tokens': values['total_tokens'],
            }
            for day, values in sorted(grouped.items())
        ]

    def get_retention_buckets(self, cutoff=None):
        grouped = defaultdict(int)
        for row in self._rows(cutoff=cutoff):
            grouped[row['user_id']] += 1

        counts = defaultdict(int)
        for total_messages in grouped.values():
            if total_messages == 1:
                counts['1'] += 1
            elif total_messages <= 5:
                counts['2-5'] += 1
            elif total_messages <= 15:
                counts['6-15'] += 1
            elif total_messages <= 30:
                counts['16-30'] += 1
            else:
                counts['31+'] += 1

        order = ['1', '2-5', '6-15', '16-30', '31+']
        return [
            {'bucket': bucket, 'user_count': counts[bucket]}
            for bucket in order
            if counts[bucket]
        ]

    def get_conversation_depth_trend(self, days=30, cutoff=None):
        grouped = defaultdict(list)
        for row in self._rows(cutoff=cutoff):
            grouped[row['timestamp'].date().isoformat()].append(row)

        trend = []
        for day, rows in sorted(grouped.items()):
            active_users = len({row['user_id'] for row in rows})
            total_messages = len(rows)
            total_tokens = sum(int(row['token_count'] or 0) for row in rows)
            trend.append({
                'date': day,
                'active_users': active_users,
                'total_messages': total_messages,
                'avg_messages_per_user': round(total_messages / active_users, 1) if active_users else 0,
                'avg_tokens_per_message': round(total_tokens / total_messages, 1) if total_messages else 0,
            })
        return trend

    def get_user_conversation_feed(self, user_id, limit=50, cutoff=None):
        rows = sorted(self._rows(cutoff=cutoff, user_id=user_id), key=lambda row: row['timestamp'], reverse=True)
        return [
            {
                'id': row['id'],
                'timestamp': row['timestamp'].isoformat(),
                'user_message': row['user_message'],
                'bot_response': row['bot_response'],
                'important': bool(row['important_flag']),
                'token_count': int(row['token_count'] or 0),
            }
            for row in rows[:limit]
        ]

    def get_user_snapshot(self, user_id, cutoff=None):
        rows = self._rows(cutoff=cutoff, user_id=user_id)
        if not rows:
            return None

        rows = sorted(rows, key=lambda row: row['timestamp'])
        total_messages = len(rows)
        important_messages = sum(1 for row in rows if row['important_flag'])
        return {
            'user_id': user_id,
            'display_name': user_id.upper(),
            'total_messages': total_messages,
            'important_messages': important_messages,
            'total_tokens': sum(int(row['token_count'] or 0) for row in rows),
            'important_ratio': round(important_messages / total_messages, 3) if total_messages else 0.0,
            'last_interaction': rows[-1]['timestamp'].isoformat(),
            'first_interaction': rows[0]['timestamp'].isoformat(),
        }


class FakeDashboardManager:
    def __init__(self, conversations, follow_up_count=2):
        self.conversations = sorted(conversations, key=lambda row: row['timestamp'])
        self.follow_up_count = follow_up_count
        self.queries = []

    def execute_query(self, query, params=(), dictionary=False):
        self.queries.append((query, params, dictionary))

        if 'FROM follow_ups' in query:
            return [(self.follow_up_count,)]

        if 'FROM multi_ai_logs' in query and 'COUNT(*) AS cnt' in query:
            return [(0, None, None)]

        if 'FROM conversations c' in query:
            assert 'important_flag' in query
            assert 'is_important' not in query
            start_dt, end_dt, limit = params
            rows = [
                row for row in self.conversations
                if start_dt <= row['timestamp'] <= end_dt
            ]
            rows.sort(key=lambda row: row['timestamp'])
            return [
                (
                    row['user_id'],
                    row['user_message'],
                    row['bot_response'],
                    row['timestamp'],
                    row['token_count'],
                    row['important_flag'],
                )
                for row in rows[:limit]
            ]

        return []


def _build_conversations(now):
    conversations = []
    next_id = 1

    def add(user_id, days_ago, important, token_count, label):
        nonlocal next_id
        timestamp = (now - timedelta(days=days_ago, minutes=next_id)).replace(microsecond=0)
        conversations.append({
            'id': next_id,
            'user_id': user_id,
            'timestamp': timestamp,
            'user_message': f'{label} user',
            'bot_response': f'{label} bot',
            'token_count': token_count,
            'important_flag': important,
        })
        next_id += 1

    add('user-a', 2, True, 100, 'user-a-recent-1')
    add('user-a', 1, False, 200, 'user-a-recent-2')
    add('user-a', 40, True, 300, 'user-a-older')

    for index in range(12):
        add('user-b', 20, index < 4, 250, f'user-b-{index}')

    add('user-c', 70, True, 400, 'user-c-older')

    for index in range(3):
        add('user-d', 8, index == 0, 150, f'user-d-{index}')

    return conversations


def _build_progress_events(now):
    return {
        'user-a': [
            {
                'timestamp': (now - timedelta(days=2)).replace(microsecond=0).isoformat(),
                'risk_level': 'high',
                'keywords': ['suicide'],
            },
            {
                'timestamp': (now - timedelta(days=50)).replace(microsecond=0).isoformat(),
                'risk_level': 'medium',
                'keywords': ['stress'],
            },
        ],
        'user-b': [
            {
                'timestamp': (now - timedelta(days=45)).replace(microsecond=0).isoformat(),
                'risk_level': 'high',
                'keywords': ['self-harm'],
            },
        ],
        'user-d': [
            {
                'timestamp': (now - timedelta(days=6)).replace(microsecond=0).isoformat(),
                'risk_level': 'medium',
                'keywords': ['anxiety'],
            },
        ],
    }


@pytest.fixture
def dashboard_testbed(monkeypatch):
    now = datetime.now().replace(second=0, microsecond=0)
    conversations = _build_conversations(now)
    fake_db = FakeDashboardDB(conversations)
    fake_manager = FakeDashboardManager(conversations)
    fake_redis = FakeRedis(_build_progress_events(now))

    monkeypatch.setattr(dashboard_module, '_db', fake_db)
    monkeypatch.setattr(dashboard_module, '_db_manager', fake_manager)
    monkeypatch.setattr(dashboard_module, '_redis_client', fake_redis)
    monkeypatch.setattr(dashboard_module, '_DASHBOARD_API_KEY', '')
    monkeypatch.setattr(dashboard_module, '_HIGH_RISK_KEYWORDS', {'suicide', 'self-harm'})
    monkeypatch.setattr(dashboard_module, '_MEDIUM_RISK_KEYWORDS', {'stress', 'anxiety'})
    monkeypatch.setattr(dashboard_module, '_GENERAL_RISK_LEVEL', 'general')
    monkeypatch.setattr(dashboard_module, '_knowledge_base', None)

    app = Flask(
        __name__,
        template_folder=str(Path(__file__).resolve().parents[1] / 'templates'),
    )
    app.config.update(TESTING=True)
    app.register_blueprint(dashboard_module.dashboard_bp)

    return {
        'client': app.test_client(),
        'db_manager': fake_manager,
        'now': now,
    }


def test_dashboard_insights_user_limit_only_changes_display_rows(dashboard_testbed):
    client = dashboard_testbed['client']

    limited = client.get('/api/dashboard/insights?lookback_days=30&limit=1&keyword_limit=10')
    expanded = client.get('/api/dashboard/insights?lookback_days=30&limit=10&keyword_limit=10')

    assert limited.status_code == 200
    assert expanded.status_code == 200

    limited_payload = limited.get_json()
    expanded_payload = expanded.get_json()

    assert limited_payload['overview'] == expanded_payload['overview']
    assert limited_payload['risk_summary'] == expanded_payload['risk_summary']
    assert limited_payload['infographic'] == expanded_payload['infographic']
    assert limited_payload['users_meta']['total'] == 3
    assert expanded_payload['users_meta']['total'] == 3
    assert limited_payload['users_meta']['displayed'] == 1
    assert expanded_payload['users_meta']['displayed'] == 3
    assert len(limited_payload['users']) == 1
    assert len(expanded_payload['users']) == 3


@pytest.mark.parametrize(
    ('lookback_days', 'expected_total', 'expected_users', 'expected_risk'),
    [
        (7, 2, 1, {'high': 1, 'medium': 1, 'general': 0, 'unknown': 0}),
        (30, 17, 3, {'high': 1, 'medium': 1, 'general': 0, 'unknown': 0}),
        (90, 19, 4, {'high': 2, 'medium': 2, 'general': 0, 'unknown': 0}),
    ],
)
def test_dashboard_insights_lookback_filters_all_metrics(
    dashboard_testbed,
    lookback_days,
    expected_total,
    expected_users,
    expected_risk,
):
    client = dashboard_testbed['client']
    response = client.get(f'/api/dashboard/insights?lookback_days={lookback_days}&limit=10&keyword_limit=10')

    assert response.status_code == 200
    payload = response.get_json()

    assert payload['overview']['total_conversations'] == expected_total
    assert payload['overview']['unique_users'] == expected_users
    assert payload['infographic']['engagement']['active_users'] == expected_users
    assert payload['users_meta']['total'] == expected_users
    assert payload['risk_summary'] == expected_risk

    if lookback_days == 7:
        assert len(payload['retention_buckets']) == 1
        assert len(payload['conversation_depth']) == 2
    elif lookback_days == 30:
        users_by_id = {user['user_id']: user for user in payload['users']}
        assert users_by_id['user-b']['recent_risk_events'] == []
        assert len(payload['conversation_depth']) == 4
    else:
        users_by_id = {user['user_id']: user for user in payload['users']}
        assert len(users_by_id['user-b']['recent_risk_events']) == 1
        assert len(payload['conversation_depth']) == 6


def test_dashboard_user_history_respects_lookback_window(dashboard_testbed):
    client = dashboard_testbed['client']

    month = client.get('/api/dashboard/users/user-a/history?lookback_days=30&limit=10')
    quarter = client.get('/api/dashboard/users/user-a/history?lookback_days=90&limit=10')

    assert month.status_code == 200
    assert quarter.status_code == 200

    month_payload = month.get_json()
    quarter_payload = quarter.get_json()

    assert month_payload['summary']['total_messages'] == 2
    assert month_payload['summary']['important_messages'] == 1
    assert len(month_payload['history']) == 2
    assert len(month_payload['risk_events']) == 1
    assert 'window' in month_payload

    assert quarter_payload['summary']['total_messages'] == 3
    assert quarter_payload['summary']['important_messages'] == 2
    assert len(quarter_payload['history']) == 3
    assert len(quarter_payload['risk_events']) == 2
    assert 'window' in quarter_payload


def test_dashboard_auth_and_boot_script(monkeypatch, dashboard_testbed):
    client = dashboard_testbed['client']
    monkeypatch.setattr(dashboard_module, '_DASHBOARD_API_KEY', 'secret-token')

    page = client.get('/dashboard')
    body = page.get_data(as_text=True)

    assert page.status_code == 200
    assert re.search(r'window\.__DASHBOARD_BOOT__\s*=\s*\{"authRequired":\s*true\};', body)

    unauthorized = client.get('/api/dashboard/insights')
    assert unauthorized.status_code == 401

    authorized = client.get(
        '/api/dashboard/insights?lookback_days=30&limit=10&keyword_limit=10',
        headers={'Authorization': 'Bearer secret-token'},
    )
    assert authorized.status_code == 200


def test_multi_ai_stats_zero_state_includes_expected_keys(dashboard_testbed):
    client = dashboard_testbed['client']
    response = client.get('/api/dashboard/multi-ai-stats?lookback_days=30')

    assert response.status_code == 200
    payload = response.get_json()

    assert payload['summary']['total_evaluations'] == 0
    assert payload['provider_avg_times'] == []
    assert payload['processing_times'] == []
    assert payload['provider_response_times'] == []
    assert 'window' in payload


def test_dashboard_export_json_and_csv_respect_filters(dashboard_testbed):
    client = dashboard_testbed['client']
    db_manager = dashboard_testbed['db_manager']
    now = dashboard_testbed['now']

    start_date = (now - timedelta(days=3)).date().isoformat()
    end_date = now.date().isoformat()

    json_response = client.get(
        f'/api/dashboard/export?format=json&start_date={start_date}&end_date={end_date}&anonymize=false&limit=10'
    )
    assert json_response.status_code == 200

    payload = json.loads(json_response.get_data(as_text=True))
    assert payload['total_records'] == 2
    assert [row['participant_id'] for row in payload['data']] == ['user-a', 'user-a']
    assert [row['is_important'] for row in payload['data']] == [True, False]
    assert payload['parameters']['start_date'].startswith(start_date)
    assert payload['parameters']['end_date'].startswith(end_date)

    export_queries = [query for query, _, _ in db_manager.queries if 'FROM conversations c' in query]
    assert export_queries
    assert all('important_flag' in query for query in export_queries)
    assert all('is_important' not in query for query in export_queries)

    csv_response = client.get(
        f'/api/dashboard/export?format=csv&start_date={start_date}&end_date={end_date}&anonymize=true&limit=10'
    )
    assert csv_response.status_code == 200
    assert csv_response.mimetype == 'text/csv'

    csv_text = csv_response.get_data(as_text=True)
    assert csv_text.startswith('\ufeff')
    rows = list(csv.DictReader(io.StringIO(csv_text.lstrip('\ufeff'))))

    assert len(rows) == 2
    assert all(row['participant_id'] != 'user-a' for row in rows)
    assert all(len(row['participant_id']) == 12 for row in rows)
    assert [row['is_important'] for row in rows] == ['True', 'False']

