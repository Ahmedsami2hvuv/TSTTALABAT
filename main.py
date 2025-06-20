from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler,
    MessageHandler, CallbackQueryHandler, ConversationHandler, filters
)
import uuid
import os
from collections import Counter
import json
import logging
import asyncio
import threading
import time

# تفعيل الـ logging للحصول على تفاصيل الأخطاء والعمليات
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# المسار الثابت لحفظ البيانات داخل وحدة التخزين (Volume)
DATA_DIR = "/mnt/data/"

# أسماء ملفات حفظ البيانات
ORDERS_FILE = os.path.join(DATA_DIR, "orders.json")
PRICING_FILE = os.path.join(DATA_DIR, "pricing.json")
INVOICE_NUMBERS_FILE = os.path.join(DATA_DIR, "invoice_numbers.json")
DAILY_PROFIT_FILE = os.path.join(DATA_DIR, "daily_profit.json")
COUNTER_FILE = os.path.join(DATA_DIR, "invoice_counter.txt")
LAST_BUTTON_MESSAGE_FILE = os.path.join(DATA_DIR, "last_button_message.json")
AREAS_FILE = os.path.join(DATA_DIR, "areas.json")  # ملف جديد لحفظ المناطق وأسعارها

# تهيئة المتغيرات العامة
orders = {}
pricing = {}
invoice_numbers = {}
daily_profit = 0.0
last_button_message = {}
areas = {  # القيمة الافتراضية للمناطق وأسعارها
    "الاسمدة": 3,
    "جيكور حزبه1": 3,
    "جيكور حزبه2": 3,
    "جيكور": 3,
    "العصفورية": 3,
    "باب سليمان": 3,
    "باب طويل": 3,
    "باب العريض": 3,
    "باب عباس": 3,
    "كوت بازل": 3,
    "باب دباغ": 3,
    "باب ميدان": 3,
    "بلد سلطان": 3,
    "ام الصخر": 3,
    "باب رمانه": 3,
    "اهل عيد": 3,
    "الباني": 3,
    "نهر خوز": 3,
    "ابو مغيرة": 3,
    "مجيبرة": 3,
    "السبيليات": 3,
    "الصنگر": 3,
    "محيلة قبل دورة ام زباله": 3,
    "طريق الوسطي": 3,
    "العاگولية": 3,
    "الصحراء": 3,
    "ابو كوصرة": 3,
    "طريزاوية": 3,
    "العوجة": 3,
    "المقيمين": 3,
    "الابطاح": 3,
    "اللكطة": 3,
    "الشجرة الطيبة": 3,
    "شيخ ابراهيم": 3,
    "نزيلة": 3,
    "عميرية": 3,
    "بلد": 3,
    "كوت البلجاني": 3,
    "الحوطة": 3,
    "السوق": 3,
    "الصنكر": 3,
    "محيله الوسطي": 3,
    "محيله قرب الجسر": 3,
    "محيله السوق": 3,
    "محيله قرب السيطرة": 3,
    "محيله شارع المشروع": 5,
    "محيله شارع سيد حامد": 5,
    "محيله شارع الاندلس": 5,
    "محيله الصكاروة": 5,
    "المناطق على": 5,
    "المعهد الصناعي": 5,
    "الدورة": 5,
    "الاندلس": 5,
    "الجديدة": 5,
    "الرومية": 5,
    "الصكاروة": 5,
    "كوت الصلحي": 5,
    "كوت الفداغ": 5,
    "جامع الشهيد": 5,
    "يوسفان": 5,
    "حمدان": 5,
    "كوت ثويني": 5,
    "البهادرية": 5,
    "محولة الزهير": 5,
    "كوت الحمداني": 5,
    "عويسيان": 5,
    "مهيجران": 5,
    "السراجي": 5,
    "عبد اليان": 5,
    "معمل الثرمستون": 3
}

# متغيرات الحفظ المؤجل
save_timer = None
save_pending = False
save_lock = threading.Lock()

# حالات المحادثة
ASK_BUY, ASK_SELL, ASK_PLACES_COUNT, ADD_AREA, REMOVE_AREA = range(5)  # أضفنا حالتين جديدتين لإدارة المناطق

# تحميل البيانات عند بدء تشغيل البوت
def load_data():
    global orders, pricing, invoice_numbers, daily_profit, last_button_message, areas

    os.makedirs(DATA_DIR, exist_ok=True)

    # Helper function to load a JSON file safely
    def load_json_file(filepath, default_value, var_name):
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

    orders_temp = load_json_file(ORDERS_FILE, {}, "orders")
    orders.clear()
    orders.update({str(k): v for k, v in orders_temp.items()})

    pricing_temp = load_json_file(PRICING_FILE, {}, "pricing")
    pricing.clear()
    pricing.update({str(pk): pv for pk, pv in pricing_temp.items()})
    for oid in pricing:
        if isinstance(pricing[oid], dict):
            pricing[oid] = {str(pk): pv for pk, pv in pricing[oid].items()}  # Ensure inner keys are strings too

    invoice_numbers_temp = load_json_file(INVOICE_NUMBERS_FILE, {}, "invoice_numbers")
    invoice_numbers.clear()
    invoice_numbers.update({str(k): v for k, v in invoice_numbers_temp.items()})

    daily_profit = load_json_file(DAILY_PROFIT_FILE, 0.0, "daily_profit")
    
    last_button_message_temp = load_json_file(LAST_BUTTON_MESSAGE_FILE, {}, "last_button_message")
    last_button_message.clear()
    last_button_message.update({str(k): v for k, v in last_button_message_temp.items()})

    # تحميل بيانات المناطق
    areas_temp = load_json_file(AREAS_FILE, areas, "areas")
    areas.clear()
    areas.update({str(k): v for k, v in areas_temp.items()})

    logger.info(f"Initial load complete. Orders: {len(orders)}, Pricing entries: {len(pricing)}, Daily Profit: {daily_profit}, Areas: {len(areas)}")

# حفظ البيانات
def _save_data_to_disk():
    global save_pending
    with save_lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        try:
            # Save to temporary files first, then rename to prevent data corruption
            with open(ORDERS_FILE + ".tmp", "w") as f:
                json.dump(orders, f, indent=4)  # Use indent for readability in files
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

            # حفظ بيانات المناطق
            with open(AREAS_FILE + ".tmp", "w") as f:
                json.dump(areas, f, indent=4, ensure_ascii=False)
            os.replace(AREAS_FILE + ".tmp", AREAS_FILE)

            logger.info("All data saved to disk successfully.")
            logger.info(f"Pricing state after _save_data_to_disk(): {pricing}")
        except Exception as e:
            logger.error(f"Error saving data to disk: {e}")
        finally:
            save_pending = False

# دالة الحفظ المؤجل
def schedule_save():
    global save_timer, save_pending
    if save_pending:
        logger.info("Save already pending, skipping new schedule.")
        return

    if save_timer is not None:
        save_timer.cancel()

    save_pending = True
    save_timer = threading.Timer(0.5, _save_data_to_disk)
    save_timer.start()
    logger.info("Data save scheduled with 0.5 sec delay.")

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

# تحميل البيانات عند بدء البوت
load_data()

# جلب التوكن ومعرف المالك من متغيرات البيئة
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_TELEGRAM_ID")) 
OWNER_PHONE_NUMBER = "+9647733921468" 

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
        await asyncio.sleep(0.1)  # زيادة التأخير لضمان ظهور الرسالة الجديدة
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.info(f"Successfully deleted message {message_id} from chat {chat_id} in background.")
    except Exception as e:
        logger.warning(f"Could not delete message {message_id} from chat {chat_id} in background: {e}.")

# دالة مساعدة لحفظ البيانات في الخلفية
async def save_data_in_background(context: ContextTypes.DEFAULT_TYPE):
    schedule_save()
    logger.info("Data save scheduled in background.")

# دالة للتحقق مما إذا كان النص يبدأ باسم منطقة معروفة
def detect_area(title):
    for area in areas:
        if title.strip().startswith(area):
            return area, areas[area]
    return None, 0

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    logger.info(f"[{update.effective_chat.id}] /start command from user {user_id}. User data before clearing: {json.dumps(context.user_data.get(user_id, {}), indent=2)}")
    if user_id in context.user_data:
        context.user_data[user_id].pop("order_id", None)
        context.user_data[user_id].pop("product", None)
        context.user_data[user_id].pop("current_active_order_id", None)
        context.user_data[user_id].pop("messages_to_delete", None) 
        context.user_data[user_id].pop("buy_price", None)  # Clear buy_price too
        logger.info(f"Cleared order-specific user_data for user {user_id} on /start command. User data after clearing: {json.dumps(context.user_data.get(user_id, {}), indent=2)}")
    
    await update.message.reply_text("أهلاً بك يا أبا الأكبر! لإعداد طلبية، دز الطلبية كلها برسالة واحدة.\n\n*السطر الأول:* عنوان الزبون (يمكن أن يبدأ باسم المنطقة).\n*الأسطر الباقية:* كل منتج بسطر واحد.", parse_mode="Markdown")
    return ConversationHandler.END

async def receive_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        logger.info(f"[{update.effective_chat.id}] Processing order from: {update.effective_user.id} - Message ID: {update.message.message_id}. User data: {json.dumps(context.user_data.get(str(update.effective_user.id), {}), indent=2)}")
        await process_order(update, context, update.message)
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in receive_order: {e}", exc_info=True)
        await update.message.reply_text("عذراً، حدث خطأ أثناء معالجة الطلب. الرجاء المحاولة مرة أخرى أو بدء طلبية جديدة.")
        return ConversationHandler.END

async def edited_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.edited_message:
            return
        logger.info(f"[{update.effective_chat.id}] Processing edited order from: {update.effective_user.id} - Message ID: {update.edited_message.message_id}. User data: {json.dumps(context.user_data.get(str(update.effective_user.id), {}), indent=2)}")
        await process_order(update, context, update.edited_message, edited=True)
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in edited_message: {e}", exc_info=True)
        await update.edited_message.reply_text("عذراً، حدث خطأ أثناء معالجة التعديل. الرجاء المحاولة مرة أخرى.")

async def process_order(update, context, message, edited=False):
    user_id = str(message.from_user.id)
    lines = [line.strip() for line in message.text.strip().split('\n') if line.strip()]
    
    if len(lines) < 2:
        if not edited:
            await message.reply_text("الرجاء التأكد من كتابة عنوان الزبون في السطر الأول والمنتجات في الأسطر التالية.")
        return

    title = lines[0]
    # تنظيف أسماء المنتجات عند الاستلام
    products = [p.strip() for p in lines[1:] if p.strip()]

    if not products:
        if not edited:
            await message.reply_text("الرجاء إضافة منتجات بعد العنوان.")
        return

    # الكشف عن المنطقة من العنوان
    detected_area, delivery_price = detect_area(title)
    if detected_area:
        logger.info(f"Detected area '{detected_area}' with delivery price {delivery_price}")
        # إزالة اسم المنطقة من العنوان إذا تم اكتشافه
        title = title.replace(detected_area, "").strip()
    else:
        logger.info("No known area detected in the title")
        delivery_price = 0

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
        orders[order_id] = {
            "user_id": user_id, 
            "title": title, 
            "products": products, 
            "places_count": 0,
            "delivery_area": detected_area,
            "delivery_price": delivery_price
        } 
        pricing[order_id] = {p: {} for p in products}
        invoice_numbers[order_id] = invoice_no
        logger.info(f"Created new order {order_id} for user {user_id}.")
    else: 
        old_products = set(orders[order_id].get("products", []))
        new_products = set(products)
        
        orders[order_id]["title"] = title
        orders[order_id]["products"] = products
        orders[order_id]["delivery_area"] = detected_area
        orders[order_id]["delivery_price"] = delivery_price

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
    
    if is_new_order:
        area_info = f" (منطقة التوصيل: {detected_area})" if detected_area else ""
        await message.reply_text(f"استلمت الطلب بعنوان: *{title}*{area_info} (عدد المنتجات: {len(products)})", parse_mode="Markdown")
        await show_buttons(message.chat_id, context, user_id, order_id)
    else:
        await show_buttons(message.chat_id, context, user_id, order_id, confirmation_message="تم تحديث الطلب. الرجاء التأكد من تسعير أي منتجات جديدة.")

# باقي الدوال (show_buttons, product_selected, receive_buy_price, receive_sell_price) تبقى كما هي بدون تغيير

async def request_places_count_standalone(chat_id, context: ContextTypes.DEFAULT_TYPE, user_id: str, order_id: str):
    try:
        logger.info(f"[{chat_id}] request_places_count_standalone called for order {order_id} from user {user_id}. User data: {json.dumps(context.user_data.get(user_id), indent=2)}")
        context.user_data.setdefault(user_id, {})["current_active_order_id"] = order_id

        buttons = []
        emojis = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟']
        for i in range(1, 11):
            buttons.append(InlineKeyboardButton(emojis[i-1], callback_data=f"places_data_{order_id}_{i}"))
        
        keyboard = [buttons[i:i + 5] for i in range(0, len(buttons), 5)]
        reply_markup = InlineKeyboardMarkup(keyboard)

        msg_places = await context.bot.send_message(
            chat_id=chat_id,
            text="تمام، كل المنتجات تسعّرت. هسه، كم محل كلفتك الطلبية؟ (اختر من الأزرار أو اكتب الرقم)", 
            reply_markup=reply_markup
        )
        
        # تخزين معرف الرسالة في user_data للرجوع إليها لاحقاً
        context.user_data[user_id]['places_count_message'] = {
            'chat_id': msg_places.chat_id,
            'message_id': msg_places.message_id
        }

        if user_id in context.user_data and 'messages_to_delete' in context.user_data[user_id]:
            logger.info(f"[{chat_id}] Scheduling deletion of {len(context.user_data[user_id].get('messages_to_delete', []))} old messages after showing places buttons for user {user_id}.")
            for msg_info in context.user_data[user_id]['messages_to_delete']:
                context.application.create_task(delete_message_in_background(context, chat_id=msg_info['chat_id'], message_id=msg_info['message_id']))
            context.user_data[user_id]['messages_to_delete'].clear()
    except Exception as e:
        logger.error(f"[{chat_id}] Error in request_places_count_standalone: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text="عذراً، حدث خطأ أثناء طلب عدد المحلات. الرجاء بدء طلبية جديدة.")

async def handle_places_count_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        global daily_profit
        
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
                        await context.bot.send_message(chat_id=chat_id, text="عذراً، الطلبية اللي حاول تختار عدد محلاتها ما موجودة عندي. الرجاء بدء طلبية جديدة.")
                        if user_id in context.user_data and "current_active_order_id" in context.user_data[user_id]:
                            del context.user_data[user_id]["current_active_order_id"]
                        return ConversationHandler.END 

                    places = int(parts[3])
                    if query.message:
                        try:
                            # حذف رسالة الأزرار بعد الاختيار
                            await context.bot.delete_message(chat_id=query.message.chat_id, message_id=query.message.message_id)
                        except Exception as e:
                            logger.warning(f"[{chat_id}] Could not delete places message {query.message.message_id} directly: {e}. Proceeding.")

                else:
                    raise ValueError(f"Unexpected callback_data format for places count: {query.data}")
            except (ValueError, IndexError) as e:
                logger.error(f"[{chat_id}] Failed to parse places count from callback data '{query.data}': {e}", exc_info=True)
                await context.bot.send_message(chat_id=chat_id, text="عذراً، حدث خطأ في بيانات الزر. الرجاء المحاولة مرة أخرى.")
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
                msg_error = await context.bot.send_message(chat_id=chat_id, text="الرجاء إدخال *رقم صحيح* لعدد المحلات.")
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
                msg_error = await context.bot.send_message(chat_id=chat_id, text="الرجاء إدخال عدد صحيح لعدد المحلات.")
                context.user_data[user_id]['messages_to_delete'].append({'chat_id': msg_error.chat_id, 'message_id': msg_error.message_id})
                return ASK_PLACES_COUNT 
        
        if places is None or order_id_to_process is None:
            logger.warning(f"[{chat_id}] handle_places_count_data: No valid places count or order ID to process.")
            await context.bot.send_message(chat_id=chat_id, text="عذراً، لم أتمكن من فهم عدد المحلات أو الطلبية. الرجاء إدخال رقم صحيح أو البدء بطلبية جديدة.")
            if user_id in context.user_data and "current_active_order_id" in context.user_data[user_id]:
                del context.user_data[user_id]["current_active_order_id"]
            return ConversationHandler.END 

        # حذف رسالة الأزرار الخاصة بعدد المحلات إذا كانت موجودة ولم يتم حذفها عن طريق الـ callback
        if 'places_count_message' in context.user_data[user_id]:
            msg_info = context.user_data[user_id]['places_count_message']
            try:
                await context.bot.delete_message(chat_id=msg_info['chat_id'], message_id=msg_info['message_id'])
            except Exception as e:
                logger.warning(f"[{chat_id}] Could not delete places count message: {e}")
            del context.user_data[user_id]['places_count_message']

        orders[order_id_to_process]["places_count"] = places
        context.application.create_task(save_data_in_background(context))
        logger.info(f"[{chat_id}] Places count {places} saved for order {order_id_to_process}. Current user_data: {json.dumps(context.user_data.get(user_id), indent=2)}")

        if user_id in context.user_data and 'messages_to_delete' in context.user_data[user_id]:
            logger.info(f"[{chat_id}] Scheduling deletion of {len(context.user_data[user_id].get('messages_to_delete', []))} old messages after showing final options for user {user_id}.")
            for msg_info in context.user_data[user_id]['messages_to_delete']:
                context.application.create_task(delete_message_in_background(context, chat_id=msg_info['chat_id'], message_id=msg_info['message_id']))
            context.user_data[user_id]['messages_to_delete'].clear()
        
        await show_final_options(chat_id, context, user_id, order_id_to_process, message_prefix="تم تحديث عدد المحلات بنجاح.")
        
        if user_id in context.user_data and "current_active_order_id" in context.user_data[user_id]:
            del context.user_data[user_id]["current_active_order_id"]
            logger.info(f"[{chat_id}] Cleared current_active_order_id for user {user_id} after processing places count.")

        return ConversationHandler.END 
    except Exception as e:
        logger.error(f"[{chat_id}] Error in handle_places_count_data: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text="عذراً، حدث خطأ أثناء معالجة عدد المحلات. الرجاء بدء طلبية جديدة.")
        return ConversationHandler.END
        
async def show_final_options(chat_id, context, user_id, order_id, message_prefix=None):
    try:
        global daily_profit
        logger.info(f"[{chat_id}] Showing final options for order {order_id} to user {user_id}. User data: {json.dumps(context.user_data.get(user_id), indent=2)}")
        
        if order_id not in orders:
            logger.warning(f"[{chat_id}] Attempted to show final options for non-existent order_id: {order_id}")
            await context.bot.send_message(chat_id=chat_id, text="عذراً، الطلب الذي تحاول الوصول إليه غير موجود أو تم حذفه. الرجاء بدء طلبية جديدة.")
            if user_id in context.user_data:
                context.user_data[user_id].pop("order_id", None)
                context.user_data[user_id].pop("product", None)
                context.user_data[user_id].pop("current_active_order_id", None)
                context.user_data[user_id].pop("messages_to_delete", None)
            return

        order = orders[order_id]
        invoice = invoice_numbers.get(order_id, "غير معروف")
        
        total_buy = 0.0
        total_sell = 0.0
        for p in order["products"]:
            if p in pricing.get(order_id, {}) and "buy" in pricing[order_id].get(p, {}) and "sell" in pricing[order_id].get(p, {}):
                total_buy += pricing[order_id][p]["buy"]
                total_sell += pricing[order_id][p]["sell"]

        net_profit = total_sell - total_buy
        
        current_places = orders[order_id].get("places_count", 0) 
        extra_cost = calculate_extra(current_places)
        delivery_cost = order.get("delivery_price", 0)
        final_total = total_sell + extra_cost + delivery_cost

        logger.info(f"[{chat_id}] Daily profit before addition for order {order_id}: {daily_profit}")
        daily_profit += net_profit
        logger.info(f"[{chat_id}] Daily profit after adding {net_profit} for order {order_id}: {daily_profit}")
        context.application.create_task(save_data_in_background(context))

        customer_invoice_lines = []
        customer_invoice_lines.append(f"**أبو الأكبر للتوصيل**") 
        customer_invoice_lines.append(f"رقم الفاتورة: {invoice}")
        customer_invoice_lines.append(f"عنوان الزبون: {order['title']}")
        if order.get("delivery_area"):
            customer_invoice_lines.append(f"منطقة التوصيل: {order['delivery_area']}")
        
        customer_invoice_lines.append(f"\n*المواد:*") 
        
        running_total_for_customer = 0.0
        for p in order["products"]:
            if p in pricing.get(order_id, {}) and "sell" in pricing[order_id].get(p, {}):
                sell = pricing[order_id][p]["sell"]
                running_total_for_customer += sell
                customer_invoice_lines.append(f"{p} - {format_float(sell)} = {format_float(running_total_for_customer)}")
            else:
                customer_invoice_lines.append(f"{p} - (لم يتم تسعيره)")
        
        customer_invoice_lines.append(f"كلفة تجهيز من - {current_places} محلات {format_float(extra_cost)} = {format_float(running_total_for_customer + extra_cost)}")
        if delivery_cost > 0:
            customer_invoice_lines.append(f"أجرة توصيل - {format_float(delivery_cost)} = {format_float(running_total_for_customer + extra_cost + delivery_cost)}")
        customer_invoice_lines.append(f"\n*المجموع الكلي:* {format_float(final_total)}") 
        
        customer_final_text = "\n".join(customer_invoice_lines)

        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=customer_final_text,
                parse_mode="Markdown"
            )
            logger.info(f"[{chat_id}] Customer invoice sent as a separate message for order {order_id}.")
        except Exception as e:
            logger.error(f"[{chat_id}] Could not send customer invoice as separate message to chat {chat_id}: {e}", exc_info=True)
            await context.bot.send_message(chat_id=chat_id, text="عذراً، لم أتمكن من إرسال فاتورة الزبون. الرجاء المحاولة مرة أخرى.")

        keyboard = [
            [InlineKeyboardButton("1️⃣ تعديل الأسعار", callback_data=f"edit_prices_{order_id}")],
            [InlineKeyboardButton("3️⃣ إرسال فاتورة الزبون (واتساب)", url=f"https://wa.me/{OWNER_PHONE_NUMBER}?text={customer_final_text.replace(' ', '%20').replace('\n', '%0A').replace('*', '')}")],
            [InlineKeyboardButton("4️⃣ إنشاء طلب جديد", callback_data="start_new_order")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        message_text = "افعل ما تريد من الأزرار:\n\n"
        if message_prefix:
            message_text = message_prefix + "\n" + message_text
        
        owner_invoice_details = []
        owner_invoice_details.append(f"رقم الفاتورة: {invoice}")
        owner_invoice_details.append(f"عنوان الزبون: {order['title']}")
        if order.get("delivery_area"):
            owner_invoice_details.append(f"منطقة التوصيل: {order['delivery_area']} (سعر التوصيل: {format_float(delivery_cost)})")
        
        for p in order["products"]:
            if p in pricing.get(order_id, {}) and "buy" in pricing[order_id].get(p, {}) and "sell" in pricing[order_id].get(p, {}):
                buy = pricing[order_id][p]["buy"]
                sell = pricing[order_id][p]["sell"] 
                profit_item = sell - buy
                owner_invoice_details.append(f"{p} - شراء: {format_float(buy)}, بيع: {format_float(sell)}, ربح: {format_float(profit_item)}")
            else:
                owner_invoice_details.append(f"{p} - (لم يتم تسعيره بعد)")
        owner_invoice_details.append(f"\nالمجموع شراء: {format_float(total_buy)}")
        owner_invoice_details.append(f"المجموع بيع: {format_float(total_sell)}")
        owner_invoice_details.append(f"الربح الكلي: {format_float(net_profit)}")
        owner_invoice_details.append(f"عدد المحلات: {current_places} (+{format_float(extra_cost)})")
        if delivery_cost > 0:
            owner_invoice_details.append(f"أجرة التوصيل: {format_float(delivery_cost)}")
        owner_invoice_details.append(f"السعر الكلي: {format_float(final_total)}")
        
        final_owner_invoice_text = "\n".join(owner_invoice_details)
        
        encoded_owner_invoice = final_owner_invoice_text.replace(" ", "%20").replace("\n", "%0A").replace("*", "")
        whatsapp_owner_button_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("إرسال فاتورة الإدارة للواتساب", url=f"https://wa.me/{OWNER_PHONE_NUMBER}?text={encoded_owner_invoice}")]
        ])

        try:
            await context.bot.send_message(
                chat_id=OWNER_ID,
                text=f"**فاتورة طلبية (الإدارة):**\n{final_owner_invoice_text}",
                parse_mode="Markdown",
                reply_markup=whatsapp_owner_button_markup
            )
            logger.info(f"[{chat_id}] Admin invoice and WhatsApp button sent to OWNER_ID: {OWNER_ID}")
        except Exception as e:
            logger.error(f"[{chat_id}] Could not send admin invoice to OWNER_ID {OWNER_ID}: {e}", exc_info=True)
            await context.bot.send_message(chat_id=chat_id, text="عذراً، لم أتمكن من إرسال فاتورة الإدارة إلى خاصك. يرجى التأكد من أنني أستطيع مراسلتك في الخاص (قد تحتاج إلى بدء محادثة معي أولاً).")

        await context.bot.send_message(chat_id=chat_id, text=message_text, reply_markup=reply_markup, parse_mode="Markdown")
        
        if user_id in context.user_data: 
            if 'messages_to_delete' in context.user_data[user_id]:
                for msg_info in context.user_data[user_id]['messages_to_delete']:
                    context.application.create_task(delete_message_in_background(context, chat_id=msg_info['chat_id'], message_id=msg_info['message_id']))
                context.user_data[user_id]['messages_to_delete'].clear()
            
            context.user_data[user_id].pop("order_id", None)
            context.user_data[user_id].pop("product", None)
            context.user_data[user_id].pop("current_active_order_id", None) 
            context.user_data[user_id].pop("buy_price", None)  # Clear buy_price too
            logger.info(f"[{chat_id}] Cleaned up order-specific user_data for user {user_id} after showing final options. User data after clean: {json.dumps(context.user_data.get(user_id, {}), indent=2)}")
    except Exception as e:
        logger.error(f"[{chat_id}] Error in show_final_options: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text="عذراً، حدث خطأ أثناء عرض الفاتورة النهائية. الرجاء بدء طلبية جديدة.")

# دالة لعرض قائمة المناطق
async def show_areas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.message.from_user.id)
        if user_id != str(OWNER_ID):
            await update.message.reply_text("عذراً، هذا الأمر متاح للمالك فقط.")
            return

        areas_list = []
        for area, price in sorted(areas.items()):
            areas_list.append(f"- {area}: {price} دينار")

        keyboard = [
            [InlineKeyboardButton("➕ إضافة منطقة", callback_data="add_area")],
            [InlineKeyboardButton("➖ حذف منطقة", callback_data="remove_area")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            f"**قائمة المناطق وأسعار التوصيل:**\n\n" + "\n".join(areas_list),
            parse_mode="Markdown",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in show_areas: {e}", exc_info=True)
        await update.message.reply_text("عذراً، حدث خطأ أثناء عرض قائمة المناطق.")

# دالة لبدء إضافة منطقة جديدة
async def add_area_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()

        if str(query.from_user.id) != str(OWNER_ID):
            await query.edit_message_text("عذراً، هذا الأمر متاح للمالك فقط.")
            return

        await query.edit_message_text("أدخل اسم المنطقة الجديدة وسعر التوصيل بالشكل التالي:\nاسم المنطقة سعر التوصيل\nمثال: المنطقة الجديدة 5")
        return ADD_AREA
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in add_area_start: {e}", exc_info=True)
        await query.edit_message_text("عذراً، حدث خطأ أثناء محاولة إضافة منطقة جديدة.")
        return ConversationHandler.END

# دالة لمعالجة إضافة منطقة جديدة
async def add_area_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.message.from_user.id)
        if user_id != str(OWNER_ID):
            await update.message.reply_text("عذراً، هذا الأمر متاح للمالك فقط.")
            return ConversationHandler.END

        parts = update.message.text.strip().rsplit(' ', 1)
        if len(parts) != 2:
            await update.message.reply_text("الرجاء إدخال البيانات بالشكل الصحيح:\nاسم المنطقة سعر التوصيل\nمثال: المنطقة الجديدة 5")
            return ADD_AREA

        area_name = parts[0].strip()
        try:
            area_price = float(parts[1])
            if area_price <= 0:
                await update.message.reply_text("سعر التوصيل يجب أن يكون أكبر من صفر.")
                return ADD_AREA
        except ValueError:
            await update.message.reply_text("الرجاء إدخال سعر توصيل صحيح.")
            return ADD_AREA

        areas[area_name] = area_price
        context.application.create_task(save_data_in_background(context))
        await update.message.reply_text(f"تمت إضافة المنطقة '{area_name}' بسعر توصيل {area_price} دينار بنجاح.")
        
        # عرض قائمة المناطق المحدثة
        await show_areas(update, context)
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in add_area_process: {e}", exc_info=True)
        await update.message.reply_text("عذراً، حدث خطأ أثناء إضافة المنطقة.")
        return ConversationHandler.END

# دالة لبدء حذف منطقة
async def remove_area_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()

        if str(query.from_user.id) != str(OWNER_ID):
            await query.edit_message_text("عذراً، هذا الأمر متاح للمالك فقط.")
            return

        if not areas:
            await query.edit_message_text("لا توجد مناطق مسجلة للحذف.")
            return

        buttons = [[InlineKeyboardButton(area, callback_data=f"remove_{area}")] for area in sorted(areas.keys())]
        reply_markup = InlineKeyboardMarkup(buttons)
        await query.edit_message_text("اختر المنطقة التي تريد حذفها:", reply_markup=reply_markup)
        return REMOVE_AREA
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in remove_area_start: {e}", exc_info=True)
        await query.edit_message_text("عذراً، حدث خطأ أثناء محاولة حذف منطقة.")
        return ConversationHandler.END

# دالة لمعالجة حذف منطقة
async def remove_area_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()

        if str(query.from_user.id) != str(OWNER_ID):
            await query.edit_message_text("عذراً، هذا الأمر متاح للمالك فقط.")
            return ConversationHandler.END

        area_to_remove = query.data.replace("remove_", "")
        if area_to_remove in areas:
            del areas[area_to_remove]
            context.application.create_task(save_data_in_background(context))
            await query.edit_message_text(f"تم حذف المنطقة '{area_to_remove}' بنجاح.")
            
            # عرض قائمة المناطق المحدثة
            await show_areas(update, context)
        else:
            await query.edit_message_text("عذراً، المنطقة المحددة غير موجودة.")
        
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in remove_area_process: {e}", exc_info=True)
        await query.edit_message_text("عذراً، حدث خطأ أثناء حذف المنطقة.")
        return ConversationHandler.END

# باقي الدوال (edit_prices, start_new_order_callback, show_profit, reset_all, confirm_reset, show_report) تبقى كما هي بدون تغيير

def main():
    app = ApplicationBuilder().token(TOKEN).build()

    # Handlers لا تدخل في أي ConversationHandler (مثل الـ /start والأوامر الإدارية)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^الارباح$|^ارباح$"), show_profit))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^صفر$|^تصفير$"), reset_all))
    app.add_handler(CallbackQueryHandler(confirm_reset, pattern="^(confirm_reset|cancel_reset)$"))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^التقارير$|^تقرير$|^تقارير$"), show_report))
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE, edited_message))
    app.add_handler(CallbackQueryHandler(edit_prices, pattern="^edit_prices_"))
    app.add_handler(CallbackQueryHandler(start_new_order_callback, pattern="^start_new_order$"))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^المناطق$|^مناطق$"), show_areas))

    # ConversationHandler لإدارة المناطق
    areas_conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(add_area_start, pattern="^add_area$"),
            CallbackQueryHandler(remove_area_start, pattern="^remove_area$")
        ],
        states={
            ADD_AREA: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_area_process)
            ],
            REMOVE_AREA: [
                CallbackQueryHandler(remove_area_process, pattern=r"^remove_.+$")
            ]
        },
        fallbacks=[
            CommandHandler("cancel", lambda u, c: ConversationHandler.END),
            MessageHandler(filters.ALL, lambda u, c: ConversationHandler.END)
        ]
    )
    app.add_handler(areas_conv_handler)

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
    
    # ConversationHandler لإنشاء الطلب وتسعير المنتجات
    order_creation_conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_order),
            CallbackQueryHandler(product_selected, pattern=r"^[a-f0-9]{8}\|.+$")
        ],
        states={
            ASK_BUY: [
                MessageHandler(filters.TEXT & filters.Regex(r"^\d+(\.\d+)?$") & ~filters.COMMAND, receive_buy_price),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_buy_price),
            ],
            ASK_SELL: [
                MessageHandler(filters.TEXT & filters.Regex(r"^\d+(\.\d+)?$") & ~filters.COMMAND, receive_sell_price),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_sell_price),
            ]
        },
        fallbacks=[
            CommandHandler("cancel", lambda u, c: ConversationHandler.END),
            MessageHandler(filters.ALL, lambda u, c: ConversationHandler.END)
        ]
    )
    app.add_handler(order_creation_conv_handler)
    
    logger.info("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler,
    MessageHandler, CallbackQueryHandler, ConversationHandler, filters
)
import uuid
import os
from collections import Counter
import json
import logging
import asyncio
import threading
import time

# تفعيل الـ logging للحصول على تفاصيل الأخطاء والعمليات
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# المسار الثابت لحفظ البيانات داخل وحدة التخزين (Volume)
DATA_DIR = "/mnt/data/"

# أسماء ملفات حفظ البيانات
ORDERS_FILE = os.path.join(DATA_DIR, "orders.json")
PRICING_FILE = os.path.join(DATA_DIR, "pricing.json")
INVOICE_NUMBERS_FILE = os.path.join(DATA_DIR, "invoice_numbers.json")
DAILY_PROFIT_FILE = os.path.join(DATA_DIR, "daily_profit.json")
COUNTER_FILE = os.path.join(DATA_DIR, "invoice_counter.txt")
LAST_BUTTON_MESSAGE_FILE = os.path.join(DATA_DIR, "last_button_message.json")
AREAS_FILE = os.path.join(DATA_DIR, "areas.json")  # ملف جديد لحفظ المناطق وأسعارها

# تهيئة المتغيرات العامة
orders = {}
pricing = {}
invoice_numbers = {}
daily_profit = 0.0
last_button_message = {}
areas = {  # القيمة الافتراضية للمناطق وأسعارها
    "الاسمدة": 3,
    "جيكور حزبه1": 3,
    "جيكور حزبه2": 3,
    "جيكور": 3,
    "العصفورية": 3,
    "باب سليمان": 3,
    "باب طويل": 3,
    "باب العريض": 3,
    "باب عباس": 3,
    "كوت بازل": 3,
    "باب دباغ": 3,
    "باب ميدان": 3,
    "بلد سلطان": 3,
    "ام الصخر": 3,
    "باب رمانه": 3,
    "اهل عيد": 3,
    "الباني": 3,
    "نهر خوز": 3,
    "ابو مغيرة": 3,
    "مجيبرة": 3,
    "السبيليات": 3,
    "الصنگر": 3,
    "محيلة قبل دورة ام زباله": 3,
    "طريق الوسطي": 3,
    "العاگولية": 3,
    "الصحراء": 3,
    "ابو كوصرة": 3,
    "طريزاوية": 3,
    "العوجة": 3,
    "المقيمين": 3,
    "الابطاح": 3,
    "اللكطة": 3,
    "الشجرة الطيبة": 3,
    "شيخ ابراهيم": 3,
    "نزيلة": 3,
    "عميرية": 3,
    "بلد": 3,
    "كوت البلجاني": 3,
    "الحوطة": 3,
    "السوق": 3,
    "الصنكر": 3,
    "محيله الوسطي": 3,
    "محيله قرب الجسر": 3,
    "محيله السوق": 3,
    "محيله قرب السيطرة": 3,
    "محيله شارع المشروع": 5,
    "محيله شارع سيد حامد": 5,
    "محيله شارع الاندلس": 5,
    "محيله الصكاروة": 5,
    "المناطق على": 5,
    "المعهد الصناعي": 5,
    "الدورة": 5,
    "الاندلس": 5,
    "الجديدة": 5,
    "الرومية": 5,
    "الصكاروة": 5,
    "كوت الصلحي": 5,
    "كوت الفداغ": 5,
    "جامع الشهيد": 5,
    "يوسفان": 5,
    "حمدان": 5,
    "كوت ثويني": 5,
    "البهادرية": 5,
    "محولة الزهير": 5,
    "كوت الحمداني": 5,
    "عويسيان": 5,
    "مهيجران": 5,
    "السراجي": 5,
    "عبد اليان": 5,
    "معمل الثرمستون": 3
}

# متغيرات الحفظ المؤجل
save_timer = None
save_pending = False
save_lock = threading.Lock()

# حالات المحادثة
ASK_BUY, ASK_SELL, ASK_PLACES_COUNT, ADD_AREA, REMOVE_AREA = range(5)  # أضفنا حالتين جديدتين لإدارة المناطق

# تحميل البيانات عند بدء تشغيل البوت
def load_data():
    global orders, pricing, invoice_numbers, daily_profit, last_button_message, areas

    os.makedirs(DATA_DIR, exist_ok=True)

    # Helper function to load a JSON file safely
    def load_json_file(filepath, default_value, var_name):
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

    orders_temp = load_json_file(ORDERS_FILE, {}, "orders")
    orders.clear()
    orders.update({str(k): v for k, v in orders_temp.items()})

    pricing_temp = load_json_file(PRICING_FILE, {}, "pricing")
    pricing.clear()
    pricing.update({str(pk): pv for pk, pv in pricing_temp.items()})
    for oid in pricing:
        if isinstance(pricing[oid], dict):
            pricing[oid] = {str(pk): pv for pk, pv in pricing[oid].items()}  # Ensure inner keys are strings too

    invoice_numbers_temp = load_json_file(INVOICE_NUMBERS_FILE, {}, "invoice_numbers")
    invoice_numbers.clear()
    invoice_numbers.update({str(k): v for k, v in invoice_numbers_temp.items()})

    daily_profit = load_json_file(DAILY_PROFIT_FILE, 0.0, "daily_profit")
    
    last_button_message_temp = load_json_file(LAST_BUTTON_MESSAGE_FILE, {}, "last_button_message")
    last_button_message.clear()
    last_button_message.update({str(k): v for k, v in last_button_message_temp.items()})

    # تحميل بيانات المناطق
    areas_temp = load_json_file(AREAS_FILE, areas, "areas")
    areas.clear()
    areas.update({str(k): v for k, v in areas_temp.items()})

    logger.info(f"Initial load complete. Orders: {len(orders)}, Pricing entries: {len(pricing)}, Daily Profit: {daily_profit}, Areas: {len(areas)}")

# حفظ البيانات
def _save_data_to_disk():
    global save_pending
    with save_lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        try:
            # Save to temporary files first, then rename to prevent data corruption
            with open(ORDERS_FILE + ".tmp", "w") as f:
                json.dump(orders, f, indent=4)  # Use indent for readability in files
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

            # حفظ بيانات المناطق
            with open(AREAS_FILE + ".tmp", "w") as f:
                json.dump(areas, f, indent=4, ensure_ascii=False)
            os.replace(AREAS_FILE + ".tmp", AREAS_FILE)

            logger.info("All data saved to disk successfully.")
            logger.info(f"Pricing state after _save_data_to_disk(): {pricing}")
        except Exception as e:
            logger.error(f"Error saving data to disk: {e}")
        finally:
            save_pending = False

# دالة الحفظ المؤجل
def schedule_save():
    global save_timer, save_pending
    if save_pending:
        logger.info("Save already pending, skipping new schedule.")
        return

    if save_timer is not None:
        save_timer.cancel()

    save_pending = True
    save_timer = threading.Timer(0.5, _save_data_to_disk)
    save_timer.start()
    logger.info("Data save scheduled with 0.5 sec delay.")

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

# تحميل البيانات عند بدء البوت
load_data()

# جلب التوكن ومعرف المالك من متغيرات البيئة
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_TELEGRAM_ID")) 
OWNER_PHONE_NUMBER = "+9647733921468" 

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
        await asyncio.sleep(0.1)  # زيادة التأخير لضمان ظهور الرسالة الجديدة
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.info(f"Successfully deleted message {message_id} from chat {chat_id} in background.")
    except Exception as e:
        logger.warning(f"Could not delete message {message_id} from chat {chat_id} in background: {e}.")

# دالة مساعدة لحفظ البيانات في الخلفية
async def save_data_in_background(context: ContextTypes.DEFAULT_TYPE):
    schedule_save()
    logger.info("Data save scheduled in background.")

# دالة للتحقق مما إذا كان النص يبدأ باسم منطقة معروفة
def detect_area(title):
    for area in areas:
        if title.strip().startswith(area):
            return area, areas[area]
    return None, 0

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    logger.info(f"[{update.effective_chat.id}] /start command from user {user_id}. User data before clearing: {json.dumps(context.user_data.get(user_id, {}), indent=2)}")
    if user_id in context.user_data:
        context.user_data[user_id].pop("order_id", None)
        context.user_data[user_id].pop("product", None)
        context.user_data[user_id].pop("current_active_order_id", None)
        context.user_data[user_id].pop("messages_to_delete", None) 
        context.user_data[user_id].pop("buy_price", None)  # Clear buy_price too
        logger.info(f"Cleared order-specific user_data for user {user_id} on /start command. User data after clearing: {json.dumps(context.user_data.get(user_id, {}), indent=2)}")
    
    await update.message.reply_text("أهلاً بك يا أبا الأكبر! لإعداد طلبية، دز الطلبية كلها برسالة واحدة.\n\n*السطر الأول:* عنوان الزبون (يمكن أن يبدأ باسم المنطقة).\n*الأسطر الباقية:* كل منتج بسطر واحد.", parse_mode="Markdown")
    return ConversationHandler.END

async def receive_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        logger.info(f"[{update.effective_chat.id}] Processing order from: {update.effective_user.id} - Message ID: {update.message.message_id}. User data: {json.dumps(context.user_data.get(str(update.effective_user.id), {}), indent=2)}")
        await process_order(update, context, update.message)
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in receive_order: {e}", exc_info=True)
        await update.message.reply_text("عذراً، حدث خطأ أثناء معالجة الطلب. الرجاء المحاولة مرة أخرى أو بدء طلبية جديدة.")
        return ConversationHandler.END

async def edited_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.edited_message:
            return
        logger.info(f"[{update.effective_chat.id}] Processing edited order from: {update.effective_user.id} - Message ID: {update.edited_message.message_id}. User data: {json.dumps(context.user_data.get(str(update.effective_user.id), {}), indent=2)}")
        await process_order(update, context, update.edited_message, edited=True)
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in edited_message: {e}", exc_info=True)
        await update.edited_message.reply_text("عذراً، حدث خطأ أثناء معالجة التعديل. الرجاء المحاولة مرة أخرى.")

async def process_order(update, context, message, edited=False):
    user_id = str(message.from_user.id)
    lines = [line.strip() for line in message.text.strip().split('\n') if line.strip()]
    
    if len(lines) < 2:
        if not edited:
            await message.reply_text("الرجاء التأكد من كتابة عنوان الزبون في السطر الأول والمنتجات في الأسطر التالية.")
        return

    title = lines[0]
    # تنظيف أسماء المنتجات عند الاستلام
    products = [p.strip() for p in lines[1:] if p.strip()]

    if not products:
        if not edited:
            await message.reply_text("الرجاء إضافة منتجات بعد العنوان.")
        return

    # الكشف عن المنطقة من العنوان
    detected_area, delivery_price = detect_area(title)
    if detected_area:
        logger.info(f"Detected area '{detected_area}' with delivery price {delivery_price}")
        # إزالة اسم المنطقة من العنوان إذا تم اكتشافه
        title = title.replace(detected_area, "").strip()
    else:
        logger.info("No known area detected in the title")
        delivery_price = 0

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
        orders[order_id] = {
            "user_id": user_id, 
            "title": title, 
            "products": products, 
            "places_count": 0,
            "delivery_area": detected_area,
            "delivery_price": delivery_price
        } 
        pricing[order_id] = {p: {} for p in products}
        invoice_numbers[order_id] = invoice_no
        logger.info(f"Created new order {order_id} for user {user_id}.")
    else: 
        old_products = set(orders[order_id].get("products", []))
        new_products = set(products)
        
        orders[order_id]["title"] = title
        orders[order_id]["products"] = products
        orders[order_id]["delivery_area"] = detected_area
        orders[order_id]["delivery_price"] = delivery_price

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
    
    if is_new_order:
        area_info = f" (منطقة التوصيل: {detected_area})" if detected_area else ""
        await message.reply_text(f"استلمت الطلب بعنوان: *{title}*{area_info} (عدد المنتجات: {len(products)})", parse_mode="Markdown")
        await show_buttons(message.chat_id, context, user_id, order_id)
    else:
        await show_buttons(message.chat_id, context, user_id, order_id, confirmation_message="تم تحديث الطلب. الرجاء التأكد من تسعير أي منتجات جديدة.")

# باقي الدوال (show_buttons, product_selected, receive_buy_price, receive_sell_price) تبقى كما هي بدون تغيير

async def request_places_count_standalone(chat_id, context: ContextTypes.DEFAULT_TYPE, user_id: str, order_id: str):
    try:
        logger.info(f"[{chat_id}] request_places_count_standalone called for order {order_id} from user {user_id}. User data: {json.dumps(context.user_data.get(user_id), indent=2)}")
        context.user_data.setdefault(user_id, {})["current_active_order_id"] = order_id

        buttons = []
        emojis = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟']
        for i in range(1, 11):
            buttons.append(InlineKeyboardButton(emojis[i-1], callback_data=f"places_data_{order_id}_{i}"))
        
        keyboard = [buttons[i:i + 5] for i in range(0, len(buttons), 5)]
        reply_markup = InlineKeyboardMarkup(keyboard)

        msg_places = await context.bot.send_message(
            chat_id=chat_id,
            text="تمام، كل المنتجات تسعّرت. هسه، كم محل كلفتك الطلبية؟ (اختر من الأزرار أو اكتب الرقم)", 
            reply_markup=reply_markup
        )
        
        # تخزين معرف الرسالة في user_data للرجوع إليها لاحقاً
        context.user_data[user_id]['places_count_message'] = {
            'chat_id': msg_places.chat_id,
            'message_id': msg_places.message_id
        }

        if user_id in context.user_data and 'messages_to_delete' in context.user_data[user_id]:
            logger.info(f"[{chat_id}] Scheduling deletion of {len(context.user_data[user_id].get('messages_to_delete', []))} old messages after showing places buttons for user {user_id}.")
            for msg_info in context.user_data[user_id]['messages_to_delete']:
                context.application.create_task(delete_message_in_background(context, chat_id=msg_info['chat_id'], message_id=msg_info['message_id']))
            context.user_data[user_id]['messages_to_delete'].clear()
    except Exception as e:
        logger.error(f"[{chat_id}] Error in request_places_count_standalone: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text="عذراً، حدث خطأ أثناء طلب عدد المحلات. الرجاء بدء طلبية جديدة.")

async def handle_places_count_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        global daily_profit
        
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
                        await context.bot.send_message(chat_id=chat_id, text="عذراً، الطلبية اللي حاول تختار عدد محلاتها ما موجودة عندي. الرجاء بدء طلبية جديدة.")
                        if user_id in context.user_data and "current_active_order_id" in context.user_data[user_id]:
                            del context.user_data[user_id]["current_active_order_id"]
                        return ConversationHandler.END 

                    places = int(parts[3])
                    if query.message:
                        try:
                            # حذف رسالة الأزرار بعد الاختيار
                            await context.bot.delete_message(chat_id=query.message.chat_id, message_id=query.message.message_id)
                        except Exception as e:
                            logger.warning(f"[{chat_id}] Could not delete places message {query.message.message_id} directly: {e}. Proceeding.")

                else:
                    raise ValueError(f"Unexpected callback_data format for places count: {query.data}")
            except (ValueError, IndexError) as e:
                logger.error(f"[{chat_id}] Failed to parse places count from callback data '{query.data}': {e}", exc_info=True)
                await context.bot.send_message(chat_id=chat_id, text="عذراً، حدث خطأ في بيانات الزر. الرجاء المحاولة مرة أخرى.")
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
                msg_error = await context.bot.send_message(chat_id=chat_id, text="الرجاء إدخال *رقم صحيح* لعدد المحلات.")
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
                msg_error = await context.bot.send_message(chat_id=chat_id, text="الرجاء إدخال عدد صحيح لعدد المحلات.")
                context.user_data[user_id]['messages_to_delete'].append({'chat_id': msg_error.chat_id, 'message_id': msg_error.message_id})
                return ASK_PLACES_COUNT 
        
        if places is None or order_id_to_process is None:
            logger.warning(f"[{chat_id}] handle_places_count_data: No valid places count or order ID to process.")
            await context.bot.send_message(chat_id=chat_id, text="عذراً، لم أتمكن من فهم عدد المحلات أو الطلبية. الرجاء إدخال رقم صحيح أو البدء بطلبية جديدة.")
            if user_id in context.user_data and "current_active_order_id" in context.user_data[user_id]:
                del context.user_data[user_id]["current_active_order_id"]
            return ConversationHandler.END 

        # حذف رسالة الأزرار الخاصة بعدد المحلات إذا كانت موجودة ولم يتم حذفها عن طريق الـ callback
        if 'places_count_message' in context.user_data[user_id]:
            msg_info = context.user_data[user_id]['places_count_message']
            try:
                await context.bot.delete_message(chat_id=msg_info['chat_id'], message_id=msg_info['message_id'])
            except Exception as e:
                logger.warning(f"[{chat_id}] Could not delete places count message: {e}")
            del context.user_data[user_id]['places_count_message']

        orders[order_id_to_process]["places_count"] = places
        context.application.create_task(save_data_in_background(context))
        logger.info(f"[{chat_id}] Places count {places} saved for order {order_id_to_process}. Current user_data: {json.dumps(context.user_data.get(user_id), indent=2)}")

        if user_id in context.user_data and 'messages_to_delete' in context.user_data[user_id]:
            logger.info(f"[{chat_id}] Scheduling deletion of {len(context.user_data[user_id].get('messages_to_delete', []))} old messages after showing final options for user {user_id}.")
            for msg_info in context.user_data[user_id]['messages_to_delete']:
                context.application.create_task(delete_message_in_background(context, chat_id=msg_info['chat_id'], message_id=msg_info['message_id']))
            context.user_data[user_id]['messages_to_delete'].clear()
        
        await show_final_options(chat_id, context, user_id, order_id_to_process, message_prefix="تم تحديث عدد المحلات بنجاح.")
        
        if user_id in context.user_data and "current_active_order_id" in context.user_data[user_id]:
            del context.user_data[user_id]["current_active_order_id"]
            logger.info(f"[{chat_id}] Cleared current_active_order_id for user {user_id} after processing places count.")

        return ConversationHandler.END 
    except Exception as e:
        logger.error(f"[{chat_id}] Error in handle_places_count_data: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text="عذراً، حدث خطأ أثناء معالجة عدد المحلات. الرجاء بدء طلبية جديدة.")
        return ConversationHandler.END
        
async def show_final_options(chat_id, context, user_id, order_id, message_prefix=None):
    try:
        global daily_profit
        logger.info(f"[{chat_id}] Showing final options for order {order_id} to user {user_id}. User data: {json.dumps(context.user_data.get(user_id), indent=2)}")
        
        if order_id not in orders:
            logger.warning(f"[{chat_id}] Attempted to show final options for non-existent order_id: {order_id}")
            await context.bot.send_message(chat_id=chat_id, text="عذراً، الطلب الذي تحاول الوصول إليه غير موجود أو تم حذفه. الرجاء بدء طلبية جديدة.")
            if user_id in context.user_data:
                context.user_data[user_id].pop("order_id", None)
                context.user_data[user_id].pop("product", None)
                context.user_data[user_id].pop("current_active_order_id", None)
                context.user_data[user_id].pop("messages_to_delete", None)
            return

        order = orders[order_id]
        invoice = invoice_numbers.get(order_id, "غير معروف")
        
        total_buy = 0.0
        total_sell = 0.0
        for p in order["products"]:
            if p in pricing.get(order_id, {}) and "buy" in pricing[order_id].get(p, {}) and "sell" in pricing[order_id].get(p, {}):
                total_buy += pricing[order_id][p]["buy"]
                total_sell += pricing[order_id][p]["sell"]

        net_profit = total_sell - total_buy
        
        current_places = orders[order_id].get("places_count", 0) 
        extra_cost = calculate_extra(current_places)
        delivery_cost = order.get("delivery_price", 0)
        final_total = total_sell + extra_cost + delivery_cost

        logger.info(f"[{chat_id}] Daily profit before addition for order {order_id}: {daily_profit}")
        daily_profit += net_profit
        logger.info(f"[{chat_id}] Daily profit after adding {net_profit} for order {order_id}: {daily_profit}")
        context.application.create_task(save_data_in_background(context))

        customer_invoice_lines = []
        customer_invoice_lines.append(f"**أبو الأكبر للتوصيل**") 
        customer_invoice_lines.append(f"رقم الفاتورة: {invoice}")
        customer_invoice_lines.append(f"عنوان الزبون: {order['title']}")
        if order.get("delivery_area"):
            customer_invoice_lines.append(f"منطقة التوصيل: {order['delivery_area']}")
        
        customer_invoice_lines.append(f"\n*المواد:*") 
        
        running_total_for_customer = 0.0
        for p in order["products"]:
            if p in pricing.get(order_id, {}) and "sell" in pricing[order_id].get(p, {}):
                sell = pricing[order_id][p]["sell"]
                running_total_for_customer += sell
                customer_invoice_lines.append(f"{p} - {format_float(sell)} = {format_float(running_total_for_customer)}")
            else:
                customer_invoice_lines.append(f"{p} - (لم يتم تسعيره)")
        
        customer_invoice_lines.append(f"كلفة تجهيز من - {current_places} محلات {format_float(extra_cost)} = {format_float(running_total_for_customer + extra_cost)}")
        if delivery_cost > 0:
            customer_invoice_lines.append(f"أجرة توصيل - {format_float(delivery_cost)} = {format_float(running_total_for_customer + extra_cost + delivery_cost)}")
        customer_invoice_lines.append(f"\n*المجموع الكلي:* {format_float(final_total)}") 
        
        customer_final_text = "\n".join(customer_invoice_lines)

        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=customer_final_text,
                parse_mode="Markdown"
            )
            logger.info(f"[{chat_id}] Customer invoice sent as a separate message for order {order_id}.")
        except Exception as e:
            logger.error(f"[{chat_id}] Could not send customer invoice as separate message to chat {chat_id}: {e}", exc_info=True)
            await context.bot.send_message(chat_id=chat_id, text="عذراً، لم أتمكن من إرسال فاتورة الزبون. الرجاء المحاولة مرة أخرى.")

        keyboard = [
            [InlineKeyboardButton("1️⃣ تعديل الأسعار", callback_data=f"edit_prices_{order_id}")],
            [InlineKeyboardButton("3️⃣ إرسال فاتورة الزبون (واتساب)", url=f"https://wa.me/{OWNER_PHONE_NUMBER}?text={customer_final_text.replace(' ', '%20').replace('\n', '%0A').replace('*', '')}")],
            [InlineKeyboardButton("4️⃣ إنشاء طلب جديد", callback_data="start_new_order")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        message_text = "افعل ما تريد من الأزرار:\n\n"
        if message_prefix:
            message_text = message_prefix + "\n" + message_text
        
        owner_invoice_details = []
        owner_invoice_details.append(f"رقم الفاتورة: {invoice}")
        owner_invoice_details.append(f"عنوان الزبون: {order['title']}")
        if order.get("delivery_area"):
            owner_invoice_details.append(f"منطقة التوصيل: {order['delivery_area']} (سعر التوصيل: {format_float(delivery_cost)})")
        
        for p in order["products"]:
            if p in pricing.get(order_id, {}) and "buy" in pricing[order_id].get(p, {}) and "sell" in pricing[order_id].get(p, {}):
                buy = pricing[order_id][p]["buy"]
                sell = pricing[order_id][p]["sell"] 
                profit_item = sell - buy
                owner_invoice_details.append(f"{p} - شراء: {format_float(buy)}, بيع: {format_float(sell)}, ربح: {format_float(profit_item)}")
            else:
                owner_invoice_details.append(f"{p} - (لم يتم تسعيره بعد)")
        owner_invoice_details.append(f"\nالمجموع شراء: {format_float(total_buy)}")
        owner_invoice_details.append(f"المجموع بيع: {format_float(total_sell)}")
        owner_invoice_details.append(f"الربح الكلي: {format_float(net_profit)}")
        owner_invoice_details.append(f"عدد المحلات: {current_places} (+{format_float(extra_cost)})")
        if delivery_cost > 0:
            owner_invoice_details.append(f"أجرة التوصيل: {format_float(delivery_cost)}")
        owner_invoice_details.append(f"السعر الكلي: {format_float(final_total)}")
        
        final_owner_invoice_text = "\n".join(owner_invoice_details)
        
        encoded_owner_invoice = final_owner_invoice_text.replace(" ", "%20").replace("\n", "%0A").replace("*", "")
        whatsapp_owner_button_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("إرسال فاتورة الإدارة للواتساب", url=f"https://wa.me/{OWNER_PHONE_NUMBER}?text={encoded_owner_invoice}")]
        ])

        try:
            await context.bot.send_message(
                chat_id=OWNER_ID,
                text=f"**فاتورة طلبية (الإدارة):**\n{final_owner_invoice_text}",
                parse_mode="Markdown",
                reply_markup=whatsapp_owner_button_markup
            )
            logger.info(f"[{chat_id}] Admin invoice and WhatsApp button sent to OWNER_ID: {OWNER_ID}")
        except Exception as e:
            logger.error(f"[{chat_id}] Could not send admin invoice to OWNER_ID {OWNER_ID}: {e}", exc_info=True)
            await context.bot.send_message(chat_id=chat_id, text="عذراً، لم أتمكن من إرسال فاتورة الإدارة إلى خاصك. يرجى التأكد من أنني أستطيع مراسلتك في الخاص (قد تحتاج إلى بدء محادثة معي أولاً).")

        await context.bot.send_message(chat_id=chat_id, text=message_text, reply_markup=reply_markup, parse_mode="Markdown")
        
        if user_id in context.user_data: 
            if 'messages_to_delete' in context.user_data[user_id]:
                for msg_info in context.user_data[user_id]['messages_to_delete']:
                    context.application.create_task(delete_message_in_background(context, chat_id=msg_info['chat_id'], message_id=msg_info['message_id']))
                context.user_data[user_id]['messages_to_delete'].clear()
            
            context.user_data[user_id].pop("order_id", None)
            context.user_data[user_id].pop("product", None)
            context.user_data[user_id].pop("current_active_order_id", None) 
            context.user_data[user_id].pop("buy_price", None)  # Clear buy_price too
            logger.info(f"[{chat_id}] Cleaned up order-specific user_data for user {user_id} after showing final options. User data after clean: {json.dumps(context.user_data.get(user_id, {}), indent=2)}")
    except Exception as e:
        logger.error(f"[{chat_id}] Error in show_final_options: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text="عذراً، حدث خطأ أثناء عرض الفاتورة النهائية. الرجاء بدء طلبية جديدة.")

# دالة لعرض قائمة المناطق
async def show_areas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.message.from_user.id)
        if user_id != str(OWNER_ID):
            await update.message.reply_text("عذراً، هذا الأمر متاح للمالك فقط.")
            return

        areas_list = []
        for area, price in sorted(areas.items()):
            areas_list.append(f"- {area}: {price} دينار")

        keyboard = [
            [InlineKeyboardButton("➕ إضافة منطقة", callback_data="add_area")],
            [InlineKeyboardButton("➖ حذف منطقة", callback_data="remove_area")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            f"**قائمة المناطق وأسعار التوصيل:**\n\n" + "\n".join(areas_list),
            parse_mode="Markdown",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in show_areas: {e}", exc_info=True)
        await update.message.reply_text("عذراً، حدث خطأ أثناء عرض قائمة المناطق.")

# دالة لبدء إضافة منطقة جديدة
async def add_area_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()

        if str(query.from_user.id) != str(OWNER_ID):
            await query.edit_message_text("عذراً، هذا الأمر متاح للمالك فقط.")
            return

        await query.edit_message_text("أدخل اسم المنطقة الجديدة وسعر التوصيل بالشكل التالي:\nاسم المنطقة سعر التوصيل\nمثال: المنطقة الجديدة 5")
        return ADD_AREA
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in add_area_start: {e}", exc_info=True)
        await query.edit_message_text("عذراً، حدث خطأ أثناء محاولة إضافة منطقة جديدة.")
        return ConversationHandler.END

# دالة لمعالجة إضافة منطقة جديدة
async def add_area_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.message.from_user.id)
        if user_id != str(OWNER_ID):
            await update.message.reply_text("عذراً، هذا الأمر متاح للمالك فقط.")
            return ConversationHandler.END

        parts = update.message.text.strip().rsplit(' ', 1)
        if len(parts) != 2:
            await update.message.reply_text("الرجاء إدخال البيانات بالشكل الصحيح:\nاسم المنطقة سعر التوصيل\nمثال: المنطقة الجديدة 5")
            return ADD_AREA

        area_name = parts[0].strip()
        try:
            area_price = float(parts[1])
            if area_price <= 0:
                await update.message.reply_text("سعر التوصيل يجب أن يكون أكبر من صفر.")
                return ADD_AREA
        except ValueError:
            await update.message.reply_text("الرجاء إدخال سعر توصيل صحيح.")
            return ADD_AREA

        areas[area_name] = area_price
        context.application.create_task(save_data_in_background(context))
        await update.message.reply_text(f"تمت إضافة المنطقة '{area_name}' بسعر توصيل {area_price} دينار بنجاح.")
        
        # عرض قائمة المناطق المحدثة
        await show_areas(update, context)
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in add_area_process: {e}", exc_info=True)
        await update.message.reply_text("عذراً، حدث خطأ أثناء إضافة المنطقة.")
        return ConversationHandler.END

# دالة لبدء حذف منطقة
async def remove_area_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()

        if str(query.from_user.id) != str(OWNER_ID):
            await query.edit_message_text("عذراً، هذا الأمر متاح للمالك فقط.")
            return

        if not areas:
            await query.edit_message_text("لا توجد مناطق مسجلة للحذف.")
            return

        buttons = [[InlineKeyboardButton(area, callback_data=f"remove_{area}")] for area in sorted(areas.keys())]
        reply_markup = InlineKeyboardMarkup(buttons)
        await query.edit_message_text("اختر المنطقة التي تريد حذفها:", reply_markup=reply_markup)
        return REMOVE_AREA
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in remove_area_start: {e}", exc_info=True)
        await query.edit_message_text("عذراً، حدث خطأ أثناء محاولة حذف منطقة.")
        return ConversationHandler.END

# دالة لمعالجة حذف منطقة
async def remove_area_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()

        if str(query.from_user.id) != str(OWNER_ID):
            await query.edit_message_text("عذراً، هذا الأمر متاح للمالك فقط.")
            return ConversationHandler.END

        area_to_remove = query.data.replace("remove_", "")
        if area_to_remove in areas:
            del areas[area_to_remove]
            context.application.create_task(save_data_in_background(context))
            await query.edit_message_text(f"تم حذف المنطقة '{area_to_remove}' بنجاح.")
            
            # عرض قائمة المناطق المحدثة
            await show_areas(update, context)
        else:
            await query.edit_message_text("عذراً، المنطقة المحددة غير موجودة.")
        
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in remove_area_process: {e}", exc_info=True)
        await query.edit_message_text("عذراً، حدث خطأ أثناء حذف المنطقة.")
        return ConversationHandler.END

# باقي الدوال (edit_prices, start_new_order_callback, show_profit, reset_all, confirm_reset, show_report) تبقى كما هي بدون تغيير

def main():
    app = ApplicationBuilder().token(TOKEN).build()

    # Handlers لا تدخل في أي ConversationHandler (مثل الـ /start والأوامر الإدارية)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^الارباح$|^ارباح$"), show_profit))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^صفر$|^تصفير$"), reset_all))
    app.add_handler(CallbackQueryHandler(confirm_reset, pattern="^(confirm_reset|cancel_reset)$"))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^التقارير$|^تقرير$|^تقارير$"), show_report))
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE, edited_message))
    app.add_handler(CallbackQueryHandler(edit_prices, pattern="^edit_prices_"))
    app.add_handler(CallbackQueryHandler(start_new_order_callback, pattern="^start_new_order$"))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^المناطق$|^مناطق$"), show_areas))

    # ConversationHandler لإدارة المناطق
    areas_conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(add_area_start, pattern="^add_area$"),
            CallbackQueryHandler(remove_area_start, pattern="^remove_area$")
        ],
        states={
            ADD_AREA: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_area_process)
            ],
            REMOVE_AREA: [
                CallbackQueryHandler(remove_area_process, pattern=r"^remove_.+$")
            ]
        },
        fallbacks=[
            CommandHandler("cancel", lambda u, c: ConversationHandler.END),
            MessageHandler(filters.ALL, lambda u, c: ConversationHandler.END)
        ]
    )
    app.add_handler(areas_conv_handler)

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
    
    # ConversationHandler لإنشاء الطلب وتسعير المنتجات
    order_creation_conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_order),
            CallbackQueryHandler(product_selected, pattern=r"^[a-f0-9]{8}\|.+$")
        ],
        states={
            ASK_BUY: [
                MessageHandler(filters.TEXT & filters.Regex(r"^\d+(\.\d+)?$") & ~filters.COMMAND, receive_buy_price),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_buy_price),
            ],
            ASK_SELL: [
                MessageHandler(filters.TEXT & filters.Regex(r"^\d+(\.\d+)?$") & ~filters.COMMAND, receive_sell_price),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_sell_price),
            ]
        },
        fallbacks=[
            CommandHandler("cancel", lambda u, c: ConversationHandler.END),
            MessageHandler(filters.ALL, lambda u, c: ConversationHandler.END)
        ]
    )
    app.add_handler(order_creation_conv_handler)
    
    logger.info("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
    
