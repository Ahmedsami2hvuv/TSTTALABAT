import json
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, filters

# مسار ملف المناطق
# هذا المسار يستخدم التخزين الدائمي في Railway.
# os.path.join يستخدم لإنشاء مسار صحيح للنظام (سواء ويندوز أو لينكس)
# /mnt/data/ هو المسار اللي توفره Railway للتخزين الدائمي.
ZONES_FILE = os.path.join("/mnt/data/", "zones.json")


# دالة لتحميل بيانات المناطق من ملف JSON.
# إذا لم يكن الملف موجودًا أو كان فارغًا/تالفًا، ترجع قاموسًا فارغًا.
def load_zones():
    # التأكد من وجود المجلد قبل محاولة فتح الملف لتجنب الأخطاء
    os.makedirs(os.path.dirname(ZONES_FILE), exist_ok=True)
    if os.path.exists(ZONES_FILE):
        with open(ZONES_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                # في حالة وجود خطأ في قراءة JSON (الملف فارغ أو غير صحيح)، يتم إرجاع قاموس فارغ
                return {}
    return {}

# دالة لحفظ بيانات المناطق إلى ملف JSON.
def save_zones(zones):
    os.makedirs(os.path.dirname(ZONES_FILE), exist_ok=True)
    with open(ZONES_FILE, "w", encoding="utf-8") as f:
        json.dump(zones, f, ensure_ascii=False, indent=2)

# دالة لعرض قائمة المناطق الحالية مع أزرار الإدارة (إضافة، إزالة).
async def list_zones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    zones = load_zones() # تحميل المناطق الحالية

    if not zones:
        text = "لا توجد مناطق مسجلة حالياً."
    else:
        text = "📍 المناطق الحالية وسعر التوصيل:\n\n"
        for name, price in zones.items():
            text += f"▫️ {name} — {price} دينار\n"
            
    # تعريف الأزرار المضمنة (Inline Keyboard Buttons)
    buttons = [
        [InlineKeyboardButton("➕ إضافة منطقة", callback_data="add_zone")],
        [InlineKeyboardButton("➖ إزالة منطقة", callback_data="remove_zone")],
        # زر "تعديل منطقة" غير مضمن في هذا الإصدار المبسط من إدارة المناطق.
        # إذا أردت إضافته، ستحتاج إلى دالة CallbackQueryHandler ومعالج منطقي خاص به.
    ]
    reply_markup = InlineKeyboardMarkup(buttons)

    # محاولة تعديل الرسالة السابقة إذا كانت موجودة (في حالة الـ CallbackQuery)، أو إرسال رسالة جديدة.
    if update.callback_query and update.callback_query.message:
        try:
            # إذا كان التحديث ناتجًا عن CallbackQuery وكان هناك رسالة لإعادة تحريرها
            await update.callback_query.message.edit_text(text, reply_markup=reply_markup)
        except Exception:
            # في حالة فشل تعديل الرسالة (مثل أن تكون الرسالة قديمة جداً أو محذوفة)، يتم إرسال رسالة جديدة.
            await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=reply_markup)
    elif update.message:
        # إذا كان التحديث ناتجًا عن رسالة نصية مباشرة (مثل /zones أو "مناطق")
        await update.message.reply_text(text, reply_markup=reply_markup)


# دالة تبدأ محادثة لإضافة أو إزالة منطقة.
# يتم استدعاؤها عندما يضغط المستخدم على زر "إضافة منطقة" أو "إزالة منطقة".
async def ask_zone_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer() # يجب دائمًا الإجابة على الـ callback_query
    context.user_data["action"] = query.data # لتخزين نوع العملية المطلوبة (add_zone أو remove_zone)
    await query.message.reply_text("أرسل اسم المنطقة وسعرها، مثل: أبو الخصيب 3000 (للإضافة) أو اسم المنطقة فقط (للحذف).")
    return "WAITING_FOR_ZONE_ACTION_INPUT" # تُرجع هذه الحالة لتوجيه المحادثة إلى handle_zone_edit

# دالة تتعامل مع مدخلات المستخدم لإضافة أو إزالة المناطق بناءً على الـ "action" المخزن في user_data.
async def handle_zone_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    zones = load_zones() # تحميل المناطق الحالية

    action = context.user_data.get("action") # استرجاع نوع العملية المخزنة

    if action == "add_zone":
        try:
            name_parts = text.rsplit(" ", 1) # تقسيم النص من اليمين بمسافة واحدة للحصول على الاسم والسعر
            if len(name_parts) < 2:
                await update.message.reply_text("❌ صيغة غير صحيحة. أرسل مثل: البصرة 3000")
                return "WAITING_FOR_ZONE_ACTION_INPUT" # إبقاء المحادثة مفتوحة لطلب إدخال صحيح
            
            name = name_parts[0].strip()
            price_str = name_parts[1].strip()
            price = int(price_str) # تحويل السعر إلى عدد صحيح
            
            zones[name] = price # إضافة أو تحديث المنطقة في القاموس
            save_zones(zones) # حفظ المناطق المحدثة
            await update.message.reply_text(f"✅ تم إضافة المنطقة: {name} بسعر {price} دينار")
        except ValueError:
            await update.message.reply_text("❌ صيغة السعر غير صحيحة. السعر يجب أن يكون رقمًا. أرسل مثل: البصرة 3000")
            return "WAITING_FOR_ZONE_ACTION_INPUT" # إبقاء المحادثة مفتوحة
        except Exception:
            await update.message.reply_text("❌ حدث خطأ غير متوقع في الإضافة. الرجاء المحاولة مرة أخرى بصيغة صحيحة: البصرة 3000")
            return "WAITING_FOR_ZONE_ACTION_INPUT" # إبقاء المحادثة مفتوحة

    elif action == "remove_zone":
        if text in zones:
            del zones[text] # حذف المنطقة من القاموس
            save_zones(zones) # حفظ المناطق المحدثة
            await update.message.reply_text(f"🗑️ تم حذف المنطقة: {text}")
        else:
            await update.message.reply_text("❌ هذه المنطقة غير موجودة.")
            return "WAITING_FOR_ZONE_ACTION_INPUT" # إبقاء المحادثة مفتوحة لطلب إدخال صحيح
    
    # بعد إتمام العملية (سواء بنجاح أو بعد ظهور خطأ في الصيغة)، يتم مسح الـ "action" من user_data
    context.user_data.pop("action", None)
    
    # بعد الانتهاء من عملية الإضافة/الإزالة، يتم عرض قائمة المناطق المحدثة مع الأزرار.
    # يتم إنشاء كائن update وهمي إذا كان التحديث الأصلي عبارة عن CallbackQuery، لضمان عمل list_zones بشكل صحيح.
    fake_update_for_list_zones = update
    if update.callback_query:
        fake_update_for_list_zones = Update(update_id=update.update_id, message=update.callback_query.message)
    elif update.message:
        fake_update_for_list_zones = update

    await list_zones(fake_update_for_list_zones, context) # استدعاء list_zones لعرض القائمة المحدثة
    return ConversationHandler.END # إنهاء المحادثة بعد اكتمال العملية


# دالة لاستخراج سعر التوصيل من سطر عنوان الطلب (الذي قد يحتوي على اسم المنطقة).
def get_delivery_price(order_title_line):
    zones = load_zones() # تحميل المناطق الحالية
    for zone_name, price in zones.items(): # المرور على كل المناطق المسجلة
        if zone_name in order_title_line: # التحقق إذا كان اسم المنطقة موجودًا في سطر العنوان
            return price # إرجاع سعر التوصيل الخاص بهذه المنطقة
    return 0 # إذا لم يتم العثور على أي منطقة مطابقة في العنوان، يكون سعر التوصيل صفر.

# دالة لإضافة مناطق متعددة دفعة واحدة باستخدام أمر مخصص (مثلاً /add_zones_bulk).
# هذا الأمر متاح فقط لمالك البوت (OWNER_TELEGRAM_ID).
async def add_zones_bulk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    owner_id_env = os.getenv("OWNER_TELEGRAM_ID") # جلب معرف المالك من متغيرات البيئة
    
    if owner_id_env is None:
        await update.message.reply_text("عذراً، معرف المالك غير محدد في إعدادات البوت.")
        return

    try:
        owner_id = int(owner_id_env)
    except ValueError:
        await update.message.reply_text("عذراً، معرف المالك غير صحيح.")
        return

    # التحقق من أن المستخدم الذي أرسل الأمر هو مالك البوت.
    if user_id != str(owner_id):
        await update.message.reply_text("عذراً، هذا الأمر متاح للمالك فقط.")
        return

    # فصل الأمر عن محتوى الرسالة للحصول على قائمة المناطق.
    message_text_parts = update.message.text.split(' ', 1)
    if len(message_text_parts) > 1:
        message_content = message_text_parts[1].strip()
    else:
        # إذا لم يرسل المستخدم أي مناطق بعد الأمر، يتم عرض تعليمات الاستخدام.
        await update.message.reply_text(
            "الرجاء إرسال المناطق بالصيغة الصحيحة.\n"
            "مثال:\n`/add_zones_bulk البصرة 3000\nالزبير 4000\nالفاو 5000`"
        )
        return

    lines = message_content.split('\n') # تقسيم المحتوى إلى أسطر (كل سطر يمثل منطقة).
    added_count = 0 # عداد للمناطق الجديدة التي تمت إضافتها
    updated_count = 0 # عداد للمناطق التي تم تحديث سعرها
    errors = [] # قائمة لتخزين أي أخطاء تحدث أثناء معالجة الأسطر

    zones_data = load_zones() # تحميل المناطق الحالية

    for line in lines:
        line = line.strip()
        if not line: # تخطي الأسطر الفارغة
            continue
        try:
            name_parts = line.rsplit(" ", 1) # تقسيم السطر إلى اسم وسعر
            if len(name_parts) < 2:
                errors.append(f"صيغة خاطئة في السطر '{line}'. يجب أن تكون 'الاسم السعر'.")
                continue

            name = name_parts[0].strip()
            price_str = name_parts[1].strip()
            price = int(price_str) # تحويل السعر إلى عدد صحيح

            if name in zones_data: # التحقق إذا كانت المنطقة موجودة مسبقًا
                updated_count += 1
            else:
                added_count += 1
            zones_data[name] = price # إضافة أو تحديث المنطقة
        except ValueError:
            errors.append(f"سعر غير صحيح في السطر '{line}'. السعر يجب أن يكون رقمًا صحيحًا.")
        except Exception as e:
            errors.append(f"خطأ غير متوقع في السطر '{line}': {e}.")

    save_zones(zones_data) # حفظ قائمة المناطق بعد التحديث

    # بناء رسالة الرد للمستخدم
    response_text = f"✅ تم إضافة {added_count} منطقة جديدة وتحديث {updated_count} منطقة موجودة.\n"
    if errors:
        response_text += "\n⚠️ الأخطاء التي حدثت:\n" + "\n".join(errors)
    else:
        response_text += "\nكل المناطق تمت إضافتها/تحديثها بنجاح."

    await update.message.reply_text(response_text)

    # بعد الانتهاء من عملية الإضافة الجماعية، يتم عرض قائمة المناطق المحدثة مع الأزرار.
    await list_zones(update, context) # استدعاء list_zones لعرض القائمة المحدثة
