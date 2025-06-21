import json import os from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters

ZONES_FILE = "zones.json"

تحميل أو تهيئة قائمة المناطق

def load_zones(): if os.path.exists(ZONES_FILE): with open(ZONES_FILE, "r", encoding="utf-8") as f: return json.load(f) return {}

حفظ قائمة المناطق

def save_zones(zones): with open(ZONES_FILE, "w", encoding="utf-8") as f: json.dump(zones, f, ensure_ascii=False, indent=2)

عرض الأمر /المناطق

async def list_zones(update: Update, context: ContextTypes.DEFAULT_TYPE): zones = load_zones() if not zones: text = "لا توجد مناطق حالياً." else: text = "\n".join([f"{name} - {price} آلاف" for name, price in zones.items()])

buttons = [
    [InlineKeyboardButton("➕ إضافة منطقة", callback_data="add_zone")],
    [InlineKeyboardButton("➖ إزالة منطقة", callback_data="remove_zone")],
]
await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))

استقبال إضافة اسم منطقة

async def ask_zone_name(update: Update, context: ContextTypes.DEFAULT_TYPE): query = update.callback_query await query.answer() context.user_data["action"] = query.data await query.message.reply_text("أرسل اسم المنطقة وسعرها، مثل: أبو الخصيب 3")

استقبال الرد وإضافة أو حذف

async def handle_zone_edit(update: Update, context: ContextTypes.DEFAULT_TYPE): text = update.message.text.strip() zones = load_zones()

if context.user_data.get("action") == "add_zone":
    try:
        name, price = text.rsplit(" ", 1)
        zones[name.strip()] = int(price.strip())
        save_zones(zones)
        await update.message.reply_text(f"✅ تم إضافة المنطقة: {name.strip()} بسعر {price.strip()} آلاف")
    except:
        await update.message.reply_text("❌ صيغة غير صحيحة. أرسل مثل: البصرة 3")

elif context.user_data.get("action") == "remove_zone":
    if text in zones:
        del zones[text]
        save_zones(zones)
        await update.message.reply_text(f"🗑️ تم حذف المنطقة: {text}")
    else:
        await update.message.reply_text("❌ هذه المنطقة غير موجودة")

context.user_data.pop("action", None)

استخراج السعر من أول سطر الطلب

def get_delivery_price(order_text): zones = load_zones() lines = order_text.strip().split("\n") if lines: for zone in zones: if zone in lines[0]: return zones[zone] return 0  # إذا ما لگه أي منطقة

تضمين السعر داخل الفاتورة

نستخدم get_delivery_price داخل الدالة الرئيسية للفاتورة في ملف main.py مثل:

delivery_price = get_delivery_price(order_text)

total += delivery_price

msg += f"\n\n🚚 سعر التوصيل: {delivery_price} آلاف"

✅ تسجيل الأوامر

zone_handlers = [ CommandHandler("المناطق", list_zones), CallbackQueryHandler(ask_zone_name, pattern="^(add_zone|remove_zone)$"), MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, handle_zone_edit), ]

في main.py ضيف السطر التالي:

from features.delivery_zones import zone_handlers

for handler in zone_handlers:

app.add_handler(handler)



وأيضاً:

من تريد تستخدم السعر داخل إنشاء الفاتورة:

from features.delivery_zones import get_delivery_price

