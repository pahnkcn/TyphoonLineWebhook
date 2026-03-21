"""
โมดูลเริ่มต้นฐานข้อมูลสำหรับแชทบอท 'ใจดี'
ใช้สำหรับตรวจสอบและสร้างตารางฐานข้อมูลที่จำเป็น
"""
import logging
from typing import Dict, Any
from .utils import safe_db_operation
from .database_manager import DatabaseManager

class DatabaseInitializer:
    """
    คลาสสำหรับเริ่มต้นและตั้งค่าฐานข้อมูล
    ใช้ DatabaseManager เพื่อจัดการการเชื่อมต่อและการดำเนินการกับฐานข้อมูล
    """

    def __init__(self, db_manager: DatabaseManager):
        """
        สร้างอินสแตนซ์ของ DatabaseInitializer

        Args:
            db_manager: DatabaseManager instance
        """
        self.db = db_manager

    @safe_db_operation
    def check_and_create_tables(self) -> bool:
        """
        ตรวจสอบและสร้างตารางฐานข้อมูลที่จำเป็นถ้ายังไม่มี

        Returns:
            bool: True หากสำเร็จ
        """
        try:
            # ตรวจสอบและสร้างตาราง conversations
            if not self.db.table_exists('conversations'):
                logging.info("ไม่พบตาราง conversations กำลังสร้าง...")
                self._create_conversations_table()
                logging.info("สร้างตาราง conversations สำเร็จ")

            # ตรวจสอบและสร้างตาราง follow_ups
            if not self.db.table_exists('follow_ups'):
                logging.info("ไม่พบตาราง follow_ups กำลังสร้าง...")
                self._create_follow_ups_table()
                logging.info("สร้างตาราง follow_ups สำเร็จ")

            # ตรวจสอบและสร้างตาราง user_metrics
            if not self.db.table_exists('user_metrics'):
                logging.info("ไม่พบตาราง user_metrics กำลังสร้าง...")
                self._create_user_metrics_table()
                logging.info("สร้างตาราง user_metrics สำเร็จ")

            # ตรวจสอบและสร้างตาราง registration_codes
            if not self.db.table_exists('registration_codes'):
                logging.info("ไม่พบตาราง registration_codes กำลังสร้าง...")
                self._create_registration_codes_table()
                logging.info("สร้างตาราง registration_codes สำเร็จ")

            logging.info("การเริ่มต้นฐานข้อมูลสำเร็จ")
            return True

        except Exception as e:
            logging.error(f"เกิดข้อผิดพลาดในการเริ่มต้นฐานข้อมูล: {str(e)}")
            raise

    def _create_conversations_table(self) -> None:
        """สร้างตาราง conversations"""
        query = """
            CREATE TABLE conversations (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id VARCHAR(50) NOT NULL,
                timestamp DATETIME NOT NULL,
                user_message TEXT NOT NULL,
                bot_response TEXT NOT NULL,
                token_count INT DEFAULT 0,
                important_flag BOOLEAN DEFAULT FALSE,
                INDEX idx_user_id (user_id),
                INDEX idx_timestamp (timestamp),
                INDEX idx_important (important_flag),
                INDEX idx_user_timestamp (user_id, timestamp)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """
        self.db.execute_and_commit(query)

    def _create_follow_ups_table(self) -> None:
        """สร้างตาราง follow_ups"""
        query = """
            CREATE TABLE follow_ups (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id VARCHAR(50) NOT NULL,
                status VARCHAR(20) NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                scheduled_date DATETIME,
                INDEX idx_user_id (user_id),
                INDEX idx_status (status),
                INDEX idx_scheduled (scheduled_date),
                INDEX idx_user_status (user_id, status)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """
        self.db.execute_and_commit(query)

    def _create_user_metrics_table(self) -> None:
        """สร้างตาราง user_metrics"""
        query = """
            CREATE TABLE user_metrics (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id VARCHAR(50) NOT NULL,
                metric_name VARCHAR(50) NOT NULL,
                metric_value FLOAT NOT NULL,
                timestamp DATETIME NOT NULL,
                UNIQUE KEY unique_user_metric (user_id, metric_name),
                INDEX idx_user_id (user_id),
                INDEX idx_metric_name (metric_name)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """
        self.db.execute_and_commit(query)

    def _create_registration_codes_table(self) -> None:
        """สร้างตาราง registration_codes"""
        query = """
            CREATE TABLE registration_codes (
                code VARCHAR(10) PRIMARY KEY,
                user_id VARCHAR(50),
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                verified_at DATETIME,
                status ENUM('pending', 'verified', 'expired') DEFAULT 'pending',
                form_data JSON,
                INDEX idx_user_id (user_id),
                INDEX idx_status (status)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """
        self.db.execute_and_commit(query)

def initialize_database(config: Dict[str, Any]) -> bool:
    """
    ฟังก์ชันสำหรับเริ่มต้นฐานข้อมูล

    Args:
        config: การตั้งค่าการเชื่อมต่อฐานข้อมูล

    Returns:
        bool: True หากสำเร็จ
    """
    db_manager = DatabaseManager(config)
    initializer = DatabaseInitializer(db_manager)
    return initializer.check_and_create_tables()
