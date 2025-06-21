import json
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, filters

# مسار ملف المناطق
# هذا المسار يستخدم التخزين الدائمي في Railway.
ZONES_FILE = os.path.join("/mnt/data/", "zones.json")

# دالة لتحميل بيانات المناطق من ملف JSON.
def load_zones():
    os.makedirs(os.path.dirname(ZONES_FILE), exist_ok=True)
    if os.path.exists(ZONES_FILE):
        with open(ZONES_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}

# دالة لحفظ بيانات المناطق إلى ملف JSON.
def save_zones(zones):
    os.makedirs(os.path.dirname(ZONES_FILE), exist_ok=True)
    with open(ZONES_FILE, "w", encoding="utf-8") as f:
        json.dump(zones, f, ensure_ascii=False, indent=2)

# دالة لعرض قائمة المناطق الحالية مع أزرار الإدارة (إضافة، إزالة).
async def list_zones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    zones = load_zones()
    if not zones:
        text = "لا توجد مناطق مسجلة حالياً."
    else:
        text = "📍 المناطق الحالية وسعر التوصيل:\n\n"
        for name, price in zones.items():
            text += f"▫️ {name} — {price} دينار\n"
            
    buttons = [
        [InlineKeyboardButton("➕ إضافة منطقة", callback_data="add_zone")],
        [InlineKeyboardButton("➖ إزالة منطقة", callback_data="remove_zone")],
    ]
    reply_markup = InlineKeyboardMarkup(buttons)

    if update.callback_query and update.callback_query.message:
        try:
            await update.callback_query.message.edit_text(text, reply_markup=reply_markup)
        except Exception:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=reply_markup)
    elif update.message:
        await update.message.reply_text(text, reply_markup=reply_markup)

# دالة تبدأ محادثة لإضافة أو إزالة منطقة.
async def ask_zone_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["action"] = query.data
    await query.message.reply_text("أرسل اسم المنطقة وسعرها، مثل: أبو الخصيب 3000 (للإضافة) أو اسم المنطقة فقط (للحذف).")
    return "WAITING_FOR_ZONE_ACTION_INPUT"

# دالة تتعامل مع مدخلات المستخدم لإضافة أو إزالة المناطق.
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
    
    fake_update_for_list_zones = update
    if update.callback_query:
        fake_update_for_list_zones = Update(update_id=update.update_id, message=update.callback_query.message)
    elif update.message:
        fake_update_for_list_zones = update

    await list_zones(fake_update_for_list_zones, context)
    return ConversationHandler.END

# دالة لاستخراج سعر التوصيل من سطر عنوان الطلب.
def get_delivery_price(order_title_line):
    zones = load_zones()
    for zone_name, price in zones.items():
        if zone_name in order_title_line:
            return price
    return 0

# دالة لإضافة مناطق متعددة دفعة واحدة باستخدام أمر مخصص (/add_zones_bulk).
async def add_zones_bulk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    owner_id_env = os.getenv("OWNER_TELEGRAM_ID")
    
    if owner_id_env is None:
        await update.message.reply_text("عذراً، معرف المالك غير محدد في إعدادات البوت.")
        return

    try:
        owner_id = int(owner_id_env)
    except ValueError:
        await update.message.reply_text("عذراً، معرف المالك غير صحيح.")
        return

    if user_id != str(owner_id):
        await update.message.reply_text("عذراً، هذا الأمر متاح للمالك فقط.")
        return

    message_text_parts = update.message.text.split(' ', 1)
    if len(message_text_parts) > 1:
        message_content = message_text_parts[1].strip()
    else:
        await update.message.reply_text(
            "الرجاء إرسال المناطق بالصيغة الصحيحة.\n"
            "مثال:\n`/add_zones_bulk البصرة 3000\nالزبير 4000\nالفاو 5000`"
        )
        return

    lines = message_content.split('\n')
    added_count = 0
    updated_count = 0
    errors = []

    zones_data = load_zones()

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            name_parts = line.rsplit(" ", 1)
            if len(name_parts) < 2:
                errors.append(f"صيغة خاطئة في السطر '{line}'. يجب أن تكون 'الاسم السعر'.")
                continue

            name = name_parts[0].strip()
            price_str = name_parts[1].strip()
            price = int(price_str)

            if name in zones_data:
                updated_count += 1
            else:
                added_count += 1
            zones_data[name] = price
        except ValueError:
            errors.append(f"سعر غير صحيح في السطر '{line}'. السعر يجب أن يكون رقمًا صحيحًا.")
        except Exception as e:
            errors.append(f"خطأ غير متوقع في السطر '{line}': {e}.")

    save_zones(zones_data)

    response_text = f"✅ تم إضافة {added_count} منطقة جديدة وتحديث {updated_count} منطقة موجودة.\n"
    if errors:
        response_text += "\n⚠️ الأخطاء التي حدثت:\n" + "\n".join(errors)
    else:
        response_text += "\nكل المناطق تمت إضافتها/تحديثها بنجاح."

    await update.message.reply_text(response_text)

    await list_zones(update, context) # عرض القائمة المحدثة
