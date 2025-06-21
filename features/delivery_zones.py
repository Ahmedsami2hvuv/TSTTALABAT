import json
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters

# مسار ملف المناطق
ZONES_FILE = "zones.json"

# تحميل أو تهيئة قائمة المناطق
def load_zones():
    if os.path.exists(ZONES_FILE):
        with open(ZONES_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                # إذا الملف فارغ أو تالف، ارجع قاموس فارغ
                return {}
    return {}

# حفظ قائمة المناطق
def save_zones(zones):
    with open(ZONES_FILE, "w", encoding="utf-8") as f:
        json.dump(zones, f, ensure_ascii=False, indent=2)

# عرض الأمر /المناطق (أو "المناطق")
async def list_zones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    zones = load_zones()
    if not zones:
        text = "لا توجد مناطق حالياً."
    else:
        text = "📍 المناطق الحالية وسعر التوصيل:\n\n"
        # تأكد من أن السعر يظهر بشكل صحيح، كان عندك "آلاف" بس السعر int
        # إذا السعر المخزن هو 3 مثلاً ويقصد بي 3000، لازم تحسب هذا الشي
        # لو القصد إن السعر مخزون 3000 وتطلع 3 آلاف، فخليه 3000
        for name, price in zones.items():
            text += f"▫️ {name} — {price} دينار\n" # غيرتها لـ "دينار" حتى تكون واضحة إذا السعر كامل
            
    buttons = [
        [InlineKeyboardButton("➕ إضافة منطقة", callback_data="add_zone")],
        [InlineKeyboardButton("➖ إزالة منطقة", callback_data="remove_zone")],
        # أزرار تعديل المناطق (إذا تريد ترجعها، لازم نضيف دوالها هنا)
        # [InlineKeyboardButton("📝 تعديل منطقة", callback_data="edit_zone")] 
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))

# استقبال إضافة اسم منطقة (مدخل لـ handle_zone_edit)
async def ask_zone_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["action"] = query.data # add_zone أو remove_zone
    await query.message.reply_text("أرسل اسم المنطقة وسعرها، مثل: أبو الخصيب 3000 (للاضافة) أو اسم المنطقة فقط (للحذف).")
    # بما أن handle_zone_edit تتعامل مع الحالتين، هنا نرجع حالة تمثل انتظار الإدخال
    return "WAITING_FOR_ZONE_ACTION_INPUT" # حالة جديدة لـ ConversationHandler


# استقبال الرد وإضافة أو حذف (التعامل مع الإضافة والحذف بنفس الدالة)
async def handle_zone_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    zones = load_zones()

    action = context.user_data.get("action")

    if action == "add_zone":
        try:
            name_parts = text.rsplit(" ", 1)
            if len(name_parts) < 2:
                await update.message.reply_text("❌ صيغة غير صحيحة. أرسل مثل: البصرة 3000")
                return "WAITING_FOR_ZONE_ACTION_INPUT" # ارجع لنفس الحالة لطلب إعادة الإدخال
            
            name = name_parts[0].strip()
            price_str = name_parts[1].strip()
            price = int(price_str)
            
            zones[name] = price
            save_zones(zones)
            await update.message.reply_text(f"✅ تم إضافة المنطقة: {name} بسعر {price} دينار")
        except ValueError:
            await update.message.reply_text("❌ صيغة السعر غير صحيحة. السعر يجب أن يكون رقمًا. أرسل مثل: البصرة 3000")
            return "WAITING_FOR_ZONE_ACTION_INPUT" # ارجع لنفس الحالة
        except Exception:
            await update.message.reply_text("❌ حدث خطأ غير متوقع في الإضافة. الرجاء المحاولة مرة أخرى بصيغة صحيحة: البصرة 3000")
            return "WAITING_FOR_ZONE_ACTION_INPUT" # ارجع لنفس الحالة

    elif action == "remove_zone":
        if text in zones:
            del zones[text]
            save_zones(zones)
            await update.message.reply_text(f"🗑️ تم حذف المنطقة: {text}")
        else:
            await update.message.reply_text("❌ هذه المنطقة غير موجودة.")
            return "WAITING_FOR_ZONE_ACTION_INPUT" # ارجع لنفس الحالة لطلب إعادة الإدخال
    
    # بعد الانتهاء من العملية بنجاح، ننهي المحادثة ونمسح الـ action
    context.user_data.pop("action", None)
    return ConversationHandler.END


# استخراج السعر من أول سطر الطلب
def get_delivery_price(order_title_line):
    zones = load_zones()
    # هنا راح نبحث عن اسم المنطقة في عنوان الطلب
    for zone_name, price in zones.items():
        if zone_name in order_title_line: # إذا اسم المنطقة موجود في سطر العنوان
            return price
    return 0 # إذا ما لقى أي منطقة مطابقة، سعر التوصيل صفر


# إذا تريد تضيف أزرار التعديل والدوال الخاصة بيها (start_edit_zone, select_zone_to_edit, apply_zone_edit)
# لازم ترجعهم من الكود القديم لملف delivery_zones.py وتضيفهم هنا.
# وبعدين لازم تعدل الـ ConversationHandler بـ main.py حتى يستخدمها.

# هذا الجزء (zone_handlers) لا يُستخدم مباشرة هنا، بل يتم استيراده في main.py
# zone_handlers = [
#     CommandHandler("المناطق", list_zones),
#     CallbackQueryHandler(ask_zone_name, pattern="^(add_zone|remove_zone)$"),
#     MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, handle_zone_edit),
# ]
