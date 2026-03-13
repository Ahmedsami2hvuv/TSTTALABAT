# -*- coding: utf-8 -*-
"""
المنطق الجديد: طلبات المتجر الإلكتروني (الرسالة اللي بدايتها «اسم الزبون: »).
يُستدعى من main عندما تكون بداية الرسالة "اسم الزبون: " أو عند وجود طلبية معلّقة في الكروب الثاني.
"""
import re
from telegram import Update
from telegram.ext import ContextTypes

from features.delivery_zones import load_zones

# معرفات الكروبات (نفس قيم main)
SITE_SOURCE_CHAT_ID = 2082135888
SITE_TARGET_CHAT_ID = 2447525875

# قائمة الطلبات المعلّقة (منطقة أو رقم)
pending_site_orders = []

# أنماط التحليل
_RE_product_line = re.compile(r"^الاسم\s*[:\：]\s*(.+)$", re.IGNORECASE)
_RE_quantity_line = re.compile(r"^الكمية\s*[:\：]\s*(\d+)", re.IGNORECASE)
_RE_price_line = re.compile(r"^السعر\s*[:\：]\s*(\d+)", re.IGNORECASE)
_STRIP_START = "\uFEFF\u200E\u200F\u202A\u202B\u202C\u202D\u202E\u200B\u200C\u200D\u2060"


def _normalize_for_site_check(text: str) -> str:
    if not text:
        return ""
    t = text.strip()
    while t and t[0] in _STRIP_START:
        t = t[1:].strip()
    return t


def is_site_order_message(text: str) -> bool:
    """
    هل الرسالة تبدأ بـ «اسم الزبون» (طلب موقع)؟
    main يستخدمها للتوجيه فقط: إذا True → الملف الجديد، إذا False → الملف القديم.
    نعتبر أي رسالة بدايتها "اسم الزبون" طلب موقع (بدون اشتراط "معلومات الطلب").
    """
    t = _normalize_for_site_check(text or "")
    if not t:
        return False
    # بداية النص أو أول سطر يبدأ بـ اسم الزبون
    if t.startswith("اسم الزبون"):
        return True
    first_line = t.split("\n")[0].strip()
    return first_line.startswith("اسم الزبون")


def _parse_site_order_message(text: str):
    """تحليل نص طلب الموقع: اسم الزبون، العنوان، النقطة الدالة، المنتجات (الاسم + الكمية + السعر فقط)."""
    if not text:
        return None
    lines = [l.strip() for l in text.splitlines()]
    customer_name = ""
    address = ""
    landmark = ""
    items = []
    total_price = None
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if re.match(r"^اسم\s*الزبون\s*[:\：]", line):
            m = re.search(r"[:\：]\s*(.+)$", line)
            if m:
                customer_name = m.group(1).strip()
            i += 1
            continue
        if re.match(r"^العنوان\s*[:\：]", line):
            m = re.search(r"[:\：]\s*(.+)$", line)
            if m:
                address = m.group(1).strip()
            i += 1
            continue
        if re.match(r"^اقرب\s*نقطة\s*دالة\s*[:\：]", line):
            m = re.search(r"[:\：]\s*(.+)$", line)
            if m:
                landmark = m.group(1).strip()
            i += 1
            continue
        if re.match(r"^ملاحظات\s*[:\：]?", line) or line in ("**", "***", "******", "معلومات الطلب", "") or re.match(r"^-+$", line):
            i += 1
            continue
        if "السعر الكلي" in line:
            try:
                rest = line.replace("السعر الكلي", "").replace("*", "").strip()
                if rest.isdigit():
                    total_price = int(rest)
                elif i + 1 < n and lines[i + 1].replace("*", "").strip().isdigit():
                    total_price = int(lines[i + 1].replace("*", "").strip())
                elif i + 2 < n and lines[i + 2].replace("*", "").strip().isdigit():
                    total_price = int(lines[i + 2].replace("*", "").strip())
            except ValueError:
                pass
            i += 1
            continue
        m_name = _RE_product_line.match(line)
        if m_name:
            raw = m_name.group(1).strip()
            if re.match(r"^اسم\s*المحل\s*[:\：]\s*", raw):
                raw = re.sub(r"^اسم\s*المحل\s*[:\：]\s*", "", raw).strip()
            elif re.match(r"^اسم\s*المحل\s+", raw):
                raw = re.sub(r"^اسم\s*المحل\s+", "", raw).strip()
            name = raw
            qty = 1
            price = 0
            if i + 1 < n:
                m_q = _RE_quantity_line.match(lines[i + 1])
                if m_q:
                    try:
                        qty = int(m_q.group(1))
                    except ValueError:
                        pass
            if i + 2 < n:
                m_p = _RE_price_line.match(lines[i + 2])
                if m_p:
                    try:
                        price = int(m_p.group(1))
                    except ValueError:
                        pass
            if name and name != "اسم المحل":
                items.append({"name": name, "qty": qty, "price": price})
            i += 1
            if i < n and _RE_quantity_line.match(lines[i]):
                i += 1
            if i < n and _RE_price_line.match(lines[i]):
                i += 1
            continue
        if _RE_quantity_line.match(line) or _RE_price_line.match(line):
            i += 1
            continue
        i += 1
    return {
        "customer_name": customer_name,
        "address": address,
        "landmark": landmark,
        "items": items,
        "total_price": total_price,
    }


def _extract_phone_number(text: str):
    cleaned = re.sub(r"[^\d]", "", text or "")
    m = re.search(r"07\d{8,10}", cleaned)
    return m.group(0) if m else None


def _is_region_in_zones(region_text: str) -> bool:
    if not (region_text or "").strip():
        return False
    zones = load_zones()
    if not zones:
        return True
    r = (region_text or "").strip()
    for zone in zones:
        if zone in r or r in zone:
            return True
    return False


def _build_rst_order_text_from_site(order_data, phone: str):
    landmark = (order_data.get("landmark") or "").strip()
    address = (order_data.get("address") or "").strip()
    title_line = landmark if landmark else (address or "طلب من الموقع")
    product_lines = []
    for item in order_data.get("items", []):
        name = item.get("name", "").strip()
        qty = item.get("qty", 1)
        if not name:
            continue
        product_lines.append(f"{name} {qty}")
    lines = [title_line, phone]
    lines.extend(product_lines)
    return "\n".join(lines)


# للويب هوك في main
build_rst_order_text_from_site = _build_rst_order_text_from_site
extract_phone_number = _extract_phone_number


async def handle_site_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    معالجة رسالة في كروب المصدر (البوت الأول).
    يُستدعى من main فقط عندما تكون الرسالة طلب موقع (بداية «اسم الزبون: »).
    """
    if not update.message or not update.message.text:
        return
    if update.effective_chat.id != SITE_SOURCE_CHAT_ID:
        return
    text = (update.message.text or "").strip()
    order_data = _parse_site_order_message(_normalize_for_site_check(text))
    if not order_data or not order_data.get("items"):
        return
    region_candidate = (order_data.get("address") or order_data.get("landmark") or "").strip()
    if not _is_region_in_zones(region_candidate):
        pending_site_orders.append({
            "order_data": order_data,
            "needs_region": True,
            "needs_phone": not bool(_extract_phone_number(text)),
        })
        await context.bot.send_message(
            chat_id=SITE_TARGET_CHAT_ID,
            text="📦 طلبية من المتجر الإلكتروني.\nالمنطقة اللي مكتوبة مو موجودة عندنا. دز اسم المنطقه الصحيحة.",
        )
        return
    phone = _extract_phone_number(text)
    if phone:
        rst_text = _build_rst_order_text_from_site(order_data, phone)
        await context.bot.send_message(chat_id=SITE_TARGET_CHAT_ID, text=rst_text)
        return
    pending_site_orders.append({
        "order_data": order_data,
        "needs_region": False,
        "needs_phone": True,
    })
    landmark = order_data.get("landmark") or order_data.get("address") or "غير معروف"
    await context.bot.send_message(
        chat_id=SITE_TARGET_CHAT_ID,
        text=(
            "📦 اجت طلبية جديدة من المتجر الإلكتروني.\n"
            f"العنوان/النقطة الدالة: {landmark}\n"
            "بس الطلب ما بي رقم زبون.\n"
            "دزوا رقم الموبايل فقط حتى أكمل الطلبية."
        ),
    )


async def handle_site_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    معالجة رسالة في كروب الهدف (RSTTALABAT).
    يُستدعى من main عندما تكون الرسالة طلب موقع أو عند وجود طلبية معلّقة.
    """
    if not update.message or not update.message.text:
        return
    if update.effective_chat.id != SITE_TARGET_CHAT_ID:
        return
    text = (update.message.text or "").strip()

    if is_site_order_message(text):
        order_data = _parse_site_order_message(_normalize_for_site_check(text))
        if order_data and order_data.get("items"):
            region_candidate = (order_data.get("address") or order_data.get("landmark") or "").strip()
            if not _is_region_in_zones(region_candidate):
                pending_site_orders.append({
                    "order_data": order_data,
                    "needs_region": True,
                    "needs_phone": not bool(_extract_phone_number(text)),
                })
                await context.bot.send_message(
                    chat_id=SITE_TARGET_CHAT_ID,
                    text="📦 تم أخذ تفاصيل الطلبية.\nالمنطقة اللي مكتوبة مو موجودة عندنا. دز اسم المنطقه الصحيحة.",
                )
                return
            phone = _extract_phone_number(text)
            if phone:
                rst_text = _build_rst_order_text_from_site(order_data, phone)
                await context.bot.send_message(chat_id=SITE_TARGET_CHAT_ID, text=rst_text)
                return
            pending_site_orders.append({
                "order_data": order_data,
                "needs_region": False,
                "needs_phone": True,
            })
            landmark = order_data.get("landmark") or order_data.get("address") or "غير معروف"
            await context.bot.send_message(
                chat_id=SITE_TARGET_CHAT_ID,
                text=(
                    "📦 تم أخذ تفاصيل الطلبية.\n"
                    f"العنوان/النقطة الدالة: {landmark}\n"
                    "دز رقم الموبايل فقط حتى أكمل الطلبية."
                ),
            )
        return

    if not pending_site_orders:
        return
    entry = pending_site_orders[0]
    if isinstance(entry, dict) and "order_data" in entry:
        order_data = entry["order_data"]
        needs_region = entry.get("needs_region", False)
        needs_phone = entry.get("needs_phone", False)
    else:
        order_data = entry
        needs_region = False
        needs_phone = True

    if needs_region:
        order_data["address"] = text.strip()
        entry["needs_region"] = False
        if not order_data.get("phone") and not _extract_phone_number(text):
            entry["needs_phone"] = True
            await context.bot.send_message(
                chat_id=SITE_TARGET_CHAT_ID,
                text="تم. دز رقم الموبايل فقط حتى أكمل الطلبية.",
            )
        else:
            phone = _extract_phone_number(text) or text.strip()
            if len(phone) >= 10:
                pending_site_orders.pop(0)
                rst_text = _build_rst_order_text_from_site(order_data, phone)
                await context.bot.send_message(chat_id=SITE_TARGET_CHAT_ID, text=rst_text)
            else:
                entry["needs_phone"] = True
                await context.bot.send_message(
                    chat_id=SITE_TARGET_CHAT_ID,
                    text="دز رقم الموبايل فقط حتى أكمل الطلبية.",
                )
        return

    if needs_phone:
        phone = _extract_phone_number(text)
        if not phone:
            return
        pending_site_orders.pop(0)
        rst_text = _build_rst_order_text_from_site(order_data, phone)
        await context.bot.send_message(chat_id=SITE_TARGET_CHAT_ID, text=rst_text)
