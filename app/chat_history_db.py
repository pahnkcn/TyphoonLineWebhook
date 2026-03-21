"""
โมดูลฐานข้อมูลประวัติการแชทสำหรับแชทบอท 'ใจดี'
"""
from datetime import datetime, timedelta
import logging
from typing import List, Tuple, Dict, Any, Optional, Union
from .utils import safe_db_operation
from .token_counter import TokenCounter
from .database_manager import DatabaseManager

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
    def get_user_conversation_feed(self, user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        '''Return most recent conversation entries for dashboard views.'''
        limit = max(1, min(int(limit or 0), 200))
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
            ORDER BY timestamp DESC
            LIMIT %s
        '''

        try:
            rows = self.db.execute_query(query, (user_id, limit))
        except TypeError:
            rows = self.db.execute_query(
                query.replace('LIMIT %s', f'LIMIT {limit}'),
                (user_id,)
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
    def get_user_snapshot(self, user_id: str) -> Optional[Dict[str, Any]]:
        '''Collect aggregate metrics for a single user.'''
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
            GROUP BY user_id
            LIMIT 1
        '''

        try:
            rows = self.db.execute_query(query, (user_id,), dictionary=True)
        except TypeError:
            rows = self.db.execute_query(query.replace('LIMIT 1', 'LIMIT 1'), (user_id,))

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
    def get_recent_daily_message_totals(self, days: int = 14) -> List[Dict[str, Any]]:
        '''Aggregate conversation counts per day within a rolling window.'''
        days = max(1, min(int(days or 0), 90))
        cutoff = datetime.now() - timedelta(days=days - 1)

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
    def save_conversation(self, user_id: str, user_message: str, bot_response: str,
                         token_count: int = 0, important: bool = None) -> bool:
        """
        บันทึกการสนทนาเดี่ยวลงในฐานข้อมูลด้วยประสิทธิภาพที่ดีขึ้น

        Args:
            user_id: LINE User ID
            user_message: ข้อความของผู้ใช้
            bot_response: การตอบกลับของบอท
            token_count: จำนวนโทเค็นที่ใช้
            important: ความสำคัญของข้อความ (None = ตรวจสอบอัตโนมัติ)

        Returns:
            bool: True หากสำเร็จ
        """
        try:
            # ตรวจสอบความสำคัญถ้าไม่มีการระบุ
            if important is None:
                important = self._check_message_importance(user_message, bot_response)

            # ถ้าไม่มีการระบุจำนวนโทเค็น ให้คำนวณ
            if not token_count:
                token_count = self.counter.count_tokens(user_message + bot_response)

            # ใช้ query ที่มีประสิทธิภาพ
            query = '''
                INSERT INTO conversations
                (user_id, timestamp, user_message, bot_response, token_count, important_flag)
                VALUES (%s, %s, %s, %s, %s, %s)
            '''

            params = (
                user_id,
                datetime.now(),
                user_message,
                bot_response,
                token_count,
                important
            )

            # ใช้ DatabaseManager เพื่อดำเนินการ query
            self.db.execute_and_commit(query, params)
            return True

        except Exception as e:
            logging.error(f"Error saving conversation: {str(e)}")
            raise

    @safe_db_operation
    def save_batch_conversations(self, conversations: List[Dict[str, Any]]) -> bool:
        """
        บันทึกหลายการสนทนาพร้อมกันเพื่อประสิทธิภาพที่ดีขึ้น

        Args:
            conversations: รายการข้อมูลการสนทนา

        Returns:
            bool: True หากสำเร็จ
        """
        if not conversations:
            return True

        try:
            # Prepare batch values with optimized processing
            values = []
            for conv in conversations:
                # ตรวจสอบความสำคัญถ้าไม่มีการระบุ
                important = conv.get('important')
                if important is None:
                    important = self._check_message_importance(
                        conv['user_message'], conv['bot_response']
                    )

                # ถ้าไม่มีการระบุจำนวนโทเค็น ให้คำนวณ
                token_count = conv.get('token_count', 0)
                if not token_count:
                    token_count = self.counter.count_tokens(
                        conv['user_message'] + conv['bot_response']
                    )

                values.append((
                    conv['user_id'],
                    conv.get('timestamp', datetime.now()),
                    conv['user_message'],
                    conv['bot_response'],
                    token_count,
                    important
                ))

            # ใช้ query ที่มีประสิทธิภาพ
            query = '''
                INSERT INTO conversations
                (user_id, timestamp, user_message, bot_response, token_count, important_flag)
                VALUES (%s, %s, %s, %s, %s, %s)
            '''

            # ใช้ DatabaseManager เพื่อดำเนินการ batch insert
            self.db.execute_many(query, values)
            return True

        except Exception as e:
            logging.error(f"Error saving batch conversations: {str(e)}")
            raise

    def _check_message_importance(self, user_message, bot_response):
        """
        ตรวจสอบความสำคัญของข้อความตามเนื้อหา

        Args:
            user_message (str): ข้อความของผู้ใช้
            bot_response (str): คำตอบของบอท

        Returns:
            bool: True หากข้อความสำคัญ
        """
        # ตรวจสอบคำสำคัญในข้อความของผู้ใช้
        important_keywords = [
            'ฆ่าตัวตาย', 'ทำร้ายตัวเอง', 'อยากตาย',
            'overdose', 'เกินขนาด', 'ก้าวร้าว',
            'ซึมเศร้า', 'วิตกกังวล', 'ความทรงจำ',
            'ไม่มีความสุข', 'ทรมาน', 'เครียด',
            'เลิก', 'หยุด', 'อดทน'
        ]

        combined_text = (user_message + " " + bot_response).lower()
        for keyword in important_keywords:
            if keyword.lower() in combined_text:
                return True

        # ตรวจสอบความยาวของข้อความ (ข้อความที่ยาวมักมีเนื้อหาสำคัญ)
        if len(user_message) > 300 or len(bot_response) > 500:
            return True

        return False

    @safe_db_operation
    def get_user_history_count(self, user_id: str) -> int:
        """
        นับจำนวนการสนทนาของผู้ใช้

        Args:
            user_id: LINE User ID

        Returns:
            int: จำนวนบันทึกการสนทนา
        """
        query = 'SELECT COUNT(*) FROM conversations WHERE user_id = %s'
        result = self.db.execute_query(query, (user_id,))
        return result[0][0] if result else 0

    @safe_db_operation
    def get_important_message_count(self, user_id: str) -> int:
        """
        นับจำนวนข้อความสำคัญของผู้ใช้

        Args:
            user_id: LINE User ID

        Returns:
            int: จำนวนข้อความสำคัญ
        """
        query = 'SELECT COUNT(*) FROM conversations WHERE user_id = %s AND important_flag = TRUE'
        result = self.db.execute_query(query, (user_id,))
        return result[0][0] if result else 0

    @safe_db_operation
    def get_last_interaction(self, user_id: str) -> str:
        """
        ดึงเวลาของการสนทนาล่าสุด

        Args:
            user_id: LINE User ID

        Returns:
            str: เวลาในรูปแบบ string หรือ "ไม่มีข้อมูล"
        """
        query = 'SELECT MAX(timestamp) FROM conversations WHERE user_id = %s'
        result = self.db.execute_query(query, (user_id,))

        timestamp = result[0][0] if result and result[0] else None
        if timestamp:
            return timestamp.strftime('%Y-%m-%d %H:%M:%S')
        return "ไม่มีข้อมูล"

    @safe_db_operation
    def get_total_tokens(self, user_id: str) -> int:
        """
        คำนวณจำนวนโทเค็นทั้งหมดที่ใช้งานโดยผู้ใช้

        Args:
            user_id: LINE User ID

        Returns:
            int: จำนวนโทเค็นทั้งหมด
        """
        query = 'SELECT SUM(token_count) FROM conversations WHERE user_id = %s'
        result = self.db.execute_query(query, (user_id,))
        return result[0][0] or 0 if result and result[0] else 0

    @safe_db_operation
    def clear_user_history(self, user_id: str) -> bool:
        """
        ลบประวัติการสนทนาทั้งหมดของผู้ใช้

        Args:
            user_id: LINE User ID

        Returns:
            bool: True หากสำเร็จ
        """
        try:
            query = 'DELETE FROM conversations WHERE user_id = %s'
            self.db.execute_and_commit(query, (user_id,))
            return True
        except Exception as e:
            logging.error(f"Error clearing user history: {str(e)}")
            raise

    @safe_db_operation
    def update_follow_up_status(self, user_id: str, status: str, timestamp: datetime = None) -> bool:
        """
        อัพเดทสถานะการติดตามผลสำหรับผู้ใช้

        Args:
            user_id: LINE User ID
            status: สถานะการติดตาม ('scheduled', 'sent', 'completed')
            timestamp: เวลาที่อัพเดท (ถ้าไม่ระบุจะใช้เวลาปัจจุบัน)

        Returns:
            bool: True หากสำเร็จ
        """
        try:
            if timestamp is None:
                timestamp = datetime.now()

            # ตรวจสอบว่ามีรายการติดตามอยู่แล้วหรือไม่
            query = 'SELECT id FROM follow_ups WHERE user_id = %s AND status != %s'
            result = self.db.execute_query(query, (user_id, 'completed'))

            if result and result[0]:
                # อัพเดทรายการที่มีอยู่
                update_query = 'UPDATE follow_ups SET status = %s, updated_at = %s WHERE id = %s'
                self.db.execute_and_commit(update_query, (status, timestamp, result[0][0]))
            else:
                # สร้างรายการใหม่
                insert_query = '''
                    INSERT INTO follow_ups
                    (user_id, status, created_at, updated_at)
                    VALUES (%s, %s, %s, %s)
                '''
                self.db.execute_and_commit(insert_query, (user_id, status, timestamp, timestamp))

            return True

        except Exception as e:
            logging.error(f"Error updating follow-up status: {str(e)}")
            raise

    @safe_db_operation
    def get_dashboard_overview(self) -> Dict[str, int]:
        """Collect global conversation metrics for the practitioner dashboard."""
        query = (
            """
            SELECT
                COUNT(*) AS total_conversations,
                COUNT(DISTINCT user_id) AS unique_users,
                COALESCE(SUM(CASE WHEN important_flag THEN 1 ELSE 0 END), 0) AS important_messages
            FROM conversations
            """
        )

        try:
            result = self.db.execute_query(query, dictionary=True)
        except TypeError:
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
    def get_recent_user_summaries(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return per-user conversation snapshots ordered by recency."""
        query = (
            """
            SELECT
                user_id,
                COUNT(*) AS total_messages,
                SUM(CASE WHEN important_flag THEN 1 ELSE 0 END) AS important_messages,
                MAX(timestamp) AS last_interaction,
                COALESCE(SUM(token_count), 0) AS total_tokens
            FROM conversations
            GROUP BY user_id
            ORDER BY last_interaction DESC
            LIMIT %s
            """
        )

        try:
            rows = self.db.execute_query(query, (limit,), dictionary=True)
        except TypeError:
            rows = self.db.execute_query(query.replace('%s', str(limit)))

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
