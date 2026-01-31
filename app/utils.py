"""
โมดูลยูทิลิตี้สำหรับแชทบอท 'ใจดี'
รวมฟังก์ชันช่วยเหลือและเดโครเรเตอร์ต่างๆ
"""
import functools
import json
import re
import logging
import traceback
import time
import requests
from typing import Callable, Any, TypeVar, cast, Dict, List, Optional

# Lazy imports for summary functions to avoid circular dependencies
_grok_client = None
_config_module = None

def _get_grok_client():
    global _grok_client
    if _grok_client is None:
        from .llm import grok_client
        _grok_client = grok_client
    return _grok_client

def _get_config():
    global _config_module
    if _config_module is None:
        from . import config as cfg
        _config_module = cfg
    return _config_module

# ตัวแปรประเภทสำหรับฟังก์ชัน
F = TypeVar('F', bound=Callable[..., Any])

def safe_db_operation(func: F) -> F:
    """
    เดโครเรเตอร์สำหรับการดำเนินการฐานข้อมูลแบบปลอดภัย
    
    Args:
        func: ฟังก์ชันฐานข้อมูลที่ต้องการห่อหุ้ม
        
    Returns:
        F: ฟังก์ชันที่ห่อหุ้มแล้ว
    """
    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                error_str = str(e).lower()
                retry_count += 1
                
                # Handle specific database errors that can be retried
                if any(error_pattern in error_str for error_pattern in [
                    'connection', 'timeout', 'server has gone away', 
                    'lost connection', 'can\'t connect', 'unread result'
                ]):
                    if retry_count < max_retries:
                        wait_time = retry_count * 2  # Progressive backoff
                        logging.warning(f"Database connection error in {func.__name__} (attempt {retry_count}/{max_retries}): {str(e)}")
                        logging.info(f"Retrying in {wait_time} seconds...")
                        time.sleep(wait_time)
                        continue
                
                # For non-retryable errors or max retries reached
                logging.error(f"เกิดข้อผิดพลาดของฐานข้อมูลใน {func.__name__}: {str(e)}")
                logging.debug(traceback.format_exc())
                
                # คืนค่าเริ่มต้นที่เหมาะสมตามชื่อฟังก์ชัน
                if func.__name__.startswith('get_'):
                    # สำหรับฟังก์ชันการดึงข้อมูล
                    if 'count' in func.__name__:
                        return 0
                    return None
                # สำหรับฟังก์ชันการบันทึกหรือการอัพเดท
                return False
        
        # If we get here, all retries failed
        logging.error(f"Database operation {func.__name__} failed after {max_retries} retries")
        if func.__name__.startswith('get_'):
            if 'count' in func.__name__:
                return 0
            return None
        return False
        
    return cast(F, wrapper)

def safe_api_call(func: F) -> F:
    """
    เดโครเรเตอร์สำหรับการเรียก API แบบปลอดภัยพร้อมลอจิกการลองใหม่ที่ปรับปรุงแล้ว
    
    Args:
        func: ฟังก์ชันเรียก API ที่ต้องการห่อหุ้ม
        
    Returns:
        F: ฟังก์ชันที่ห่อหุ้มแล้ว
    """
    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        max_retries = kwargs.pop('max_retries', 3)
        retry_count = 0
        last_error = None
        
        # บันทึก log เริ่มต้นการเรียก API
        func_name = func.__name__
        logging.debug(f"เริ่มเรียก API: {func_name}")
        
        while retry_count < max_retries:
            try:
                start_time = time.time()
                response = func(*args, **kwargs)
                execution_time = time.time() - start_time
                
                # บันทึก log เวลาการทำงาน
                logging.debug(f"API {func_name} ทำงานเสร็จใน {execution_time:.2f} วินาที")
                
                # ตรวจสอบความถูกต้องของ response
                if response is None:
                    raise ValueError(f"API {func_name} ส่งคืนค่า None")
                
                return response
                
            except (requests.exceptions.Timeout, 
                    requests.exceptions.ConnectionError,
                    requests.exceptions.HTTPError) as e:
                # ข้อผิดพลาดเครือข่าย ลองใหม่ได้
                last_error = e
                retry_count += 1
                wait_time = 2 ** retry_count  # Exponential backoff
                
                logging.warning(
                    f"เกิดข้อผิดพลาดเครือข่ายใน {func_name} "
                    f"(ความพยายามที่ {retry_count}/{max_retries}): {str(e)}"
                )
                
                if retry_count < max_retries:
                    logging.info(f"รอ {wait_time} วินาทีก่อนลองอีกครั้ง...")
                    time.sleep(wait_time)
                
            except Exception as e:
                # ข้อผิดพลาดอื่นๆ ที่ไม่ใช่เครือข่าย
                error_msg = str(e)
                logging.error(f"ข้อผิดพลาดใน {func_name}: {error_msg}")
                
                # ตรวจสอบข้อผิดพลาดของ API ที่ทราบ เช่น rate limit
                if "rate limit" in error_msg.lower():
                    last_error = e
                    retry_count += 1
                    wait_time = 5 * retry_count  # Rate limit ต้องรอนานกว่า
                    
                    if retry_count < max_retries:
                        logging.warning(f"พบข้อจำกัดอัตรา API รอ {wait_time} วินาทีก่อนลองใหม่...")
                        time.sleep(wait_time)
                    continue
                
                # ข้อผิดพลาดอื่นๆ ที่ไม่ใช่ rate limit ไม่ต้องลองใหม่
                if kwargs.get('raise_error', False):
                    raise
                
                # ส่งคืนผลลัพธ์เริ่มต้นที่เหมาะสม
                return kwargs.get('default_value', None)
        
        # หลังจากลองครบตามจำนวนครั้งแล้วยังไม่สำเร็จ
        if retry_count >= max_retries:
            logging.error(f"การเรียก {func_name} ล้มเหลวหลังจากลอง {max_retries} ครั้ง")
            
            if kwargs.get('raise_error', False):
                raise last_error or ValueError(f"Failed after {max_retries} retries")
                
            # ส่งคืนผลลัพธ์เริ่มต้นที่เหมาะสม
            return kwargs.get('default_value', None)
            
    return cast(F, wrapper)

def handle_grok_api_error(e, user_id, user_message):
    """
    จัดการกับข้อผิดพลาดของ xAI Grok API และส่งข้อความที่เหมาะสมไปยังผู้ใช้
    
    Args:
        e (Exception): ข้อผิดพลาดที่เกิดขึ้น
        user_id (str): LINE user ID
        user_message (str): ข้อความของผู้ใช้
        
    Returns:
        str: ข้อความแจ้งข้อผิดพลาดที่ควรส่งให้ผู้ใช้
    """
    error_str = str(e).lower()
    
    # ข้อผิดพลาดเกี่ยวกับการเกินขีดจำกัดอัตรา (rate limit)
    if "rate limit" in error_str or "too many requests" in error_str:
        logging.warning(f"ผู้ใช้ {user_id} พบข้อจำกัดอัตรา API")
        return (
            "ขออภัยครับ ระบบกำลังมีการใช้งานสูง กรุณารอสักครู่และลองอีกครั้ง\n"
            "น้องใจดีจะรีบกลับมาช่วยเหลือคุณโดยเร็วที่สุด"
        )
    
    # ข้อผิดพลาดเกี่ยวกับการเชื่อมต่อ
    elif any(keyword in error_str for keyword in ["timeout", "connection", "network", "socket"]):
        logging.error(f"เกิดปัญหาการเชื่อมต่อสำหรับผู้ใช้ {user_id}: {error_str}")
        return (
            "ขออภัยครับ เกิดปัญหาในการเชื่อมต่อกับระบบ\n"
            "กรุณาลองอีกครั้งในอีกสักครู่ หากยังมีปัญหา โปรดติดต่อผู้ดูแลระบบ"
        )
    
    # ข้อผิดพลาดเกี่ยวกับเนื้อหาที่ถูกกรอง (content filtering)
    elif any(keyword in error_str for keyword in ["content filter", "filtered", "moderation"]):
        logging.warning(f"เนื้อหาไม่เหมาะสมจากผู้ใช้ {user_id}: {user_message[:100]}...")
        return (
            "ขออภัยครับ ระบบไม่สามารถตอบคำถามนี้ได้เนื่องจากนโยบายการใช้งาน\n"
            "กรุณาสอบถามในหัวข้อที่เกี่ยวกับการเลิกสารเสพติดและการบำบัด"
        )
    
    # ข้อผิดพลาดทั่วไป
    else:
        logging.error(f"ข้อผิดพลาด API ที่ไม่คาดคิดสำหรับผู้ใช้ {user_id}: {error_str}")
        return (
            "ขออภัยครับ เกิดข้อผิดพลาดในการประมวลผล\n"
            "น้องใจดีกำลังหาทางแก้ไข กรุณาลองใหม่อีกครั้งในอีกสักครู่"
        )

def format_timestamp(timestamp: float, format_str: str = '%Y-%m-%d %H:%M:%S') -> str:
    """
    จัดรูปแบบของเวลาในรูปแบบที่อ่านได้
    
    Args:
        timestamp (float): เวลาในรูปแบบ UNIX timestamp
        format_str (str): รูปแบบการแสดงผล
        
    Returns:
        str: สตริงของเวลาที่จัดรูปแบบแล้ว
    """
    import datetime
    return datetime.datetime.fromtimestamp(timestamp).strftime(format_str)

def validate_line_user_id(user_id: str) -> bool:
    """
    ตรวจสอบว่า LINE user ID มีรูปแบบที่ถูกต้อง
    
    Args:
        user_id (str): LINE user ID ที่ต้องการตรวจสอบ
        
    Returns:
        bool: True ถ้ารูปแบบถูกต้อง, False ถ้าไม่ถูกต้อง
    """
    import re
    # LINE user IDs มีรูปแบบตัวอักษร/ตัวเลข 33 ตัว
    return bool(re.match(r'^U[a-f0-9]{32}$', user_id))

def sanitize_input(text: str) -> str:
    """
    ทำความสะอาดข้อความอินพุตเพื่อความปลอดภัย
    
    Args:
        text (str): ข้อความอินพุต
        
    Returns:
        str: ข้อความที่ทำความสะอาดแล้ว
    """
    # ลบอักขระที่อาจเป็นอันตรายหรือใช้สำหรับการฉีด (injection)
    import re
    # ลบอักขระพิเศษที่อาจใช้สำหรับการฉีด SQL หรือถูกใช้ในการโจมตี
    cleaned = re.sub(r'[;<>&$()]', '', text)
    # จำกัดความยาว
    return cleaned[:1000]

def mask_sensitive_data(logs: Dict[str, Any]) -> Dict[str, Any]:
    """
    ปกปิดข้อมูลที่ละเอียดอ่อนในบันทึก
    
    Args:
        logs (Dict[str, Any]): ข้อมูลบันทึกต้นฉบับ
        
    Returns:
        Dict[str, Any]: ข้อมูลบันทึกที่ปกปิดแล้ว
    """
    masked_logs = logs.copy()
    sensitive_fields = ['api_key', 'password', 'secret', 'token', 'access_token']
    
    # ฟังก์ชันเพื่อปกปิดค่าในฟิลด์ที่ละเอียดอ่อน
    def mask_value(key: str, value: Any) -> Any:
        if isinstance(value, dict):
            return {k: mask_value(k, v) for k, v in value.items()}
        elif isinstance(value, list):
            return [mask_value('item', item) for item in value]
        elif isinstance(value, str) and any(field in key.lower() for field in sensitive_fields):
            if len(value) > 8:
                return value[:4] + '*' * (len(value) - 8) + value[-4:]
            else:
                return '*' * len(value)
        return value
    
    return {k: mask_value(k, v) for k, v in masked_logs.items()}

def calculate_message_priority(message: str) -> int:
    """
    คำนวณความสำคัญของข้อความตามเนื้อหา
    
    Args:
        message (str): ข้อความที่ต้องการประเมิน
        
    Returns:
        int: คะแนนความสำคัญ (1-10)
    """
    priority = 5  # ค่าความสำคัญเริ่มต้นปานกลาง
    
    # คำที่บ่งชี้ความสำคัญสูง
    high_priority_words = [
        'ฆ่าตัวตาย', 'ทำร้ายตัวเอง', 'อยากตาย', 'overdose', 'od', 'ฉุกเฉิน', 
        'ช่วยด่วน', 'เลือดออก', 'หายใจไม่ออก', 'โคม่า', 'ชัก'
    ]
    
    # คำที่บ่งชี้ความสำคัญปานกลาง
    medium_priority_words = [
        'เครียด', 'ซึมเศร้า', 'กังวล', 'ไม่อยากมีชีวิตอยู่', 'ทรมาน',
        'เสพติด', 'กลับไปเสพ', 'อยากเสพ', 'cravings'
    ]
    
    # ตรวจสอบคำสำคัญ
    message_lower = message.lower()
    
    # เพิ่มความสำคัญตามคำที่พบ
    for word in high_priority_words:
        if word in message_lower:
            priority += 3
    
    for word in medium_priority_words:
        if word in message_lower:
            priority += 1
    
    # จำกัดค่าสูงสุดที่ 10
    return min(priority, 10)

def clean_ai_response(response_text):
    """
    ทำความสะอาด response จาก AI model ก่อนส่งไปให้ผู้ใช้
    
    ลบ markup และข้อความที่ไม่ต้องการต่างๆ เช่น
    - แท็ก <|start_header_id|>assistant<|end_header_id|>
    - คำว่า "assistant" ที่ขึ้นต้นข้อความ
    
    Args:
        response_text (str): ข้อความตอบกลับจาก AI model
        
    Returns:
        str: ข้อความที่ทำความสะอาดแล้ว
    """
    if not response_text:
        return ""
    
    # เก็บข้อความต้นฉบับเพื่อตรวจสอบการเปลี่ยนแปลง
    original_text = response_text
    
    try:
        # 1. ลบแท็ก <|start_header_id|>assistant<|end_header_id|> และแท็กที่คล้ายกัน
        response_text = re.sub(r'<\|start_header_id\|>.*?<\|end_header_id\|>\s*', '', response_text)
        
        # 2. ลบรูปแบบแท็กอื่นๆ ที่อาจเกิดขึ้น
        response_text = re.sub(r'<\|.*?\|>', '', response_text)
        
        # 3. ลบคำว่า "assistant" ที่ขึ้นต้นข้อความ
        # รูปแบบต่างๆ ที่อาจเกิดขึ้น:
        # - "assistant" อยู่บรรทัดแรกเพียงอย่างเดียว
        # - "assistant" ตามด้วยบรรทัดว่าง
        # - "assistant" ขึ้นต้นข้อความโดยไม่มีบรรทัดว่าง
        response_text = re.sub(r'^assistant\s*\n+', '', response_text, flags=re.IGNORECASE)
        response_text = re.sub(r'^assistant\s*[:]\s*', '', response_text, flags=re.IGNORECASE)
        response_text = re.sub(r'^assistant\s+', '', response_text, flags=re.IGNORECASE)
        
        # 4. ลบช่องว่างและบรรทัดว่างที่มากเกินไป
        response_text = re.sub(r'\n{3,}', '\n\n', response_text)  # ลดบรรทัดว่างที่ติดกันมากกว่า 2 บรรทัด
        response_text = response_text.strip()  # ลบช่องว่างหัวท้าย
        
        # หากมีการเปลี่ยนแปลง ให้บันทึกลง log
        if original_text != response_text:
            logging.info(f"ทำความสะอาด response แล้ว: ลบแท็กและข้อความที่ไม่ต้องการออก")
            
        return response_text
        
    except Exception as e:
        # หากเกิดข้อผิดพลาด กลับไปใช้ข้อความเดิม
        logging.error(f"เกิดข้อผิดพลาดในการทำความสะอาด response: {str(e)}")
        return original_text

def check_hospital_inquiry(user_message):
    """ตรวจสอบว่าข้อความเป็นการสอบถามเกี่ยวกับที่ตั้งของสถานพยาบาลหรือไม่"""
    user_message_lower = user_message.lower()
    
    # คำที่เกี่ยวข้องกับสถานพยาบาล
    hospital_terms = ['โรงพยาบาล', 'สถานพยาบาล', 'รพ.', 'ร.พ.', 'คลินิก', 'hospital', 'clinic']
    
    # คำที่บ่งชี้การสอบถามที่ตั้ง/ตำแหน่ง
    location_inquiry_terms = [
        'ที่อยู่', 'อยู่ที่ไหน', 'ใกล้บ้าน', 'ใกล้ฉัน', 'ใกล้เคียง', 'ใกล้ที่สุด',
        'รักษาที่ไหน', 'ไปหาหมอ', 'พบแพทย์', 'ไปรักษา', 'ไปดู',
        'location', 'address', 'where', 'nearest', 'close to', 'near me'
    ]
    
    # คำถามที่บ่งชี้การสอบถามที่ตั้ง
    location_question_patterns = [
        'หา', 'มี', 'แนะนำ', 'ช่วย', 'บอก', 'ขอ', 'ต้องการ', 'อยาก',
        'find', 'recommend', 'suggest', 'help', 'need', 'want'
    ]
    
    # ตรวจสอบว่ามีคำเกี่ยวกับสถานพยาบาล
    has_hospital_term = any(term in user_message_lower for term in hospital_terms)
    
    # ตรวจสอบว่ามีคำบ่งชี้การสอบถามที่ตั้ง
    has_location_inquiry = any(term in user_message_lower for term in location_inquiry_terms)
    
    # ตรวจสอบว่ามีรูปแบบการถาม
    has_question_pattern = any(pattern in user_message_lower for pattern in location_question_patterns)
    
    # คืนค่า True เฉพาะเมื่อมีทั้งคำเกี่ยวกับสถานพยาบาล และมีการสอบถามที่ตั้ง
    # หรือมีคำเกี่ยวกับสถานพยาบาล + รูปแบบการถาม + คำบ่งชี้ที่ตั้ง
    return has_hospital_term and (has_location_inquiry or (has_question_pattern and any(word in user_message_lower for word in ['ไหน', 'ที่', 'แห่ง', 'สถานที่', 'where'])))

def get_hospital_information_message():
    """ส่งคืนข้อความข้อมูลสถานพยาบาล"""
    return (
        "📍 ข้อมูลสถานพยาบาล\n\n"
        "สำหรับข้อมูลที่อยู่และรายละเอียดของสถานพยาบาลในพื้นที่ของคุณ:\n\n"
        "📞 โทร 1413 - ศูนย์บริการสารสนเทศสุขภาพ กรมสนับสนุนบริการสุขภาพ\n"
        "• ให้บริการ 24 ชั่วโมง\n"
        "• มีข้อมูลสถานพยาบาลทั่วประเทศ\n"
        "• สามารถสอบถามบริการรักษายาเสพติดได้\n\n"
        "💡 เจ้าหน้าที่จะแนะนำสถานพยาบาลที่ใกล้บ้านคุณที่สุด และมีบริการที่เหมาะสมกับความต้องการของคุณ"
    )


def chunk_conversation_history(history, chunk_size=10):
    """
    แบ่งประวัติการสนทนาเป็นส่วนๆ (chunks) เพื่อการสรุปที่มีประสิทธิภาพ

    Args:
        history (list): ประวัติการสนทนา [(id, user_msg, bot_resp), ...]
        chunk_size (int): ขนาดของแต่ละส่วน

    Returns:
        list: รายการของส่วนประวัติการสนทนา
    """
    return [history[i:i + chunk_size] for i in range(0, len(history), chunk_size)]


@safe_api_call
def summarize_conversation_chunk(chunk):
    """
    สรุปส่วนของประวัติการสนทนา

    Args:
        chunk (list): ส่วนของประวัติการสนทนา [(id, user_msg, bot_resp), ...]

    Returns:
        str: ข้อความสรุป
    """
    if not chunk:
        return ""

    # Import config values needed for summarization at function start
    from .config import SYSTEM_MESSAGES, SUMMARY_GENERATION_CONFIG

    grok_client = _get_grok_client()
    config = _get_config()

    # สร้างข้อความสนทนา
    conversation_text = ""
    for _, msg, resp in chunk:
        conversation_text += f"ผู้ใช้: {msg}\nบอท: {resp}\n\n"

    summary_prompt = f"""
โปรดสรุปประวัติการสนทนาต่อไปนี้โดยเน้นประเด็นสำคัญตามหลัก Motivational Interviewing:

{conversation_text}

กรุณาสรุปโดยครอบคลุม:
1. **ปัญหาหลัก**: สารเสพติดที่ใช้ และปัญหาที่เกี่ยวข้อง
2. **ระยะของการเปลี่ยนแปลง**: Precontemplation / Contemplation / Preparation / Action / Maintenance
3. **Change Talk**: ความปรารถนา ความสามารถ เหตุผล ความจำเป็น ความมุ่งมั่น การลงมือ (DARN-CAT)
4. **อุปสรรคหลัก**: สิ่งที่ขัดขวางการเปลี่ยนแปลง
5. **ความคืบหน้า**: ความสำเร็จหรือการกลับไปเสพซ้ำ (ถ้ามี)

**ไม่ต้องมีคำนำหรือคำอธิบายวิธีการสรุป เริ่มต้นเนื้อหาสรุปเลยทันที**
"""

    try:
        text = grok_client.send_chat(
            messages=[
                SYSTEM_MESSAGES,
                {"role": "user", "content": summary_prompt}
            ],
            model=config.XAI_MODEL,
            **SUMMARY_GENERATION_CONFIG,
        )

        return text
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดใน summarize_conversation_chunk: {str(e)}")
        return ""


@safe_api_call
def summarize_conversation_history(history):
    """
    สรุปประวัติการสนทนาให้กระชับ โดยมีการจัดการขนาด

    Args:
        history (list): รายการประวัติการสนทนา [(id, user_msg, bot_resp), ...]

    Returns:
        str: ข้อความสรุป
    """
    if not history:
        return ""

    # Import config values needed for summarization at function start
    from .config import SYSTEM_MESSAGES, SUMMARY_GENERATION_CONFIG

    grok_client = _get_grok_client()
    config = _get_config()

    # แบ่งประวัติเป็นส่วนๆ หากมีขนาดใหญ่
    if len(history) > 20:
        # แบ่งเป็นชิ้นและสรุปแต่ละชิ้น
        chunks = chunk_conversation_history(history, chunk_size=10)
        summaries = []

        for chunk in chunks:
            chunk_summary = summarize_conversation_chunk(chunk)
            if chunk_summary:
                summaries.append(chunk_summary)

        # รวมสรุปทั้งหมด
        if summaries:
            combined_summary = "\n".join([f"• {summary}" for summary in summaries])
            return combined_summary

    # หากมีขนาดเล็ก ใช้วิธีสรุปแบบปกติ
    conversation_text = ""
    for _, msg, resp in history:
        conversation_text += f"ผู้ใช้: {msg}\nบอท: {resp}\n\n"

    summary_prompt = f"""
โปรดสรุปประวัติการสนทนาต่อไปนี้โดยเน้นประเด็นสำคัญตามหลัก Motivational Interviewing:

{conversation_text}

กรุณาสรุปโดยครอบคลุม:
1. **ปัญหาหลัก**: สารเสพติดที่ใช้ และปัญหาที่เกี่ยวข้อง
2. **ระยะของการเปลี่ยนแปลง**: Precontemplation / Contemplation / Preparation / Action / Maintenance
3. **Change Talk**: ความปรารถนา ความสามารถ เหตุผล ความจำเป็น ความมุ่งมั่น การลงมือ (DARN-CAT)
4. **อุปสรรคหลัก**: สิ่งที่ขัดขวางการเปลี่ยนแปลง
5. **ความคืบหน้า**: ความสำเร็จหรือการกลับไปเสพซ้ำ (ถ้ามี)

ให้สรุปแบบครอบคลุมประเด็นสำคัญทั้งหมด:
"""

    try:
        text = grok_client.send_chat(
            messages=[
                SYSTEM_MESSAGES,
                {"role": "user", "content": summary_prompt}
            ],
            model=config.XAI_MODEL,
            **SUMMARY_GENERATION_CONFIG,
        )

        return text
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดใน summarize_conversation_history: {str(e)}")
        return ""
