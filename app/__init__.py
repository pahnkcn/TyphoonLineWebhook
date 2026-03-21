"""
แพ็คเกจหลักของแชทบอท 'ใจดี'
รวมโมดูลทั้งหมดสำหรับแอปพลิเคชันแชทบอท
"""
import os
import logging
from logging.handlers import RotatingFileHandler

__version__ = '1.0.0'

# ตั้งค่าการบันทึกข้อมูล พร้อมหมุนไฟล์เมื่อขนาดเกิน 5MB
os.makedirs('logs', exist_ok=True)
logging.basicConfig(
    level=getattr(logging, os.getenv('LOG_LEVEL', 'INFO')),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler('logs/app.log', maxBytes=5 * 1024 * 1024, backupCount=3),
        logging.StreamHandler()
    ]
)

# นำเข้าส่วนประกอบหลักเพื่อให้ใช้งานได้ง่าย
try:
    from .app_main import app, init_scheduler
    from .llm.grok_client import send_chat, astream_chat, stream_chat, astream_chat_iter
    from .chat_history_db import ChatHistoryDB
    from .token_counter import TokenCounter
    from .utils import safe_api_call, safe_db_operation
    from .config import load_config
    from .database_manager import DatabaseManager
    from .session_manager import (
        init_session_manager,
        get_chat_session,
        save_chat_session,
        check_session_timeout,
        update_last_activity,
        hybrid_context_management,
    )
    from .risk_assessment import (
        init_risk_assessment,
        assess_risk,
        save_progress_data,
        generate_progress_report,
    )

    # ส่งออกส่วนประกอบที่จำเป็นสำหรับการใช้งานจากภายนอก
    __all__ = [
        'app',
        'init_scheduler',
        'send_chat',
        'astream_chat',
        'stream_chat',
        'astream_chat_iter',
        'ChatHistoryDB',
        'TokenCounter',
        'safe_api_call',
        'safe_db_operation',
        'load_config',
        'DatabaseManager',
        'init_session_manager',
        'get_chat_session',
        'save_chat_session',
        'check_session_timeout',
        'update_last_activity',
        'hybrid_context_management',
        'init_risk_assessment',
        'assess_risk',
        'save_progress_data',
        'generate_progress_report'
    ]

except ImportError as e:
    logging.error(f"เกิดข้อผิดพลาดในการนำเข้าโมดูล: {str(e)}")
    # ส่งออกเฉพาะเวอร์ชันในกรณีที่มีข้อผิดพลาดในการนำเข้า
    __all__ = ['__version__']
