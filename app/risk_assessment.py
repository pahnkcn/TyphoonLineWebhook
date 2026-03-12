"""Risk assessment utilities for the Jai Dee chatbot."""
import json
import logging
import re
from datetime import datetime
from typing import Dict, List, Set, Tuple, Union

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
        # สัญญาณเร่งด่วน/อาการอันตราย (ลบ "od"/"OD"/"OD'd" — false positive กับ today/good/body/code)
        "เกินขนาด", "overdose", "กินยาเกิน", "กินยาเกินขนาด",
        "เลือดออก", "ชัก", "หมดสติ", "อาเจียนเป็นเลือด", "แน่นหน้าอก", "หายใจไม่ออก",
        # วลีภาษาอังกฤษ/สแลงที่สื่อถึงการทำร้ายตัวเอง
        "suicide", "kill myself", "kms", "kys", "unalive", "end it", "i want to die",
        "i can’t go on", "hope i don’t wake up",
        # อีโมจิที่สื่อถึงอันตรายอย่างชัดเจน (ย้าย 😖😞😭💊 ไป medium เพราะใช้ทั่วไปในแชทไทย)
        "🔪", "🩸", "⚰️", "🪦",
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
        "รีแล็ปส์", "กลับไปใช้", "หลุดเคลน", "อยากรีแลป", "อยากกลับไป",
        # แอลกอฮอล์/นิโคติน (ลบ "เมา" สั้นเกินไป — เก็บ "เมายา")
        "เหล้า", "เบียร์", "ไวน์", "สุรา", "เมายา", "เลิกเหล้า", "ถอนเหล้า",
        "นิโคติน", "บุหรี่", "บุหรี่ไฟฟ้า", "vape", "เวป", "พอด",
        # แอมเฟตามีน/เมทแอมเฟตามีน
        "ยาบ้า", "ยาบ่้า", "ยาบา", "ยาขับ", "ยาฟ้า", "yaba", "เมทแอมเฟตามีน",
        "เมทฯ", "เมทแอม", "ไอซ์", "ยาไอซ์", "ชาบู", "shabu",
        # กัญชา/กระท่อม (ลบ "กัญ" สั้นเกินไป → match กัญญา, ลบ "ice" → match nice/iced)
        "กัญชา", "weed", "กัญฯ", "กัญชง", "คานาเบิส", "cannabis", "กัญชาอัดแท่ง",
        "บ้อง", "ดูดบ้อง", "ดูดกัญ", "สูบกัญ", "กระท่อม", "ใบกระท่อม", "น้ำท่อม",
        "ไมทราไจนีน", "kratom", "สี่คูณร้อย", "4x100", "4*100",
        # โอปิออยด์/แก้ปวดแรง
        "เฮโรอีน", "มอร์ฟีน", "โคเดอีน", "เฟนทานิล", "fentanyl", "ฝิ่น", "opiate", "opioid",
        "ทริมาดอล", "tramadol", "ออกซี่", "oxycodone", "ไฮโดรโคโดน",
        # เบนโซไดอะซีพีน/ยานอนหลับ
        "เบนโซ", "benzodiazepine", "ไดอะซีแพม", "diazepam", "อัลพราโซแลม", "alprazolam",
        "แซแนกซ์", "xanax", "โคลนาซีแพม", "clonazepam", "ยานอนหลับ", "หลับไม่ตื่น",
        # กระตุ้น/หลอนประสาท/คลับดรัก (ลบ "เค","E","กาว" สั้นเกินไป — ลบ uppercase MDMA/LSD/GHB/NO2 dead code หลัง .lower())
        "โคเคน", "cocaine", "โคค", "เคตามีน", "ketamine", "ยาเค",
        "mdma", "เอ็กซ์ตาซี", "ยาอี", "ยาx", "lsd", "เห็ดเมา", "shrooms",
        "ดมกาว", "ทินเนอร์", "สารระเหย", "แก๊สหัวเราะ", "nitrous", "no2", "balloon",
        "ghb", "ยาเสียสาว", "กาบา", "gamma-hydroxybutyrate",
        # ยาแก้ไอ/น้ำแดง/ผสม
        "ยาแก้ไอ", "ไอทิล", "น้ำแดง", "ยาน้ำ", "น้ำท่อม+ยาแก้ไอ", "สี่คูณร้อยน้ำแดง",
        # รูปแบบการใช้/บริบทสิ่งแวดล้อม (ลบ short keywords ที่ false positive สูง: เสพ→เสพยา, ฉีด→ฉีดยา, สูบ→สูบยา, ดม→ดมยา, ผสม→ผสมยา, บาร์→ลบ, คลับ→ลบ)
        "เสพยา", "ฉีดยา", "สูบยา", "ดมยา", "เคี้ยว", "ต้มกิน", "ผสมยา", "ตีของ", "ตุ๋นท่อม",
        "เพื่อนชวน", "วงเพื่อน", "ปาร์ตี้", "แหล่งเก่า", "ของมา",
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
        # อีโมจิอารมณ์ลบ/สาร (รวม 💊😖😞😭 ที่ย้ายมาจาก high_risk)
        "💊", "😖", "😞", "😭",
        "😔", "😢", "😩", "🥺", "💔", "🍺", "🍻", "🍷", "🚬", "💨", "💉", "🧪",
    ],
}


# จำนวนคำความเสี่ยงระดับปานกลางที่จะยกระดับเป็นความเสี่ยงสูง
MEDIUM_RISK_THRESHOLD = 2

GENERAL_RISK_LEVEL = 'general'
LEGACY_LOW_RISK_LEVEL = 'low'
LOW_RISK_LEVELS = {GENERAL_RISK_LEVEL, LEGACY_LOW_RISK_LEVEL}

# Negation prefixes — ถ้าพบก่อน keyword จะลดระดับความเสี่ยง 1 ขั้น
NEGATION_PREFIXES = [
    "ไม่ได้", "ไม่เคย", "ไม่ได้จะ", "ไม่คิดจะ", "เลิก",
    "หยุด", "ไม่ได้อยาก", "ไม่มีความคิด",
]
# จำนวนตัวอักษร lookback ก่อน keyword เพื่อเช็ค negation
_NEGATION_WINDOW = 12

# Keywords ภาษาอังกฤษสั้น (≤3 ตัวอักษร) ที่ต้องใช้ word boundary matching
_SHORT_EN_KEYWORDS: Set[str] = set()
# Regex pattern สำหรับ short English keywords (compiled lazily)
_short_en_pattern: re.Pattern = None


def _prepare_keywords():
    """Lowercase all keywords and identify short English keywords needing word boundary matching."""
    global _short_en_pattern
    for level in RISK_KEYWORDS:
        lowered = []
        seen = set()
        for kw in RISK_KEYWORDS[level]:
            lk = kw.lower()
            if lk not in seen:
                seen.add(lk)
                lowered.append(lk)
                # Flag short pure-ASCII keywords for word boundary matching
                if lk.isascii() and len(lk) <= 3 and lk.isalpha():
                    _SHORT_EN_KEYWORDS.add(lk)
        RISK_KEYWORDS[level] = lowered

    # Build a single regex for all short English keywords: \b(kms|kys|...)\b
    if _SHORT_EN_KEYWORDS:
        escaped = [re.escape(k) for k in sorted(_SHORT_EN_KEYWORDS, key=len, reverse=True)]
        _short_en_pattern = re.compile(r'\b(' + '|'.join(escaped) + r')\b', re.IGNORECASE)


_prepare_keywords()


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


def _is_negated(message: str, keyword_pos: int) -> bool:
    """Check if a keyword at the given position is preceded by a negation prefix."""
    start = max(0, keyword_pos - _NEGATION_WINDOW)
    prefix_text = message[start:keyword_pos]
    for neg in NEGATION_PREFIXES:
        if prefix_text.endswith(neg):
            return True
    return False


def _match_keyword(keyword: str, message: str) -> bool:
    """Check if keyword appears in message, using word boundary for short English keywords."""
    if keyword in _SHORT_EN_KEYWORDS:
        # Use regex word boundary for short English keywords to avoid "kms" matching "bookmarks"
        if _short_en_pattern:
            return bool(re.search(r'\b' + re.escape(keyword) + r'\b', message))
        return False
    return keyword in message


def _find_keyword_pos(keyword: str, message: str) -> int:
    """Find the position of a keyword in the message (handles word boundary keywords)."""
    if keyword in _SHORT_EN_KEYWORDS:
        m = re.search(r'\b' + re.escape(keyword) + r'\b', message)
        return m.start() if m else 0
    return message.find(keyword)


def assess_risk(message: str) -> RiskAssessmentResult:
    """Assess risk level from message.

    ระดับความเสี่ยงจะถูกยกระดับเป็น "high" หากพบคำความเสี่ยงระดับสูง
    หรือพบคำความเสี่ยงระดับปานกลางหลายคำในข้อความเดียวกัน
    ถ้าพบ negation prefix ก่อน keyword จะลดระดับ 1 ขั้น (high→medium, medium→skip)

    Returns a RiskAssessmentResult (supports tuple unpacking: level, keywords = assess_risk(msg)).
    """
    message = message.lower()
    matched_keywords: List[str] = []
    negated_high: List[str] = []

    # ตรวจหาคำความเสี่ยงสูง
    for keyword in RISK_KEYWORDS["high_risk"]:
        if _match_keyword(keyword, message):
            pos = _find_keyword_pos(keyword, message)
            if _is_negated(message, pos):
                negated_high.append(keyword)
            else:
                matched_keywords.append(keyword)

    if matched_keywords:
        return RiskAssessmentResult("high", matched_keywords)

    # Negated high keywords ลดเป็น medium
    if negated_high:
        return RiskAssessmentResult("medium", negated_high)

    # ลบคำซ้ำซ้อน (เช่น เจอ 'สารเสพติด' ไม่ต้องนับ 'เสพติด' อีก)
    def _deduplicate_matches(matches: List[str]) -> List[str]:
        # Sort by length descending, so longer words are kept
        sorted_matches = sorted(matches, key=len, reverse=True)
        deduped = []
        for match in sorted_matches:
            # Add to deduped if this match is not a substring of any already added longer match
            if not any(match in added and match != added for added in deduped):
                deduped.append(match)
        return deduped

    # ตรวจหาคำความเสี่ยงปานกลาง
    medium_matches: List[str] = []
    for keyword in RISK_KEYWORDS["medium_risk"]:
        if _match_keyword(keyword, message):
            pos = _find_keyword_pos(keyword, message)
            if not _is_negated(message, pos):
                medium_matches.append(keyword)
    
    # หักลบคำที่ซ้อนกันเองก่อนเข้าเงื่อนไข (เช่น 'สารเสพติด' + 'เสพติด' -> เหลือแค่ 'สารเสพติด')
    medium_matches = _deduplicate_matches(medium_matches)
    matched_keywords.extend(medium_matches)

    if len(medium_matches) >= MEDIUM_RISK_THRESHOLD:
        return RiskAssessmentResult("high", matched_keywords)
    elif medium_matches:
        return RiskAssessmentResult("medium", matched_keywords)

    return RiskAssessmentResult(GENERAL_RISK_LEVEL, matched_keywords)


# คำสำคัญสำหรับจำแนกประเภทความเสี่ยงย่อย
_SUICIDE_KEYWORDS = {
    "ฆ่าตัวตาย", "ทำร้ายตัวเอง", "คิดสั้น", "ไม่อยากมีชีวิตอยู่", "อยากตาย",
    "อยากจบชีวิต", "อยากหายไป", "ไม่อยากอยู่แล้ว", "จบๆไป", "อยากจบๆ",
    "หายไปจากโลกนี้", "ไม่อยากตื่นมาอีก", "ขอตาย", "อยู่ไปก็เท่านั้น",
    "หมดหนทาง", "ไม่เห็นทางออก", "suicide", "kill myself", "kms", "kys",
    "unalive", "end it", "i want to die", "i can't go on", "hope i don't wake up",
    "🔪", "🩸", "⚰️", "🪦",
}
_OVERDOSE_KEYWORDS = {
    "เกินขนาด", "overdose", "กินยาเกิน", "กินยาเกินขนาด",
    "เลือดออก", "ชัก", "หมดสติ", "อาเจียนเป็นเลือด", "แน่นหน้าอก", "หายใจไม่ออก",
}


def classify_risk_category(keywords: List[str]) -> str:
    """Classify matched keywords into a risk sub-category.

    Returns one of: 'suicide', 'overdose', 'substance', 'emotional'.
    Used to tailor emergency messages and admin alerts.
    """
    kw_set = set(k.lower() for k in keywords)
    if kw_set & _SUICIDE_KEYWORDS:
        return "suicide"
    if kw_set & _OVERDOSE_KEYWORDS:
        return "overdose"
    # ถ้ามี keyword เกี่ยวกับสาร/ยา → substance, ไม่งั้น → emotional
    substance_signals = {"ยา", "เสพ", "ติด", "เหล้า", "สุรา", "บุหรี่"}
    for kw in kw_set:
        for signal in substance_signals:
            if signal in kw:
                return "substance"
    return "emotional"


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
