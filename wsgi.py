"""
จุดเริ่มต้น WSGI สำหรับแชทบอท 'ใจดี'
ไฟล์นี้ใช้สำหรับการเริ่มต้นแอปพลิเคชันในสภาพแวดล้อมการผลิต
ด้วยเซิร์ฟเวอร์ WSGI เช่น Gunicorn หรือ uWSGI
"""
import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv

# เพิ่มไดเรกทอรีปัจจุบันลงในเส้นทางระบบ
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# โหลดตัวแปรสภาพแวดล้อมจากไฟล์ .env
load_dotenv()

# ตั้งค่า logging พร้อมหมุนไฟล์
os.makedirs('logs', exist_ok=True)
logging.basicConfig(
    level=getattr(logging, os.getenv('LOG_LEVEL', 'INFO')),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler('logs/wsgi.log', maxBytes=5 * 1024 * 1024, backupCount=3),
        logging.StreamHandler()
    ]
)

try:
    # นำเข้าแอปและขั้นตอนการเริ่มต้น
    from app.app_main import app, init_scheduler
    
    # เริ่มต้นตัวกำหนดการเมื่อเริ่มต้นแอปพลิเคชัน
    init_scheduler()
    
    # แสดงข้อความว่าแอปพลิเคชันกำลังทำงาน
    logging.info("แอปพลิเคชันแชทบอท 'ใจดี' กำลังทำงาน (โหมดการผลิต)")
    
except Exception as e:
    logging.critical(f"เกิดข้อผิดพลาดร้ายแรงในการเริ่มต้นแอปพลิเคชัน: {str(e)}")
    raise

# สำหรับ Gunicorn
application = app

# สำหรับการรันโดยตรง (เช่น ทดสอบ)
if __name__ == "__main__":
    from waitress import serve
    
    # กำหนดพอร์ตจากตัวแปรสภาพแวดล้อมหรือใช้ค่าเริ่มต้น
    port = int(os.getenv('PORT', 5000))
    
    logging.info(f"เริ่มต้นเซิร์ฟเวอร์ Waitress บนพอร์ต {port}")
    serve(app, host='0.0.0.0', port=port)
