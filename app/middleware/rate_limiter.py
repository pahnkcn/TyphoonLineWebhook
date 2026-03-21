"""
โมดูลการจำกัดอัตราการใช้งาน (Rate Limiting)
ช่วยป้องกันการใช้งานบริการมากเกินไปและป้องกันการโจมตี DDoS
"""
import logging
from flask import request, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

def init_limiter(app):
    """
    เริ่มต้นและตั้งค่า rate limiter สำหรับแอปพลิเคชัน Flask
    
    Args:
        app: อินสแตนซ์ Flask
        
    Returns:
        Limiter: อินสแตนซ์ของตัวจำกัดอัตราที่กำหนดค่าแล้ว
    """
    
    # เริ่มต้น Limiter
    limiter = Limiter(
        app=app,
        key_func=lambda: request.headers.get('X-Line-Signature', get_remote_address()),
        default_limits=["200 per day", "50 per hour"],
        strategy="fixed-window"  # ใช้อัลกอริทึมหน้าต่างคงที่
    )
    
    # ตั้งค่าตัวจัดการข้อผิดพลาด
    @app.errorhandler(429)
    def ratelimit_handler(e):
        """จัดการกรณีที่เกินขีดจำกัดการใช้งาน"""
        logging.warning(f"Rate limit exceeded: {get_remote_address()}")
        return jsonify({
            "error": "rate_limit_exceeded",
            "message": "ขออภัย คุณส่งคำขอมากเกินไป กรุณาลองใหม่ในภายหลัง",
            "retry_after": getattr(e, "retry_after", 60)
        }), 429
    
    # ตั้งค่าขีดจำกัดเฉพาะสำหรับเส้นทางต่างๆ
    limiter.limit("10/minute")(app.route("/callback", methods=["POST"]))
    limiter.limit("60/hour")(app.route("/health", methods=["GET"]))
    
    # ยกเว้นเส้นทางบางอย่างจากการจำกัดอัตรา
    limiter.exempt(app.route("/favicon.ico"))
    
    return limiter

def get_custom_limiter(redis_client, app=None):
    """
    สร้าง Limiter ที่ใช้ Redis เป็นข้อมูลสำรอง
    
    Args:
        redis_client: การเชื่อมต่อ Redis
        app: อินสแตนซ์ Flask (ไม่จำเป็น)
        
    Returns:
        Limiter: อินสแตนซ์ของตัวจำกัดอัตราที่ใช้ Redis
    """
    try:
        from flask_limiter.util import get_ipaddr
        from flask_limiter.extension import Limiter
        
        def get_identifier():
            """กำหนดตัวระบุสำหรับ rate limiting"""
            # เลือกใช้ Line User ID ถ้ามี, มิฉะนั้นใช้ IP address
            line_user_id = request.headers.get('X-Line-User-ID')
            if line_user_id:
                return f"line:{line_user_id}"
            
            # หรือลายเซ็น LINE ถ้ามี
            line_signature = request.headers.get('X-Line-Signature')
            if line_signature:
                return f"linesig:{line_signature}"
                
            # กลับไปใช้ตัวระบุ IP เป็นตัวเลือกสุดท้าย
            return get_ipaddr()
        
        # สร้าง Limiter ด้วย Redis
        limiter = Limiter(
            app=app,
            key_func=get_identifier,
            default_limits=["200 per day", "50 per hour"],
            storage_uri=f"redis://{redis_client.connection_pool.connection_kwargs['host']}:" 
                      f"{redis_client.connection_pool.connection_kwargs['port']}/"
                      f"{redis_client.connection_pool.connection_kwargs['db']}"
        )
        
        return limiter
    except Exception as e:
        logging.error(f"Error creating Redis-backed limiter: {str(e)}")
        # กลับไปใช้การเริ่มต้นระดับพื้นฐาน
        return init_limiter(app)