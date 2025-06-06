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

# تهيئة المتغيرات العامة
orders = {}
pricing = {}
invoice_numbers = {}
daily_profit = 0.0
last_button_message = {}

# متغيرات الحفظ المؤجل
save_timer = None
save_pending = False
save_lock = threading.Lock()

# تحميل البيانات عند بدء تشغيل البوت
def load_data():
    global orders, pricing, invoice_numbers, daily_profit, last_button_message

    os.makedirs(DATA_DIR, exist_ok=True)

    if os.path.exists(ORDERS_FILE):
        with open(ORDERS_FILE, "r") as f:
            try:
                temp_data = json.load(f)
                orders.clear() 
                orders.update(temp_data) 
                orders = {str(k): v for k, v in orders.items()}
            except json.JSONDecodeError:
                orders.clear() 
                logger.warning("orders.json is corrupted or empty, reinitializing.")
            except Exception as e:
                logger.error(f"Error loading orders.json: {e}, reinitializing.")
                orders.clear()

    if os.path.exists(PRICING_FILE):
        with open(PRICING_FILE, "r") as f:
            try:
                temp_data = json.load(f)
                pricing.clear()
                pricing.update(temp_data)
                pricing = {str(pk): pv for pk, pv in pricing.items()}
                for oid in pricing:
                    if isinstance(pricing[oid], dict):
                        pricing[oid] = {str(pk): pv for pk, pv in pricing[oid].items()}
            except json.JSONDecodeError:
                pricing.clear()
                logger.warning("pricing.json is corrupted or empty, reinitializing.")
            except Exception as e:
                logger.error(f"Error loading pricing.json: {e}, reinitializing.")
                pricing.clear()

    if os.path.exists(INVOICE_NUMBERS_FILE):
        with open(INVOICE_NUMBERS_FILE, "r") as f:
            try:
                temp_data = json.load(f)
                invoice_numbers.clear()
                invoice_numbers.update(temp_data)
                invoice_numbers = {str(k): v for k, v in invoice_numbers.items()}
            except json.JSONDecodeError:
                invoice_numbers.clear()
                logger.warning("invoice_numbers.json is corrupted or empty, reinitializing.")
            except Exception as e:
                logger.error(f"Error loading invoice_numbers.json: {e}, reinitializing.")
                invoice_numbers.clear()

    if os.path.exists(DAILY_PROFIT_FILE):
        with open(DAILY_PROFIT_FILE, "r") as f:
            try:
                daily_profit = json.load(f)
                logger.info(f"Loaded daily_profit: {daily_profit} from {DAILY_PROFIT_FILE}")
            except json.JSONDecodeError:
                daily_profit = 0.0
                logger.warning(f"{DAILY_PROFIT_FILE} is corrupted or empty, reinitializing daily_profit.")
            except Exception as e:
                logger.error(f"Error loading {DAILY_PROFIT_FILE}: {e}, reinitializing daily_profit.")
                daily_profit = 0.0
    
    if os.path.exists(LAST_BUTTON_MESSAGE_FILE):
        with open(LAST_BUTTON_MESSAGE_FILE, "r") as f:
            try:
                temp_data = json.load(f)
                last_button_message.clear()
                last_button_message.update(temp_data)
                last_button_message = {str(k): v for k, v in last_button_message.items()}
            except json.JSONDecodeError:
                last_button_message.clear()
                logger.warning("last_button_message.json is corrupted or empty, reinitializing.")
            except Exception as e:
                logger.error(f"Error loading last_button_message.json: {e}, reinitializing.")
                last_button_message.clear()

# حفظ البيانات
def _save_data_to_disk():
    global save_pending
    with save_lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        try:
            with open(ORDERS_FILE, "w") as f:
                json.dump(orders, f)
            with open(PRICING_FILE, "w") as f:
                json.dump(pricing, f)
            with open(INVOICE_NUMBERS_FILE, "w") as f:
                json.dump(invoice_numbers, f)
            with open(DAILY_PROFIT_FILE, "w") as f:
                json.dump(daily_profit, f)
                logger.info(f"Saved daily_profit: {daily_profit} to {DAILY_PROFIT_FILE}.")
            with open(LAST_BUTTON_MESSAGE_FILE, "w") as f:
                json.dump(last_button_message, f)
            logger.info("All data saved to disk successfully.")
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

# حالات المحادثة
ASK_BUY, ASK_SELL = range(2)

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
        await asyncio.sleep(0.05) # تأخير خفيف قبل الحذف
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.info(f"Successfully deleted message {message_id} from chat {chat_id} in background.")
    except Exception as e:
        logger.warning(f"Could not delete message {message_id} from chat {chat_id} in background: {e}.")

# دالة مساعدة لحفظ البيانات في الخلفية
async def save_data_in_background(context: ContextTypes.DEFAULT_TYPE):
    schedule_save()
    logger.info("Data save scheduled in background.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    if user_id in context.user_data:
        context.user_data[user_id].pop("order_id", None)
        context.user_data[user_id].pop("product", None)
        context.user_data[user_id].pop("current_active_order_id", None)
        context.user_data[user_id].pop("messages_to_delete", None) 
        logger.info(f"Cleared order-specific user_data for user {user_id} on /start command.")
    
    await update.message.reply_text("أهلاً بك يا أبا الأكبر! لإعداد طلبية، دز الطلبية كلها برسالة واحدة.\n\n*السطر الأول:* عنوان الزبون.\n*الأسطر الباقية:* كل منتج بسطر واحد.", parse_mode="Markdown")
    return ConversationHandler.END

async def receive_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"[{update.effective_chat.id}] Processing order from: {update.effective_user.id} - Message ID: {update.message.message_id}")
    await process_order(update, context, update.message)

async def edited_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.edited_message:
        return
    logger.info(f"[{update.effective_chat.id}] Processing edited order from: {update.effective_user.id} - Message ID: {update.edited_message.message_id}")
    await process_order(update, context, update.edited_message, edited=True)

async def process_order(update, context, message, edited=False):
    user_id = str(message.from_user.id)
    lines = [line.strip() for line in message.text.strip().split('\n') if line.strip()]
    
    if len(lines) < 2:
        if not edited:
            await message.reply_text("الرجاء التأكد من كتابة عنوان الزبون في السطر الأول والمنتجات في الأسطر التالية.")
        return

    title = lines[0]
    products = [p for p in lines[1:] if p.strip()]

    if not products:
        if not edited:
            await message.reply_text("الرجاء إضافة منتجات بعد العنوان.")
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
        orders[order_id] = {"user_id": user_id, "title": title, "products": products, "places_count": 0} 
        pricing[order_id] = {p: {} for p in products}
        invoice_numbers[order_id] = invoice_no
        logger.info(f"Created new order {order_id} for user {user_id}.")
    else: 
        old_products = set(orders[order_id].get("products", []))
        new_products = set(products)
        
        orders[order_id]["title"] = title
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
    
    if is_new_order:
        await message.reply_text(f"استلمت الطلب بعنوان: *{title}* (عدد المنتجات: {len(products)})", parse_mode="Markdown")
        await show_buttons(message.chat_id, context, user_id, order_id)
    else:
        await show_buttons(message.chat_id, context, user_id, order_id, confirmation_message="تم تحديث الطلب. الرجاء التأكد من تسعير أي منتجات جديدة.")
        
async def show_buttons(chat_id, context, user_id, order_id, confirmation_message=None):
    logger.info(f"[{chat_id}] show_buttons called for order {order_id}. User: {user_id}. Current pricing for order {order_id}: {pricing.get(order_id)}")
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
        else:
            pending_products.append(p)
    
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

    # ***** التعديل الكبير هنا: دائماً نحذف الرسالة القديمة (إذا موجودة) ونرسل رسالة جديدة *****
    msg_info = last_button_message.get(order_id)
    if msg_info:
        logger.info(f"[{chat_id}] Deleting old button message {msg_info['message_id']} for order {order_id} before sending new one.")
        context.application.create_task(delete_message_in_background(context, chat_id=msg_info["chat_id"], message_id=msg_info["message_id"]))
        # لا نحذفها من last_button_message هنا، بل بعد أن يتم إرسال الرسالة الجديدة بنجاح
        # del last_button_message[order_id] 

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=message_text,
        reply_markup=markup,
        parse_mode="Markdown"
    )
    logger.info(f"[{chat_id}] Sent new button message {msg.message_id} for order {order_id}")
    last_button_message[order_id] = {"chat_id": chat_id, "message_id": msg.message_id}
    context.application.create_task(save_data_in_background(context))

    # ***** هنا راح نضيف عملية الحذف المتأخر للرسائل القديمة (أسئلة وأجوبة سابقة) *****
    if user_id in context.user_data and 'messages_to_delete' in context.user_data[user_id]:
        logger.info(f"[{chat_id}] Scheduling deletion of {len(context.user_data[user_id].get('messages_to_delete', []))} old messages after showing new buttons for user {user_id}.")
        for msg_info in context.user_data[user_id]['messages_to_delete']:
            context.application.create_task(delete_message_in_background(context, chat_id=msg_info['chat_id'], message_id=msg_info['message_id']))
        context.user_data[user_id]['messages_to_delete'].clear()


async def product_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    logger.info(f"[{query.message.chat_id}] Callback query received: {query.data} from user {query.from_user.id}")

    user_id = str(query.from_user.id)
    
    try:
        order_id, product = query.data.split("|", 1) 
    except ValueError as e:
        logger.error(f"[{query.message.chat_id}] Failed to parse callback_data for product selection: {query.data}. Error: {e}")
        await query.message.reply_text("عذراً، حدث خطأ في بيانات الزر. الرجاء بدء طلبية جديدة.")
        return ConversationHandler.END

    if order_id not in orders or product not in orders[order_id].get("products", []):
        logger.warning(f"[{query.message.chat_id}] Order ID '{order_id}' not found or Product '{product}' not in products for order '{order_id}'.")
        await query.message.reply_text("عذراً، الطلب أو المنتج غير موجود. الرجاء بدء طلبية جديدة أو التحقق من المنتجات.")
        if user_id in context.user_data:
            context.user_data[user_id].pop("order_id", None)
            context.user_data[user_id].pop("product", None)
            context.user_data[user_id].pop("current_active_order_id", None)
            context.user_data[user_id].pop("messages_to_delete", None)
        return ConversationHandler.END
    
    context.user_data.setdefault(user_id, {}).update({"order_id": order_id, "product": product})
    logger.info(f"[{query.message.chat_id}] User {user_id} selected product '{product}' for order '{order_id}'. User data updated: {context.user_data.get(user_id)}")
    
    if 'messages_to_delete' not in context.user_data[user_id]:
        context.user_data[user_id]['messages_to_delete'] = [] 

    # التعديل هنا: لا نحذف رسالة الأزرار مباشرة، بل نضيفها لقائمة الحذف المتأخر.
    # الأزرار ستُحذف لاحقاً بواسطة show_buttons عند إرسال القائمة الجديدة.
    if query.message:
        context.user_data[user_id]['messages_to_delete'].append({
            'chat_id': query.message.chat_id,
            'message_id': query.message.message_id
        })
        logger.info(f"[{query.message.chat_id}] Added button message {query.message.message_id} to delete queue.")
        # نعدل الرسالة لتظهر أنها تم الضغط عليها (يمكن إزالة الأزرار فوراً)
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=query.message.chat_id,
                message_id=query.message.message_id,
                reply_markup=None 
            )
        except Exception as e:
            logger.warning(f"[{query.message.chat_id}] Could not clear buttons from message {query.message.message_id} directly: {e}. Proceeding.")


    msg = await query.message.reply_text(f"تمام، كم سعر شراء *'{product}'*؟", parse_mode="Markdown")
    context.user_data[user_id]['messages_to_delete'].append({'chat_id': msg.chat_id, 'message_id': msg.message_id})
    logger.info(f"[{query.message.chat_id}] Asking for buy price for '{product}'. Next state: ASK_BUY. Current user_data: {context.user_data.get(user_id)}")
    
    return ASK_BUY
    
async def receive_buy_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    logger.info(f"[{update.effective_chat.id}] Received message for buy price from user {user_id}: '{update.message.text}'. Current user_data: {context.user_data.get(user_id)}")

    context.user_data.setdefault(user_id, {})
    if 'messages_to_delete' not in context.user_data[user_id]:
        context.user_data[user_id]['messages_to_delete'] = []
    
    context.user_data[user_id]['messages_to_delete'].append({
        'chat_id': update.message.chat_id,
        'message_id': update.message.message_id
    })

    data = context.user_data.get(user_id)
    if not data or "order_id" not in data or "product" not in data:
        logger.error(f"[{update.effective_chat.id}] Buy price: Missing order_id or product in user_data for user {user_id}. User data: {data}")
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
    
    if not update.message.text.strip().replace('.', '', 1).isdigit(): 
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
        logger.error(f"[{update.effective_chat.id}] Buy price: ValueError for user {user_id} with input '{update.message.text}': {e}")
        msg_error = await update.message.reply_text("الرجاء إدخال رقم صحيح")
        context.user_data[user_id]['messages_to_delete'].append({
                'chat_id': msg_error.chat_id, 
                'message_id': msg_error.message_id
            })
        return ASK_BUY
    
    msg = await update.message.reply_text(f"شكراً. وهسه، بيش راح تبيع *'{product}'*؟", parse_mode="Markdown")
    context.user_data[user_id]['messages_to_delete'].append({'chat_id': msg.chat_id, 'message_id': msg.message_id})
    logger.info(f"[{update.effective_chat.id}] Asking for sell price for '{product}'. Next state: ASK_SELL. Current user_data: {context.user_data.get(user_id)}")
    
    return ASK_SELL


async def receive_sell_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    logger.info(f"[{update.effective_chat.id}] Received message for sell price from user {user_id}: '{update.message.text}'. Current user_data: {context.user_data.get(user_id)}")

    context.user_data.setdefault(user_id, {})
    if 'messages_to_delete' not in context.user_data[user_id]:
        context.user_data[user_id]['messages_to_delete'] = []
    context.user_data[user_id]['messages_to_delete'].append({'chat_id': update.message.chat_id, 'message_id': update.message.message_id})

    data = context.user_data.get(user_id)
    if not data or "order_id" not in data or "product" not in data:
        logger.error(f"[{update.effective_chat.id}] Sell price: Missing order_id or product in user_data for user {user_id}. User data: {data}")
        msg_error = await update.message.reply_text("عذراً، لم أتمكن من تحديد الطلبية أو المنتج لتسعيره. الرجاء اضغط على المنتج من القائمة أولاً لتحديد سعره، أو ابدأ طلبية جديدة.", parse_mode="Markdown")
        context.user_data[user_id]['messages_to_delete'].append({
            'chat_id': msg_error.chat_id, 
            'message_id': msg_error.message_id
        })
        return ConversationHandler.END
    
    order_id, product = data["order_id"], data["product"]
    
    if order_id not in orders or product not in orders[order_id].get("products", []):
        logger.warning(f"[{update.effective_chat.id}] Sell price: Order ID '{order_id}' not found or Product '{product}' not in products for order '{order_id}'.")
        msg_error = await update.message.reply_text("عذراً، الطلبية أو المنتج لم يعد موجوداً. الرجاء بدء طلبية جديدة.")
        context.user_data[user_id]['messages_to_delete'].append({
            'chat_id': msg_error.chat_id, 
            'message_id': msg_error.message_id
        })
        return ConversationHandler.END

    if not update.message.text.strip().replace('.', '', 1).isdigit(): 
        logger.warning(f"[{update.effective_chat.id}] Sell price: Non-numeric input from user {user_id}: '{update.message.text}'")
        msg_error = await update.message.reply_text("الرجاء إدخال *رقم* صحيح لسعر البيع.")
        context.user_data[user_id]['messages_to_delete'].append({'chat_id': msg_error.chat_id, 'message_id': msg_error.message_id})
        return ASK_SELL 

    try:
        price = float(update.message.text.strip())
        if price < 0:
            logger.warning(f"[{update.effective_chat.id}] Sell price: Negative price from user {user_id}: '{update.message.text}'")
            msg_error = await update.message.reply_text("سعر البيع يجب أن يكون رقماً إيجابياً. بيش راح تبيع بالضبط؟")
            context.user_data[user_id]['messages_to_delete'].append({'chat_id': msg_error.chat_id, 'message_id': msg_error.message_id})
            return ASK_SELL 
    except ValueError as e:
        logger.error(f"[{update.effective_chat.id}] Sell price: ValueError for user {user_id} with input '{update.message.text}': {e}")
        msg_error = await update.message.reply_text("الرجاء إدخال رقم صحيح لسعر البيع. بيش حتبيع؟")
        context.user_data[user_id]['messages_to_delete'].append({'chat_id': msg_error.chat_id, 'message_id': msg_error.message_id})
        return ASK_SELL 
    
    pricing.setdefault(order_id, {}).setdefault(product, {})["sell"] = price
    context.application.create_task(save_data_in_background(context))
    logger.info(f"[{update.effective_chat.id}] Sell price for '{product}' in order '{order_id}' saved. Current user_data: {context.user_data.get(user_id)}. Updated pricing for order {order_id}: {pricing.get(order_id)}") # Log pricing

    order = orders[order_id]
    all_priced = True
    for p in order["products"]:
        if p not in pricing.get(order_id, {}) or "buy" not in pricing[order_id].get(p, {}) or "sell" not in pricing[order_id].get(p, {}):
            all_priced = False
            break
            
    if all_priced:
        context.user_data[user_id]["current_active_order_id"] = order_id
        logger.info(f"[{update.effective_chat.id}] All products priced for order {order_id}. Requesting places count. Exiting conversation for user {user_id}.")
        await request_places_count_standalone(update.effective_chat.id, context, user_id, order_id)
        return ConversationHandler.END
    else:
        confirmation_msg = f"تم حفظ السعر لـ *'{product}'*."
        logger.info(f"[{update.effective_chat.id}] Price saved for '{product}' in order {order_id}. Showing updated buttons with confirmation. User {user_id} can select next product.")
        # ***** هنا الاستدعاء لدالة show_buttons هو المهم جداً *****
        # هذا سيضمن إرسال الأزرار المحدثة (مع الصح) وحذف الرسائل القديمة تلقائياً.
        await show_buttons(update.effective_chat.id, context, user_id, order_id, confirmation_message=confirmation_msg)
        return ConversationHandler.END # ننهي المحادثة هنا بعد كل تسعير منتج

async def request_places_count_standalone(chat_id, context: ContextTypes.DEFAULT_TYPE, user_id: str, order_id: str):
    logger.info(f"[{chat_id}] Requesting places count for order {order_id} from user {user_id}.")
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
    context.user_data.setdefault(user_id, {}).setdefault('messages_to_delete', []).append({'chat_id': msg_places.chat_id, 'message_id': msg_places.message_id})

    # ***** هنا نضيف عملية الحذف المتأخر للرسائل القديمة (سؤال وجواب السعر) *****
    if user_id in context.user_data and 'messages_to_delete' in context.user_data[user_id]:
        logger.info(f"[{chat_id}] Scheduling deletion of {len(context.user_data[user_id].get('messages_to_delete', []))} old messages after showing places buttons for user {user_id}.")
        for msg_info in context.user_data[user_id]['messages_to_delete']:
            context.application.create_task(delete_message_in_background(context, chat_id=msg_info['chat_id'], message_id=msg_info['message_id']))
        context.user_data[user_id]['messages_to_delete'].clear()


async def handle_places_count_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global daily_profit
    
    places = None
    chat_id = update.effective_chat.id
    user_id = str(update.effective_user.id) 
    logger.info(f"[{chat_id}] handle_places_count_data triggered by user {user_id}. Update type: {'CallbackQuery' if update.callback_query else 'Message'}. Current user_data: {context.user_data.get(user_id)}")

    context.user_data.setdefault(user_id, {})
    if 'messages_to_delete' not in context.user_data[user_id]:
        context.user_data[user_id]['messages_to_delete'] = []

    order_id_to_process = None 

    if update.callback_query:
        query = update.callback_query
        logger.info(f"[{chat_id}] Places count callback query received (standalone): {query.data}")
        await query.answer()
        
        try:
            parts = query.data.split('_')
            if len(parts) == 4 and parts[0] == "places" and parts[1] == "data":
                order_id_to_process = parts[2] 
                
                if order_id_to_process not in orders:
                    logger.error(f"[{chat_id}] Order ID '{order_id_to_process}' from callback data not found in global orders (standalone).")
                    await context.bot.send_message(chat_id=chat_id, text="عذراً، الطلبية اللي حاول تختار عدد محلاتها ما موجودة عندي. الرجاء بدء طلبية جديدة.")
                    if user_id in context.user_data and "current_active_order_id" in context.user_data[user_id]:
                        del context.user_data[user_id]["current_active_order_id"]
                    return 

                places = int(parts[3])
                if query.message:
                    context.user_data[user_id]['messages_to_delete'].append({
                        'chat_id': query.message.chat_id,
                        'message_id': query.message.message_id
                    })
                    logger.info(f"[{chat_id}] Added places buttons message {query.message.message_id} to delete queue.")
                    try:
                        await context.bot.edit_message_reply_markup(
                            chat_id=query.message.chat_id,
                            message_id=query.message.message_id,
                            reply_markup=None
                        )
                    except Exception as e:
                        logger.warning(f"[{chat_id}] Could not clear buttons from places message {query.message.message_id} directly: {e}. Proceeding.")

            else:
                raise ValueError(f"Unexpected callback_data format for places count (standalone): {query.data}")
        except (ValueError, IndexError) as e:
            logger.error(f"[{chat_id}] Failed to parse places count from callback data (standalone) '{query.data}': {e}")
            await context.bot.send_message(chat_id=chat_id, text="عذراً، حدث خطأ في بيانات الزر. الرجاء المحاولة مرة أخرى.")
            return 

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
             return

        if not update.message.text.strip().isdigit(): 
            logger.warning(f"[{chat_id}] Places count text input: Non-integer input from user {user_id}: '{update.message.text}'")
            msg_error = await context.bot.send_message(chat_id=chat_id, text="الرجاء إدخال *رقم صحيح* لعدد المحلات.")
            context.user_data[user_id]['messages_to_delete'].append({'chat_id': msg_error.chat_id, 'message_id': msg_error.message_id})
            return 

        try:
            places = int(update.message.text.strip())
            if places < 0:
                logger.warning(f"[{chat_id}] Places count text input: Negative value from user {user_id}: '{update.message.text}'")
                msg_error = await context.bot.send_message(chat_id=chat_id, text="عدد المحلات يجب أن يكون رقماً موجباً. الرجاء إدخال عدد المحلات بشكل صحيح.")
                context.user_data[user_id]['messages_to_delete'].append({'chat_id': msg_error.chat_id, 'message_id': msg_error.message_id})
                return 
        except ValueError as e: 
            logger.error(f"[{chat_id}] Places count text input: ValueError for user {user_id} with input '{update.message.text}': {e}")
            msg_error = await context.bot.send_message(chat_id=chat_id, text="الرجاء إدخال عدد صحيح لعدد المحلات.")
            context.user_data[user_id]['messages_to_delete'].append({'chat_id': msg_error.chat_id, 'message_id': msg_error.message_id})
            return 
    
    if places is None or order_id_to_process is None:
        logger.warning(f"[{chat_id}] handle_places_count_data: No valid places count or order ID to process.")
        await context.bot.send_message(chat_id=chat_id, text="عذراً، لم أتمكن من فهم عدد المحلات أو الطلبية. الرجاء إدخال رقم صحيح أو البدء بطلبية جديدة.")
        if user_id in context.user_data and "current_active_order_id" in context.user_data[user_id]:
            del context.user_data[user_id]["current_active_order_id"]
        return 

    orders[order_id_to_process]["places_count"] = places
    context.application.create_task(save_data_in_background(context))
    logger.info(f"[{chat_id}] Places count {places} saved for order {order_id_to_process}. Current user_data: {context.user_data.get(user_id)}")


    if user_id in context.user_data and 'messages_to_delete' in context.user_data[user_id]:
        logger.info(f"[{chat_id}] Scheduling deletion of {len(context.user_data[user_id].get('messages_to_delete', []))} old messages after showing final options for user {user_id}.")
        for msg_info in context.user_data[user_id]['messages_to_delete']:
            context.application.create_task(delete_message_in_background(context, chat_id=msg_info['chat_id'], message_id=msg_info['message_id']))
        context.user_data[user_id]['messages_to_delete'].clear()
    
    await show_final_options(chat_id, context, user_id, order_id_to_process, message_prefix="تم تحديث عدد المحلات بنجاح.")
    
    if user_id in context.user_data and "current_active_order_id" in context.user_data[user_id]:
        del context.user_data[user_id]["current_active_order_id"]
        logger.info(f"[{chat_id}] Cleared current_active_order_id for user {user_id} after processing places count.")

    return


async def show_final_options(chat_id, context, user_id, order_id, message_prefix=None):
    global daily_profit
    logger.info(f"[{chat_id}] Showing final options for order {order_id} to user {user_id}.")
    
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
    final_total = total_sell + extra_cost

    logger.info(f"[{chat_id}] Daily profit before addition for order {order_id}: {daily_profit}")
    daily_profit += net_profit
    logger.info(f"[{chat_id}] Daily profit after adding {net_profit} for order {order_id}: {daily_profit}")
    context.application.create_task(save_data_in_background(context))

    customer_invoice_lines = []
    customer_invoice_lines.append(f"**أبو الأكبر للتوصيل**") 
    customer_invoice_lines.append(f"رقم الفاتورة: {invoice}")
    customer_invoice_lines.append(f"عنوان الزبون: {order['title']}")
    customer_invoice_lines.append(f"\n*المواد:*") 
    
    running_total_for_customer = 0.0
    for p in order["products"]:
        if p in pricing.get(order_id, {}) and "sell" in pricing[order_id].get(p, {}):
            sell = pricing[order_id][p]["sell"]
            running_total_for_customer += sell
            customer_invoice_lines.append(f"{p} - {format_float(sell)} = {format_float(running_total_for_customer)}")
        else:
            customer_invoice_lines.append(f"{p} - (لم يتم تسعيره)")
    
    customer_invoice_lines.append(f"كلفة تجهيز من - {current_places} محلات {format_float(extra_cost)} = {format_float(final_total)}")
    customer_invoice_lines.append(f"\n*المجموع الكلي:* {format_float(final_total)} (مع احتساب عدد المحلات)") 
    
    customer_final_text = "\n".join(customer_invoice_lines)

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=customer_final_text,
            parse_mode="Markdown"
        )
        logger.info(f"[{chat_id}] Customer invoice sent as a separate message for order {order_id}.")
    except Exception as e:
        logger.error(f"[{chat_id}] Could not send customer invoice as separate message to chat {chat_id}: {e}")
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
        logger.error(f"[{chat_id}] Could not send admin invoice to OWNER_ID {OWNER_ID}: {e}")
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
        logger.info(f"[{chat_id}] Cleaned up order-specific user_data for user {user_id} after showing final options.")

async def edit_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    logger.info(f"[{query.message.chat_id}] Edit prices callback from user {user_id}: {query.data}")
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
    
    if order_id in last_button_message:
        del last_button_message[order_id]
        context.application.create_task(save_data_in_background(context))

    await show_buttons(query.message.chat_id, context, user_id, order_id, confirmation_message="يمكنك الآن تعديل أسعار المنتجات أو إضافة/حذف منتجات بتعديل الرسالة الأصلية للطلبية.")
    logger.info(f"[{query.message.chat_id}] Showing edit buttons for order {order_id}. Exiting conversation for user {user_id}.")
    return ConversationHandler.END

async def start_new_order_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    logger.info(f"[{query.message.chat_id}] Start new order callback from user {user_id}.")
    if user_id in context.user_data:
        context.user_data[user_id].pop("order_id", None)
        context.user_data[user_id].pop("product", None)
        context.user_data[user_id].pop("current_active_order_id", None)
        context.user_data[user_id].pop("messages_to_delete", None) 
        logger.info(f"[{query.message.chat_id}] Cleared order-specific user_data for user {user_id} after starting a new order from button.")

    if query.message:
        context.application.create_task(delete_message_in_background(context, chat_id=query.message.chat_id, message_id=query.message.message_id))

    await query.message.reply_text("تمام، دز الطلبية الجديدة كلها برسالة واحدة.\n\n*السطر الأول:* عنوان الزبون.\n*الأسطر الباقية:* كل منتج بسطر واحد.", parse_mode="Markdown")
    
    return ConversationHandler.END


async def show_profit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.message.from_user.id) != str(OWNER_ID):
        await update.message.reply_text("عذراً، هذا الأمر متاح للمالك فقط.")
        return
    logger.info(f"Current daily_profit requested by user {update.message.from_user.id}: {daily_profit}")
    await update.message.reply_text(f"الربح التراكمي الإجمالي: *{format_float(daily_profit)}* دينار", parse_mode="Markdown")

async def reset_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.message.from_user.id) != str(OWNER_ID):
        await update.message.reply_text("عذراً، هذا الأمر متاح للمالك فقط.")
        return
    
    keyboard = [
        [InlineKeyboardButton("نعم، متأكد", callback_data="confirm_reset")],
        [InlineKeyboardButton("لا، إلغاء", callback_data="cancel_reset")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("هل أنت متأكد من تصفير جميع الأرباح ومسح كل الطلبات؟ هذا الإجراء لا يمكن التراجع عنه.", reply_markup=reply_markup)

async def confirm_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if str(query.from_user.id) != str(OWNER_ID):
        await query.edit_message_text("عذراً، لا تملك صلاحية لتنفيذ هذا الأمر.")
        return

    if query.data == "confirm_reset":
        global daily_profit, orders, pricing, invoice_numbers, last_button_message
        logger.info(f"Daily profit before reset: {daily_profit}")
        daily_profit = 0.0
        orders.clear()
        pricing.clear()
        invoice_numbers.clear()
        last_button_message.clear()
        
        try:
            with open(COUNTER_FILE, "w") as f:
                f.write("1")
        except Exception as e:
            logger.error(f"Could not reset invoice counter file: {e}")

        _save_data_to_disk()
        logger.info(f"Daily profit after reset: {daily_profit}")
        await query.edit_message_text("تم تصفير الأرباح ومسح كل الطلبات بنجاح.")
    elif query.data == "cancel_reset":
        await query.edit_message_text("تم إلغاء عملية التصفير.")

async def show_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^الارباح$|^ارباح$"), show_profit))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^صفر$|^تصفير$"), reset_all))
    app.add_handler(CallbackQueryHandler(confirm_reset, pattern="^(confirm_reset|cancel_reset)$"))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^التقارير$|^تقرير$|^تقارير$"), show_report))
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE, edited_message))

    app.add_handler(CallbackQueryHandler(edit_prices, pattern="^edit_prices_"))
    app.add_handler(CallbackQueryHandler(start_new_order_callback, pattern="^start_new_order$"))

    # هذا الهاندلر الخاص بأزرار المحلات فقط، ولا يستقبل أي رسالة نصية!
    app.add_handler(CallbackQueryHandler(handle_places_count_data, pattern=r"^places_data_[a-f0-9]{8}_\d+$"))
    
    order_creation_conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_order),
            CallbackQueryHandler(product_selected, pattern=r"^[a-f0-9]{8}\|.+$")
        ],
        states={
            ASK_BUY: [
                MessageHandler(filters.TEXT & filters.Regex(r"^\d+(\.\d+)?$") & ~filters.COMMAND, receive_buy_price),
                MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: ConversationHandler.END),
            ],
            ASK_SELL: [
                MessageHandler(filters.TEXT & filters.Regex(r"^\d+(\.\d+)?$") & ~filters.COMMAND, receive_sell_price),
                MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: ConversationHandler.END),
            ]
        },
        fallbacks=[
            CommandHandler("cancel", lambda u, c: ConversationHandler.END),
            MessageHandler(filters.TEXT | filters.COMMAND, lambda u, c: ConversationHandler.END)
        ]
    )
    app.add_handler(order_creation_conv_handler)
    
    logger.info("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
