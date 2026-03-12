"""
โมดูลฐานข้อมูลประวัติการแชทสำหรับแชทบอท 'ใจดี'
"""
from datetime import datetime, timedelta
import logging
from typing import List, Tuple, Dict, Any, Optional, Union
from .utils import safe_db_operation
from .token_counter import TokenCounter
from .database_manager import DatabaseManager

def _resolve_window_cutoff(days: Optional[int] = None, cutoff: Optional[datetime] = None) -> Optional[datetime]:
    """Return an inclusive start-of-day cutoff for dashboard lookback windows."""
    if cutoff is not None:
        return cutoff
    if days is None:
        return None

    days = max(1, min(int(days or 0), 180))
    window_start = (datetime.now() - timedelta(days=days - 1)).date()
    return datetime.combine(window_start, datetime.min.time())

class ChatHistoryDB:
    """
    คลาสสำหรับจัดการการดำเนินการกับฐานข้อมูลประวัติการแชท
    ใช้ DatabaseManager เพื่อจัดการการเชื่อมต่อและการดำเนินการกับฐานข้อมูล
    """

    def __init__(self, db_manager: DatabaseManager):
        """
        สร้างอินสแตนซ์ของ ChatHistoryDB

        Args:
            db_manager: DatabaseManager instance
        """
        self.db = db_manager
        self.counter = TokenCounter(cache_size=5000)  # เพิ่มขนาดแคชเพื่อประสิทธิภาพ
        logging.info("ChatHistoryDB initialized with enhanced database manager")

    @safe_db_operation
    def get_user_history(self, user_id: str, max_tokens: int = 100000) -> List[Tuple]:
        """
        ดึงประวัติการสนทนาของผู้ใช้แบบเหมาะสมด้วยประสิทธิภาพที่ดีขึ้น

        Args:
            user_id: LINE User ID
            max_tokens: จำนวนโทเค็นสูงสุดในประวัติ (สมดุลระหว่างบริบทและประสิทธิภาพ)

        Returns:
            List[Tuple]: ประวัติการสนทนาที่เลือก [(id, user_message, bot_response), ...]
        """
        # ใช้ query ที่มีประสิทธิภาพมากขึ้นด้วย LIMIT ที่เหมาะสม
        query = '''
            SELECT c.id, c.timestamp, c.user_message, c.bot_response, c.token_count
            FROM conversations c
            WHERE c.user_id = %s
            ORDER BY
                c.important_flag DESC, -- Important messages first
                c.timestamp DESC -- Then most recent
            LIMIT 200 -- เพียงพอสำหรับการดึงบริบทที่สำคัญ
        '''

        try:
            # ใช้ DatabaseManager เพื่อดำเนินการ query
            all_messages = self.db.execute_query(query, (user_id,))

            # Apply token limit with optimized processing
            selected_history = []
            total_tokens = 0

            for msg in all_messages:
                # ใช้ token_count จากฐานข้อมูลถ้ามี หรือคำนวณใหม่ถ้าไม่มี
                msg_tokens = msg[4] or self.counter.count_tokens(msg[2] + msg[3])

                if total_tokens + msg_tokens <= max_tokens:
                    selected_history.append((msg[0], msg[2], msg[3]))
                    total_tokens += msg_tokens
                else:
                    # ถ้าเกินขีดจำกัดโทเค็น ให้หยุด
                    break

            return selected_history
        except Exception as e:
            logging.error(f"Error retrieving user history: {str(e)}")
            # คืนค่ารายการว่างในกรณีที่มีข้อผิดพลาด
            return []
    @safe_db_operation
    def get_user_conversation_feed(
        self,
        user_id: str,
        limit: int = 50,
        cutoff: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        '''Return most recent conversation entries for dashboard views.'''
        limit = max(1, min(int(limit or 0), 200))
        cutoff = _resolve_window_cutoff(cutoff=cutoff)

        query = '''
            SELECT
                id,
                timestamp,
                user_message,
                bot_response,
                important_flag,
                COALESCE(token_count, 0) AS token_count
            FROM conversations
            WHERE user_id = %s
        '''
        params: List[Any] = [user_id]
        if cutoff is not None:
            query += '\n            AND timestamp >= %s'
            params.append(cutoff)
        query += '\n            ORDER BY timestamp DESC\n            LIMIT %s'
        params.append(limit)

        try:
            rows = self.db.execute_query(query, tuple(params))
        except TypeError:
            rows = self.db.execute_query(
                query.replace('LIMIT %s', f'LIMIT {limit}'),
                tuple(params[:-1])
            )

        history: List[Dict[str, Any]] = []
        for row in rows or []:
            if isinstance(row, dict):
                timestamp = row.get('timestamp')
                payload = {
                    'id': row.get('id'),
                    'timestamp': timestamp,
                    'user_message': row.get('user_message'),
                    'bot_response': row.get('bot_response'),
                    'important': bool(row.get('important_flag')),
                    'token_count': int(row.get('token_count') or 0),
                }
            else:
                row_id, timestamp, user_msg, bot_resp, important, token_count = row
                payload = {
                    'id': row_id,
                    'timestamp': timestamp,
                    'user_message': user_msg,
                    'bot_response': bot_resp,
                    'important': bool(important),
                    'token_count': int(token_count or 0),
                }

            if isinstance(payload['timestamp'], datetime):
                payload['timestamp'] = payload['timestamp'].isoformat()

            history.append(payload)

        return history

    @safe_db_operation
    def get_user_snapshot(
        self,
        user_id: str,
        cutoff: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        '''Collect aggregate metrics for a single user.'''
        cutoff = _resolve_window_cutoff(cutoff=cutoff)
        query = '''
            SELECT
                user_id,
                COUNT(*) AS total_messages,
                SUM(CASE WHEN important_flag THEN 1 ELSE 0 END) AS important_messages,
                MAX(timestamp) AS last_interaction,
                MIN(timestamp) AS first_interaction,
                COALESCE(SUM(token_count), 0) AS total_tokens
            FROM conversations
            WHERE user_id = %s
        '''
        params: List[Any] = [user_id]
        if cutoff is not None:
            query += '\n            AND timestamp >= %s'
            params.append(cutoff)
        query += '\n            GROUP BY user_id\n            LIMIT 1'

        try:
            rows = self.db.execute_query(query, tuple(params), dictionary=True)
        except TypeError:
            rows = self.db.execute_query(query, tuple(params))

        if not rows:
            return None

        if isinstance(rows[0], dict):
            data = dict(rows[0])
        else:
            result = rows[0]
            data = {
                'user_id': result[0],
                'total_messages': result[1],
                'important_messages': result[2],
                'last_interaction': result[3],
                'first_interaction': result[4],
                'total_tokens': result[5],
            }

        total_messages = int(data.get('total_messages') or 0)
        important_messages = int(data.get('important_messages') or 0)
        total_tokens = int(data.get('total_tokens') or 0)

        snapshot = {
            'user_id': data.get('user_id'),
            'total_messages': total_messages,
            'important_messages': important_messages,
            'total_tokens': total_tokens,
            'important_ratio': round(important_messages / total_messages, 3) if total_messages else 0.0,
        }

        last_interaction = data.get('last_interaction')
        first_interaction = data.get('first_interaction')

        if isinstance(last_interaction, datetime):
            snapshot['last_interaction'] = last_interaction.isoformat()
        else:
            snapshot['last_interaction'] = last_interaction

        if isinstance(first_interaction, datetime):
            snapshot['first_interaction'] = first_interaction.isoformat()
        else:
            snapshot['first_interaction'] = first_interaction

        return snapshot

    @safe_db_operation
    def get_recent_daily_message_totals(
        self,
        days: int = 14,
        cutoff: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        '''Aggregate conversation counts per day within a rolling window.'''
        cutoff = _resolve_window_cutoff(days=days, cutoff=cutoff)

        query = '''
            SELECT
                DATE(timestamp) AS day_value,
                COUNT(*) AS total_messages,
                SUM(CASE WHEN important_flag THEN 1 ELSE 0 END) AS important_messages,
                COALESCE(SUM(token_count), 0) AS total_tokens
            FROM conversations
            WHERE timestamp >= %s
            GROUP BY DATE(timestamp)
            ORDER BY DATE(timestamp)
        '''

        rows = self.db.execute_query(query, (cutoff,))

        totals: List[Dict[str, Any]] = []
        for row in rows or []:
            if isinstance(row, dict):
                day_value = row.get('day_value') or row.get('date') or row.get('timestamp')
                total_messages = row.get('total_messages')
                important_messages = row.get('important_messages')
                total_tokens = row.get('total_tokens')
            else:
                day_value, total_messages, important_messages, total_tokens = row

            if isinstance(day_value, datetime):
                day_str = day_value.date().isoformat()
            else:
                day_str = str(day_value) if day_value is not None else None

            totals.append({
                'date': day_str,
                'total_messages': int(total_messages or 0),
                'important_messages': int(important_messages or 0),
                'total_tokens': int(total_tokens or 0),
            })

        return totals

    @safe_db_operation
    def get_dashboard_overview(self, cutoff: Optional[datetime] = None) -> Dict[str, int]:
        """Collect global conversation metrics for the practitioner dashboard."""
        cutoff = _resolve_window_cutoff(cutoff=cutoff)
        query = """
            SELECT
                COUNT(*) AS total_conversations,
                COUNT(DISTINCT user_id) AS unique_users,
                COALESCE(SUM(CASE WHEN important_flag THEN 1 ELSE 0 END), 0) AS important_messages
            FROM conversations
        """
        params: List[Any] = []
        if cutoff is not None:
            query += "\n            WHERE timestamp >= %s"
            params.append(cutoff)

        try:
            if params:
                result = self.db.execute_query(query, tuple(params), dictionary=True)
            else:
                result = self.db.execute_query(query, dictionary=True)
        except TypeError:
            if params:
                result = self.db.execute_query(query, tuple(params))
            else:
                result = self.db.execute_query(query)
            if result:
                total_conversations, unique_users, important_messages = result[0]
            else:
                total_conversations = unique_users = important_messages = 0
            return {
                'total_conversations': int(total_conversations or 0),
                'unique_users': int(unique_users or 0),
                'important_messages': int(important_messages or 0),
            }

        row = (result or [{}])[0] if isinstance(result, list) else {}
        return {
            'total_conversations': int((row or {}).get('total_conversations', 0) or 0),
            'unique_users': int((row or {}).get('unique_users', 0) or 0),
            'important_messages': int((row or {}).get('important_messages', 0) or 0),
        }

    @safe_db_operation
    def get_recent_user_summaries(
        self,
        limit: Optional[int] = 20,
        cutoff: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """Return per-user conversation snapshots ordered by recency."""
        cutoff = _resolve_window_cutoff(cutoff=cutoff)
        query = """
            SELECT
                user_id,
                COUNT(*) AS total_messages,
                SUM(CASE WHEN important_flag THEN 1 ELSE 0 END) AS important_messages,
                MAX(timestamp) AS last_interaction,
                COALESCE(SUM(token_count), 0) AS total_tokens
            FROM conversations
        """
        params: List[Any] = []
        if cutoff is not None:
            query += "\n            WHERE timestamp >= %s"
            params.append(cutoff)
        query += "\n            GROUP BY user_id\n            ORDER BY last_interaction DESC"
        limited = limit is not None
        if limited:
            limit = max(1, min(int(limit or 0), 500))
            query += "\n            LIMIT %s"
            params.append(limit)

        try:
            if params:
                rows = self.db.execute_query(query, tuple(params), dictionary=True)
            else:
                rows = self.db.execute_query(query, dictionary=True)
        except TypeError:
            fallback_query = query
            fallback_params = tuple(params)
            if limited:
                fallback_query = query.replace('LIMIT %s', f'LIMIT {limit}')
                fallback_params = tuple(params[:-1])
            if fallback_params:
                rows = self.db.execute_query(fallback_query, fallback_params)
            else:
                rows = self.db.execute_query(fallback_query)

        summaries: List[Dict[str, Any]] = []
        for row in rows or []:
            if isinstance(row, dict):
                data = row
            else:
                user_id, total_messages, important_messages, last_interaction, total_tokens = row
                data = {
                    'user_id': user_id,
                    'total_messages': total_messages,
                    'important_messages': important_messages,
                    'last_interaction': last_interaction,
                    'total_tokens': total_tokens,
                }

            summaries.append({
                'user_id': data.get('user_id'),
                'total_messages': int(data.get('total_messages') or 0),
                'important_messages': int(data.get('important_messages') or 0),
                'last_interaction': data.get('last_interaction'),
                'total_tokens': int(data.get('total_tokens') or 0),
            })

        return summaries

    @safe_db_operation
    def get_retention_buckets(self, cutoff: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Return user distribution by total session (message-pair) count buckets."""
        cutoff = _resolve_window_cutoff(cutoff=cutoff)
        query = '''
            SELECT
                CASE
                    WHEN msg_count = 1 THEN '1'
                    WHEN msg_count BETWEEN 2 AND 5 THEN '2-5'
                    WHEN msg_count BETWEEN 6 AND 15 THEN '6-15'
                    WHEN msg_count BETWEEN 16 AND 30 THEN '16-30'
                    ELSE '31+'
                END AS bucket,
                COUNT(*) AS user_count
            FROM (
                SELECT user_id, COUNT(*) AS msg_count
                FROM conversations
        '''
        params: List[Any] = []
        if cutoff is not None:
            query += '\n                WHERE timestamp >= %s'
            params.append(cutoff)
        query += '''
                GROUP BY user_id
            ) AS per_user
            GROUP BY bucket
            ORDER BY FIELD(bucket, '1', '2-5', '6-15', '16-30', '31+')
        '''

        if params:
            rows = self.db.execute_query(query, tuple(params))
        else:
            rows = self.db.execute_query(query)

        buckets: List[Dict[str, Any]] = []
        for row in rows or []:
            if isinstance(row, dict):
                buckets.append({
                    'bucket': row.get('bucket', ''),
                    'user_count': int(row.get('user_count') or 0),
                })
            else:
                buckets.append({
                    'bucket': row[0],
                    'user_count': int(row[1] or 0),
                })
        return buckets

    @safe_db_operation
    def get_conversation_depth_trend(
        self,
        days: int = 30,
        cutoff: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """Return daily averages of messages-per-user and tokens-per-message."""
        cutoff = _resolve_window_cutoff(days=days, cutoff=cutoff)

        query = '''
            SELECT
                DATE(timestamp) AS day_value,
                COUNT(DISTINCT user_id) AS active_users,
                COUNT(*) AS total_messages,
                COALESCE(SUM(token_count), 0) AS total_tokens
            FROM conversations
            WHERE timestamp >= %s
            GROUP BY DATE(timestamp)
            ORDER BY DATE(timestamp)
        '''

        rows = self.db.execute_query(query, (cutoff,))
        trend: List[Dict[str, Any]] = []
        for row in rows or []:
            if isinstance(row, dict):
                day_value = row.get('day_value') or row.get('date')
                active_users = int(row.get('active_users') or 0)
                total_messages = int(row.get('total_messages') or 0)
                total_tokens = int(row.get('total_tokens') or 0)
            else:
                day_value, active_users, total_messages, total_tokens = row
                active_users = int(active_users or 0)
                total_messages = int(total_messages or 0)
                total_tokens = int(total_tokens or 0)

            if isinstance(day_value, datetime):
                day_str = day_value.date().isoformat()
            else:
                day_str = str(day_value) if day_value is not None else None

            avg_msgs = round(total_messages / active_users, 1) if active_users else 0
            avg_tokens = round(total_tokens / total_messages, 1) if total_messages else 0

            trend.append({
                'date': day_str,
                'active_users': active_users,
                'total_messages': total_messages,
                'avg_messages_per_user': avg_msgs,
                'avg_tokens_per_message': avg_tokens,
            })
        return trend

