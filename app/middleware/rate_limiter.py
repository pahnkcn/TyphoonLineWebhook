"""
โมดูลการจำกัดอัตราการใช้งาน (Rate Limiting)
ช่วยป้องกันการใช้งานบริการมากเกินไปและป้องกันการโจมตี DDoS
"""
import logging
import os
import ipaddress
from flask import request, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

def _should_trust_proxy_headers() -> bool:
    trust_proxy_env = os.getenv('TRUST_PROXY_HEADERS', '').strip().lower()
    if trust_proxy_env in {'1', 'true', 'yes'}:
        return True

    remote_addr = (get_remote_address() or '').strip()
    if not remote_addr:
        return False

    try:
        remote_ip = ipaddress.ip_address(remote_addr)
    except ValueError:
        return False

    return remote_ip.is_loopback or remote_ip.is_private

def _get_client_identifier():
    if _should_trust_proxy_headers():
        forwarded_for = request.headers.get('X-Forwarded-For', '')
        if forwarded_for:
            forwarded_ip = forwarded_for.split(',', 1)[0].strip()
            if forwarded_ip:
                try:
                    ipaddress.ip_address(forwarded_ip)
                    return forwarded_ip
                except ValueError:
                    pass

        real_ip = request.headers.get('X-Real-IP', '').strip()
        if real_ip:
            try:
                ipaddress.ip_address(real_ip)
                return real_ip
            except ValueError:
                pass

    return get_remote_address()

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
        key_func=_get_client_identifier,
        default_limits=["200 per day", "50 per hour"],
        strategy="fixed-window"  # ใช้อัลกอริทึมหน้าต่างคงที่
    )
    
    # ตั้งค่าตัวจัดการข้อผิดพลาด
    @app.errorhandler(429)
    def ratelimit_handler(e):
        """จัดการกรณีที่เกินขีดจำกัดการใช้งาน"""
        logging.warning(f"Rate limit exceeded: {_get_client_identifier()}")
        return jsonify({
            "error": "rate_limit_exceeded",
            "message": "ขออภัย คุณส่งคำขอมากเกินไป กรุณาลองใหม่ในภายหลัง",
            "retry_after": getattr(e, "retry_after", 60)
        }), 429
    
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
        from flask_limiter.extension import Limiter
        
        def get_identifier():
            """กำหนดตัวระบุสำหรับ rate limiting"""
            # เลือกใช้ Line User ID ถ้ามี, มิฉะนั้นใช้ IP address
            line_user_id = request.headers.get('X-Line-User-ID', '').strip()
            if line_user_id:
                return f"line:{line_user_id}"
            
            return _get_client_identifier()
        
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