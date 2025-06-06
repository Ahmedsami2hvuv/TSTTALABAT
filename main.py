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
        # مسح فقط بيانات الطلب الحالية وليس كل شيء
        context.user_data[user_id].pop("order_id", None)
        context.user_data[user_id].pop("product", None)
        context.user_data[user_id].pop("current_active_order_id", None)
        context.user_data[user_id].pop("messages_to_delete", None) 
        logger.info(f"Cleared order-specific user_data for user {user_id} on /start command.")
    
    await update.message.reply_text("أهلاً بك يا أبا الأكبر! لإعداد طلبية، دز الطلبية كلها برسالة واحدة.\n\n*السطر الأول:* عنوان الزبون.\n*الأسطر الباقية:* كل منتج بسطر واحد.", parse_mode="Markdown")
    return ConversationHandler.END

async def receive_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await process_order(update, context, update.message)

async def edited_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.edited_message:
        return
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
    is_new_order = True # نفترض إنها طلبية جديدة بالبداية

    if edited:
        for oid, msg_info in last_button_message.items():
            if msg_info and msg_info.get("message_id") == message.message_id and str(msg_info.get("chat_id")) == str(message.chat_id):
                if oid in orders and str(orders[oid].get("user_id")) == user_id:
                    order_id = oid
                    is_new_order = False
                    logger.info(f"Found existing order {order_id} for user {user_id} based on message ID (edited message).")
                    break
                else:
                    logger.warning(f"Message ID {message.message_id} found in last_button_message but not linked to user {user_id} or order {oid} is missing. Treating as new.")
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
        logger.info(f"Updated existing order {order_id} for user {user_id}.")

    context.application.create_task(save_data_in_background(context))
    
    if is_new_order:
        await message.reply_text(f"استلمت الطلب بعنوان: *{title}* (عدد المنتجات: {len(products)})", parse_mode="Markdown")
        await show_buttons(message.chat_id, context, user_id, order_id)
    else:
        await show_buttons(message.chat_id, context, user_id, order_id, confirmation_message="تم تحديث الطلب. الرجاء التأكد من تسعير أي منتجات جديدة.")
        
async def show_buttons(chat_id, context, user_id, order_id, confirmation_message=None):
    if order_id not in orders:
        logger.warning(f"Attempted to show buttons for non-existent order_id: {order_id}")
        await context.bot.send_message(chat_id=chat_id, text="عذراً، الطلب الذي تحاول الوصول إليه غير موجود أو تم حذفه. الرجاء بدء طلبية جديدة.")
        # مسح بيانات الطلب من user_data إذا كان الطلب غير موجود
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

    msg_info = last_button_message.get(order_id)
    if msg_info and str(msg_info.get("chat_id")) == str(chat_id):
        try:
            msg = await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_info["message_id"],
                text=message_text,
                reply_markup=markup,
                parse_mode="Markdown"
            )
            logger.info(f"Edited existing button message {msg_info['message_id']} for order {order_id}.")
        except Exception as e:
            logger.warning(f"Could not edit message {msg_info['message_id']} for order {order_id}: {e}. Sending new one.")
            msg = await context.bot.send_message(
                chat_id=chat_id,
                text=message_text,
                reply_markup=markup,
                parse_mode="Markdown"
            )
            last_button_message[order_id] = {"chat_id": chat_id, "message_id": msg.message_id}
            context.application.create_task(save_data_in_background(context))
    else:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=message_text,
            reply_markup=markup,
            parse_mode="Markdown"
        )
        logger.info(f"Sent new button message {msg.message_id} for order {order_id}")
        last_button_message[order_id] = {"chat_id": chat_id, "message_id": msg.message_id}
        context.application.create_task(save_data_in_background(context))

async def product_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    logger.info(f"Callback query received: {query.data}")

    user_id = str(query.from_user.id)
    
    try:
        order_id, product = query.data.split("|", 1) 
    except ValueError as e:
        logger.error(f"Failed to parse callback_data for product selection: {query.data}. Error: {e}")
        await query.message.reply_text("عذراً، حدث خطأ في بيانات الزر. الرجاء بدء طلبية جديدة.")
        return ConversationHandler.END

    if order_id not in orders or product not in orders[order_id].get("products", []):
        logger.warning(f"Order ID '{order_id}' not found or Product '{product}' not in products for order '{order_id}'.")
        await query.message.reply_text("عذراً، الطلب أو المنتج غير موجود. الرجاء بدء طلبية جديدة أو التحقق من المنتجات.")
        # مسح بيانات الطلب من user_data إذا كان الطلب غير موجود
        if user_id in context.user_data:
            context.user_data[user_id].pop("order_id", None)
            context.user_data[user_id].pop("product", None)
            context.user_data[user_id].pop("current_active_order_id", None)
            context.user_data[user_id].pop("messages_to_delete", None)
        return ConversationHandler.END
    
    context.user_data.setdefault(user_id, {}).update({"order_id": order_id, "product": product})
    
    if 'messages_to_delete' not in context.user_data[user_id]:
        context.user_data[user_id]['messages_to_delete'] = [] 

    if query.message:
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=query.message.chat_id,
                message_id=query.message.message_id,
                reply_markup=InlineKeyboardMarkup([[]]) 
            )
            logger.info(f"Cleared buttons from message {query.message.message_id} for order {order_id}.")
        except Exception as e:
            logger.warning(f"Could not clear buttons from message {query.message.message_id}: {e}. Proceeding.")

    msg = await query.message.reply_text(f"تمام، كم سعر شراء *'{product}'*؟", parse_mode="Markdown")
    context.user_data[user_id]['messages_to_delete'].append({'chat_id': msg.chat_id, 'message_id': msg.message_id})
    
    return ASK_BUY
    
async def receive_buy_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    
    context.user_data.setdefault(user_id, {})
    if 'messages_to_delete' not in context.user_data[user_id]:
        context.user_data[user_id]['messages_to_delete'] = []
    
    context.user_data[user_id]['messages_to_delete'].append({
        'chat_id': update.message.chat_id,
        'message_id': update.message.message_id
    })

    data = context.user_data.get(user_id)
    # ***** هنا هو مكان التعديل اللي يحل المشكلة! *****
    # الرسالة تكون عامة إذا ما لكينا معلومات الطلب/المنتج من الـ user_data
    if not data or "order_id" not in data or "product" not in data:
        msg_error = await update.message.reply_text("عذراً، لم أتمكن من تحديد الطلبية أو المنتج لتسعيره. الرجاء اضغط على المنتج من القائمة أولاً لتحديد سعره، أو ابدأ طلبية جديدة.", parse_mode="Markdown")
        context.user_data[user_id]['messages_to_delete'].append({
            'chat_id': msg_error.chat_id, 
            'message_id': msg_error.message_id
        })
        return ConversationHandler.END
    
    order_id = data["order_id"]
    product = data["product"]
    
    # ***** التحقق من وجود الطلبية والمنتج وكونهما تابعين للمستخدم الحالي *****
    if order_id not in orders or str(orders[order_id].get("user_id")) != user_id or product not in orders[order_id].get("products", []):
        msg_error = await update.message.reply_text("عذراً، الطلبية أو المنتج لم يعد موجوداً أو ليس لك. الرجاء بدء طلبية جديدة أو التحقق من المنتجات.")
        context.user_data[user_id]['messages_to_delete'].append({
            'chat_id': msg_error.chat_id, 
            'message_id': msg_error.message_id
        })
        return ConversationHandler.END
    
    try:
        price = float(update.message.text.strip())
        if price < 0:
            msg_error = await update.message.reply_text("السعر يجب أن يكون موجباً")
            context.user_data[user_id]['messages_to_delete'].append({
                'chat_id': msg_error.chat_id, 
                'message_id': msg_error.message_id
            })
            return ASK_BUY
    except ValueError:
        msg_error = await update.message.reply_text("الرجاء إدخال رقم صحيح")
        context.user_data[user_id]['messages_to_delete'].append({
                'chat_id': msg_error.chat_id, 
                'message_id': msg_error.message_id
            })
        return ASK_BUY
    
    msg = await update.message.reply_text(f"شكراً. وهسه، بيش راح تبيع *'{product}'*؟", parse_mode="Markdown")
    context.user_data[user_id]['messages_to_delete'].append({
        'chat_id': msg.chat_id,
        'message_id': msg.message_id
    })
    
    pricing.setdefault(order_id, {}).setdefault(product, {})["buy"] = price
    context.application.create_task(save_data_in_background(context))
    
    return ASK_SELL


async def receive_sell_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    
    context.user_data.setdefault(user_id, {})
    if 'messages_to_delete' not in context.user_data[user_id]:
        context.user_data[user_id]['messages_to_delete'] = []
    context.user_data[user_id]['messages_to_delete'].append({'chat_id': update.message.chat_id, 'message_id': update.message.message_id})

    data = context.user_data.get(user_id)
    # ***** هنا أيضاً هو مكان التعديل اللي يحل المشكلة! *****
    # الرسالة تكون عامة إذا ما لكينا معلومات الطلب/المنتج من الـ user_data
    if not data or "order_id" not in data or "product" not in data:
        msg_error = await update.message.reply_text("عذراً، لم أتمكن من تحديد الطلبية أو المنتج لتسعيره. الرجاء اضغط على المنتج من القائمة أولاً لتحديد سعره، أو ابدأ طلبية جديدة.", parse_mode="Markdown")
        context.user_data[user_id]['messages_to_delete'].append({
            'chat_id': msg_error.chat_id, 
            'message_id': msg_error.message_id
        })
        return ConversationHandler.END
    
    order_id, product = data["order_id"], data["product"]
    
    # ***** التحقق من وجود الطلبية والمنتج وكونهما تابعين للمستخدم الحالي *****
    if order_id not in orders or str(orders[order_id].get("user_id")) != user_id or product not in orders[order_id].get("products", []):
        msg_error = await update.message.reply_text("عذراً، الطلبية أو المنتج لم يعد موجوداً أو ليس لك. الرجاء بدء طلبية جديدة.")
        context.user_data[user_id]['messages_to_delete'].append({
            'chat_id': msg_error.chat_id, 
            'message_id': msg_error.message_id
        })
        return ConversationHandler.END

    try:
        price = float(update.message.text.strip())
        if price < 0:
            msg_error = await update.message.reply_text("سعر البيع يجب أن يكون رقماً إيجابياً. بيش راح تبيع بالضبط؟")
            context.user_data[user_id]['messages_to_delete'].append({'chat_id': msg_error.chat_id, 'message_id': msg_error.message_id})
            return ASK_SELL 
    except ValueError:
        msg_error = await update.message.reply_text("الرجاء إدخال رقم صحيح لسعر البيع. بيش حتبيع؟")
        context.user_data[user_id]['messages_to_delete'].append({'chat_id': msg_error.chat_id, 'message_id': msg_error.message_id})
        return ASK_SELL 
    
    pricing.setdefault(order_id, {}).setdefault(product, {})["sell"] = price
    context.application.create_task(save_data_in_background(context))

    logger.info(f"Scheduling deletion of {len(context.user_data[user_id].get('messages_to_delete', []))} messages for user {user_id}.")
    for msg_info in context.user_data[user_id].get('messages_to_delete', []):
        context.application.create_task(delete_message_in_background(context, chat_id=msg_info['chat_id'], message_id=msg_info['message_id']))
    context.user_data[user_id]['messages_to_delete'].clear()

    msg_info_buttons = last_button_message.get(order_id)
    if msg_info_buttons and str(msg_info_buttons.get("chat_id")) == str(update.effective_chat.id):
        try:
            await context.bot.delete_message(chat_id=msg_info_buttons["chat_id"], message_id=msg_info_buttons["message_id"])
            logger.info(f"Successfully deleted previous button message {msg_info_buttons['message_id']} for order {order_id}.")
        except Exception as e:
            logger.warning(f"Could not delete previous button message {msg_info_buttons['message_id']} for order {order_id}: {e}. Attempting to edit.")
            try:
                await context.bot.edit_message_text(
                    chat_id=msg_info_buttons["chat_id"],
                    message_id=msg_info_buttons["message_id"],
                    text="." 
                )
                logger.info(f"Edited previous button message {msg_info_buttons['message_id']} to remove buttons.")
            except Exception as edit_e:
                logger.warning(f"Could not edit previous button message {msg_info_buttons['message_id']} for order {order_id}: {edit_e}. Skipping.")
        
        if order_id in last_button_message:
            del last_button_message[order_id]
            context.application.create_task(save_data_in_background(context))

    order = orders[order_id]
    all_priced = True
    for p in order["products"]:
        if p not in pricing.get(order_id, {}) or "buy" not in pricing[order_id].get(p, {}) or "sell" not in pricing[order_id].get(p, {}):
            all_priced = False
            break
            
    if all_priced:
        context.user_data[user_id]["current_active_order_id"] = order_id
        await request_places_count_standalone(update.effective_chat.id, context, user_id, order_id)
        return ConversationHandler.END
    else:
        confirmation_msg = f"تم حفظ السعر لـ *'{product}'*."
        logger.info(f"Price saved for '{product}' in order {order_id}. Showing updated buttons with confirmation.")
        await show_buttons(update.effective_chat.id, context, user_id, order_id, confirmation_message=confirmation_msg)
        return ConversationHandler.END


async def request_places_count_standalone(chat_id, context: ContextTypes.DEFAULT_TYPE, user_id: str, order_id: str):
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


async def handle_places_count_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global daily_profit
    
    places = None
    chat_id = update.effective_chat.id
    user_id = str(update.effective_user.id)
    
    context.user_data.setdefault(user_id, {})
    if 'messages_to_delete' not in context.user_data[user_id]:
        context.user_data[user_id]['messages_to_delete'] = []

    order_id_to_process = None 

    if update.callback_query:
        query = update.callback_query
        logger.info(f"Places count callback query received (standalone): {query.data}")
        await query.answer()
        
        try:
            parts = query.data.split('_')
            if len(parts) == 4 and parts[0] == "places" and parts[1] == "data":
                order_id_to_process = parts[2] 
                
                if order_id_to_process not in orders or str(orders[order_id_to_process].get("user_id")) != user_id:
                    logger.error(f"Order ID '{order_id_to_process}' from callback data not found or not for user {user_id} in global orders (standalone).")
                    await context.bot.send_message(chat_id=chat_id, text="عذراً، الطلبية اللي حاول تختار عدد محلاتها ما موجودة عندي أو ما تخصك. الرجاء بدء طلبية جديدة.")
                    if user_id in context.user_data and "current_active_order_id" in context.user_data[user_id]:
                        del context.user_data[user_id]["current_active_order_id"]
                    return 

                places = int(parts[3])
                if query.message:
                    context.application.create_task(delete_message_in_background(context, chat_id=query.message.chat_id, message_id=query.message.message_id))
            else:
                raise ValueError(f"Unexpected callback_data format for places count (standalone): {query.data}")
        except (ValueError, IndexError) as e:
            logger.error(f"Failed to parse places count from callback data (standalone) '{query.data}': {e}")
            await context.bot.send_message(chat_id=chat_id, text="عذراً، حدث خطأ في بيانات الزر. الرجاء المحاولة مرة أخرى.")
            return 

    elif update.message: 
        context.user_data[user_id]['messages_to_delete'].append({'chat_id': update.message.chat_id, 'message_id': update.message.message_id})
        
        order_id_to_process = context.user_data[user_id].get("current_active_order_id")

        if not order_id_to_process or order_id_to_process not in orders or str(orders[order_id_to_process].get("user_id")) != user_id:
             msg_error = await context.bot.send_message(chat_id=chat_id, text="عذراً، ماكو طلبية حالية منتظر عدد محلاتها أو الطلبية قديمة جداً. الرجاء استخدم الأزرار لتحديد عدد المحلات، أو بدء طلبية جديدة.", parse_mode="Markdown")
             context.user_data[user_id]['messages_to_delete'].append({'chat_id': msg_error.chat_id, 'message_id': msg_error.message_id})
             if user_id in context.user_data and "current_active_order_id" in context.user_data[user_id]:
                 del context.user_data[user_id]["current_active_order_id"]
             return

        try:
            places = int(update.message.text.strip())
            if places < 0:
                msg_error = await context.bot.send_message(chat_id=chat_id, text="عدد المحلات يجب أن يكون رقماً موجباً. الرجاء إدخال عدد المحلات بشكل صحيح.")
                context.user_data[user_id]['messages_to_delete'].append({'chat_id': msg_error.chat_id, 'message_id': msg_error.message_id})
                return 
        except ValueError:
            msg_error = await context.bot.send_message(chat_id=chat_id, text="الرجاء إدخال عدد صحيح لعدد المحلات.")
            context.user_data[user_id]['messages_to_delete'].append({'chat_id': msg_error.chat_id, 'message_id': msg_error.message_id})
            return 
    
    if places is None or order_id_to_process is None:
        logger.warning("No places count or order ID received or invalid input in handle_places_count_data.")
        await context.bot.send_message(chat_id=chat_id, text="عذراً، لم أتمكن من فهم عدد المحلات أو الطلبية. الرجاء إدخال رقم صحيح أو البدء بطلبية جديدة.")
        if user_id in context.user_data and "current_active_order_id" in context.user_data[user_id]:
            del context.user_data[user_id]["current_active_order_id"]
        return 

    orders[order_id_to_process]["places_count"] = places
    context.application.create_task(save_data_in_background(context))

    if user_id in context.user_data and 'messages_to_delete' in context.user_data[user_id]:
        for msg_info in context.user_data[user_id]['messages_to_delete']:
            context.application.create_task(delete_message_in_background(context, chat_id=msg_info['chat_id'], message_id=msg_info['message_id']))
        context.user_data[user_id]['messages_to_delete'].clear()
    
    await show_final_options(chat_id, context, user_id, order_id_to_process, message_prefix="تم تحديث عدد المحلات بنجاح.")
    
    if user_id in context.user_data and "current_active_order_id" in context.user_data[user_id]:
        del context.user_data[user_id]["current_active_order_id"]
        logger.info(f"Cleared current_active_order_id for user {user_id} after processing places count.")

    return


async def show_final_options(chat_id, context, user_id, order_id, message_prefix=None):
    global daily_profit
    
    if order_id not in orders:
        logger.warning(f"Attempted to show final options for non-existent order_id: {order_id}")
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

    logger.info(f"Daily profit before addition for order {order_id}: {daily_profit}")
    daily_profit += net_profit
    logger.info(f"Daily profit after adding {net_profit} for order {order_id}: {daily_profit}")
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
    customer_invoice_lines.append(f"\n*
