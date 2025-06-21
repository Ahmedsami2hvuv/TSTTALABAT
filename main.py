import os
import json
import uuid
import time
import asyncio
import logging
import threading
from collections import Counter

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
    global orders, pricing, invoice_numbers, daily_profit, last_button_message
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
    global orders, pricing, invoice_numbers, daily_profit, last_button_message

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
ASK_BUY, ASK_SELL, ASK_PLACES_COUNT = range(3)

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
    
    await update.message.reply_text("أهلاً بك يا أبا الأكبر! لإعداد طلبية، دز الطلبية كلها برسالة واحدة.\n\n*السطر الأول:* عنوان الزبون.\n*السطر الثاني:* رقم هاتف الزبون.\n*الأسطر الباقية:* كل منتج بسطر واحد.", parse_mode="Markdown")
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
        await update.message.reply_text("عذراً، حدث خطأ أثناء معالجة الطلب. الرجاء المحاولة مرة أخرى أو بدء طلبية جديدة.")
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
        await update.edited_message.reply_text("عذراً، حدث خطأ أثناء معالجة التعديل. الرجاء المحاولة مرة أخرى.")

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
            await message.reply_text("الرجاء التأكد من كتابة عنوان الزبون في السطر الأول، رقم الهاتف في السطر الثاني، والمنتجات في الأسطر التالية.")
        return

    title = lines[0]
    phone_number = lines[1].replace(" ", "").replace("+", "") # ✅ استخراج رقم الهاتف وإزالة المسافات وعلامة +
    products = [p.strip() for p in lines[2:] if p.strip()] # ✅ المنتجات تبدأ من السطر الثالث

    if not products:
        if not edited:
            await message.reply_text("الرجاء إضافة منتجات بعد العنوان ورقم الهاتف.")
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
        # ✅ إضافة phone_number إلى قاموس الطلبية
        orders[order_id] = {"user_id": user_id, "title": title, "phone_number": phone_number, "products": products, "places_count": 0} 
        pricing[order_id] = {p: {} for p in products}
        invoice_numbers[order_id] = invoice_no
        logger.info(f"Created new order {order_id} for user {user_id}.")
    else: 
        old_products = set(orders[order_id].get("products", []))
        new_products = set(products)
        
        orders[order_id]["title"] = title
        orders[order_id]["phone_number"] = phone_number # ✅ تحديث رقم الهاتف في الطلبية الموجودة
        orders[order_id]["products"] = products

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
    
    # ✅ تعديل رسالة الاستلام لتضمين رقم الهاتف
    if is_new_order:
        await message.reply_text(f"استلمت الطلب بعنوان: *{title}* (الرقم: `{phone_number}`) (عدد المنتجات: {len(products)})", parse_mode="Markdown")
        await show_buttons(message.chat_id, context, user_id, order_id)
    else:
        await show_buttons(message.chat_id, context, user_id, order_id, confirmation_message="تم تحديث الطلب. الرجاء التأكد من تسعير أي منتجات جديدة.")
        
async def show_buttons(chat_id, context, user_id, order_id, confirmation_message=None):
    orders = context.application.bot_data['orders']
    pricing = context.application.bot_data['pricing']
    last_button_message = context.application.bot_data['last_button_message']

    try:
        logger.info(f"[{chat_id}] show_buttons called for order {order_id}. User: {user_id}.")
        logger.info(f"[{chat_id}] Current pricing data for order {order_id} in show_buttons: {json.dumps(pricing.get(order_id), indent=2)}")

        if order_id not in orders:
            logger.warning(f"[{chat_id}] Attempted to show buttons for non-existent order_id: {order_id}")
            await context.bot.send_message(chat_id=chat_id, text="عذراً، الطلب الذي تحاول الوصول إليه غير موجود أو تم حذفه. الرجاء بدء طلبية جديدة.")
            if user_id in context.user_data:
                context.user_data[user_id].pop("order_id", None)
                context.user_data[user_id].pop("product", None)
                context.user_data[user_id].pop("current_active_order_id", None)
                context.user_data[user_id].pop("messages_to_delete", None)
            return

        order = orders[order_id]
        
        completed_products = []
        pending_products = []
        
        for p in order["products"]:
            if p in pricing.get(order_id, {}) and 'buy' in pricing[order_id].get(p, {}) and 'sell' in pricing[order_id].get(p, {}):
                completed_products.append(p)
                logger.info(f"[{chat_id}] Product '{p}' in order {order_id} is completed.")
            else:
                pending_products.append(p)
                logger.info(f"[{chat_id}] Product '{p}' in order {order_id} is pending. Pricing state for this product: {json.dumps(pricing.get(order_id, {}).get(p, {}), indent=2)}")
        
        buttons_list = []
        for p in completed_products:
            buttons_list.append([InlineKeyboardButton(f"✅ {p}", callback_data=f"{order_id}|{p}")])
        for p in pending_products:
            buttons_list.append([InlineKeyboardButton(p, callback_data=f"{order_id}|{p}")])
        
        markup = InlineKeyboardMarkup(buttons_list)
        
        message_text = ""
        if confirmation_message:
            message_text += f"{confirmation_message}\n\n"
        message_text += f"اضغط على منتج لتحديد سعره من *{order['title']}*:"

        msg_info = last_button_message.get(order_id)
        if msg_info:
            logger.info(f"[{chat_id}] Deleting old button message {msg_info['message_id']} for order {order_id} before sending new one.")
            context.application.create_task(delete_message_in_background(context, chat_id=msg_info["chat_id"], message_id=msg_info["message_id"]))
            # No del last_button_message[order_id] here, it's updated after new message is sent

        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=message_text,
            reply_markup=markup,
            parse_mode="Markdown"
        )
        logger.info(f"[{chat_id}] Sent new button message {msg.message_id} for order {order_id}")
        last_button_message[order_id] = {"chat_id": chat_id, "message_id": msg.message_id}
        context.application.create_task(save_data_in_background(context)) 

        if user_id in context.user_data and 'messages_to_delete' in context.user_data[user_id]:
            logger.info(f"[{chat_id}] Scheduling deletion of {len(context.user_data[user_id].get('messages_to_delete', []))} old messages after showing new buttons for user {user_id}.")
            for msg_info in context.user_data[user_id]['messages_to_delete']:
                context.application.create_task(delete_message_in_background(context, chat_id=msg_info['chat_id'], message_id=msg_info['message_id']))
            context.user_data[user_id]['messages_to_delete'].clear()
    except Exception as e:
        logger.error(f"[{chat_id}] Error in show_buttons for order {order_id}: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text="عذراً، حدث خطأ أثناء عرض الأزرار. الرجاء بدء طلبية جديدة.")


async def product_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    orders = context.application.bot_data['orders']
    pricing = context.application.bot_data['pricing']
    last_button_message = context.application.bot_data['last_button_message']

    try: 
        query = update.callback_query
        await query.answer()
        
        user_id = str(query.from_user.id)
        logger.info(f"[{query.message.chat_id}] Product selected callback from user {user_id}: {query.data}. User data at product_selected start: {json.dumps(context.user_data.get(user_id, {}), indent=2)}")

        context.user_data.setdefault(user_id, {}).setdefault('messages_to_delete', []).append({
            'chat_id': query.message.chat_id,
            'message_id': query.message.message_id
        })
        logger.info(f"[{query.message.chat_id}] Added product selection button message {query.message.message_id} to delete queue.")
        
        order_id, product = query.data.split('|', 1)
        
        if order_id not in orders:
            logger.warning(f"[{query.message.chat_id}] Product selected: Order ID '{order_id}' not found.")
            msg_error = await query.edit_message_text("عذراً، الطلبية لم تعد موجودة. الرجاء بدء طلبية جديدة.")
            context.user_data[user_id]['messages_to_delete'].append({
                'chat_id': msg_error.chat_id,
                'message_id': msg_error.message_id
            })
            return ConversationHandler.END

        context.user_data[user_id]["order_id"] = order_id
        context.user_data[user_id]["product"] = product
        
        context.user_data[user_id].pop("buy_price", None)

        logger.info(f"[{query.message.chat_id}] Product '{product}' selected for order '{order_id}'. User data after product selection: {json.dumps(context.user_data.get(user_id), indent=2)}")
        
        current_buy = pricing.get(order_id, {}).get(product, {}).get("buy")
        current_sell = pricing.get(order_id, {}).get(product, {}).get("sell")

        if current_buy is not None and current_sell is not None:
            msg_edit = await query.message.reply_text(
                f"سعر *'{product}'* حالياً هو شراء: {format_float(current_buy)}، بيع: {format_float(current_sell)}.\n"
                "شنو سعر الشراء الجديد؟ (أو دز نفس السعر إذا ماكو تغيير)", parse_mode="Markdown"
            )
            context.user_data[user_id]['messages_to_delete'].append({
                'chat_id': msg_edit.chat_id, 
                'message_id': msg_edit.message_id
            })
            return ASK_BUY 
        else:
            msg_new = await query.message.reply_text(f"تمام، بيش اشتريت *'{product}'*؟", parse_mode="Markdown")
            context.user_data[user_id]['messages_to_delete'].append({
                'chat_id': msg_new.chat_id, 
                'message_id': msg_new.message_id
            })
            return ASK_BUY 

    except Exception as e: 
        logger.error(f"[{update.effective_chat.id}] Error in product_selected: {e}", exc_info=True)
        await update.callback_query.message.reply_text("عذراً، حدث خطأ أثناء اختيار المنتج. الرجاء بدء طلبية جديدة.")
        return ConversationHandler.END
    
async def receive_buy_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    orders = context.application.bot_data['orders']
    pricing = context.application.bot_data['pricing']

    try:
        user_id = str(update.message.from_user.id)
        logger.info(f"[{update.effective_chat.id}] Received message for buy price from user {user_id}: '{update.message.text}'. User data at start of receive_buy_price: {json.dumps(context.user_data.get(user_id), indent=2)}")

        context.user_data.setdefault(user_id, {})
        if 'messages_to_delete' not in context.user_data[user_id]:
            context.user_data[user_id]['messages_to_delete'] = []
        
        context.user_data[user_id]['messages_to_delete'].append({
            'chat_id': update.message.chat_id,
            'message_id': update.message.message_id
        })

        data = context.user_data.get(user_id)
        if not data or "order_id" not in data or "product" not in data:
            logger.error(f"[{update.effective_chat.id}] Buy price: Missing order_id or product in user_data for user {user_id}. User data: {json.dumps(data, indent=2)}")
            msg_error = await update.message.reply_text("عذراً، لم أتمكن من تحديد الطلبية أو المنتج لتسعيره. الرجاء اضغط على المنتج من القائمة أولاً لتحديد سعره، أو ابدأ طلبية جديدة.", parse_mode="Markdown")
            context.user_data[user_id]['messages_to_delete'].append({
                'chat_id': msg_error.chat_id, 
                'message_id': msg_error.message_id
            })
            return ConversationHandler.END
        
        order_id = data["order_id"]
        product = data["product"]
        
        if order_id not in orders or product not in orders[order_id].get("products", []):
            logger.warning(f"[{update.effective_chat.id}] Buy price: Order ID '{order_id}' not found or Product '{product}' not in products for order '{order_id}'.")
            msg_error = await update.message.reply_text("عذراً، الطلبية أو المنتج لم يعد موجوداً. الرجاء بدء طلبية جديدة أو التحقق من المنتجات.")
            context.user_data[user_id]['messages_to_delete'].append({
                'chat_id': msg_error.chat_id, 
                'message_id': msg_error.message_id
            })
            return ConversationHandler.END
        
        if not filters.Regex(r"^\d+(\.\d+)?$").check_update(update): 
            logger.warning(f"[{update.effective_chat.id}] Buy price: Non-numeric input from user {user_id}: '{update.message.text}'")
            msg_error = await update.message.reply_text("الرجاء إدخال *رقم* صحيح لسعر الشراء.")
            context.user_data[user_id]['messages_to_delete'].append({
                'chat_id': msg_error.chat_id, 
                'message_id': msg_error.message_id
            })
            return ASK_BUY 

        try:
            price = float(update.message.text.strip())
            if price < 0:
                logger.warning(f"[{update.effective_chat.id}] Buy price: Negative price from user {user_id}: '{update.message.text}'")
                msg_error = await update.message.reply_text("السعر يجب أن يكون موجباً")
                context.user_data[user_id]['messages_to_delete'].append({
                    'chat_id': msg_error.chat_id, 
                    'message_id': msg_error.message_id
                })
                return ASK_BUY
        except ValueError as e: 
            logger.error(f"[{update.effective_chat.id}] Buy price: ValueError for user {user_id} with input '{update.message.text}': {e}", exc_info=True)
            msg_error = await update.message.reply_text("الرجاء إدخال رقم صحيح")
            context.user_data[user_id]['messages_to_delete'].append({
                    'chat_id': msg_error.chat_id, 
                    'message_id': msg_error.message_id
                })
            return ASK_BUY
        
        context.user_data[user_id]["buy_price"] = price 
        logger.info(f"[{update.effective_chat.id}] Buy price '{price}' stored in user_data for product '{product}'. User data after storing buy_price: {json.dumps(context.user_data.get(user_id), indent=2)}")

        msg = await update.message.reply_text(f"شكراً. وهسه، بيش راح تبيع *'{product}'*؟", parse_mode="Markdown")
        context.user_data[user_id]['messages_to_delete'].append({'chat_id': msg.chat_id, 'message_id': msg.message_id})
        logger.info(f"[{update.effective_chat.id}] Asking for sell price for '{product}'. Next state: ASK_SELL. Current user_data: {json.dumps(context.user_data.get(user_id), indent=2)}")
        
        return ASK_SELL
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in receive_buy_price: {e}", exc_info=True)
        await update.message.reply_text("عذراً، حدث خطأ أثناء إدخال سعر الشراء. الرجاء بدء طلبية جديدة.")
        return ConversationHandler.END


async def receive_sell_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    orders = context.application.bot_data['orders']
    pricing = context.application.bot_data['pricing']

    try:
        user_id = str(update.message.from_user.id)
        logger.info(f"[{update.effective_chat.id}] Received message for sell price from user {user_id}: '{update.message.text}'. User data at start of receive_sell_price: {json.dumps(context.user_data.get(user_id), indent=2)}")

        context.user_data.setdefault(user_id, {})
        if 'messages_to_delete' not in context.user_data[user_id]:
            context.user_data[user_id]['messages_to_delete'] = []
        context.user_data[user_id]['messages_to_delete'].append({'chat_id': update.message.chat_id, 'message_id': update.message.message_id})

        data = context.user_data.get(user_id)
        if not data or "order_id" not in data or "product" not in data or "buy_price" not in data: 
            logger.error(f"[{update.effective_chat.id}] Sell price: Missing order_id, product, or buy_price in user_data for user {user_id}. User data: {json.dumps(data, indent=2)}")
            msg_error = await update.message.reply_text("عذراً، لم أتمكن من تحديد الطلبية أو المنتج لتسعيره. الرجاء اضغط على المنتج من القائمة أولاً لتحديد سعره، أو ابدأ طلبية جديدة.", parse_mode="Markdown")
            context.user_data[user_id]['messages_to_delete'].append({
                'chat_id': msg_error.chat_id, 
                'message_id': msg_error.message_id
            })
            return ConversationHandler.END
        
        order_id, product, buy_price_from_user_data = data["order_id"], data["product"], data["buy_price"]
        
        if order_id not in orders or product not in orders[order_id].get("products", []):
            logger.warning(f"[{update.effective_chat.id}] Sell price: Order ID '{order_id}' not found or Product '{product}' not in products for order '{order_id}'.")
            msg_error = await update.message.reply_text("عذراً، الطلبية أو المنتج لم يعد موجوداً. الرجاء بدء طلبية جديدة.")
            context.user_data[user_id]['messages_to_delete'].append({
                'chat_id': msg_error.chat_id, 
                'message_id': msg_error.message_id
            })
            return ConversationHandler.END

        if not filters.Regex(r"^\d+(\.\d+)?$").check_update(update): 
            logger.warning(f"[{update.effective_chat.id}] Sell price: Non-numeric input from user {user_id}: '{update.message.text}'")
            msg_error = await update.message.reply_text("الرجاء إدخال *رقم* صحيح لسعر البيع.")
            context.user_data[user_id]['messages_to_delete'].append({'chat_id': msg_error.chat_id, 'message_id': msg_error.message_id})
            return ASK_SELL 

        try:
            sell_price = float(update.message.text.strip())
            if sell_price < 0:
                logger.warning(f"[{update.effective_chat.id}] Sell price: Negative price from user {user_id}: '{update.message.text}'")
                msg_error = await update.message.reply_text("سعر البيع يجب أن يكون رقماً إيجابياً. بيش راح تبيع بالضبط؟")
                context.user_data[user_id]['messages_to_delete'].append({'chat_id': msg_error.chat_id, 'message_id': msg_error.message_id})
                return ASK_SELL 
        except ValueError as e:
            logger.error(f"[{update.effective_chat.id}] Sell price: ValueError for user {user_id} with input '{update.message.text}': {e}", exc_info=True)
            msg_error = await update.message.reply_text("الرجاء إدخال رقم صحيح لسعر البيع. بيش حتبيع؟")
            context.user_data[user_id]['messages_to_delete'].append({'chat_id': msg_error.chat_id, 'message_id': msg_error.message_id})
            return ASK_SELL 
        
        pricing.setdefault(order_id, {}).setdefault(product, {})["buy"] = buy_price_from_user_data
        pricing[order_id][product]["sell"] = sell_price
        
        logger.info(f"[{update.effective_chat.id}] Pricing for order '{order_id}' and product '{product}' AFTER SAVE: {json.dumps(pricing.get(order_id, {}).get(product), indent=2)}")
        context.application.create_task(save_data_in_background(context)) 
        logger.info(f"[{update.effective_chat.id}] Sell price for '{product}' in order '{order_id}' saved. Current user_data: {json.dumps(context.user_data.get(user_id), indent=2)}. Updated pricing for order {order_id}: {json.dumps(pricing.get(order_id), indent=2)}")

        order = orders[order_id]
        all_priced = True
        for p in order["products"]:
            if p not in pricing.get(order_id, {}) or "buy" not in pricing[order_id].get(p, {}) or "sell" not in pricing[order_id].get(p, {}):
                all_priced = False
                break
                
        if all_priced:
            context.user_data[user_id]["current_active_order_id"] = order_id
            logger.info(f"[{update.effective_chat.id}] All products priced for order {order_id}. Requesting places count. Transitioning to ASK_PLACES_COUNT.")
            await request_places_count_standalone(update.effective_chat.id, context, user_id, order_id)
            return ConversationHandler.END 
        else:
            confirmation_msg = f"تم حفظ السعر لـ *'{product}'*."
            logger.info(f"[{update.effective_chat.id}] Price saved for '{product}' in order {order_id}. Showing updated buttons with confirmation. User {user_id} can select next product. Staying in conversation.")
            await show_buttons(update.effective_chat.id, context, user_id, order_id, confirmation_message=confirmation_msg)
            return ConversationHandler.END 
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in receive_sell_price: {e}", exc_info=True)
        await update.message.reply_text("عذراً، حدث خطأ أثناء إدخال سعر البيع. الرجاء بدء طلبية جديدة.")
        return ConversationHandler.END

async def request_places_count_standalone(chat_id, context: ContextTypes.DEFAULT_TYPE, user_id: str, order_id: str):
    orders = context.application.bot_data['orders']
    pricing = context.application.bot_data['pricing']

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
                        await context.bot.send_message(chat_id=chat_id, text="عذراً، الطلبية اللي حاول تختار عدد محلاتها ما موجودة عندي. الرجاء بدء طلبية جديدة.")
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
        
        await show_final_options(chat_id, context, user_id, order_id_to_process, message_prefix="تم تحديث عدد المحلات بنجاح.")
        
        if user_id in context.user_data and "current_active_order_id" in context.user_data[user_id]:
            del context.user_data[user_id]["current_active_order_id"]
            logger.info(f"[{chat_id}] Cleared current_active_order_id for user {user_id} after processing places count.")

        return ConversationHandler.END 
    except Exception as e:
        logger.error(f"[{chat_id}] Error in handle_places_count_data: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text="عذراً، حدث خطأ أثناء معالجة عدد المحلات. الرجاء بدء طلبية جديدة.")
        return ConversationHandler.END

from urllib.parse import quote

async def show_final_options(chat_id, context, user_id, order_id, message_prefix=None):
    orders = context.application.bot_data['orders']
    pricing = context.application.bot_data['pricing']
    invoice_numbers = context.application.bot_data['invoice_numbers']
    daily_profit_current = context.application.bot_data['daily_profit']

    try:
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
        phone_number = order.get('phone_number', 'لا يوجد رقم') # ✅ جلب رقم الهاتف

        total_buy = 0.0
        total_sell = 0.0
        for p in order["products"]:
            if p in pricing.get(order_id, {}) and "buy" in pricing[order_id].get(p, {}) and "sell" in pricing[order_id].get(p, {}):
                total_buy += pricing[order_id][p]["buy"]
                total_sell += pricing[order_id][p]["sell"]

        net_profit = total_sell - total_buy
        
        current_places = orders[order_id].get("places_count", 0)
        extra_cost = calculate_extra(current_places)

        delivery_fee = get_delivery_price(order.get('title', ''))

        # total_before_delivery_fee هو المجموع قبل إضافة أجرة التوصيل
        total_before_delivery_fee = total_sell + extra_cost
        
        final_total = total_before_delivery_fee + delivery_fee

        context.application.bot_data['daily_profit'] = daily_profit_current + net_profit
        logger.info(f"[{chat_id}] Daily profit after adding {net_profit} for order {order_id}: {context.application.bot_data['daily_profit']}")
        context.application.create_task(save_data_in_background(context))

        # فاتورة الزبون - هذا الجزء تم تعديله مسبقاً
        customer_invoice_lines = [
            "**أبو الأكبر للتوصيل**",
            f"رقم الفاتورة: {invoice}",
            f"عنوان الزبون: {order['title']}",
            f"رقم الزبون: `{phone_number}`", # ✅ إضافة رقم الزبون لفاتورة الزبون
            "\n*المواد:*"
        ]
        
        current_display_total = 0.0 # هذا المتغير لتتبع المجموع اللي ينعرض بالسطور
        for p in order["products"]:
            if p in pricing.get(order_id, {}) and "sell" in pricing[order_id][p]:
                sell = pricing[order_id][p]["sell"]
                current_display_total += sell
                customer_invoice_lines.append(f"{p} - {format_float(sell)} = {format_float(current_display_total)}")
            else:
                customer_invoice_lines.append(f"{p} - (لم يتم تسعيره)")

        # سطر كلفة تجهيز المحلات يتم إضافته هنا، ويتم جمعها مع الـ current_display_total
        current_display_total += extra_cost
        customer_invoice_lines.append(f"كلفة تجهيز من - {current_places} محلات {format_float(extra_cost)} = {format_float(current_display_total)}")

        # سطر أجرة التوصيل (إذا كانت موجودة)
        if delivery_fee > 0:
            customer_invoice_lines.append(f"أجرة التوصيل: {format_float(delivery_fee)}")

        # سطور المجموع الكلي الجديدة
        customer_invoice_lines.append(f"\n*المجموع الكلي:*")
        customer_invoice_lines.append(f"بدون التوصيل = {format_float(total_before_delivery_fee)}")
        customer_invoice_lines.append(f"مــــع التوصيل = {format_float(final_total)}")
        
        customer_final_text = "\n".join(customer_invoice_lines)

        # حفظ فاتورة الزبون
        invoices_dir = "invoices"
        os.makedirs(invoices_dir, exist_ok=True)
        try:
            customer_invoice_filename = os.path.join(invoices_dir, f"invoice_{invoice}_customer.txt")
            with open(customer_invoice_filename, "w", encoding="utf-8") as f:
                f.write("فاتورة الزبون\n" + "="*40 + "\n" + customer_final_text)
            logger.info(f"[{chat_id}] Saved customer invoice to {customer_invoice_filename}")
        except Exception as e:
            logger.error(f"[{chat_id}] Failed to save customer invoice to file: {e}")

        # إرسال الفاتورة للزبون
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=customer_final_text,
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"[{chat_id}] Could not send customer invoice as message: {e}")

        # فاتورة الإدارة - تم تعديل هذا الجزء ليتوافق مع طلبك الجديد
        owner_invoice_details = [
            f"رقم الفاتورة: {invoice}",
            f"رقم الزبون: `{phone_number}`", # ✅ إضافة رقم الزبون لفاتورة الإدارة
            f"عنوان الزبون: {order['title']}"
        ]
        
        for p in order["products"]:
            if p in pricing.get(order_id, {}) and "buy" in pricing[order_id][p] and "sell" in pricing[order_id][p]:
                buy = pricing[order_id][p]["buy"]
                sell = pricing[order_id][p]["sell"]
                profit_item = sell - buy
                owner_invoice_details.append(f"{p} - شراء: {format_float(buy)}, بيع: {format_float(sell)}, ربح: {format_float(profit_item)}")
            else:
                owner_invoice_details.append(f"{p} - (لم يتم تسعيره بعد)")

        # هنا تم تعديل ترتيب وتسميات الأسطر
        owner_invoice_details.extend([
            f"\nالمجموع شراء: {format_float(total_buy)}",
            f"الــربـــح الكلي: {format_float(net_profit)}", # تم تغيير التسمية
            f"التــجـهيز ({current_places}) : {format_float(extra_cost)}", # تم تغيير التسمية والشكل
            f"مـــــجموع بيع: {format_float(total_sell + extra_cost)}" # تم تغيير التسمية والموقع، واحتساب المجموع المطلوب
        ])
        if delivery_fee > 0:
            owner_invoice_details.append(f"أجرة التوصيل: {format_float(delivery_fee)}")
        owner_invoice_details.append(f"الــســعر الكلي: {format_float(final_total)}") # تم تغيير التسمية

        final_owner_invoice_text = "\n".join(owner_invoice_details)

        try:
            invoice_filename = os.path.join(invoices_dir, f"invoice_{invoice}_admin.txt")
            with open(invoice_filename, "w", encoding="utf-8") as f:
                f.write("فاتورة الإدارة\n" + "="*40 + "\n" + final_owner_invoice_text)
            logger.info(f"[{chat_id}] Saved invoice to {invoice_filename}")
        except Exception as e:
            logger.error(f"[{chat_id}] Failed to save admin invoice to file: {e}")

        # إرسال على واتساب
        encoded_owner_invoice = quote(final_owner_invoice_text, safe='')
        encoded_customer_text = quote(customer_final_text, safe='')

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
        except Exception as e:
            logger.error(f"[{chat_id}] Could not send admin invoice to OWNER_ID {OWNER_ID}: {e}")
            await context.bot.send_message(chat_id=chat_id, text="عذراً، لم أتمكن من إرسال فاتورة الإدارة إلى خاصك.")

        # أزرار التحكم - تم إضافة زر "رفع الطلبية" الجديد هنا
        keyboard = [
            [InlineKeyboardButton("1️⃣ تعديل الأسعار", callback_data=f"edit_prices_{order_id}")],
            [InlineKeyboardButton("2️⃣ رفع الطلبية", url="https://d.ksebstor.site/client/96f743f604a4baf145939298")], # الزر الجديد
            [InlineKeyboardButton("3️⃣ إرسال فاتورة الزبون (واتساب)", url=f"https://wa.me/{OWNER_PHONE_NUMBER}?text={encoded_customer_text}")],
            [InlineKeyboardButton("4️⃣ إنشاء طلب جديد", callback_data="start_new_order")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        message_text = "افعل ما تريد من الأزرار:\n\n"
        if message_prefix:
            message_text = message_prefix + "\n" + message_text

        await context.bot.send_message(chat_id=chat_id, text=message_text, reply_markup=reply_markup, parse_mode="Markdown")

        # تنظيف بيانات المستخدم
        if user_id in context.user_data:
            context.user_data[user_id].pop("order_id", None)
            context.user_data[user_id].pop("product", None)
            context.user_data[user_id].pop("current_active_order_id", None)
            context.user_data[user_id].pop("messages_to_delete", None)

    except Exception as e:
        logger.error(f"[{chat_id}] Error in show_final_options: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text="عذراً، حدث خطأ أثناء عرض الفاتورة النهائية. الرجاء بدء طلبية جديدة.")
    
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
            await query.message.reply_text("عذراً، حدث خطأ في بيانات الزر. الرجاء المحاولة مرة أخرى.")
            return ConversationHandler.END

        if order_id not in orders:
            logger.warning(f"[{query.message.chat_id}] Edit prices: Order {order_id} not found.")
            await query.message.reply_text("عذراً، الطلب الذي تحاول تعديله غير موجود.")
            return ConversationHandler.END

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
        
        await show_buttons(query.message.chat_id, context, user_id, order_id, confirmation_message="يمكنك الآن تعديل أسعار المنتجات أو إضافة/حذف منتجات بتعديل الرسالة الأصلية للطلبية.")
        logger.info(f"[{query.message.chat_id}] Showing edit buttons for order {order_id}. Exiting conversation for user {user_id}.")
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in edit_prices: {e}", exc_info=True)
        await update.callback_query.message.reply_text("عذراً، حدث خطأ أثناء تعديل الأسعار. الرجاء بدء طلبية جديدة.")
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
        await update.callback_query.message.reply_text("عذراً، حدث خطأ أثناء بدء طلب جديد. الرجاء المحاولة مرة أخرى.")
        return ConversationHandler.END


# الدوال الخاصة بالتقارير والأرباح (ستُجزأ لاحقاً إلى features/reports.py)
async def show_profit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    daily_profit = context.application.bot_data['daily_profit']

    try:
        if str(update.message.from_user.id) != str(OWNER_ID):
            await update.message.reply_text("عذراً، هذا الأمر متاح للمالك فقط.")
            return
        logger.info(f"Current daily_profit requested by user {update.message.from_user.id}: {daily_profit}")
        await update.message.reply_text(f"الربح التراكمي الإجمالي: *{format_float(daily_profit)}* دينار", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in show_profit: {e}", exc_info=True)
        await update.message.reply_text("عذراً، حدث خطأ أثناء عرض الأرباح.")

async def reset_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if str(update.message.from_user.id) != str(OWNER_ID):
            await update.message.reply_text("عذراً، هذا الأمر متاح للمالك فقط.")
            return
        
        keyboard = [
            [InlineKeyboardButton("نعم، متأكد", callback_data="confirm_reset")],
            [InlineKeyboardButton("لا، إلغاء", callback_data="cancel_reset")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("هل أنت متأكد من تصفير جميع الأرباح ومسح كل الطلبات؟ هذا الإجراء لا يمكن التراجع عنه.", reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in reset_all: {e}", exc_info=True)
        await update.message.reply_text("عذراً، حدث خطأ أثناء محاولة التصفير.")

async def confirm_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # الوصول إلى المتغيرات العالمية لتصفيرها
    orders = context.application.bot_data['orders']
    pricing = context.application.bot_data['pricing']
    invoice_numbers = context.application.bot_data['invoice_numbers']
    last_button_message = context.application.bot_data['last_button_message']
    daily_profit = context.application.bot_data['daily_profit'] # الوصول إلى القيمة الحالية قبل التصفير
    
    try:
        query = update.callback_query
        await query.answer()

        if str(query.from_user.id) != str(OWNER_ID):
            await query.edit_message_text("عذراً، لا تملك صلاحية لتنفيذ هذا الأمر.")
            return

        if query.data == "confirm_reset":
            logger.info(f"Daily profit before reset: {daily_profit}")
            
            # تصفير القيم في الذاكرة
            orders.clear()
            pricing.clear()
            invoice_numbers.clear()
            last_button_message.clear()
            daily_profit_value = 0.0 # القيمة الجديدة للربح اليومي

            try:
                # إعادة تعيين عداد الفواتير
                with open(COUNTER_FILE, "w") as f:
                    f.write("1")
            except Exception as e:
                logger.error(f"Could not reset invoice counter file: {e}", exc_info=True)
            
            # تحديث القيم في bot_data بعد التصفير
            context.application.bot_data['orders'] = orders
            context.application.bot_data['pricing'] = pricing
            context.application.bot_data['invoice_numbers'] = invoice_numbers
            context.application.bot_data['last_button_message'] = last_button_message
            context.application.bot_data['daily_profit'] = daily_profit_value # تحديث القيمة المصفّرة

            # استدعاء دالة الحفظ العامة لحفظ التغييرات على القرص
            _save_data_to_disk_global_func = context.application.bot_data.get('_save_data_to_disk_global_func')
            if _save_data_to_disk_global_func:
                _save_data_to_disk_global_func()
            else:
                logger.error("Could not find _save_data_to_disk_global_func in bot_data.")
            
            logger.info(f"Daily profit after reset: {context.application.bot_data['daily_profit']}")
            await query.edit_message_text("تم تصفير الأرباح ومسح كل الطلبات بنجاح.")
        elif query.data == "cancel_reset":
            await query.edit_message_text("تم إلغاء عملية التصفير.")
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in confirm_reset: {e}", exc_info=True)
        await update.callback_query.message.reply_text("عذراً، حدث خطأ أثناء عملية التصفير.")

async def show_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    orders = context.application.bot_data['orders']
    pricing = context.application.bot_data['pricing']
    invoice_numbers = context.application.bot_data['invoice_numbers']
    daily_profit = context.application.bot_data['daily_profit']

    try:
        if str(update.message.from_user.id) != str(OWNER_ID):
            await update.message.reply_text("عذراً، هذا الأمر متاح للمالك فقط.")
            return
        
        total_orders = len(orders)
        total_products = 0
        total_buy_all_orders = 0.0 
        total_sell_all_orders = 0.0 
        product_counter = Counter()
        details = []

        for order_id, order in orders.items():
            invoice = invoice_numbers.get(order_id, "غير معروف")
            details.append(f"\n**فاتورة رقم:** {invoice}")
            details.append(f"**عنوان الزبون:** {order['title']}")
            
            order_buy = 0.0
            order_sell = 0.0
            
            if isinstance(order.get("products"), list):
                for p_name in order["products"]:
                    total_products += 1
                    product_counter[p_name] += 1
                    
                    if p_name in pricing.get(order_id, {}) and "buy" in pricing[order_id].get(p_name, {}) and "sell" in pricing[order_id].get(p_name, {}):
                        buy = pricing[order_id][p_name]["buy"]
                        sell = pricing[order_id][p_name]["sell"]
                        profit = sell - buy
                        order_buy += buy
                        order_sell += sell
                        details.append(f"  - {p_name} | شراء: {format_float(buy)} | بيع: {format_float(sell)} | ربح: {format_float(profit)}")
                    else:
                        details.append(f"  - {p_name} | (لم يتم تسعيره)")
            else:
                details.append(f"  (لا توجد منتجات محددة لهذا الطلب)")

            total_buy_all_orders += order_buy
            total_sell_all_orders += order_sell
            details.append(f"  *ربح هذه الطلبية:* {format_float(order_sell - order_buy)}")

        top_product_str = "لا يوجد"
        if product_counter:
            top_product_name, top_product_count = product_counter.most_common(1)[0]
            top_product_str = f"{top_product_name} ({top_product_count} مرة)"

        result = (
            f"**--- تقرير عام عن الطلبات ---**\n"
            f"**إجمالي عدد الطلبات المعالجة:** {total_orders}\n"
            f"**إجمالي عدد المنتجات المباعة (في الطلبات المعالجة):** {total_products}\n"
            f"**أكثر منتج تم طلبه:** {top_product_str}\n\n"
            f"**مجموع الشراء الكلي (للطلبات المعالجة):** {format_float(total_buy_all_orders)}\n"
            f"**مجموع البيع الكلي (للطلبات المعالجة):** {format_float(total_sell_all_orders)}\n" 
            f"**صافي الربح الكلي (للطلبات المعالجة):** {format_float(total_sell_all_orders - total_buy_all_orders)}\n" 
            f"**الربح التراكمي في البوت (منذ آخر تصفير):** {format_float(daily_profit)} دينار\n\n"
            f"**--- تفاصيل الطلبات ---**\n" + "\n".join(details)
        )
        await update.message.reply_text(result, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in show_report: {e}", exc_info=True)
        await update.message.reply_text("عذراً، حدث خطأ أثناء عرض التقرير.")

def main():
    app = ApplicationBuilder().token(TOKEN).build()

    # وضع المتغيرات العالمية في bot_data
    app.bot_data['orders'] = orders
    app.bot_data['pricing'] = pricing
    app.bot_data['invoice_numbers'] = invoice_numbers
    app.bot_data['daily_profit'] = daily_profit
    app.bot_data['last_button_message'] = last_button_message
    
    # تمرير دوال الحفظ العامة لـ bot_data حتى تتمكن الدوال الأخرى من استدعائها
    app.bot_data['schedule_save_global_func'] = schedule_save_global
    app.bot_data['_save_data_to_disk_global_func'] = _save_data_to_disk_global


    # ✅ Handlers خارج المحادثة
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("profit", show_profit))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^الارباح$|^ارباح$"), show_profit))
    app.add_handler(CommandHandler("reset", reset_all))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^صفر$|^تصفير$"), reset_all))
    app.add_handler(CallbackQueryHandler(confirm_reset, pattern="^(confirm_reset|cancel_reset)$"))
    app.add_handler(CommandHandler("report", show_report))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^التقارير$|^تقرير$|^تقارير$"), show_report))
    
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE, edited_message))
    app.add_handler(CallbackQueryHandler(edit_prices, pattern="^edit_prices_"))
    app.add_handler(CallbackQueryHandler(start_new_order_callback, pattern="^start_new_order$"))
    # أمر /zones لعرض المناطق
    app.add_handler(CommandHandler("zones", list_zones))
    # استجابة نصية "مناطق" أو "المناطق"
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"^(مناطق|المناطق)$"), list_zones))
    # ✅ تم إزالة أمر /add_zones_bulk لأنه لن يتم استخدامه بعد الآن

    # ✅ ConversationHandler لعدد المحلات
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

    # ✅ ConversationHandler لإنشاء وتسعير الطلبات
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

    # ✅ تشغيل البوت
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
