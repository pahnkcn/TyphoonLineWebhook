"""
โมดูลจัดการฐานข้อมูลสำหรับแชทบอท 'ใจดี'
จัดการการเชื่อมต่อฐานข้อมูลและการดำเนินการที่เกี่ยวข้อง
"""
import logging
import time
import mysql.connector
from mysql.connector import pooling
from typing import Dict, Any, Optional, List, Tuple, Union
from contextlib import contextmanager
from functools import wraps

class DatabaseManager:
    """
    จัดการการเชื่อมต่อฐานข้อมูลและการดำเนินการที่เกี่ยวข้อง
    """
    
    def __init__(self, config: Dict[str, Any], pool_size: int = 32, pool_name: str = "chat_pool"):
        """
        สร้างอินสแตนซ์ของ DatabaseManager
        
        Args:
            config: การตั้งค่าการเชื่อมต่อฐานข้อมูล
            pool_size: ขนาดของ connection pool (increased default from 10 to 50)
            pool_name: ชื่อของ connection pool
        """
        self.config = {
            'host': config.get('MYSQL_HOST', 'localhost'),
            'port': int(config.get('MYSQL_PORT', 3306)),
            'user': config.get('MYSQL_USER', 'root'),
            'password': config.get('MYSQL_PASSWORD', ''),
            'database': config.get('MYSQL_DB', 'chatbot'),
            'charset': 'utf8mb4',
            'use_unicode': True,
            'connect_timeout': 30,  # Increased from 10
            'autocommit': True,
            'raise_on_warnings': True,
            'sql_mode': 'TRADITIONAL',
            'get_warnings': True
        }
        self.pool_size = pool_size
        self.pool_name = pool_name
        self.pool = None
        self.max_retries = 3
        self.retry_delay = 1
        self.health_check_interval = 300  # 5 minutes
        self.last_health_check = 0
        self.init_pool()
        
    def init_pool(self) -> None:
        """
        เริ่มต้น connection pool with enhanced configuration and startup wait
        """
        # Wait for database to be ready (for Docker container startup)
        self._wait_for_database()
        
        try:
            self.pool = pooling.MySQLConnectionPool(
                pool_name=self.pool_name,
                pool_size=self.pool_size,
                pool_reset_session=True,  # Reset session state on reuse
                **self.config
            )
            logging.info(f"MySQL connection pool initialized with size {self.pool_size}")
            
            # Test the pool with a simple query
            self._test_pool_connection()
            
        except Exception as e:
            logging.error(f"Error initializing MySQL connection pool: {str(e)}")
            raise
    
    def _wait_for_database(self, max_wait_time: int = 60, retry_interval: int = 2) -> None:
        """
        Wait for database to be ready before initializing connection pool
        
        Args:
            max_wait_time: Maximum time to wait in seconds
            retry_interval: Time between retries in seconds
        """
        import socket
        
        start_time = time.time()
        host = self.config['host']
        port = self.config['port']
        
        logging.info(f"Waiting for database at {host}:{port} to become available...")
        
        while time.time() - start_time < max_wait_time:
            try:
                # Try to connect to the database port
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(retry_interval)
                result = sock.connect_ex((host, port))
                sock.close()
                
                if result == 0:
                    logging.info(f"Database port {host}:{port} is accessible")
                    # Additional wait to ensure MySQL is fully ready
                    time.sleep(2)
                    return
                    
            except Exception as e:
                logging.debug(f"Database port check failed: {str(e)}")
            
            logging.info(f"Database not ready yet, waiting {retry_interval} seconds...")
            time.sleep(retry_interval)
        
        logging.warning(f"Database at {host}:{port} did not become available within {max_wait_time} seconds")
    
    def _test_pool_connection(self) -> None:
        """
        Test the connection pool with a simple query
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                cursor.fetchone()
                cursor.close()
            logging.info("Connection pool test successful")
        except Exception as e:
            logging.error(f"Connection pool test failed: {str(e)}")
            raise
    
    @contextmanager
    def get_connection(self):
        """
        Context manager สำหรับการจัดการการเชื่อมต่อฐานข้อมูล with retry logic
        
        Yields:
            Connection: การเชื่อมต่อฐานข้อมูล
        """
        conn = None
        retries = 0
        last_error = None

        while retries < self.max_retries:
            conn = None
            connection_yielded = False
            try:
                # Perform health check if needed
                self._perform_health_check_if_needed()

                conn = self.pool.get_connection()

                # Verify connection is alive
                if not conn.is_connected():
                    raise mysql.connector.Error("Connection is not active")

                connection_yielded = True
                yield conn
                return

            except mysql.connector.Error as e:
                # If we've already handed the connection to the caller, propagate the error
                if connection_yielded:
                    raise

                last_error = e
                retries += 1

                if retries % 3 == 1 or retries >= self.max_retries:
                    logging.error(f"Database connection error (attempt {retries}/{self.max_retries}): {str(e)}")

                # Handle specific connection-related error types
                if getattr(e, 'errno', None) in [2006, 2013, 2055]:  # Server gone away, lost connection, packet too large
                    if retries % 3 == 1:
                        logging.info("Connection lost, attempting to reinitialize pool...")
                    try:
                        self.init_pool()
                    except Exception as init_error:
                        logging.error(f"Failed to reinitialize pool: {str(init_error)}")

                if retries < self.max_retries:
                    sleep_time = self.retry_delay * (2 ** (retries - 1))  # Exponential backoff
                    if retries % 3 == 1:
                        logging.info(f"Retrying connection in {sleep_time} seconds...")
                    time.sleep(sleep_time)
                else:
                    logging.error(f"Failed to get database connection after {self.max_retries} retries")
                    raise last_error

            except Exception as e:
                if connection_yielded:
                    raise
                logging.error(f"Unexpected database error: {str(e)}")
                raise

            finally:
                if conn and conn.is_connected():
                    try:
                        conn.close()
                    except Exception as close_error:
                        logging.warning(f"Error closing connection: {str(close_error)}")
                conn = None

        # If loop exits without returning, re-raise last error
        if last_error is not None:
            raise last_error
        raise mysql.connector.Error("Unable to acquire database connection")

    @contextmanager
    def get_cursor(self, dictionary=False):
        """
        Context manager สำหรับการจัดการ cursor
        
        Args:
            dictionary: ถ้าเป็น True จะคืนค่าเป็น dictionary แทน tuple
            
        Yields:
            Cursor: cursor สำหรับการดำเนินการกับฐานข้อมูล
        """
        with self.get_connection() as conn:
            cursor = conn.cursor(dictionary=dictionary)
            try:
                yield cursor
                conn.commit()
            except Exception as e:
                conn.rollback()
                logging.error(f"Database operation error: {str(e)}")
                raise
            finally:
                cursor.close()
    
    def execute_query(self, query: str, params: Optional[Tuple] = None, dictionary: bool = False) -> List[Dict[str, Any]]:
        """
        ดำเนินการ query และคืนค่าผลลัพธ์
        
        Args:
            query: SQL query
            params: พารามิเตอร์สำหรับ query
            dictionary: ถ้าเป็น True จะคืนค่าเป็น dictionary แทน tuple
            
        Returns:
            List: ผลลัพธ์ของ query
        """
        with self.get_cursor(dictionary=dictionary) as cursor:
            cursor.execute(query, params or ())
            return cursor.fetchall()
    
    def execute_many(self, query: str, params_list: List[Tuple]) -> int:
        """
        ดำเนินการ query หลายครั้งด้วยชุดพารามิเตอร์ที่แตกต่างกัน
        
        Args:
            query: SQL query
            params_list: รายการของพารามิเตอร์สำหรับแต่ละการดำเนินการ
            
        Returns:
            int: จำนวนแถวที่ได้รับผลกระทบ
        """
        with self.get_cursor() as cursor:
            cursor.executemany(query, params_list)
            return cursor.rowcount
    
    def execute_and_commit(self, query: str, params: Optional[Tuple] = None) -> int:
        """
        ดำเนินการ query และ commit การเปลี่ยนแปลง
        
        Args:
            query: SQL query
            params: พารามิเตอร์สำหรับ query
            
        Returns:
            int: จำนวนแถวที่ได้รับผลกระทบ
        """
        with self.get_cursor() as cursor:
            cursor.execute(query, params or ())
            return cursor.rowcount
    
    def execute_and_get_last_id(self, query: str, params: Optional[Tuple] = None) -> int:
        """
        ดำเนินการ query และคืนค่า last insert ID
        
        Args:
            query: SQL query
            params: พารามิเตอร์สำหรับ query
            
        Returns:
            int: last insert ID
        """
        with self.get_cursor() as cursor:
            cursor.execute(query, params or ())
            return cursor.lastrowid
    
    def check_connection(self) -> bool:
        """
        ตรวจสอบการเชื่อมต่อฐานข้อมูล with comprehensive health check
        
        Returns:
            bool: True ถ้าการเชื่อมต่อปกติ
        """
        try:
            with self.get_connection() as conn:
                if not conn.is_connected():
                    return False
                
                # Test with a simple query
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                result = cursor.fetchone()
                cursor.close()
                
                return result is not None
        except Exception as e:
            logging.warning(f"Connection health check failed: {str(e)}")
            return False
    
    def _perform_health_check_if_needed(self) -> None:
        """
        Perform health check if enough time has passed since last check
        """
        current_time = time.time()
        if current_time - self.last_health_check > self.health_check_interval:
            self.last_health_check = current_time
            if not self.check_connection():
                logging.warning("Health check failed, reinitializing connection pool")
                self.init_pool()
    
    def get_pool_status(self) -> Dict[str, Any]:
        """
        Get connection pool status information
        
        Returns:
            Dict: Pool status information
        """
        try:
            if not self.pool:
                return {'status': 'not_initialized'}
            
            # Note: mysql-connector-python doesn't expose pool statistics directly
            # This is a basic implementation
            return {
                'status': 'active',
                'pool_name': self.pool_name,
                'pool_size': self.pool_size,
                'last_health_check': self.last_health_check,
                'health_check_interval': self.health_check_interval
            }
        except Exception as e:
            return {'status': 'error', 'error': str(e)}
    
    def get_table_schema(self, table_name: str) -> List[Dict[str, Any]]:
        """
        ดึงโครงสร้างของตาราง
        
        Args:
            table_name: ชื่อตาราง
            
        Returns:
            List: รายการคอลัมน์และชนิดข้อมูล
        """
        query = """
            SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, IS_NULLABLE, COLUMN_KEY
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
            ORDER BY ORDINAL_POSITION
        """
        return self.execute_query(query, (table_name,), dictionary=True)
    
    def table_exists(self, table_name: str) -> bool:
        """
        ตรวจสอบว่าตารางมีอยู่หรือไม่
        
        Args:
            table_name: ชื่อตาราง
            
        Returns:
            bool: True ถ้าตารางมีอยู่
        """
        query = """
            SELECT COUNT(*) 
            FROM information_schema.tables 
            WHERE table_schema = DATABASE()
            AND table_name = %s
        """
        result = self.execute_query(query, (table_name,))
        return result[0][0] > 0
    
    def get_connection_metrics(self) -> Dict[str, Any]:
        """
        Get database connection and performance metrics
        
        Returns:
            Dict: Connection and performance metrics
        """
        metrics = {
            'connection_pool': self.get_pool_status(),
            'database_status': {},
            'performance_metrics': {}
        }
        
        try:
            # Get database status variables
            status_query = """
                SHOW STATUS WHERE Variable_name IN (
                    'Connections', 'Max_used_connections', 'Threads_connected',
                    'Threads_running', 'Queries', 'Slow_queries', 'Uptime'
                )
            """
            result = self.execute_query(status_query, dictionary=True)
            
            for row in result:
                if isinstance(row, dict):
                    metrics['database_status'][row['Variable_name']] = row['Value']
                else:
                    # Handle tuple format
                    metrics['database_status'][row[0]] = row[1]
            
            # Get database size information
            size_query = """
                SELECT 
                    table_schema as 'Database',
                    ROUND(SUM(data_length + index_length) / 1024 / 1024, 2) as 'Size_MB'
                FROM information_schema.tables 
                WHERE table_schema = DATABASE()
                GROUP BY table_schema
            """
            size_result = self.execute_query(size_query)
            if size_result:
                metrics['performance_metrics']['database_size_mb'] = size_result[0][1]
            
        except Exception as e:
            logging.warning(f"Could not collect database metrics: {str(e)}")
            metrics['error'] = str(e)
        
        return metrics
