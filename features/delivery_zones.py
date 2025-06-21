import json
import os
import requests # تم إضافة المكتبة لعمل طلبات HTTP
import time     # تم إضافة المكتبة لاستخدام الوقت في الـ Cache
import logging  # تم استخدامها لتسجيل رسائل التتبع والأخطاء

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes 

# تم تعريف مسار GitHub Raw URL للملف
GITHUB_ZONES_RAW_URL = "https://raw.githubusercontent.com/Ahmedsami2hvuv/TSTTALABAT/refs/heads/main/data/delivery_zones.json"

# إعداد الـ logging لهذا الملف (للتتبع)
logger = logging.getLogger(__name__)

# ذاكرة تخزين مؤقتة للمناطق
_zones_cache = None
_last_load_time = 0
_CACHE_LIFETIME_SECONDS = 300 # صلاحية الذاكرة المؤقتة: 5 دقائق (300 ثانية)

# دالة لتحميل بيانات المناطق. ستحاول التحميل من GitHub، وتستخدم ذاكرة مؤقتة.
def load_zones():
    global _zones_cache, _last_load_time

    # التحقق من صلاحية الذاكرة المؤقتة: إذا كانت البيانات موجودة وصالحة، ارجعها مباشرة.
    if _zones_cache is not None and (time.time() - _last_load_time) < _CACHE_LIFETIME_SECONDS:
        logger.info("Using cached zones data.")
        return _zones_cache

    logger.info("Attempting to load zones from GitHub.")
    try:
        response = requests.get(GITHUB_ZONES_RAW_URL)
        response.raise_for_status() # تثير خطأ إذا كان الرد HTTP غير ناجح (مثل 404 أو 500)
        zones_data = response.json() # تحويل الرد إلى JSON
        
        # تحديث الذاكرة المؤقتة بالبيانات الجديدة
        _zones_cache = zones_data
        _last_load_time = time.time()
        logger.info(f"Successfully loaded zones from GitHub. Cache updated at {time.time()}")
        return zones_data
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching zones from GitHub URL: {e}")
        # إذا فشل جلب البيانات من GitHub، ارجع البيانات من الذاكرة المؤقتة إذا كانت موجودة (كخيار احتياطي).
        if _zones_cache is not None:
            logger.warning("Failed to fetch new zones, returning cached data.")
            return _zones_cache
        logger.error("No cached zones data available. Returning empty zones.")
        return {} # ارجع قاموس فارغ إذا فشل الجلب ولا توجد بيانات مخزنة مؤقتًا.
    except json.JSONDecodeError as e:
        # في حالة أن الرد من GitHub لم يكن بصيغة JSON صحيحة.
        logger.error(f"Error decoding JSON from GitHub response: {e}. Response text (partial): {response.text[:200]}...")
        if _zones_cache is not None:
            logger.warning("Failed to decode new zones, returning cached data.")
            return _zones_cache
        logger.error("No cached zones data available. Returning empty zones.")
        return {}

# دالة save_zones تم حذفها لأن التعديل سيكون يدوياً على GitHub.

# دالة لعرض قائمة المناطق الحالية فقط (بدون أزرار إدارة بما أن التعديل يدوياً).
async def list_zones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    zones = load_zones() # تحميل المناطق الحالية من GitHub أو الذاكرة المؤقتة

    if not zones:
        text = "لا توجد مناطق مسجلة حالياً."
    else:
        text = "📍 المناطق الحالية وسعر التوصيل:\n\n"
        for name, price in zones.items():
            text += f"▫️ {name} — {price} دينار\n"
            
    # لا توجد أزرار إدارة (إضافة/حذف) هنا، لأن التعديل سيكون يدوياً على GitHub.
    reply_markup = None 

    # محاولة تعديل الرسالة السابقة إذا كانت موجودة (خاصة بالكولباك)، وإلا إرسال رسالة جديدة.
    if update.callback_query and update.callback_query.message:
        try:
            await update.callback_query.message.edit_text(text, reply_markup=reply_markup)
        except Exception:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=reply_markup)
    elif update.message:
        await update.message.reply_text(text, reply_markup=reply_markup)

# دوال ask_zone_name, handle_zone_edit, و add_zones_bulk
# تم حذفها من هذا الملف لأن التعديل سيكون يدوياً على GitHub، ولن تتم هذه العمليات من خلال البوت.

# دالة لاستخراج سعر التوصيل من سطر عنوان الطلب.
def get_delivery_price(order_title_line):
    zones = load_zones() # تحميل المناطق الحالية من GitHub
    for zone_name, price in zones.items():
        if zone_name in order_title_line:
            return price
    return 0
