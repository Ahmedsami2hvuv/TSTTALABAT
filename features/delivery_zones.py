import json
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, filters

# مسار ملف المناطق
# تم التعديل ليتناسب مع مسار DATA_DIR الثابت في Railway
ZONES_FILE = os.path.join("/mnt/data/", "zones.json")


# تحميل أو تهيئة قائمة المناطق
def load_zones():
    # تأكد من وجود المجلد قبل محاولة فتح الملف
    os.makedirs(os.path.dirname(ZONES_FILE), exist_ok=True)
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
    os.makedirs(os.path.dirname(ZONES_FILE), exist_ok=True)
    with open(ZONES_FILE, "w", encoding="utf-8") as f:
        json.dump(zones, f, ensure_ascii=False, indent=2)

# عرض الأمر /المناطق (أو "المناطق")
async def list_zones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    zones = load_zones()
    if not zones:
        text = "لا توجد مناطق حالياً."
    else:
        text = "📍 المناطق الحالية وسعر التوصيل:\n\n"
        for name, price in zones.items():
            text += f"▫️ {name} — {price} دينار\n"
            
    buttons = [
        [InlineKeyboardButton("➕ إضافة منطقة", callback_data="add_zone")],
        [InlineKeyboardButton("➖ إزالة منطقة", callback_data="remove_zone")],
    ]
    # هنا لازم الرسالة الجديدة تجي كـ reply_text مو edit_message_text لو البوت بادي محادثة جديدة
    # لو ماكو رسالة قبل، فهذا يصير update.message.reply_text
    # لو جاية من callback، نستخدم query.edit_message_text
    # بما إنها handler عام، الأفضل تكون reply_text
    if update.message: # إذا الأمر جاي من رسالة نصية مباشرة
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    elif update.callback_query and update.callback_query.message: # إذا جاي من كولباك (مثلاً بعد عملية إضافة/حذف)
        try:
            await update.callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        except Exception as e:
            # لو الرسالة قديمة كلش أو انحذفت، ندز رسالة جديدة
            await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=InlineKeyboardMarkup(buttons))


# استقبال إضافة اسم منطقة (مدخل لـ handle_zone_edit)
async def ask_zone_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["action"] = query.data # add_zone أو remove_zone
    await query.message.reply_text("أرسل اسم المنطقة وسعرها، مثل: أبو الخصيب 3000 (للاضافة) أو اسم المنطقة فقط (للحذف).")
    return "WAITING_FOR_ZONE_ACTION_INPUT"


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
                return "WAITING_FOR_ZONE_ACTION_INPUT"
            
            name = name_parts[0].strip()
            price_str = name_parts[1].strip()
            price = int(price_str)
            
            zones[name] = price
            save_zones(zones)
            await update.message.reply_text(f"✅ تم إضافة المنطقة: {name} بسعر {price} دينار")
        except ValueError:
            await update.message.reply_text("❌ صيغة السعر غير صحيحة. السعر يجب أن يكون رقمًا. أرسل مثل: البصرة 3000")
            return "WAITING_FOR_ZONE_ACTION_INPUT"
        except Exception:
            await update.message.reply_text("❌ حدث خطأ غير متوقع في الإضافة. الرجاء المحاولة مرة أخرى بصيغة صحيحة: البصرة 3000")
            return "WAITING_FOR_ZONE_ACTION_INPUT"

    elif action == "remove_zone":
        if text in zones:
            del zones[text]
            save_zones(zones)
            await update.message.reply_text(f"🗑️ تم حذف المنطقة: {text}")
        else:
            await update.message.reply_text("❌ هذه المنطقة غير موجودة.")
            return "WAITING_FOR_ZONE_ACTION_INPUT"
    
    context.user_data.pop("action", None)
    # بعد الانتهاء من العملية بنجاح، نكرر عرض المناطق مع الأزرار
    # نمرر الـ update object لدالة list_zones
    fake_update_for_list_zones = update
    if update.callback_query:
        fake_update_for_list_zones = Update(update_id=update.update_id, message=update.callback_query.message)
    elif update.message:
        fake_update_for_list_zones = update # already a message update

    await list_zones(fake_update_for_list_zones, context) # عرض قائمة المناطق المحدثة مع الأزرار
    return ConversationHandler.END # ننهي المحادثة هنا بعد عرض المناطق


# استخراج السعر من أول سطر الطلب
def get_delivery_price(order_title_line):
    zones = load_zones()
    for zone_name, price in zones.items():
        if zone_name in order_title_line:
            return price
    return 0
