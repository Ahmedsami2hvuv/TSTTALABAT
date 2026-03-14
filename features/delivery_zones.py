# -*- coding: utf-8 -*-
"""إدارة مناطق التوصيل وأسعارها."""
import os
import json
import difflib

ZONES_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "delivery_zones.json")


def load_delivery_zones():
    """تحميل ملف المناطق وأسعار التوصيل."""
    try:
        os.makedirs(os.path.dirname(ZONES_FILE), exist_ok=True)
        if os.path.exists(ZONES_FILE):
            with open(ZONES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"Error loading delivery zones: {e}")
    return {}


def get_delivery_price(address):
    """استخراج سعر التوصيل بناءً على العنوان (أول تطابق لمنطقة في العنوان)."""
    delivery_zones = load_delivery_zones()
    for zone, price in delivery_zones.items():
        if zone in address:
            return price
    return 0


def is_zone_known(address):
    """هل العنوان يطابق أي منطقة مسجلة في قاعدة البيانات؟"""
    if not address or not address.strip():
        return False
    delivery_zones = load_delivery_zones()
    for zone in delivery_zones.keys():
        if zone in address.strip():
            return True
    return False


def get_matching_zone_name(text):
    """يدور في النص (أي سطر) ويُرجع اسم أول منطقة من قاعدة البيانات تظهر فيه. لو ما طابقت شي يرجع None."""
    if not text or not str(text).strip():
        return None
    delivery_zones = load_delivery_zones()
    for zone in delivery_zones.keys():
        if zone in text:
            return zone
    return None


def get_closest_zone_name(text, cutoff=0.45):
    """
    يقارن الكلمة مع أسماء المناطق ويرجع أقرب منطقة (استعمال قديم، لو حاب منطقة وحدة).
    """
    names = get_closest_zone_names(text, n=1, cutoff=cutoff)
    return names[0] if names else None


def get_closest_zone_names(text, n=6, cutoff=0.4):
    """
    يرجع قائمة بأسماء المناطق الأقرب للكلمة (أكثر من كلمة).
    مثلاً: بياح → [الابطاح، عوجة، ...] عشان المستخدم يختار من الأزرار.
    n: أقصى عدد مناطق، cutoff: أقل نسبة تشابه.
    """
    if not text or not str(text).strip():
        return []
    delivery_zones = load_delivery_zones()
    zone_names = list(delivery_zones.keys())
    if not zone_names:
        return []
    text_clean = text.strip()
    return difflib.get_close_matches(text_clean, zone_names, n=n, cutoff=cutoff)


async def list_zones(update, context):
    """عرض قائمة المناطق وأسعار التوصيل (أمر /zones أو كلمة مناطق)."""
    zones = load_delivery_zones()
    if not zones:
        await update.message.reply_text("ماكو مناطق مسجلة حالياً. أضف ملف data/delivery_zones.json")
        return
    lines = ["مناطق التوصيل وأسعارها:", "-----------------------------------"]
    for zone, price in zones.items():
        lines.append(f"• {zone}: {price} دينار")
    await update.message.reply_text("\n".join(lines))
