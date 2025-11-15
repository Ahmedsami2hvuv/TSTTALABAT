import os
import json
import uuid
import time
import asyncio
import logging
import threading
from collections import Counter
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler,
    MessageHandler, CallbackQueryHandler, ConversationHandler, filters
)

# ✅ استيراد الدوال الخاصة بالمناطق من الملف الجديد
from features.delivery_zones import (
    list_zones, get_delivery_price
)

# ✅ تفعيل الـ logging للحصول على تفاصيل الأخطاء والعمليات
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ✅ مسارات التخزين داخل Railway أو Replit أو غيره
DATA_DIR = "/mnt/data/"

ORDERS_FILE = os.path.join(DATA_DIR, "orders.json")
PRICING_FILE = os.path.join(DATA_DIR, "pricing.json")
INVOICE_NUMBERS_FILE = os.path.join(DATA_DIR, "invoice_numbers.json")
DAILY_PROFIT_FILE = os.path.join(DATA_DIR, "daily_profit.json")
COUNTER_FILE = os.path.join(DATA_DIR, "invoice_counter.txt")
LAST_BUTTON_MESSAGE_FILE = os.path.join(DATA_DIR, "last_button_message.json")

# ✅ قراءة التوكن من المتغيرات البيئية (يفترض أنك ضايفه بـ Railway)
TOKEN = os.getenv("TOKEN")

# ✅ متغيرات التخزين المؤقت في الذاكرة
orders = {}
pricing = {}
invoice_numbers = {}
daily_profit = 0.0
last_button_message = {}
supplier_report_timestamps = {}

# تهيئة القفل لعمليات الحفظ
save_lock = threading.Lock()
save_timer = None
save_pending = False

# دالة تحميل JSON بشكل آمن (يمكن نقلها إلى ملف utils/data_manager لاحقاً)
def load_json_file(filepath, default_value, var_name):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    if os.path.exists(filepath):
        with open(filepath, "r") as f:
            try:
                data = json.load(f)
                logger.info(f"Loaded {var_name} from {filepath} successfully.")
                return data
            except json.JSONDecodeError:
                logger.warning(f"{filepath} is corrupted or empty, reinitializing {var_name}.")
            except Exception as e:
                logger.error(f"Error loading {filepath}: {e}, reinitializing {var_name}.")
    logger.info(f"{var_name} file not found or corrupted, initializing to default.")
    return default_value

# دالة حفظ البيانات إلى القرص (يجب أن تكون عامة ويمكن الوصول إليها)
def _save_data_to_disk_global():
    # الوصول إلى المتغيرات العالمية مباشرةً
    global orders, pricing, invoice_numbers, daily_profit, last_button_message, supplier_report_timestamps # ✅ ضفنا هنا المتغير الجديد
    with save_lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        try:
            with open(ORDERS_FILE + ".tmp", "w") as f:
                json.dump(orders, f, indent=4)
            os.replace(ORDERS_FILE + ".tmp", ORDERS_FILE)

            with open(PRICING_FILE + ".tmp", "w") as f:
                json.dump(pricing, f, indent=4)
            os.replace(PRICING_FILE + ".tmp", PRICING_FILE)

            with open(INVOICE_NUMBERS_FILE + ".tmp", "w") as f:
                json.dump(invoice_numbers, f, indent=4)
            os.replace(INVOICE_NUMBERS_FILE + ".tmp", INVOICE_NUMBERS_FILE)

            with open(DAILY_PROFIT_FILE + ".tmp", "w") as f:
                json.dump(daily_profit, f, indent=4)
            os.replace(DAILY_PROFIT_FILE + ".tmp", DAILY_PROFIT_FILE)

            with open(LAST_BUTTON_MESSAGE_FILE + ".tmp", "w") as f:
                json.dump(last_button_message, f, indent=4)
            os.replace(LAST_BUTTON_MESSAGE_FILE + ".tmp", LAST_BUTTON_MESSAGE_FILE)

            # ✅ هذا الكود الجديد لحفظ سجل أوقات التصفير
            with open(os.path.join(DATA_DIR, "supplier_report_timestamps.json") + ".tmp", "w") as f:
                json.dump(supplier_report_timestamps, f, indent=4)
            os.replace(os.path.join(DATA_DIR, "supplier_report_timestamps.json") + ".tmp", os.path.join(DATA_DIR, "supplier_report_timestamps.json"))

            logger.info("All data (global) saved to disk successfully.")
        except Exception as e:
            logger.error(f"Error saving global data to disk: {e}")

# دالة الحفظ المؤجل العامة
def schedule_save_global():
    global save_timer, save_pending
    if save_pending:
        logger.info("Save already pending, skipping new schedule.")
        return

    if save_timer is not None:
        save_timer.cancel()

    save_pending = True
    save_timer = threading.Timer(0.5, _save_data_to_disk_global)
    save_timer.start()
    logger.info("Global data save scheduled with 0.5 sec delay.")

# ✅ دالة تحميل البيانات عند بدء تشغيل البوت (تم تغيير موقعها)
def load_data():
    global orders, pricing, invoice_numbers, daily_profit, last_button_message, supplier_report_timestamps # ✅ ضفنا هنا المتغير الجديد

    os.makedirs(DATA_DIR, exist_ok=True)

    orders_temp = load_json_file(ORDERS_FILE, {}, "orders")
    orders.clear()
    orders.update({str(k): v for k, v in orders_temp.items()})

    pricing_temp = load_json_file(PRICING_FILE, {}, "pricing")
    pricing.clear()
    pricing.update({str(pk): pv for pk, pv in pricing_temp.items()})
    for oid in pricing:
        if isinstance(pricing[oid], dict):
            pricing[oid] = {str(pk): pv for pk, pv in pricing[oid].items()} # Ensure inner keys are strings too

    invoice_numbers_temp = load_json_file(INVOICE_NUMBERS_FILE, {}, "invoice_numbers")
    invoice_numbers.clear()
    invoice_numbers.update({str(k): v for k, v in invoice_numbers_temp.items()})

    daily_profit = load_json_file(DAILY_PROFIT_FILE, 0.0, "daily_profit")
    
    last_button_message_temp = load_json_file(LAST_BUTTON_MESSAGE_FILE, {}, "last_button_message")
    last_button_message.clear()
    last_button_message.update({str(k): v for k, v in last_button_message_temp.items()})

    # ✅ هذا السطر الجديد واللي بعده لتحميل سجل أوقات التصفير
    supplier_report_timestamps_temp = load_json_file(os.path.join(DATA_DIR, "supplier_report_timestamps.json"), {}, "supplier_report_timestamps")
    supplier_report_timestamps.clear()
    supplier_report_timestamps.update({str(k): v for k, v in supplier_report_timestamps_temp.items()})

    logger.info(f"Initial load complete. Orders: {len(orders)}, Pricing entries: {len(pricing)}, Daily Profit: {daily_profit}")

# تهيئة ملف عداد الفواتير
os.makedirs(DATA_DIR, exist_ok=True)
if not os.path.exists(COUNTER_FILE):
    with open(COUNTER_FILE, "w") as f:
        f.write("1")

def get_invoice_number():
    with open(COUNTER_FILE, "r") as f:
        current = int(f.read().strip())
    with open(COUNTER_FILE, "w") as f:
        f.write(str(current + 1))
    return current

# ✅ استدعاء دالة load_data() هنا، بعد تعريفها
load_data()

# حالات المحادثة
ASK_BUY, ASK_PLACES_COUNT, ASK_PRODUCT_NAME, ASK_PRODUCT_TO_DELETE, ASK_CUSTOMER_PHONE_NUMBER_FOR_DELETION, ASK_FOR_DELETION_CONFIRMATION = range(6)

# جلب التوكن ومعرف المالك من متغيرات البيئة
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_TELEGRAM_ID")) 
OWNER_PHONE_NUMBER = os.getenv("OWNER_TELEGRAM_PHONE_NUMBER", "+9647733921468")

if TOKEN is None:
    raise ValueError("TELEGRAM_BOT_TOKEN environment variable not set.")
if OWNER_ID is None:
    raise ValueError("OWNER_TELEGRAM_ID environment variable not set.")

# دالة لتنسيق الأرقام العشرية
def format_float(value):
    formatted = f"{value:g}"
    if formatted.endswith(".0"):
        return formatted[:-2]
    return formatted

# دالة لحساب مبلغ الأجرة الإضافي بناءً على عدد المحلات
def calculate_extra(places_count):
    if places_count <= 2:
        return 0
    elif places_count == 3:
        return 1
    elif places_count == 4:
        return 2
    elif places_count == 5:
        return 3
    elif places_count == 6:
        return 4
    elif places_count == 7:
        return 5
    elif places_count == 8:
        return 6
    elif places_count == 9:
        return 7
    elif places_count >= 10:
        return 8
    return 0

# دالة مساعدة لحذف الرسائل في الخلفية
async def delete_message_in_background(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    try:
        await asyncio.sleep(0.1)
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.info(f"Successfully deleted message {message_id} from chat {chat_id} in background.")
    except Exception as e:
        logger.warning(f"Could not delete message {message_id} from chat {chat_id} in background: {e}.")

# تحميل ملف المناطق واسعارها 
def load_delivery_zones():
    try:
        with open("data/delivery_zones.json", "r") as f:
            zones = json.load(f)
            return zones
    except Exception as e:
        print(f"Error loading delivery zones: {e}")
        return {}
        # استخراج سعر التوصيل بناءً على العنوان
def get_delivery_price(address):
    delivery_zones = load_delivery_zones()
    for zone, price in delivery_zones.items():
        if zone in address:
            return price
    return 0  # إذا لم يتم العثور على العنوان في المناطق

# دالة مساعدة لحفظ البيانات في الخلفية
async def save_data_in_background(context: ContextTypes.DEFAULT_TYPE):
    schedule_save_global()
    logger.info("Data save scheduled in background.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    logger.info(f"[{update.effective_chat.id}] /start command from user {user_id}. User data before clearing: {json.dumps(context.user_data.get(user_id, {}), indent=2)}")
    if user_id in context.user_data:
        context.user_data[user_id].pop("order_id", None)
        context.user_data[user_id].pop("product", None)
        context.user_data[user_id].pop("current_active_order_id", None)
        context.user_data[user_id].pop("messages_to_delete", None) 
        context.user_data[user_id].pop("buy_price", None)
        logger.info(f"Cleared order-specific user_data for user {user_id} on /start command. User data after clearing: {json.dumps(context.user_data.get(user_id, {}), indent=2)}")
    
    # ⭐⭐ زر دائم للطلبات غير المكتملة ⭐⭐
    from telegram import ReplyKeyboardMarkup
    reply_keyboard = [['الطلبات']]
    markup = ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True, input_field_placeholder='اختر "الطلبات"')
    
    await update.message.reply_text(
        "أهلاً بك يا أبا الأكبر! لإعداد طلبية، دز الطلبية كلها برسالة واحدة.\n\n*السطر الأول:* عنوان الزبون.\n*السطر الثاني:* رقم هاتف الزبون.\n*الأسطر الباقية:* كل منتج بسطر واحد.", 
        parse_mode="Markdown",
        reply_markup=markup
    )
    return ConversationHandler.END

async def receive_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    orders = context.application.bot_data['orders']
    pricing = context.application.bot_data['pricing']
    invoice_numbers = context.application.bot_data['invoice_numbers']
    last_button_message = context.application.bot_data['last_button_message']

    print("📩 تم استقبال رسالة جديدة داخل receive_order")
    try:
        logger.info(f"[{update.effective_chat.id}] Processing order from: {update.effective_user.id} - Message ID: {update.message.message_id}. User data: {json.dumps(context.user_data.get(str(update.effective_user.id), {}), indent=2)}")
        await process_order(update, context, update.message)
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in receive_order: {e}", exc_info=True)
        await update.message.reply_text("ماكدرت اعالج الطلب عاجبك لوتحاول مره ثانيه لو ادز طلب جديد ولا تصفن.")
        return ConversationHandler.END

async def edited_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    orders = context.application.bot_data['orders']
    pricing = context.application.bot_data['pricing']
    invoice_numbers = context.application.bot_data['invoice_numbers']
    last_button_message = context.application.bot_data['last_button_message']

    try:
        if not update.edited_message:
            return
        logger.info(f"[{update.effective_chat.id}] Processing edited order from: {update.effective_user.id} - Message ID: {update.edited_message.message_id}. User data: {json.dumps(context.user_data.get(str(update.effective_user.id), {}), indent=2)}")
        await process_order(update, context, update.edited_message, edited=True)
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in edited_message: {e}", exc_info=True)
        await update.edited_message.reply_text("طك بطك ماكدر اعدل تريد سوي طلب جديد.")

async def process_order(update, context, message, edited=False):
    orders = context.application.bot_data['orders']
    pricing = context.application.bot_data['pricing']
    invoice_numbers = context.application.bot_data['invoice_numbers']
    last_button_message = context.application.bot_data['last_button_message']
    
    user_id = str(message.from_user.id)
    lines = [line.strip() for line in message.text.strip().split('\n') if line.strip()]
    
    # ✅ تعديل التحقق من عدد الأسطر: الآن نتوقع 3 أسطر على الأقل (عنوان، رقم هاتف، منتجات)
    if len(lines) < 3:
        if not edited:
            await message.reply_text("باعلي تاكد انك تكتب الطلبية ك التالي اول سطر هو عنوان الزبون وثاني سطر هو رقم الزبون وراها المنتجات كل سطر بي منتج يالله فر ويلك وسوي الطلب.")
        return

    title = lines[0]
    
    # ✅ منطق جديد لمعالجة رقم الهاتف
    phone_number_raw = lines[1].strip().replace(" ", "") # إزالة المسافات
    if phone_number_raw.startswith("+964"):
        phone_number = "0" + phone_number_raw[4:] # استبدال +964 بـ 0
    else:
        phone_number = phone_number_raw.replace("+", "") # إذا ماكو +964، بس نضمن إزالة أي علامة +
    
    products = [p.strip() for p in lines[2:] if p.strip()] # ✅ المنتجات تبدأ من السطر الثالث

    if not products:
        if not edited:
            await message.reply_text("يابه لازم المنتجات ورا رقم الهاتف .")
        return

    order_id = None
    is_new_order = True 

    if edited:
        for oid, msg_info in last_button_message.items():
            if msg_info and msg_info.get("message_id") == message.message_id and str(msg_info.get("chat_id")) == str(message.chat_id):
                if oid in orders: 
                    order_id = oid
                    is_new_order = False
                    logger.info(f"Found existing order {order_id} based on message ID (edited message).")
                    break
                else:
                    logger.warning(f"Message ID {message.message_id} found in last_button_message but order {oid} is missing. Treating as new.")
                    order_id = None 
                    
    if not order_id: 
        order_id = str(uuid.uuid4())[:8]
        invoice_no = get_invoice_number() 
        # ✅ إضافة phone_number و created_at إلى قاموس الطلبية
        orders[order_id] = {
            "user_id": user_id, 
            "title": title, 
            "phone_number": phone_number, 
            "products": products, 
            "places_count": 0,
            "created_at": datetime.now(timezone.utc).isoformat() # ✅ هذا السطر الجديد
        } 
        pricing[order_id] = {p: {} for p in products}
        invoice_numbers[order_id] = invoice_no
        logger.info(f"Created new order {order_id} for user {user_id}.")
    else: 
        old_products = set(orders[order_id].get("products", []))
        new_products = set(products)
        
        orders[order_id]["title"] = title
        orders[order_id]["phone_number"] = phone_number # ✅ تحديث رقم الهاتف في الطلبية الموجودة
        orders[order_id]["products"] = products
        # اذا تم تعديل الطلبية، ما نغير تاريخ الانشاء
        
        for p in new_products:
            if p not in pricing.get(order_id, {}):
                pricing.setdefault(order_id, {})[p] = {}
        
        if order_id in pricing:
            for p in old_products - new_products:
                if p in pricing[order_id]:
                    del pricing[order_id][p]
                    logger.info(f"Removed pricing for product '{p}' from order {order_id}.")
        logger.info(f"Updated existing order {order_id}. Initiator: {user_id}.")
        
    context.application.create_task(save_data_in_background(context))
    
    # ✅ تعديل رسالة الاستلام لتضمين رقم الهاتف بالشكل الجديد
    if is_new_order:
        await message.reply_text(f"طلب : *{title}*\n(الرقم: `{phone_number}` )\n(عدد المنتجات: {len(products)})", parse_mode="Markdown")
        await show_buttons(message.chat_id, context, user_id, order_id)
    else:
        await show_buttons(message.chat_id, context, user_id, order_id, confirmation_message="دهاك حدثنه الطلب. عيني دخل الاسعار الاستاذ حدث الطلب.")
        
async def show_buttons(chat_id, context, user_id, order_id, confirmation_message=None):
    orders = context.application.bot_data['orders']
    pricing = context.application.bot_data['pricing']
    last_button_message = context.application.bot_data['last_button_message']

    try:
        if order_id not in orders:
            await context.bot.send_message(chat_id, "الطلبية مموجودة.")
            return

        order = orders[order_id]

        # 🔥 إصلاح المنتجات القديمة (string → dict)
        new_products = []
        import uuid

        for product in order["products"]:
            if isinstance(product, str):
                new_products.append({
                    "id": uuid.uuid4().hex[:8],
                    "name": product
                })
            else:
                new_products.append(product)

        order["products"] = new_products
        # 🔥 انتهى إصلاح المنتجات

        final_buttons_list = []

        completed = []
        pending = []

        # النظام الجديد للمنتجات
        for product in order["products"]:
            p_id = product["id"]
            p_name = product["name"]

            # تحقق من اكتمال التسعير
            if p_id in pricing.get(order_id, {}) and \
               "buy" in pricing[order_id][p_id] and \
               "sell" in pricing[order_id][p_id]:
                completed.append([InlineKeyboardButton(f"✅ {p_name}", callback_data=f"{order_id}|{p_id}")])
            else:
                pending.append([InlineKeyboardButton(f"{p_name}", callback_data=f"{order_id}|{p_id}")])

        final_buttons_list.extend(completed)
        final_buttons_list.extend(pending)

        # أزرار ثابتة
        final_buttons_list.append([InlineKeyboardButton("➕ إضافة منتج", callback_data=f"add_product_to_order_{order_id}")])
        final_buttons_list.append([InlineKeyboardButton("🗑️ حذف منتج", callback_data=f"delete_specific_product_{order_id}")])

        markup = InlineKeyboardMarkup(final_buttons_list)

        text = ""
        if confirmation_message:
            text += confirmation_message + "\n\n"
        text += f"دوس على منتج واكتب سعره *{order['title']}*:"

        msg_info = last_button_message.get(order_id)
        if msg_info:
            context.application.create_task(
                delete_message_in_background(context, chat_id, msg_info["message_id"])
            )

        msg = await context.bot.send_message(
            chat_id, text, reply_markup=markup, parse_mode="Markdown"
        )

        last_button_message[order_id] = {"chat_id": chat_id, "message_id": msg.message_id}

        context.application.create_task(save_data_in_background(context))

    except Exception as e:
        logger.error(f"[{chat_id}] Error in show_buttons: {e}", exc_info=True)
        await context.bot.send_message(chat_id, "خطأ بعرض الأزرار.")
        
async def product_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    orders = context.application.bot_data['orders']
    pricing = context.application.bot_data['pricing']
    last_button_message = context.application.bot_data.get('last_button_message', {})

    try:
        query = update.callback_query
        await query.answer()

        # user id كـ string عشان نستخدمه كمفتاح ثابت
        user_id = str(query.from_user.id)

        # تأكد إن user_data مهيأ
        context.user_data.setdefault(user_id, {})
        context.user_data[user_id].setdefault('messages_to_delete', [])

        logger.info(f"[{query.message.chat_id}] Product selected callback from user {user_id}: {query.data}. User data at product_selected start: {json.dumps(context.user_data.get(user_id, {}), indent=2)}")

        # إضافة رسالة الأزرار الحالية لقائمة الحذف لاحقاً
        context.user_data[user_id]['messages_to_delete'].append({
            'chat_id': query.message.chat_id,
            'message_id': query.message.message_id
        })

        # تقسيم callback_data إلى order_id و product_id (أخذ كل شيء بعد الفاصل كـ id)
        order_id, product_id = query.data.split('|', 1)

        # تحقق من وجود الطلبية
        if order_id not in orders:
            logger.warning(f"[{query.message.chat_id}] Product selected: Order ID '{order_id}' not found.")
            msg_error = await query.edit_message_text("زربت الطلبية مموجوده دديالله سوي طلب جديد.")
            # سجيل رسالة الخطأ للحذف المؤقت لو نحتاج
            context.user_data[user_id]['messages_to_delete'].append({
                'chat_id': msg_error.chat_id,
                'message_id': msg_error.message_id
            })
            return ConversationHandler.END

        # الآن نحاول نجيب المنتج داخل الطلبية
        # لأننا نعمل تحول تلقائي سابقاً، بعض المنتجات قد تكون strings (قديمة)
        product_obj = None
        for p in orders[order_id].get("products", []):
            # إذا العنصر dict وفيه id
            if isinstance(p, dict) and p.get("id") == product_id:
                product_obj = p
                break
            # إذا العنصر string (لم يتم تحويله لحد الآن) وافترضوا callback احتوى الاسم كاملاً
            if isinstance(p, str) and p == product_id:
                # نعتبر product_id هنا هو الاسم الفعلي
                # نحوله الآن dict لكي يبقى النظام موحّد
                import uuid
                new_p = {"id": uuid.uuid4().hex[:8], "name": p}
                # استبدل العنصر القديم بالقيمة الجديدة
                idx = orders[order_id]["products"].index(p)
                orders[order_id]["products"][idx] = new_p
                product_obj = new_p
                # مهم: لأن callback_data كان اسم المنتج القديم، نحتاج نضع ID جديد لهذا السياق
                product_id = new_p["id"]
                break

        if not product_obj:
            # لم نجد المنتج — ردي للمستخدم واطبع لوق للمساعدة
            logger.error(f"[{query.message.chat_id}] Product with id/name '{product_id}' not found in order {order_id}. Order products: {orders[order_id].get('products')}")
            await query.edit_message_text("هذا المنتج مموجود أو صار خلل. حاول مرة ثانية أو حمل الطلبية من جديد.")
            return ConversationHandler.END

        p_name = product_obj.get("name", str(product_id))

        # تأكد نستخدم مفاتيح ثابتة داخل user_data
        context.user_data[user_id]["order_id"] = order_id
        context.user_data[user_id]["product"] = product_id
        # مسح أي buy_price قديمة
        context.user_data[user_id].pop("buy_price", None)

        logger.info(f"[{query.message.chat_id}] Product '{p_name}' (id={product_id}) selected for order '{order_id}'. User data after product selection: {json.dumps(context.user_data.get(user_id), indent=2)}")

        # الآن نقرأ الأسعار الحالية إن وُجدت (نستخدم product_id كمفتاح في pricing)
        current_buy = pricing.get(order_id, {}).get(product_id, {}).get("buy")
        current_sell = pricing.get(order_id, {}).get(product_id, {}).get("sell")

        if current_buy is not None and current_sell is not None:
            message_prompt = f"سعر *'{p_name}'* حالياً هو شراء: {format_float(current_buy)}، بيع: {format_float(current_sell)}.\n" \
                             f"باعلي سعر الشراء الجديد بالسطر الأول، وسعر البيع بالسطر الثاني؟ (أو دز نفس الأسعار إذا ماكو تغيير)"
        else:
            message_prompt = (
                f"تمام، بيش اشتريت *'{p_name}'*؟ (بالسطر الأول)\n"
                f"وبييش راح تبيعه؟ (بالسطر الثاني)\n\n"
                f"💡 **إذا كان سعر الشراء هو نفسه سعر البيع،** اكتب الرقم مرة واحدة فقط."
            )

        msg = await query.message.reply_text(message_prompt, parse_mode="Markdown")
        # سجل رسالة الاستجابة للحذف لاحقاً
        context.user_data[user_id]['messages_to_delete'].append({
            'chat_id': msg.chat_id,
            'message_id': msg.message_id
        })

        return ASK_BUY

    except Exception as e:
        logger.error(f"product_selected error: {e}", exc_info=True)
        # رد لطيف للمستخدم
        try:
            await query.message.reply_text("ههه صار خطا باختيار المنتج. دياللة سوي طلب جديد.")
        except:
            pass
        return ConversationHandler.END
        
async def add_new_product_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer() 

    user_id = str(query.from_user.id)
    chat_id = query.message.chat_id
    order_id = query.data.replace("add_product_to_order_", "") 

    logger.info(f"[{chat_id}] Add new product button clicked for order {order_id} by user {user_id}.")

    context.user_data.setdefault(user_id, {}) 

    # حفظ الـ order_id في user_data للحالة القادمة
    context.user_data[user_id]["current_active_order_id"] = order_id
    context.user_data[user_id]["adding_new_product"] = True # علامة لتدل على أننا في عملية إضافة منتج

    # حذف رسالة الأزرار القديمة (إذا كانت موجودة)
    if query.message:
        context.application.create_task(delete_message_in_background(context, chat_id=query.message.chat_id, message_id=query.message.message_id))

    # ✅ إضافة زر الإلغاء هنا
    cancel_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ إلغاء الإضافة", callback_data=f"cancel_add_product_{order_id}")]
    ])
    await context.bot.send_message(chat_id=chat_id, text="تمام، شنو اسم المنتج الجديد اللي تريد تضيفه؟", reply_markup=cancel_keyboard)
    return ASK_PRODUCT_NAME # حالة محادثة جديدة لطلب اسم المنتج

    
async def confirm_delete_product_by_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = str(query.from_user.id)
    chat_id = query.message.chat_id

    # استخراج الـ order_id والـ product_name من الـ callback_data
    # مثلاً: "confirm_delete_product_order_123_product_بيبسي"
    data_parts = query.data.split('_')
    order_id = data_parts[3] # الجزء الرابع هو الـ order_id
    product_name_to_delete = "_".join(data_parts[4:]) # اسم المنتج ممكن يكون بأكثر من كلمة، ناخذه من الجزء الرابع للنهاية

    logger.info(f"[{chat_id}] Product '{product_name_to_delete}' confirmed for deletion from order {order_id} by user {user_id}.")

    if order_id not in orders:
        logger.warning(f"[{chat_id}] Order {order_id} not found when trying to delete product {product_name_to_delete}.")
        await context.bot.send_message(chat_id=chat_id, text="ترا الطلب مموجود حتى امسح منه منتج. سوي طلب جديد.")
        return ConversationHandler.END

    order = orders[order_id]

    if product_name_to_delete in order["products"]:
        order["products"].remove(product_name_to_delete) # حذف المنتج من قائمة المنتجات بالطلبية

        # حذف سعر المنتج من الـ pricing (إذا كان موجود)
        if order_id in pricing and product_name_to_delete in pricing[order_id]:
            del pricing[order_id][product_name_to_delete]
            logger.info(f"[{chat_id}] Deleted pricing for product '{product_name_to_delete}' from order {order_id}.")

        logger.info(f"[{chat_id}] Product '{product_name_to_delete}' deleted from order {order_id}.")
        await context.bot.send_message(chat_id=chat_id, text=f"تم حذف المنتج '{product_name_to_delete}' من الطلبية بنجاح.")
        context.application.create_task(save_data_in_background(context)) # حفظ البيانات بعد حذف المنتج
    else:
        await context.bot.send_message(chat_id=chat_id, text=f"ترا المنتج '{product_name_to_delete}' مو موجود بالطلبية أصلاً. تأكد من الاسم.")

    # نرجع نعرض الأزرار المحدثة
    await show_buttons(chat_id, context, user_id, order_id) 
    return ConversationHandler.END

async def cancel_add_product_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = str(query.from_user.id)
    chat_id = query.message.chat_id
    order_id = query.data.replace("cancel_add_product_", "")

    logger.info(f"[{chat_id}] Cancel add product button clicked for order {order_id} by user {user_id}.")

    # حذف رسالة الأزرار القديمة (إذا كانت موجودة)
    if query.message:
        context.application.create_task(delete_message_in_background(context, chat_id=query.message.chat_id, message_id=query.message.message_id))

    await context.bot.send_message(chat_id=chat_id, text="تم إلغاء عملية إضافة منتج جديد.")
    # نرجع نعرض الأزرار الأصلية
    await show_buttons(chat_id, context, user_id, order_id)
    return ConversationHandler.END

async def delete_product_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = str(query.from_user.id)
    order_id = query.data.replace("delete_specific_product_", "")

    if order_id not in orders:
        await query.message.reply_text("ماكو طلب.")
        return

    order = orders[order_id]

    buttons = []
    for product in order["products"]:
        buttons.append([InlineKeyboardButton(product["name"], callback_data=f"delprod_{order_id}_{product['id']}")])

    buttons.append([InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel_delete_product_{order_id}")])
    markup = InlineKeyboardMarkup(buttons)

    await query.message.reply_text("اختر المنتج الذي تريد حذفه:", reply_markup=markup)
    return ConversationHandler.END


async def delete_specific_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, order_id, p_id = query.data.split("_", 2)

    if order_id not in orders:
        await query.message.reply_text("ماكو طلب.")
        return

    order = orders[order_id]

    order["products"] = [p for p in order["products"] if p["id"] != p_id]

    if order_id in pricing and p_id in pricing[order_id]:
        del pricing[order_id][p_id]

    await query.message.reply_text("تم حذف المنتج.")
    await show_buttons(query.message.chat_id, context, str(query.from_user.id), order_id)
    

async def cancel_delete_product_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = str(query.from_user.id)
    chat_id = query.message.chat_id
    order_id = query.data.replace("cancel_delete_product_", "")

    logger.info(f"[{chat_id}] Cancel delete product button clicked for order {order_id} by user {user_id}.")

    # حذف رسالة الأزرار القديمة (إذا كانت موجودة)
    if query.message:
        context.application.create_task(delete_message_in_background(context, chat_id=query.message.chat_id, message_id=query.message.message_id))

    await context.bot.send_message(chat_id=chat_id, text="تم إلغاء عملية مسح المنتج.")
    # نرجع نعرض الأزرار الأصلية
    await show_buttons(chat_id, context, user_id, order_id)
    return ConversationHandler.END

async def receive_buy_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    استلام سعر الشراء وسعر البيع لمنتج معين من المجهز — نسخة مرنة تدعم التحويل من مفاتيح الأسماء القديمة إلى IDs.
    يقبل:
    - سطرين منفصلين (سطر للشراء، سطر للبيع).
    - سطر واحد (يُعتبر شراء=بيع).
    - القيمة صفر (0) لتسجيل المنتج كغير متوفر.
    """
    user_id = str(update.message.from_user.id)
    chat_id = update.effective_chat.id

    orders = context.application.bot_data['orders']
    pricing = context.application.bot_data['pricing']

    try:
        # حاول تحييد أي رسائل قديمة (لو عندك دالة delete_previous_messages)
        try:
            await delete_previous_messages(context, user_id)
        except Exception:
            pass

        # تأكد إن user_data مهيأ
        context.user_data.setdefault(user_id, {})
        context.user_data[user_id].setdefault('messages_to_delete', [])

        order_id = context.user_data[user_id].get("order_id")
        product_ref = context.user_data[user_id].get("product")  # هذا ممكن يكون product_id أو (نادر) اسم قديم

        if not order_id or order_id not in orders:
            await update.message.reply_text("❌ لم يتم تحديد طلبية أو الطلبية قديمة. أرسل الطلب من جديد.")
            return ConversationHandler.END

        if not product_ref:
            await update.message.reply_text("❌ لم يتم تحديد المنتج. اضغط على اسم المنتج من الأزرار أولاً.")
            return ConversationHandler.END

        # حفظ رسالة المستخدم للحذف لاحقاً
        context.user_data[user_id]['messages_to_delete'].append({
            'chat_id': update.message.chat_id,
            'message_id': update.message.message_id
        })

        # قراءة النص المدخل وتحليله (سطر واحد أو سطرين)
        lines = [line.strip() for line in update.message.text.split('\n') if line.strip()]
        buy_price_str = None
        sell_price_str = None

        if len(lines) == 2:
            buy_price_str, sell_price_str = lines[0], lines[1]
        elif len(lines) == 1:
            parts = [p.strip() for p in lines[0].split() if p.strip()]
            if len(parts) == 2:
                buy_price_str, sell_price_str = parts[0], parts[1]
            elif len(parts) == 1:
                buy_price_str = sell_price_str = parts[0]

        if buy_price_str is None or sell_price_str is None:
            msg_error = await update.message.reply_text("😒 دخل سعر الشراء بالسطر الأول وسعر البيع بالسطر الثاني، أو قيمة واحدة إذا متساويين.")
            context.user_data[user_id]['messages_to_delete'].append({'chat_id': msg_error.chat_id, 'message_id': msg_error.message_id})
            return ASK_BUY

        try:
            buy_price = float(buy_price_str)
            sell_price = float(sell_price_str)
            if buy_price < 0 or sell_price < 0:
                raise ValueError("الأسعار لا يمكن أن تكون سالبة.")
        except Exception:
            msg_error = await update.message.reply_text("😒 دخّل أرقام صحيحة للشراء والبيع.")
            context.user_data[user_id]['messages_to_delete'].append({'chat_id': msg_error.chat_id, 'message_id': msg_error.message_id})
            return ASK_BUY

        # ======= إيجاد المنتج داخل الطلبية سواء كان id أو اسم قديم =======
        product_obj = None
        product_id = None

        # 1) إذا product_ref يبدو كـ id (نبحث أولاً)
        for p in orders[order_id].get("products", []):
            if isinstance(p, dict) and p.get("id") == product_ref:
                product_obj = p
                product_id = p["id"]
                break

        # 2) لو ما لقيناه، جرّب إذا product_ref هو اسم قديم (نبحث عن اسم مطابق)
        if product_obj is None:
            for p in orders[order_id].get("products", []):
                if isinstance(p, dict) and p.get("name") == product_ref:
                    product_obj = p
                    product_id = p["id"]
                    break
                if isinstance(p, str) and p == product_ref:
                    # وجدنا عنصر قديم كنص — نحوله الآن إلى dict ونعطيه ID
                    import uuid
                    new_p = {"id": uuid.uuid4().hex[:8], "name": p}
                    idx = orders[order_id]["products"].index(p)
                    orders[order_id]["products"][idx] = new_p
                    product_obj = new_p
                    product_id = new_p["id"]
                    break

        # 3) لو لسا ما لقينا المنتج، جرب البحث بالاسم داخل dicts (يتعامل مع حالات مختلفة)
        if product_obj is None:
            for p in orders[order_id].get("products", []):
                if isinstance(p, dict) and str(p.get("id")) == str(product_ref):
                    product_obj = p
                    product_id = p["id"]
                    break

        if product_obj is None:
            # لم نتمكن من إيجاد المنتج؛ اعمل لوق وارجع رسالة واضحة
            logger.error(f"[{chat_id}] Could not resolve product '{product_ref}' in order {order_id}. Products: {orders[order_id].get('products')}")
            await update.message.reply_text("هذا المنتج مموجود أو صار خلل. حاول تحميل الطلبية من جديد أو أضف المنتج مرة ثانية.")
            return ConversationHandler.END

        # ======= الآن نضمن أن pricing يستخدم product_id كمفتاح =======
        pricing.setdefault(order_id, {})

        # migration: لو موجود بيانات قديمة باسم المنتج (name) نحولها إلى المفتاح الجديد (id)
        name_key = product_obj.get("name")
        if name_key in pricing[order_id] and product_id not in pricing[order_id]:
            # انقل المحتوى
            pricing[order_id][product_id] = pricing[order_id].pop(name_key)
            logger.info(f"Migrated pricing key for order {order_id}: '{name_key}' -> '{product_id}'")

        # تأكد وجود dict للمنتج
        pricing[order_id].setdefault(product_id, {})

        # حفظ الأسعار
        pricing[order_id][product_id]["buy"] = buy_price
        pricing[order_id][product_id]["sell"] = sell_price

        # وضع supplier_id في الطلب
        orders[order_id]["supplier_id"] = user_id

        logger.info(f"[{chat_id}] Saved pricing for order '{order_id}', product_id '{product_id}': buy={buy_price}, sell={sell_price}")
        context.application.create_task(save_data_in_background(context))

        # تنظيف user_data الحقلين
        context.user_data[user_id].pop("order_id", None)
        context.user_data[user_id].pop("product", None)

        # هل كل المنتجات مسعّرة الآن؟
        is_order_complete = True
        for p in orders[order_id].get("products", []):
            pid = p["id"] if isinstance(p, dict) else None
            if pid is None or pid not in pricing.get(order_id, {}) or 'buy' not in pricing[order_id].get(pid, {}):
                is_order_complete = False
                break

        if is_order_complete:
            await request_places_count_standalone(chat_id, context, user_id, order_id)
            return ConversationHandler.END
        else:
            await show_buttons(chat_id, context, user_id, order_id, confirmation_message="تم إدخال السعر. بقي منتجات أخرى؟")
            return ConversationHandler.END

    except Exception as e:
        logger.error(f"[{chat_id}] Critical error in receive_buy_price: {e}", exc_info=True)
        try:
            msg_error = await update.message.reply_text("كسها صار خطا مدري وين؛ رجع سوي طلب جديد أو أضف المنتج مرة ثانية.")
            context.user_data.setdefault(user_id, {}).setdefault('messages_to_delete', []).append({
                'chat_id': msg_error.chat_id,
                'message_id': msg_error.message_id
            })
        except:
            pass
        return ConversationHandler.END
        
async def receive_new_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    chat_id = update.effective_chat.id
    new_product_name = update.message.text.strip()

    logger.info(f"[{chat_id}] Received new product name '{new_product_name}' from user {user_id}.")

    order_id = context.user_data[user_id].get("current_active_order_id")

    if not order_id or order_id not in orders:
        await update.message.reply_text("ماكو طلب فعال حتى أضيفله منتج. سوي طلب جديد.")
        context.user_data[user_id].pop("adding_new_product", None)
        return ConversationHandler.END

    order = orders[order_id]

    # إنشاء ID للمنتج
    import uuid
    product_id = uuid.uuid4().hex[:8]

    # إضافة المنتج بصيغة dict
    order["products"].append({"id": product_id, "name": new_product_name})

    await update.message.reply_text(f"تمت إضافة المنتج '{new_product_name}' للطلبية.")

    context.application.create_task(save_data_in_background(context))

    context.user_data[user_id].pop("adding_new_product", None)
    context.user_data[user_id].pop("current_active_order_id", None)

    await show_buttons(chat_id, context, user_id, order_id)
    return ConversationHandler.END


async def request_places_count_standalone(chat_id, context: ContextTypes.DEFAULT_TYPE, user_id: str, order_id: str):
    orders = context.application.bot_data['orders']

    try:
        # تأكيد وجود الطلب
        if order_id not in orders:
            await context.bot.send_message(chat_id, "⚠️ الطلب غير موجود.")
            return

        # ضبط البيانات الخاصة بالمستخدم
        user_data = context.user_data.setdefault(user_id, {})
        user_data["current_active_order_id"] = order_id

        # إنشاء أزرار الأعداد (1 إلى 10)
        emojis = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟']
        buttons = [
            InlineKeyboardButton(
                emojis[i - 1],
                callback_data=f"places_data_{order_id}_{i}"
            )
            for i in range(1, 11)
        ]

        keyboard = [buttons[i:i + 5] for i in range(0, len(buttons), 5)]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # إرسال رسالة اختيار عدد المحلات
        msg_places = await context.bot.send_message(
            chat_id=chat_id,
            text="✔️ كملت تسعير كل المنتجات\nاختار عدد المحلات من الأزرار التالية:",
            reply_markup=reply_markup
        )

        # حفظ معلومات الرسالة حتى تنحذف لاحقًا
        user_data['places_count_message'] = {
            'chat_id': msg_places.chat_id,
            'message_id': msg_places.message_id
        }

        # حذف الرسائل القديمة إذا موجودة
        messages_to_delete = user_data.get("messages_to_delete", [])
        if messages_to_delete:
            for msg_info in messages_to_delete:
                context.application.create_task(
                    delete_message_in_background(
                        context,
                        chat_id=msg_info['chat_id'],
                        message_id=msg_info['message_id']
                    )
                )
            user_data["messages_to_delete"] = []

    except Exception as e:
        logger.error(f"[{chat_id}] Error in request_places_count_standalone: {e}", exc_info=True)
        await context.bot.send_message(
            chat_id,
            "❌ صار خلل أثناء اختيار عدد المحلات.\nرجاءً سوّي طلب جديد."
        )
        
async def handle_places_count_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    orders = context.application.bot_data['orders']
    pricing = context.application.bot_data['pricing']
    daily_profit = context.application.bot_data['daily_profit']
    
    try:
        places = None
        chat_id = update.effective_chat.id
        user_id = str(update.effective_user.id) 
        logger.info(f"[{chat_id}] handle_places_count_data triggered by user {user_id}. Update type: {'CallbackQuery' if update.callback_query else 'Message'}. User data: {json.dumps(context.user_data.get(user_id), indent=2)}")

        context.user_data.setdefault(user_id, {})
        if 'messages_to_delete' not in context.user_data[user_id]:
            context.user_data[user_id]['messages_to_delete'] = []

        order_id_to_process = None 

        if update.callback_query:
            query = update.callback_query
            logger.info(f"[{chat_id}] Places count callback query received: {query.data}")
            await query.answer()
            
            try:
                parts = query.data.split('_')
                if len(parts) == 4 and parts[0] == "places" and parts[1] == "data":
                    order_id_to_process = parts[2] 
                    
                    if order_id_to_process not in orders:
                        logger.error(f"[{chat_id}] Order ID '{order_id_to_process}' from callback data not found in global orders.")
                        await context.bot.send_message(chat_id=chat_id, text="باعلي هيو الطلبية الي ددوس عدد محلاتها ماهيه ولا دكلي وينهيا . تريد سوي طلب جديد")
                        if user_id in context.user_data and "current_active_order_id" in context.user_data[user_id]:
                            del context.user_data[user_id]["current_active_order_id"]
                        return ConversationHandler.END 

                    places = int(parts[3])
                    if query.message:
                        try:
                            await context.bot.delete_message(chat_id=query.message.chat_id, message_id=query.message.message_id)
                        except Exception as e:
                            logger.warning(f"[{chat_id}] Could not delete places message {query.message.message_id} directly: {e}. Proceeding.")

                else:
                    raise ValueError(f"Unexpected callback_data format for places count: {query.data}")
            except (ValueError, IndexError) as e:
                logger.error(f"[{chat_id}] Failed to parse places count from callback data '{query.data}': {e}", exc_info=True)
                await context.bot.send_message(chat_id=chat_id, text="😐الدكمة زربت سوي طلب جديد.")
                return ConversationHandler.END 
        
        elif update.message: 
            context.user_data[user_id]['messages_to_delete'].append({'chat_id': update.message.chat_id, 'message_id': update.message.message_id})
            logger.info(f"[{chat_id}] Received text message for places count from user {user_id}: '{update.message.text}'")
            
            order_id_to_process = context.user_data[user_id].get("current_active_order_id")

            if not order_id_to_process or order_id_to_process not in orders:
                 logger.warning(f"[{chat_id}] Places count text input: No current active order for user {user_id} or order {order_id_to_process} is invalid.")
                 msg_error = await context.bot.send_message(chat_id=chat_id, text="عذراً، ماكو طلبية حالية منتظر عدد محلاتها أو الطلبية قديمة جداً. الرجاء استخدم الأزرار لتحديد عدد المحلات، أو بدء طلبية جديدة.", parse_mode="Markdown")
                 context.user_data[user_id]['messages_to_delete'].append({'chat_id': msg_error.chat_id, 'message_id': msg_error.message_id})
                 if user_id in context.user_data and "current_active_order_id" in context.user_data[user_id]:
                            del context.user_data[user_id]["current_active_order_id"]
                 return ConversationHandler.END 

            if not update.message.text.strip().isdigit(): 
                logger.warning(f"[{chat_id}] Places count text input: Non-integer input from user {user_id}: '{update.message.text}'")
                msg_error = await context.bot.send_message(chat_id=chat_id, text="😐يابه دوس رقم صحيح.")
                context.user_data[user_id]['messages_to_delete'].append({'chat_id': msg_error.chat_id, 'message_id': msg_error.message_id})
                return ASK_PLACES_COUNT 
            
            try:
                places = int(update.message.text.strip())
                if places < 0:
                    logger.warning(f"[{chat_id}] Places count text input: Negative value from user {user_id}: '{update.message.text}'")
                    msg_error = await context.bot.send_message(chat_id=chat_id, text="عدد المحلات يجب أن يكون رقماً موجباً. الرجاء إدخال عدد المحلات بشكل صحيح.")
                    context.user_data[user_id]['messages_to_delete'].append({'chat_id': msg_error.chat_id, 'message_id': msg_error.message_id})
                    return ASK_PLACES_COUNT 
            except ValueError as e: 
                logger.error(f"[{chat_id}] Places count text input: ValueError for user {user_id} with input '{update.message.text}': {e}", exc_info=True)
                msg_error = await context.bot.send_message(chat_id=chat_id, text="😐يابه ددوس عدل.")
                context.user_data[user_id]['messages_to_delete'].append({'chat_id': msg_error.chat_id, 'message_id': msg_error.message_id})
                return ASK_PLACES_COUNT 
        
        if places is None or order_id_to_process is None:
            logger.warning(f"[{chat_id}] handle_places_count_data: No valid places count or order ID to process.")
            await context.bot.send_message(chat_id=chat_id, text="عذراً، لم أتمكن من فهم عدد المحلات أو الطلبية. الرجاء إدخال رقم صحيح أو البدء بطلبية جديدة.")
            if user_id in context.user_data and "current_active_order_id" in context.user_data[user_id]:
                            del context.user_data[user_id]["current_active_order_id"]
            return ConversationHandler.END 

        if 'places_count_message' in context.user_data[user_id]:
            msg_info = context.user_data[user_id]['places_count_message']
            try:
                await context.bot.delete_message(chat_id=msg_info['chat_id'], message_id=msg_info['message_id'])
            except Exception as e:
                logger.warning(f"[{chat_id}] Could not delete places count message: {e}")
            del context.user_data[user_id]['places_count_message']

        orders[order_id_to_process]["places_count"] = places
        # هنا لازم نحفظ daily_profit المحدثة
        # نحدث daily_profit مباشرة في bot_data أو عبر دالة حفظ عامة
        context.application.bot_data['daily_profit'] = daily_profit # تحديث القيمة في bot_data
        context.application.create_task(save_data_in_background(context))

        logger.info(f"[{chat_id}] Places count {places} saved for order {order_id_to_process}. Current user_data: {json.dumps(context.user_data.get(user_id), indent=2)}")

        if user_id in context.user_data and 'messages_to_delete' in context.user_data[user_id]:
            logger.info(f"[{chat_id}] Scheduling deletion of {len(context.user_data[user_id].get('messages_to_delete', []))} old messages after showing final options for user {user_id}.")
            for msg_info in context.user_data[user_id]['messages_to_delete']:
                context.application.create_task(delete_message_in_background(context, chat_id=msg_info['chat_id'], message_id=msg_info['message_id']))
            context.user_data[user_id]['messages_to_delete'].clear()
        
        await show_final_options(chat_id, context, user_id, order_id_to_process, message_prefix="هلهل كللوش.")
        
        if user_id in context.user_data and "current_active_order_id" in context.user_data[user_id]:
            del context.user_data[user_id]["current_active_order_id"]
            logger.info(f"[{chat_id}] Cleared current_active_order_id for user {user_id} after processing places count.")

        return ConversationHandler.END 
    except Exception as e:
        logger.error(f"[{chat_id}] Error in handle_places_count_data: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text="عذراً، حدث خطأ أثناء معالجة عدد المحلات. الرجاء بدء طلبية جديدة.", parse_mode="Markdown")
        return ConversationHandler.END

from urllib.parse import quote

async def show_final_options(chat_id, context, user_id, order_id, message_prefix=None):
    orders = context.application.bot_data['orders']
    pricing = context.application.bot_data['pricing']

    try:
        if order_id not in orders:
            await context.bot.send_message(chat_id, "⚠️ الطلب غير موجود.")
            return

        order = orders[order_id]
        products = order["products"]

        text = ""

        # إضافة المقدمة إذا موجودة
        if message_prefix:
            text += f"{message_prefix}\n\n"

        text += f"📦 *الطلب:* {order['title']}\n"
        text += "━━━━━━━━━━━━━━\n"

        total_profit = 0

        for product in products:
            p_id = product["id"]
            p_name = product["name"]

            if p_id in pricing.get(order_id, {}):
                pr = pricing[order_id][p_id]

                if "buy" in pr and "sell" in pr:
                    profit = pr["sell"] - pr["buy"]
                    total_profit += profit

                    text += f"🔹 {p_name}\n"
                    text += f"   شراء: {pr['buy']}\n"
                    text += f"   بيع: {pr['sell']}\n"
                    text += f"   ربح: {profit}\n\n"
                else:
                    text += f"❗ {p_name} — لم يتم تسعيره بالكامل.\n\n"
            else:
                text += f"❗ {p_name} — لم يتم تسعيره.\n\n"

        text += "━━━━━━━━━━━━━━\n"
        text += f"💰 *الربح الكلي:* {total_profit}"

        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 إرسال الفاتورة", callback_data=f"send_invoice_{order_id}")],
            [InlineKeyboardButton("🗑️ حذف منتج", callback_data=f"delete_specific_product_{order_id}")],
            [InlineKeyboardButton("🔙 رجوع", callback_data=f"back_to_order_{order_id}")]
        ])

        await context.bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"[{chat_id}] Error in show_final_options: {e}", exc_info=True)
        await context.bot.send_message(chat_id, "❌ خطأ أثناء عرض خيارات الفاتورة.")
        
async def edit_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    orders = context.application.bot_data['orders']
    pricing = context.application.bot_data['pricing']
    
    try:
        query = update.callback_query
        await query.answer()
        
        user_id = str(query.from_user.id)
        logger.info(f"[{query.message.chat_id}] Edit prices callback from user {user_id}: {query.data}. User data: {json.dumps(context.user_data.get(user_id, {}), indent=2)}")
        if query.data.startswith("edit_prices_"):
            order_id = query.data.replace("edit_prices_", "")
        else:
            await query.message.reply_text("زربة الدكمة عطبت بالله دجرب من جديد😐.")
            return ConversationHandler.END

        if order_id not in orders:
            logger.warning(f"[{query.message.chat_id}] Edit prices: Order {order_id} not found.")
            await query.message.reply_text("😐زربه الطلب الي تريد تعدلة ماموجود ولا تسالين وين راح .")
            return ConversationHandler.END

        # وضع علامة أن المستخدم في وضع التعديل
        context.user_data.setdefault(user_id, {})["editing_mode"] = True

        if query.message:
            context.user_data.setdefault(user_id, {}).setdefault('messages_to_delete', []).append({
                'chat_id': query.message.chat_id,
                'message_id': query.message.message_id
            })
            logger.info(f"[{query.message.chat_id}] Added edit prices button message {query.message.message_id} to delete queue.")
            try:
                await context.bot.edit_message_reply_markup(
                    chat_id=query.message.chat_id,
                    message_id=query.message.message_id,
                    reply_markup=None 
                )
            except Exception as e:
                logger.warning(f"[{query.message.chat_id}] Could not clear buttons from edit prices message {query.message.message_id} directly: {e}. Proceeding.")
        
        await show_buttons(query.message.chat_id, context, user_id, order_id, confirmation_message="يمكنك الآن تعديل أسعار المنتجات أو الضغط على إلغاء التعديل للعودة للفاتورة.")
        logger.info(f"[{query.message.chat_id}] Showing edit buttons for order {order_id}. Exiting conversation for user {user_id}.")
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in edit_prices: {e}", exc_info=True)
        await update.callback_query.message.reply_text("😏زربة صار خطا بالتعديل دسوي طلبية جديده بدون حجي زايد.")
        return ConversationHandler.END

async def cancel_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    order_id = query.data.replace("cancel_edit_", "")
    
    # إزالة وضع التعديل
    if user_id in context.user_data:
        context.user_data[user_id].pop("editing_mode", None)
    
    # حذف رسالة الأزرار القديمة
    try:
        await query.message.delete()
    except Exception as e:
        logger.warning(f"Could not delete edit message: {e}")
    
    # العودة لعرض الفاتورة النهائية
    await show_final_options(query.message.chat_id, context, user_id, order_id, message_prefix="ترا سطرتني عدل الغي عدل الغي لغيتها.")
    return ConversationHandler.END
    

async def start_new_order_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.from_user.id)
    try:
        query = update.callback_query
        await query.answer()
        
        logger.info(f"[{query.message.chat_id}] Start new order callback from user {user_id}. User data: {json.dumps(context.user_data.get(user_id, {}), indent=2)}")
        if user_id in context.user_data:
            context.user_data[user_id].pop("order_id", None)
            context.user_data[user_id].pop("product", None)
            context.user_data[user_id].pop("current_active_order_id", None)
            context.user_data[user_id].pop("messages_to_delete", None) 
            context.user_data[user_id].pop("buy_price", None) # Clear buy_price too
            logger.info(f"[{query.message.chat_id}] Cleared order-specific user_data for user {user_id} after starting a new order from button. User data after clean: {json.dumps(context.user_data.get(user_id, {}), indent=2)}")

        if query.message:
            context.application.create_task(delete_message_in_background(context, chat_id=query.message.chat_id, message_id=query.message.message_id))

        await query.message.reply_text("تمام، دز الطلبية الجديدة كلها برسالة واحدة.\n\n*السطر الأول:* عنوان الزبون.\n*السطر الثاني:* رقم هاتف الزبون.\n*الأسطر الباقية:* كل منتج بسطر واحد.", parse_mode="Markdown")
        
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in start_new_order_callback: {e}", exc_info=True)
        await update.callback_query.message.reply_text("😐زربة ماكدرت اسوي طلبية جديده اشو بالله دسوي مره ثانيه علكولتهم حاول من جديد.")
        return ConversationHandler.END


# الدوال الخاصة بالتقارير والأرباح (ستُجزأ لاحقاً إلى features/reports.py)
async def show_profit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    orders = context.application.bot_data['orders'] # نجيب كل الطلبيات
    pricing = context.application.bot_data['pricing'] # نحتاج الأسعار لحساب الربح

    try:
        if str(update.message.from_user.id) != str(OWNER_ID):
            await update.message.reply_text("😏لاتاكل خره ماتكدر تسوي هالشي.")
            return

        total_net_profit_products_all_orders = 0.0 # صافي ربح المنتجات الكلي
        total_extra_profit_all_orders = 0.0 # ربح المحلات الكلي

        for order_id, order_data in orders.items():
            order_net_profit_products = 0.0 # ربح منتجات الطلبية الواحدة
            order_extra_profit_single_order = 0.0 # ربح محلات الطلبية الواحدة

            # حساب ربح المنتجات للطلبية
            if isinstance(order_data.get("products"), list):
                for p_name in order_data["products"]:
                    if p_name in pricing.get(order_id, {}) and "buy" in pricing[order_id].get(p_name, {}) and "sell" in pricing[order_id].get(p_name, {}):
                        buy = pricing[order_id][p_name]["buy"]
                        sell = pricing[order_id][p_name]["sell"]
                        order_net_profit_products += (sell - buy)

            # حساب ربح المحلات للطلبية
            num_places = order_data.get("places_count", 0)
            order_extra_profit_single_order = calculate_extra(num_places) # نستخدم الدالة الموجودة

            total_net_profit_products_all_orders += order_net_profit_products
            total_extra_profit_all_orders += order_extra_profit_single_order

        # مجموع الربح الكلي (منتجات + محلات)
        overall_cumulative_profit = total_net_profit_products_all_orders + total_extra_profit_all_orders

        logger.info(f"Overall cumulative profit requested by user {update.message.from_user.id}: {overall_cumulative_profit}")
        await update.message.reply_text(f"ربح البيع والتجهيز💵: *{format_float(overall_cumulative_profit)}* دينار", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in show_profit: {e}", exc_info=True)
        await update.message.reply_text("😐كسها ماكدرت اطلعلك الارباح")

async def reset_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if str(update.message.from_user.id) != str(OWNER_ID):
            await update.message.reply_text("😏لاتاكل خره ماتكدر تسوي هالشي.")
            return
        
        keyboard = [
            [InlineKeyboardButton("اي صفر", callback_data="confirm_reset")],
            [InlineKeyboardButton("لا لاتصفر", callback_data="cancel_reset")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("😏يابه انته متاكد تريد تصفر راجع روحك اخذ خيره مو بعدين دكول لا حرامات ", reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in reset_all: {e}", exc_info=True)
        await update.message.reply_text("😐، هذا الضراط ماكدرت اصفر.")

async def confirm_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    orders = context.application.bot_data['orders']
    pricing = context.application.bot_data['pricing']
    invoice_numbers = context.application.bot_data['invoice_numbers']
    last_button_message = context.application.bot_data['last_button_message']
    daily_profit = context.application.bot_data['daily_profit'] 
    supplier_report_timestamps = context.application.bot_data['supplier_report_timestamps'] # ✅ جبنا هذا المتغير

    try:
        query = update.callback_query
        await query.answer() # ✅ هذا السطر مهم جداً حتى يختفي التحميل من الزر

        if str(query.from_user.id) != str(OWNER_ID):
            await query.edit_message_text("😏لاتاكل خره ماتكدر تسوي هالشي.")
            return

        if query.data == "confirm_reset":
            logger.info(f"Daily profit before reset: {daily_profit}")
            
            # تصفير القيم في الذاكرة
            orders.clear()
            pricing.clear()
            invoice_numbers.clear()
            last_button_message.clear()
            supplier_report_timestamps.clear() # ✅ تصفير سجلات المجهزين
            
            daily_profit_value = 0.0 # القيمة الجديدة للربح اليومي

            try:
                # إعادة تعيين عداد الفواتير
                with open(COUNTER_FILE, "w") as f:
                    f.write("1")
            except Exception as e:
                logger.error(f"Could not reset invoice counter file: {e}", exc_info=True)
            
            # تحديث القيم في bot_data بعد التصفير (هذا الجزء مهم)
            context.application.bot_data['orders'] = orders
            context.application.bot_data['pricing'] = pricing
            context.application.bot_data['invoice_numbers'] = invoice_numbers
            context.application.bot_data['last_button_message'] = last_button_message
            context.application.bot_data['daily_profit'] = daily_profit_value
            context.application.bot_data['supplier_report_timestamps'] = supplier_report_timestamps # ✅ تحديث سجل المجهزين في bot_data

            # استدعاء دالة الحفظ العامة لحفظ التغييرات على القرص
            _save_data_to_disk_global_func = context.application.bot_data.get('_save_data_to_disk_global_func')
            if _save_data_to_disk_global_func:
                _save_data_to_disk_global_func()
            else:
                logger.error("Could not find _save_data_to_disk_global_func in bot_data.")
            
            logger.info(f"Daily profit after reset: {context.application.bot_data['daily_profit']}")
            await query.edit_message_text("😒صفرنه ومسحنه عندك شي ثاني.")
        elif query.data == "cancel_reset":
            await query.edit_message_text("😏لغيناها ارتاحيت.")
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in confirm_reset: {e}", exc_info=True)
        await update.callback_query.message.reply_text("😐، هذا الضراط ماكدرت اصفر.")
        
async def show_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    orders = context.application.bot_data['orders']
    pricing = context.application.bot_data['pricing']
    invoice_numbers = context.application.bot_data['invoice_numbers']
    daily_profit = context.application.bot_data['daily_profit'] # هذا المتغير يمثل الربح التراكمي الكلي

    try:
        if str(update.message.from_user.id) != str(OWNER_ID):
            await update.message.reply_text("لاتاكل خره هذا الامر للمدير افتهمت لولا.")
            return

        total_orders = len(orders)
        total_products = 0
        total_buy_all_orders = 0.0 
        total_sell_all_orders = 0.0 
        total_net_profit_all_orders = 0.0 # ✅ هذا يمثل صافي الربح الكلي لكل الطلبيات
        total_extra_profit_all_orders = 0.0 # ✅ متغير جديد لربح المحلات الكلي
        product_counter = Counter()
        details = []

        for order_id, order in orders.items():
            invoice = invoice_numbers.get(order_id, "غير معروف")
            details.append(f"\n**فاتورة رقم:🔢** {invoice}")
            details.append(f"**عنوان الزبون:🏠** {order['title']}")

            order_buy = 0.0
            order_sell = 0.0
            order_net_profit = 0.0 # صافي ربح الطلبية الواحدة
            order_extra_profit = 0.0 # ربح المحلات للطلبية الواحدة

            if isinstance(order.get("products"), list):
                for p_name in order["products"]:
                    total_products += 1
                    product_counter[p_name] += 1

                    if p_name in pricing.get(order_id, {}) and "buy" in pricing[order_id].get(p_name, {}) and "sell" in pricing[order_id].get(p_name, {}):
                        buy = pricing[order_id][p_name]["buy"]
                        sell = pricing[order_id][p_name]["sell"]
                        profit_item = sell - buy
                        order_buy += buy
                        order_sell += sell
                        order_net_profit += profit_item # نجمع ربح كل منتج
                        details.append(f"  - {p_name} | شراء💸: {format_float(buy)} | بيع💵 : {format_float(sell)} | ربح💲: {format_float(profit_item)}")
                    else:
                        details.append(f"  - {p_name} | (لم يتم تسعيره)")
            else:
                details.append(f"  (لا توجد منتجات محددة لهذا الطلب)")

            # حساب ربح المحلات للطلبية الواحدة
            num_places = order.get("places_count", 0)
            order_extra_profit = calculate_extra(num_places) # نحسب الربح من عدد المحلات

            total_buy_all_orders += order_buy
            total_sell_all_orders += order_sell
            total_net_profit_all_orders += order_net_profit # نجمع صافي ربح الطلبية
            total_extra_profit_all_orders += order_extra_profit # نجمع ربح المحلات الكلي

            details.append(f"  *ربح المنتجات في هذه الطلبية:🛍️💵* {format_float(order_net_profit)}")
            details.append(f"  *ربح المحلات في هذه الطلبية ({num_places} محل):🏪💵* {format_float(order_extra_profit)}")
            details.append(f"  *إجمالي ربح هذه الطلبية:🏪🛍️💵* {format_float(order_net_profit + order_extra_profit)}")


        top_product_str = "لا يوجد"
        if product_counter:
            top_product_name, top_product_count = product_counter.most_common(1)[0]
            top_product_str = f"{top_product_name} ({top_product_count} مرة)"

        result = (
            f"**--- تقرير عام عن الطلبات🗒️ ---**\n"
            f"**إجمالي عدد الطلبات المعالجة:🛍️** {total_orders}\n"
            f"**إجمالي عدد المنتجات المباعة (في الطلبات المعالجة):🛒** {total_products}\n"
            f"**أكثر منتج تم طلبه:🛍️** {top_product_str}\n\n"
            f"**مجموع الشراء الكلي (للمنتجات):💸** {format_float(total_buy_all_orders)}\n"
            f"**مجموع البيع الكلي (للمنتجات):💵 ** {format_float(total_sell_all_orders)}\n" 
            f"**صافي ربح المنتجات الكلي:🛍️💵 ** {format_float(total_net_profit_all_orders)}\n" 
            f"**ربح المحلات الكلي:🏪💵** {format_float(total_extra_profit_all_orders)}\n"
            f"**ربح البيع والتجهيز:🏪🛍️💵** {format_float(total_net_profit_all_orders + total_extra_profit_all_orders)} دينار\n\n"
            f"**--- تفاصيل الطلبات🗒 ---**\n" + "\n".join(details)
        )
        await update.message.reply_text(result, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in show_report: {e}", exc_info=True)
        await update.message.reply_text("😐هذا الظراط ماكدرت ادزلك التقرير .")
        
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    # وضع المتغيرات العالمية في bot_data
    app.bot_data['orders'] = orders
    app.bot_data['pricing'] = pricing
    app.bot_data['invoice_numbers'] = invoice_numbers
    app.bot_data['daily_profit'] = daily_profit
    app.bot_data['last_button_message'] = last_button_message
    app.bot_data['supplier_report_timestamps'] = supplier_report_timestamps

    # تمرير دوال الحفظ العامة لـ bot_data حتى تتمكن الدوال الأخرى من استدعائها
    app.bot_data['schedule_save_global_func'] = schedule_save_global
    app.bot_data['_save_data_to_disk_global_func'] = _save_data_to_disk_global

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("profit", show_profit))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"^(الارباح|ارباح)$"), show_profit))
    app.add_handler(CommandHandler("reset", reset_all))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"^تصفير$"), reset_all))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"^صفر$"), reset_supplier_report))
    app.add_handler(CallbackQueryHandler(confirm_reset, pattern="^(confirm_reset|cancel_reset)$"))
    app.add_handler(CommandHandler("report", show_report))
    app.add_handler(CommandHandler("myreport", show_supplier_report))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"^(تقاريري|تقريري)$"), show_supplier_report))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"^(التقارير|تقرير|تقارير)$"), show_report))
    app.add_handler(CallbackQueryHandler(cancel_edit, pattern=r"^cancel_edit_.*$"))

    # ⭐⭐ إضافة الأمر لعرض الطلبات غير المكتملة ⭐⭐
    app.add_handler(CommandHandler("incomplete", show_incomplete_orders))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"^(طلبات|الطلبات|طلبات غير مكتملة|طلبات ناقصة)$"), show_incomplete_orders))

    # ⭐⭐ إضافة handler لأزرار الطلبات غير المكتملة ⭐⭐
    app.add_handler(CallbackQueryHandler(handle_incomplete_order_selection, pattern=r"^(load_incomplete_|cancel_incomplete)"))

    app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE, edited_message))
    app.add_handler(CallbackQueryHandler(edit_prices, pattern=r"^edit_prices_"))
    app.add_handler(CallbackQueryHandler(start_new_order_callback, pattern=r"^start_new_order$"))
    
    # أمر /zones لعرض المناطق
    app.add_handler(CommandHandler("zones", list_zones))
    # استجابة نصية "مناطق" أو "المناطق"
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"^(مناطق|المناطق)$"), list_zones))

    # ConversationHandler لعدد المحلات
    places_conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(handle_places_count_data, pattern=r"^places_data_[a-f0-9]{8}_\d+$"),
        ],
        states={
            ASK_PLACES_COUNT: [
                MessageHandler(filters.TEXT & filters.Regex(r"^\d+(\.\d+)?$") & ~filters.COMMAND, handle_places_count_data),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_places_count_data),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", lambda u, c: ConversationHandler.END),
            MessageHandler(filters.ALL, lambda u, c: ConversationHandler.END)
        ]
    )
    app.add_handler(places_conv_handler)

    # ConversationHandler لمسح الطلبية
    delete_order_conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.TEXT & filters.Regex(r"^(مسح)$"), delete_order_command),
            CommandHandler("delete_order", delete_order_command),
        ],
        states={
            ASK_CUSTOMER_PHONE_NUMBER_FOR_DELETION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_customer_phone_for_deletion),
            ],
            ASK_FOR_DELETION_CONFIRMATION: [
                CallbackQueryHandler(handle_order_selection_for_deletion, 
                                 pattern=r"^(select_order_to_delete_.*|confirm_final_delete_.*|cancel_delete_order|cancel_delete_order_final_selection)$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", lambda u, c: ConversationHandler.END),
            MessageHandler(filters.ALL & ~filters.COMMAND, lambda u, c: ConversationHandler.END) 
        ]
    )
    app.add_handler(delete_order_conv_handler)

    # ConversationHandler لإنشاء وتسعير الطلبات وإضافة المنتجات
    order_creation_conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_order),
            CallbackQueryHandler(product_selected, pattern=r"^[a-f0-9]{8}\|.+$"),
            CallbackQueryHandler(add_new_product_callback, pattern=r"^add_product_to_order_.*$"),
            CallbackQueryHandler(delete_product_callback, pattern=r"^delete_specific_product_.*$"), 
            CallbackQueryHandler(confirm_delete_product_by_button_callback, pattern=r"^confirm_delete_product_.*$"), 
            CallbackQueryHandler(cancel_delete_product_callback, pattern=r"^cancel_delete_product_.*$")
        ],
        states={
            ASK_BUY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_buy_price),
            ],
            ASK_PRODUCT_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_product_name),
                CallbackQueryHandler(cancel_add_product_callback, pattern=r"^cancel_add_product_.*$")
            ],
        },
        fallbacks=[
            CommandHandler("cancel", lambda u, c: ConversationHandler.END),
            MessageHandler(filters.ALL, lambda u, c: ConversationHandler.END)
        ]
    )
    app.add_handler(order_creation_conv_handler)

    # تشغيل البوت
    app.run_polling(allowed_updates=Update.ALL_TYPES)
   

async def show_supplier_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    orders = context.application.bot_data['orders']
    pricing = context.application.bot_data['pricing']
    supplier_report_timestamps = context.application.bot_data['supplier_report_timestamps']

    user_id = str(update.message.from_user.id)
    report_text = f"**تقرير الطلبيات اللي جهزتها يا بطل:**\n\n"
    has_orders = False
    total_purchases_all_orders = 0.0 # ✅ متغير جديد لمجموع المشتريات الكلي للمجهز

    # جلب آخر وقت تصفير لهذا المجهز (إذا موجود)
    last_reset_timestamp_str = supplier_report_timestamps.get(user_id)
    last_reset_datetime = None
    if last_reset_timestamp_str:
        try:
            # تحويل الـ timestamp من string الى datetime object
            last_reset_datetime = datetime.fromisoformat(last_reset_timestamp_str)
            logger.info(f"[{update.effective_chat.id}] Last report reset for supplier {user_id} was at: {last_reset_datetime}")
        except ValueError as e:
            logger.error(f"[{update.effective_chat.id}] Error parsing last_reset_timestamp_str '{last_reset_timestamp_str}': {e}")
            last_reset_datetime = None # إذا صار خطأ بالتحويل، نعتبر ماكو وقت تصفير

    for order_id, order in orders.items():
        if order.get("supplier_id") == user_id:
            order_created_at_str = order.get("created_at")
            if last_reset_datetime and order_created_at_str:
                try:
                    order_created_datetime = datetime.fromisoformat(order_created_at_str)
                    if order_created_datetime <= last_reset_datetime:
                        continue
                except ValueError as e:
                    logger.error(f"[{update.effective_chat.id}] Error parsing order_created_at_str '{order_created_at_str}' for order {order_id}: {e}")

            has_orders = True
            report_text += f"▪️ *عنوان الزبون:🏠 * {order['title']}\n"
            report_text += f"   *رقم الزبون:📞* `{order.get('phone_number', 'لا يوجد رقم')}`\n"

            order_buy_total = 0.0

            report_text += "   *المنتجات (سعر الشراء افتهمت لولا):💸*\n"
            for p_name in order["products"]:
                if p_name in pricing.get(order_id, {}) and "buy" in pricing[order_id].get(p_name, {}):
                    buy_price = pricing[order_id][p_name]["buy"]
                    order_buy_total += buy_price
                    report_text += f"     - {p_name}: {format_float(buy_price)}\n"
                else:
                    report_text += f"     - {p_name}: (لم يتم تسعيره)\n"

            report_text += f"   *مجموع الشراء لهذه الطلبية:💸* {format_float(order_buy_total)}\n\n"
            total_purchases_all_orders += order_buy_total # ✅ جمع مشتريات هاي الطلبية للمجموع الكلي

    if not has_orders:
        report_text = "🖕🏻ماكو أي طلبية جديدة مسجلة باسمك بعد آخر تصفير."
    else: # ✅ إذا جان اكو طلبيات، نضيف المجموع الكلي للمشتريات بنهاية التقرير
        report_text += f"**💰 مجموع مشترياتك الكلي: {format_float(total_purchases_all_orders)} دينار💸**"

    await update.message.reply_text(report_text, parse_mode="Markdown")

async def reset_supplier_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    supplier_report_timestamps = context.application.bot_data['supplier_report_timestamps']
    schedule_save_global = context.application.bot_data['schedule_save_global_func']

    user_id = str(update.message.from_user.id)
    
    # نسجل الوقت الحالي كـ آخر وقت تصفير لهذا المجهز
    now_iso = datetime.now(timezone.utc).isoformat()
    supplier_report_timestamps[user_id] = now_iso
    
    # نحفظ التغييرات
    schedule_save_global()
    logger.info(f"[{update.effective_chat.id}] Supplier report for user {user_id} reset to {now_iso}.")

    await update.message.reply_text("📬تم تصفير تقاريرك بنجاح. أي طلبية جديدة تجهزها من الآن راح تظهر بالتقرير القادم.")

async def delete_order_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    chat_id = update.effective_chat.id

    if user_id != str(OWNER_ID):
        await update.message.reply_text("😏لاتاكل خره ماتكدر تسوي هالشي. هذا الأمر متاح للمالك فقط.")
        return ConversationHandler.END

    await update.message.reply_text("تمام، دزلي رقم الزبون للطلبية اللي تريد تمسحها:")
    context.user_data[user_id] = {"deleting_order": True}  # إعادة تهيئة user_data
    return ASK_CUSTOMER_PHONE_NUMBER_FOR_DELETION




async def receive_customer_phone_for_deletion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    chat_id = update.effective_chat.id
    customer_phone_number = update.message.text.strip()

    logger.info(f"[{chat_id}] Received phone number '{customer_phone_number}' for order deletion from user {user_id}.")

    # تأكد من أن المستخدم يمتلك صلاحيات المالك
    if user_id != str(OWNER_ID):
        await update.message.reply_text("😏لاتاكل خره ماتكدر تسوي هالشي. هذا الأمر متاح للمالك فقط.")
        context.user_data[user_id].pop("deleting_order", None)
        return ConversationHandler.END

    # البحث عن جميع الطلبات لهذا الرقم سواء مكتملة أو غير مكتملة
    found_orders = {oid: o for oid, o in orders.items() if o.get("phone_number") == customer_phone_number}

    if not found_orders:
        await update.message.reply_text("ما لكييت أي طلبية لهذا الرقم.")
        context.user_data[user_id].pop("deleting_order", None)
        return ConversationHandler.END

    orders_list_details = []
    keyboard_buttons = []

    # ترتيب الطلبات حسب تاريخ الإنشاء (الأحدث أولاً)
    sorted_orders_items = sorted(found_orders.items(), key=lambda item: item[1].get('created_at', ''), reverse=True)

    # حفظ الطلبيات المطابقة في user_data ليتعامل معها handle_order_selection_for_deletion
    context.user_data[user_id]["matching_order_ids"] = [oid for oid, _ in sorted_orders_items]

    for i, (oid, order_data) in enumerate(sorted_orders_items):
        invoice = invoice_numbers.get(oid, "غير معروف")
        is_priced = all(p in pricing.get(oid, {}) and 'buy' in pricing[oid].get(p, {}) and 'sell' in pricing[oid].get(p, {}) for p in order_data.get("products", []))
        status = "مكتملة التسعير" if is_priced else "غير مكتملة التسعير"

        orders_list_details.append(
            f"🔹 *الفاتورة رقم #{invoice}* ({status})\n"
            f"    العنوان: {order_data.get('title', 'غير متوفر')}\n"
            f"    المنتجات: {', '.join(order_data.get('products', []))}"
        )
        # هنا سنستخدم "select_order_to_delete_{order_id}" مباشرة
        # وستقوم دالة handle_order_selection_for_deletion بتأكيد الحذف
        keyboard_buttons.append(
            [InlineKeyboardButton(f"مسح الفاتورة #{invoice} ({status})", callback_data=f"select_order_to_delete_{oid}")]
        )

    keyboard_buttons.append([InlineKeyboardButton("❌ إلغاء العملية", callback_data="cancel_delete_order")])

    await update.message.reply_text(
        f"تم العثور على {len(found_orders)} طلبية لهذا الرقم:\n\n" +
        "\n\n".join(orders_list_details) +
        "\n\nاختر الفاتورة التي تريد مسحها:",
        reply_markup=InlineKeyboardMarkup(keyboard_buttons),
        parse_mode="Markdown"
    )
    return ASK_FOR_DELETION_CONFIRMATION



async def handle_order_selection_for_deletion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = str(query.from_user.id)
    chat_id = query.message.chat_id
    data = query.data

    if user_id != str(OWNER_ID):
        await query.edit_message_text("عذراً، لا تملك صلاحية لتنفيذ هذا الأمر.")
        context.user_data[user_id].pop("deleting_order", None)
        return ConversationHandler.END

    # إذا ضغط المستخدم على زر إلغاء العملية
    if data == "cancel_delete_order":
        await query.edit_message_text("تم إلغاء عملية مسح الطلبية.")
        context.user_data[user_id].pop("deleting_order", None)
        context.user_data[user_id].pop("matching_order_ids", None)
        return ConversationHandler.END

    # إذا ضغط المستخدم على زر اختيار طلبية من القائمة
    if data.startswith("select_order_to_delete_"):
        order_id_to_confirm = data.replace("select_order_to_delete_", "")
        
        if order_id_to_confirm not in orders:
            await query.edit_message_text("الطلبية غير موجودة أو تم حذفها مسبقاً.")
            context.user_data[user_id].pop("deleting_order", None)
            context.user_data[user_id].pop("matching_order_ids", None)
            return ConversationHandler.END

        # حفظ order_id للتأكيد النهائي
        context.user_data[user_id]["order_id_to_delete_final"] = order_id_to_confirm

        invoice_num = invoice_numbers.get(order_id_to_confirm, "غير معروف")
        confirm_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ نعم، امسحها", callback_data=f"confirm_final_delete_{order_id_to_confirm}")],
            [InlineKeyboardButton("❌ لا، بطلت", callback_data="cancel_delete_order_final_selection")] # زر إلغاء بعد الاختيار
        ])
        await query.edit_message_text(
            f"هل أنت متأكد من مسح الفاتورة رقم `{invoice_num}`؟ هذا الإجراء لا يمكن التراجع عنه.",
            reply_markup=confirm_keyboard,
            parse_mode="Markdown"
        )
        return ASK_FOR_DELETION_CONFIRMATION # البقاء في نفس الحالة لانتظار التأكيد النهائي

    # إذا ضغط المستخدم على زر التأكيد النهائي للحذف
    if data.startswith("confirm_final_delete_"):
        order_id_to_delete = data.replace("confirm_final_delete_", "")

        # تحقق مرة أخرى من order_id_to_delete_final لضمان أننا نمسح الطلب الصحيح
        if context.user_data[user_id].get("order_id_to_delete_final") != order_id_to_delete:
            logger.warning(f"[{chat_id}] Mismatch in order ID for final deletion confirmation. Expected {context.user_data[user_id].get('order_id_to_delete_final')}, got {order_id_to_delete}.")
            await query.edit_message_text("حدث خطأ، الطلبية المحددة للحذف غير مطابقة. الرجاء المحاولة مرة أخرى.")
            context.user_data[user_id].pop("deleting_order", None)
            context.user_data[user_id].pop("matching_order_ids", None)
            context.user_data[user_id].pop("order_id_to_delete_final", None)
            return ConversationHandler.END

        # تنفيذ الحذف
        try:
            invoice_number_to_display = invoice_numbers.get(order_id_to_delete, "غير معروف")
            if order_id_to_delete in orders:
                del orders[order_id_to_delete]
            if order_id_to_delete in pricing:
                del pricing[order_id_to_delete]
            if order_id_to_delete in invoice_numbers:
                del invoice_numbers[order_id_to_delete]
            if order_id_to_delete in last_button_message: # حذف رسالة الزر من السجل إذا كانت موجودة
                del last_button_message[order_id_to_delete]

            context.application.create_task(save_data_in_background(context))

            logger.info(f"[{chat_id}] Order {order_id_to_delete} deleted successfully by user {user_id}.")
            await query.edit_message_text(f"تم مسح الطلبية رقم `{invoice_number_to_display}` بنجاح!")
        except Exception as e:
            logger.error(f"[{chat_id}] Error deleting order {order_id_to_delete}: {e}", exc_info=True)
            await query.edit_message_text("عذراً، صار خطأ أثناء مسح الطلبية.")

        context.user_data[user_id].pop("deleting_order", None)
        context.user_data[user_id].pop("matching_order_ids", None)
        context.user_data[user_id].pop("order_id_to_delete_final", None)
        return ConversationHandler.END
    
    # التعامل مع إلغاء الاختيار النهائي بعد اختيار طلبية
    if data == "cancel_delete_order_final_selection":
        await query.edit_message_text("تم إلغاء عملية مسح الطلبية.")
        context.user_data[user_id].pop("deleting_order", None)
        context.user_data[user_id].pop("matching_order_ids", None)
        context.user_data[user_id].pop("order_id_to_delete_final", None)
        return ConversationHandler.END

    logger.warning(f"[{chat_id}] Unhandled callback_data in handle_order_selection_for_deletion: {data}")
    await query.edit_message_text("خطأ غير متوقع. الرجاء المحاولة مرة أخرى.")
    return ConversationHandler.END

async def show_incomplete_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض الطلبات غير المكتملة على شكل أزرار"""
    try:
        user_id = str(update.effective_user.id)
        chat_id = update.effective_chat.id
        
        # البحث عن الطلبات غير المكتملة
        incomplete_orders = {}
        for order_id, order in orders.items():
            # التحقق إذا كانت الطلبية غير مكتملة (أي منتج لم يتم تسعيره)
            is_complete = True
            for p_name in order.get("products", []):
                if p_name not in pricing.get(order_id, {}) or "buy" not in pricing[order_id].get(p_name, {}) or "sell" not in pricing[order_id].get(p_name, {}):
                    is_complete = False
                    break
            
            if not is_complete:
                incomplete_orders[order_id] = order
        
        if not incomplete_orders:
            await update.message.reply_text("🎉 لا توجد طلبات غير مكتملة حالياً!")
            return
        
        # إنشاء أزرار للطلبات غير المكتملة
        buttons = []
        for order_id, order in incomplete_orders.items():
            title = order.get("title", "بدون عنوان")[:20]  # تقليل طول النص
            phone = order.get("phone_number", "بدون رقم")[-4:]  # آخر 4 أرقام فقط
            buttons.append([InlineKeyboardButton(f"{title} (...{phone})", callback_data=f"load_incomplete_{order_id}")])
        
        # إضافة زر الإلغاء
        buttons.append([InlineKeyboardButton("❌ إلغاء", callback_data="cancel_incomplete")])
        
        markup = InlineKeyboardMarkup(buttons)
        
        await update.message.reply_text(
            f"الطلبات غير المكتملة ({len(incomplete_orders)}):\nاختر طلبية لتحميلها:",
            reply_markup=markup
        )
        
    except Exception as e:
        logger.error(f"Error in show_incomplete_orders: {e}")
        await update.message.reply_text("❌ حدث خطأ في عرض الطلبات غير المكتملة")

async def handle_incomplete_order_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة اختيار طلبية غير مكتملة"""
    try:
        query = update.callback_query
        await query.answer()
        
        if query.data == "cancel_incomplete":
            await query.edit_message_text("تم إلغاء عملية تحميل الطلبات.")
            return
        
        if query.data.startswith("load_incomplete_"):
            order_id = query.data.replace("load_incomplete_", "")
            user_id = str(query.from_user.id)
            
            if order_id not in orders:
                await query.edit_message_text("❌ هذه الطلبية لم تعد موجودة.")
                return
            
            # حذف رسالة القائمة
            try:
                await query.message.delete()
            except:
                pass
            
            # عرض الطلبية المحددة بأزرارها
            await show_buttons(query.message.chat_id, context, user_id, order_id, 
                             confirmation_message="تم تحميل الطلبية غير المكتملة:")
            
    except Exception as e:
        logger.error(f"Error in handle_incomplete_order_selection: {e}")
        try:
            await query.edit_message_text("❌ حدث خطأ في تحميل الطلبية")
        except:
            pass
    
    
if __name__ == "__main__":
    main()
