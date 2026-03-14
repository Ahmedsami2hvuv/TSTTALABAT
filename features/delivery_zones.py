# -*- coding: utf-8 -*-
"""إدارة مناطق التوصيل وأسعارها."""
import os
import re
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
    n: أقصى عدد مناطق، cutoff: أقل نسبة تشابه.
    """
    if not text or not str(text).strip():
        return []
    try:
        delivery_zones = load_delivery_zones()
        zone_names = [str(k) for k in delivery_zones.keys() if k]
    except Exception:
        return []
    if not zone_names:
        return []
    text_clean = str(text).strip()
    return difflib.get_close_matches(text_clean, zone_names, n=n, cutoff=cutoff)


def get_all_close_zones_from_words(full_text, per_word_n=4, cutoff=0.4):
    """
    يقارن كل كلمة في النص بقاعدة المناطق، ويرجع كل المناطق اللي ممكن تكون قريبة من أي كلمة.
    يرجع قائمة بدون تكرار. لو صار خطأ يرجع قائمة فاضية.
    """
    pairs = get_close_zones_with_words(full_text, per_word_n=per_word_n, cutoff=cutoff)
    return [zone for zone, _ in pairs]


def get_close_zones_with_words(full_text, per_word_n=4, cutoff=0.4, max_zones_per_word=2):
    """
    يقارن كل كلمة في الرسالة بقاعدة المناطق، ويرجع قائمة (منطقة، كلمة).
    عشان نعرض: "عوجة قريبة لحوجة"، "ابطاح قريبة لبياح".
    max_zones_per_word: أقصى عدد مناطق لكل كلمة عشان ما تستأثر كلمة وحدة بكل المقترحات
    (مثلاً بياح وسمتي يملون القائمة قبل ما توصل لـ حوجة → عوجة).
    """
    if not full_text or not str(full_text).strip():
        return []
    try:
        words = re.split(r"[\s\n]+", str(full_text).strip())
        seen_zones = set()
        result = []  # [(zone, word), ...]
        for w in words:
            w = (w or "").strip()
            if len(w) < 2:
                continue
            if w.isdigit():
                continue
            if w.startswith("+") or all(c in "0123456789+" for c in w):
                continue
            word_cutoff = 0.35 if 3 <= len(w) <= 5 else cutoff
            zones = get_closest_zone_names(w, n=per_word_n, cutoff=word_cutoff)
            added_for_word = 0
            for z in zones:
                if z and z not in seen_zones and added_for_word < max_zones_per_word:
                    seen_zones.add(z)
                    result.append((z, w))
                    added_for_word += 1
        return result
    except Exception:
        return []


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
