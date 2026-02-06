"""Risk assessment utilities for the Jai Dee chatbot."""
import json
import logging
from datetime import datetime
from typing import Dict, List, Tuple, Union

from .models import RiskAssessmentResult

redis_client = None

# คำสำคัญที่ใช้ประเมินความเสี่ยงจากข้อความของผู้ใช้
# เพิ่มคำที่เกี่ยวข้องกับการใช้สารเสพติดและอาการซึมเศร้าเพิ่มเติม (รวมทั้งสแลง สะกดผิด อีโมจิ ภาษาอังกฤษ/ทับศัพท์)
RISK_KEYWORDS = {
    "high_risk": [
        # เจตนาฆ่าตัวตาย/ทำร้ายตัวเอง
        "ฆ่าตัวตาย", "ทำร้ายตัวเอง", "คิดสั้น", "ไม่อยากมีชีวิตอยู่", "อยากตาย", "อยากจบชีวิต",
        "อยากหายไป", "ไม่อยากอยู่แล้ว", "จบๆไป", "อยากจบๆ", "หายไปจากโลกนี้",
        "ไม่อยากตื่นมาอีก", "ขอตาย", "อยู่ไปก็เท่านั้น", "หมดหนทาง", "ไม่เห็นทางออก",
        # สัญญาณเร่งด่วน/อาการอันตราย
        "เกินขนาด", "overdose", "od", "OD", "OD'd", "กินยาเกิน", "กินยาเกินขนาด",
        "เลือดออก", "ชัก", "หมดสติ", "อาเจียนเป็นเลือด", "แน่นหน้าอก", "หายใจไม่ออก",
        # วลีภาษาอังกฤษ/สแลงที่สื่อถึงการทำร้ายตัวเอง
        "suicide", "kill myself", "kms", "kys", "unalive", "end it", "i want to die",
        "i can’t go on", "hope i don’t wake up",
        # อีโมจิที่มักมาคู่บริบทเสี่ยงสูง
        "🔪", "🩸", "💊", "⚰️", "🪦", "😖", "😞", "😭",
    ],
    "medium_risk": [
        # อารมณ์/อาการซึมเศร้า วิตกกังวล เหงา ท้อ
        "นอนไม่หลับ", "เครียด", "กังวล", "วิตกกังวล",
        "ซึมเศร้า", "เศร้า", "หดหู่", "เหงา", "ท้อแท้", "สิ้นหวัง",
        "หมดไฟ", "หมดแรงใจ", "ไม่ไหวแล้ว", "ร้องไห้", "น้ำตาไหล",
        "ไร้ค่า", "ไม่มีคุณค่า", "ล้มเหลว", "ไม่มีความสุข", "โดดเดี่ยว",
        "ไม่มีใครเข้าใจ", "อยากอยู่คนเดียว", "อยากหายเงียบ", "ใจสั่น",
        "เบื่อชีวิต", "คิดมาก", "ฝันร้าย", "กินไม่ลง", "เบื่ออาหาร",
        # สารเสพติด: ชื่อสาร/สแลง/ทับศัพท์/รูปแบบพิมพ์
        "อยากใช้ยา", "อยากเสพ", "อยากกลับไปเสพ", "เสี้ยน", "อยากยา", "ลงแดง",
        "ยาเสพติด", "สารเสพติด", "เสพติด", "ติดยา", "ติดเหล้า", "เลิกไม่ได้",
        "รีแล็ปส์", "กลับไปใช้", "หลุด", "หลุดเคลน", "อยากรีแลป", "อยากกลับไป",
        # แอลกอฮอล์/นิโคติน
        "เหล้า", "เบียร์", "ไวน์", "สุรา", "เมา", "เมายา", "เลิกเหล้า", "ถอนเหล้า",
        "นิโคติน", "บุหรี่", "บุหรี่ไฟฟ้า", "vape", "เวป", "pod", "พอด",
        # แอมเฟตามีน/เมทแอมเฟตามีน
        "ยาบ้า", "ยาบ่้า", "ยาบา", "ยาขับ", "ยาฟ้า", "yaba", "เมทแอมเฟตามีน",
        "เมทฯ", "เมท", "เมทแอม", "ไอซ์", "ยาไอซ์", "ice", "ชาบู", "shabu",
        # กัญชา/กระท่อม
        "กัญชา", "weed", "กัญ", "กัญฯ", "กัญชง", "คานาเบิส", "cannabis", "กัญชาอัดแท่ง",
        "บ้อง", "ดูดบ้อง", "ดูดกัญ", "สูบกัญ", "กระท่อม", "ใบกระท่อม", "น้ำท่อม",
        "ไมทราไจนีน", "kratom", "สี่คูณร้อย", "4x100", "4*100",
        # โอปิออยด์/แก้ปวดแรง
        "เฮโรอีน", "มอร์ฟีน", "โคเดอีน", "เฟนทานิล", "fentanyl", "ฝิ่น", "opiate", "opioid",
        "ทริมาดอล", "tramadol", "ออกซี่", "oxycodone", "ไฮโดรโคโดน",
        # เบนโซไดอะซีพีน/ยานอนหลับ
        "เบนโซ", "benzodiazepine", "ไดอะซีแพม", "diazepam", "อัลพราโซแลม", "alprazolam",
        "แซแนกซ์", "xanax", "โคลนาซีแพม", "clonazepam", "ยานอนหลับ", "หลับไม่ตื่น",
        # กระตุ้น/หลอนประสาท/คลับดรัก
        "โคเคน", "cocaine", "โคค", "เคตามีน", "ketamine", "เค", "ยาเค",
        "MDMA", "เอ็กซ์ตาซี", "ยาอี", "E", "ยาX", "LSD", "เห็ดเมา", "shrooms",
        "ดมกาว", "กาว", "ทินเนอร์", "สารระเหย", "แก๊สหัวเราะ", "nitrous", "NO2", "balloon",
        "GHB", "ยาเสียสาว", "กาบา", "gamma-hydroxybutyrate",
        # ยาแก้ไอ/น้ำแดง/ผสม
        "ยาแก้ไอ", "ไอทิล", "น้ำแดง", "ยาน้ำ", "น้ำท่อม+ยาแก้ไอ", "สี่คูณร้อยน้ำแดง",
        # รูปแบบการใช้/บริบทสิ่งแวดล้อม
        "เสพ", "ฉีด", "สูบ", "ดม", "เคี้ยว", "ต้มกิน", "ผสม", "ตีของ", "ตุ๋นท่อม",
        "เพื่อนชวน", "วงเพื่อน", "ปาร์ตี้", "คลับ", "บาร์", "แหล่งเก่า", "ของมา",
        "หาซื้อของ", "ของขาด", "ถอนยา", "ดีท็อกซ์", "detox", "rehab", "เข้าบำบัด",
        # อาการอยาก/ถอน/ผลข้างเคียง
        "เสี้ยนยา", "กระวนกระวาย", "หงุดหงิด", "เหงื่อแตก", "มือสั่น", "ใจสั่น",
        "ปวดเมื่อย", "ปวดกระดูก", "คลื่นไส้", "อาเจียน", "หนาวสั่น", "ขนลุก",
        "เพ้อ", "ประสาทหลอน", "หูแว่ว", "ตาฝาด", "สมาธิไม่อยู่",
        # ภาษาอังกฤษ/ทับศัพท์ทั่วไปที่บ่งชี้ความเสี่ยงกลาง
        "depressed", "depression", "anxious", "anxiety", "panic", "lonely", "hopeless",
        "relapse", "craving", "urge", "can’t sleep", "insomnia", "stress",
        # วลีไทยที่พบได้บ่อย
        "ไม่รู้จะทำยังไง", "ไม่เห็นอนาคต", "อยากหนีไปไกลๆ", "เหนื่อยใจ", "ใจพัง",
        "แบกไม่ไหว", "หัวใจมันหนัก", "ท้อจนพูดไม่ออก", "อยากพึ่งยา", "ขออะไรแรงๆ",
        # อีโมจิอารมณ์ลบ/สาร
        "😔", "😢", "😩", "🥺", "💔", "🍺", "🍻", "🍷", "🚬", "💨", "💉", "🧪",
    ],
}


# จำนวนคำความเสี่ยงระดับปานกลางที่จะยกระดับเป็นความเสี่ยงสูง
MEDIUM_RISK_THRESHOLD = 2

GENERAL_RISK_LEVEL = 'general'
LEGACY_LOW_RISK_LEVEL = 'low'
LOW_RISK_LEVELS = {GENERAL_RISK_LEVEL, LEGACY_LOW_RISK_LEVEL}


def normalize_risk_level(level: str) -> str:
    """Normalize risk level values, mapping legacy low risk to general."""
    normalized = (level or '').lower()
    if not normalized:
        return 'unknown'
    if normalized in LOW_RISK_LEVELS:
        return GENERAL_RISK_LEVEL
    return normalized



def init_risk_assessment(redis_instance) -> None:
    """Initialize Redis client for risk assessment."""
    global redis_client
    redis_client = redis_instance


def assess_risk(message: str) -> RiskAssessmentResult:
    """Assess risk level from message.

    ระดับความเสี่ยงจะถูกยกระดับเป็น "high" หากพบคำความเสี่ยงระดับสูง
    หรือพบคำความเสี่ยงระดับปานกลางหลายคำในข้อความเดียวกัน

    Returns a RiskAssessmentResult (supports tuple unpacking: level, keywords = assess_risk(msg)).
    """
    message = message.lower()
    matched_keywords: List[str] = []

    # ตรวจหาคำความเสี่ยงสูง
    for keyword in RISK_KEYWORDS["high_risk"]:
        if keyword in message:
            matched_keywords.append(keyword)

    if matched_keywords:
        return RiskAssessmentResult("high", matched_keywords)

    # ตรวจหาคำความเสี่ยงปานกลาง
    medium_matches = [kw for kw in RISK_KEYWORDS["medium_risk"] if kw in message]
    matched_keywords.extend(medium_matches)

    if len(medium_matches) >= MEDIUM_RISK_THRESHOLD:
        return RiskAssessmentResult("high", matched_keywords)
    elif medium_matches:
        return RiskAssessmentResult("medium", matched_keywords)

    return RiskAssessmentResult(GENERAL_RISK_LEVEL, matched_keywords)


def save_progress_data(user_id: str, risk_level: str, keywords: List[str]) -> None:
    """Save user progress data to Redis."""
    try:
        normalized_level = normalize_risk_level(risk_level)
        progress_data = {
            'timestamp': datetime.now().isoformat(),
            'risk_level': normalized_level,
            'keywords': keywords
        }
        redis_client.lpush(f"progress:{user_id}", json.dumps(progress_data))
        redis_client.ltrim(f"progress:{user_id}", 0, 99)
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการบันทึกความก้าวหน้า: {str(e)}")


def generate_progress_report(user_id: str) -> str:
    """Generate a progress report for the user."""
    try:
        progress_data = redis_client.lrange(f"progress:{user_id}", 0, -1)
        if not progress_data:
            return "ยังไม่มีข้อมูลความก้าวหน้า"

        data = [json.loads(item) for item in progress_data]
        for entry in data:
            entry['risk_level'] = normalize_risk_level(entry.get('risk_level'))
        risk_trends = {
            'high': sum(1 for d in data if d['risk_level'] == 'high'),
            'medium': sum(1 for d in data if d['risk_level'] == 'medium'),
            'general': sum(1 for d in data if d['risk_level'] == GENERAL_RISK_LEVEL)
        }
        report = (
            "📊 รายงานความก้าวหน้า\n\n"
            f"📅 ช่วงเวลา: {data[-1]['timestamp'][:10]} ถึง {data[0]['timestamp'][:10]}\n"
            f"📈 การประเมินความเสี่ยง:\n"
            f"▫️ ความเสี่ยงสูง: {risk_trends['high']} ครั้ง\n"
            f"▫️ ความเสี่ยงปานกลาง: {risk_trends['medium']} ครั้ง\n"
            f"▫️ ข้อความทั่วไป: {risk_trends['general']} ข้อความ\n"
        )
        return report
    except Exception as e:
        logging.error(f"เกิดข้อผิดพลาดในการสร้างรายงานความก้าวหน้า: {str(e)}")
        return "ไม่สามารถสร้างรายงานได้"
