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
import re # استيراد مكتبة الـ regex

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
DELIVERY_PRICING_FILE = os.path.join(DATA_DIR, "delivery_pricing.json") # ملف جديد لأسعار التوصيل

# تهيئة المتغيرات العامة
orders = {}
pricing = {}
invoice_numbers = {}
daily_profit = 0.0
last_button_message = {}
delivery_pricing = {} # متغير جديد لأسعار التوصيل

# متغيرات الحفظ المؤجل
save_timer = None
save_pending = False
save_lock = threading.Lock()

# تحميل البيانات عند بدء تشغيل البوت
def load_data():
    global orders, pricing, invoice_numbers, daily_profit, last_button_message, delivery_pricing

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
            pricing[oid] = {str(pk): pv for pk, pv in pricing[oid].items()} # Ensure inner keys are strings too

    invoice_numbers_temp = load_json_file(INVOICE_NUMBERS_FILE, {}, "invoice_numbers")
    invoice_numbers.clear()
    invoice_numbers.update({str(k): v for k, v in invoice_numbers_temp.items()})

    daily_profit = load_json_file(DAILY_PROFIT_FILE, 0.0, "daily_profit")
    
    last_button_message_temp = load_json_file(LAST_BUTTON_MESSAGE_FILE, {}, "last_button_message")
    last_button_message.clear()
    last_button_message.update({str(k): v for k, v in last_button_message_temp.items()})

    delivery_pricing_temp = load_json_file(DELIVERY_PRICING_FILE, {}, "delivery_pricing")
    delivery_pricing.clear()
    delivery_pricing.update({str(k): v for k, v in delivery_pricing_temp.items()})

    logger.info(f"Initial load complete. Orders: {len(orders)}, Pricing entries: {len(pricing)}, Daily Profit: {daily_profit}, Delivery Pricing entries: {len(delivery_pricing)}")


# حفظ البيانات
def _save_data_to_disk():
    global save_pending
    with save_lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        try:
            # Save to temporary files first, then rename to prevent data corruption
            with open(ORDERS_FILE + ".tmp", "w") as f:
                json.dump(orders, f, indent=4) # Use indent for readability in files
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

            with open(DELIVERY_PRICING_FILE + ".tmp", "w") as f:
                json.dump(delivery_pricing, f, indent=4)
            os.replace(DELIVERY_PRICING_FILE + ".tmp", DELIVERY_PRICING_FILE)

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

# حالات المحادثة
ASK_BUY, ASK_SELL, ASK_PLACES_COUNT = range(3)
ASK_REGION_NAME, ASK_REGION_PRICE, REMOVE_REGION = range(3, 6) # حالات جديدة للمناطق

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

# دالة لتنظيف وتصحيح رقم الهاتف
def clean_phone_number(phone_number_str):
    # إزالة كل الأحرف غير الرقمية
    cleaned_number = re.sub(r'[^0-9]', '', phone_number_str)
    
    # إذا كان يبدأ بـ 964، استبدالها بصفر
    if cleaned_number.startswith('964'):
        cleaned_number = '0' + cleaned_number[3:]
    elif cleaned_number.startswith('+964'): # للتعامل مع +964
        cleaned_number = '0' + cleaned_number[4:] 
    
    return cleaned_number

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
        await asyncio.sleep(0.1) # زيادة التأخير لضمان ظهور الرسالة الجديدة
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
    logger.info(f"[{update.effective_chat.id}] /start command from user {user_id}. User data before clearing: {json.dumps(context.user_data.get(user_id, {}), indent=2)}")
    if user_id in context.user_data:
        context.user_data[user_id].pop("order_id", None)
        context.user_data[user_id].pop("product", None)
        context.user_data[user_id].pop("current_active_order_id", None)
        context.user_data[user_id].pop("messages_to_delete", None) 
        context.user_data[user_id].pop("buy_price", None) # Clear buy_price too
        logger.info(f"Cleared order-specific user_data for user {user_id} on /start command. User data after clearing: {json.dumps(context.user_data.get(user_id, {}), indent=2)}")
    
    await update.message.reply_text("أهلاً بك يا أبا الأكبر! لإعداد طلبية، دز الطلبية كلها برسالة واحدة.\n\n*السطر الأول:* عنوان الزبون.\n*السطر الثاني:* رقم الزبون.\n*الأسطر الباقية:* كل منتج بسطر واحد.", parse_mode="Markdown")
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
    
    # تأكد من وجود 3 أسطر على الأقل (عنوان، رقم هاتف، منتج واحد على الأقل)
    if len(lines) < 3:
        if not edited:
            await message.reply_text("الرجاء التأكد من كتابة عنوان الزبون في السطر الأول، رقم الزبون في السطر الثاني، والمنتجات في الأسطر التالية.")
        return

    title = lines[0] # السطر الأول هو العنوان
    customer_phone = clean_phone_number(lines[1]) # تصحيح رقم الزبون
    products = [p.strip() for p in lines[2:] if p.strip()] # المنتجات تبدأ من السطر الثالث

    if not products:
        if not edited:
            await message.reply_text("الرجاء إضافة منتجات بعد رقم الزبون.")
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
                    
    # تحديد سعر التوصيل للمنطقة
    delivery_cost_for_region = 0.0
    region_name = "غير محددة" # القيمة الافتراضية إذا لم يتم العثور على منطقة
    
    # تحويل العنوان إلى حروف صغيرة للبحث
    title_lower = title.lower()

    found_region = False
    for region, price in delivery_pricing.items():
        if region.lower() in title_lower: # البحث عن اسم المنطقة داخل العنوان
            delivery_cost_for_region = price
            region_name = region
            found_region = True
            break
    
    if not order_id: 
        order_id = str(uuid.uuid4())[:8]
        invoice_no = get_invoice_number()
        orders[order_id] = {
            "user_id": user_id, 
            "title": title, 
            "customer_phone": customer_phone, # حفظ رقم الزبون
            "products": products, 
            "places_count": 0, 
            "delivery_cost": delivery_cost_for_region, # حفظ سعر التوصيل الخاص بالمنطقة
            "region_name": region_name # حفظ اسم المنطقة المطابقة
        } 
        pricing[order_id] = {p: {} for p in products}
        invoice_numbers[order_id] = invoice_no
        logger.info(f"Created new order {order_id} for user {user_id}. Region: {region_name}, Delivery cost: {delivery_cost_for_region}, Phone: {customer_phone}.")
    else: 
        old_products = set(orders[order_id].get("products", []))
        new_products = set(products)
        
        orders[order_id]["title"] = title
        orders[order_id]["customer_phone"] = customer_phone # تحديث رقم الزبون
        orders[order_id]["products"] = products
        orders[order_id]["delivery_cost"] = delivery_cost_for_region # تحديث سعر التوصيل عند التعديل
        orders[order_id]["region_name"] = region_name

        for p in new_products:
            if p not in pricing.get(order_id, {}):
                pricing.setdefault(order_id, {})[p] = {}
        
        if order_id in pricing:
            for p in old_products - new_products:
                if p in pricing[order_id]:
                    del pricing[order_id][p]
                    logger.info(f"Removed pricing for product '{p}' from order {order_id}.")
        logger.info(f"Updated existing order {order_id}. Initiator: {user_id}. Region: {region_name}, Delivery cost: {delivery_cost_for_region}, Phone: {customer_phone}.")
        
    context.application.create_task(save_data_in_background(context))
    
    if is_new_order:
        await message.reply_text(f"استلمت الطلب بعنوان: *{title}* (عدد المنتجات: {len(products)})", parse_mode="Markdown")
        await show_buttons(message.chat_id, context, user_id, order_id)
    else:
        await show_buttons(message.chat_id, context, user_id, order_id, confirmation_message="تم تحديث الطلب. الرجاء التأكد من تسعير أي منتجات جديدة.")
        
async def show_buttons(chat_id, context, user_id, order_id, confirmation_message=None):
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
    try:
        query = update.callback_query
        await query.answer()

        logger.info(f"[{query.message.chat_id}] Callback query received: {query.data} from user {query.from_user.id}. User data: {json.dumps(context.user_data.get(str(query.from_user.id), {}), indent=2)}")

        user_id = str(query.from_user.id)
        
        try:
            order_id, product = query.data.split("|", 1) 
            product = product.strip() 
        except ValueError as e:
            logger.error(f"[{query.message.chat_id}] Failed to parse callback_data for product selection: {query.data}. Error: {e}", exc_info=True)
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
        logger.info(f"[{query.message.chat_id}] User {user_id} selected product '{product}' for order '{order_id}'. User data updated: {json.dumps(context.user_data.get(user_id), indent=2)}")
        
        if 'messages_to_delete' not in context.user_data[user_id]:
            context.user_data[user_id]['messages_to_delete'] = [] 

        if query.message:
            context.user_data[user_id]['messages_to_delete'].append({
                'chat_id': query.message.chat_id,
                'message_id': query.message.message_id
            })
            logger.info(f"[{query.message.chat_id}] Added button message {query.message.message_id} to delete queue for order {order_id}.")
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
        logger.info(f"[{query.message.chat_id}] Asking for buy price for '{product}'. Next state: ASK_BUY. Current user_data: {json.dumps(context.user_data.get(user_id), indent=2)}")
        
        return ASK_BUY
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in product_selected: {e}", exc_info=True)
        await update.callback_query.message.reply_text("عذراً، حدث خطأ أثناء اختيار المنتج. الرجاء بدء طلبية جديدة.")
        return ConversationHandler.END
    
async def receive_buy_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        except ValueError: 
            logger.error(f"[{update.effective_chat.id}] Buy price: ValueError for user {user_id} with input '{update.message.text}'", exc_info=True)
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

        try:
            sell_price = float(update.message.text.strip())
            if sell_price < 0:
                logger.warning(f"[{update.effective_chat.id}] Sell price: Negative price from user {user_id}: '{update.message.text}'")
                msg_error = await update.message.reply_text("سعر البيع يجب أن يكون رقماً إيجابياً. بيش راح تبيع بالضبط؟")
                context.user_data[user_id]['messages_to_delete'].append({'chat_id': msg_error.chat_id, 'message_id': msg_error.message_id})
                return ASK_SELL 
        except ValueError:
            logger.error(f"[{update.effective_chat.id}] Sell price: ValueError for user {user_id} with input '{update.message.text}'", exc_info=True)
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
                    # Always try to delete the message with the buttons regardless of success
                    if query.message:
                        context.application.create_task(delete_message_in_background(context, chat_id=query.message.chat_id, message_id=query.message.message_id))

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

            try:
                places = int(update.message.text.strip())
                if places < 0:
                    logger.warning(f"[{chat_id}] Places count text input: Negative value from user {user_id}: '{update.message.text}'")
                    msg_error = await context.bot.send_message(chat_id=chat_id, text="عدد المحلات يجب أن يكون رقماً موجباً. الرجاء إدخال عدد المحلات بشكل صحيح.")
                    context.user_data[user_id]['messages_to_delete'].append({'chat_id': msg_error.chat_id, 'message_id': msg_error.message_id})
                    return ASK_PLACES_COUNT 
            except ValueError: 
                logger.error(f"[{chat_id}] Places count text input: ValueError for user {user_id} with input '{update.message.text}'", exc_info=True)
                msg_error = await context.bot.send_message(chat_id=chat_id, text="الرجاء إدخال عدد صحيح لعدد المحلات.")
                context.user_data[user_id]['messages_to_delete'].append({'chat_id': msg_error.chat_id, 'message_id': msg_error.message_id})
                return ASK_PLACES_COUNT 
        
        if places is None or order_id_to_process is None:
            logger.warning(f"[{chat_id}] handle_places_count_data: No valid places count or order ID to process.")
            await context.bot.send_message(chat_id=chat_id, text="عذراً، لم أتمكن من فهم عدد المحلات أو الطلبية. الرجاء إدخال رقم صحيح أو البدء بطلبية جديدة.")
            if user_id in context.user_data and "current_active_order_id" in context.user_data[user_id]:
                del context.user_data[user_id]["current_active_order_id"]
            return ConversationHandler.END 

        # Delete the "places count message" if it exists
        if 'places_count_message' in context.user_data[user_id]:
            msg_info = context.user_data[user_id]['places_count_message']
            context.application.create_task(delete_message_in_background(context, chat_id=msg_info['chat_id'], message_id=msg_info['message_id']))
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
        
        delivery_cost_from_region = order.get("delivery_cost", 0.0) # سعر التوصيل من المنطقة
        region_name = order.get("region_name", "غير محددة")
        customer_phone = order.get("customer_phone", "غير متوفر") # جلب رقم الزبون

        final_total = total_sell + extra_cost + delivery_cost_from_region # إضافة سعر التوصيل الإجمالي
        
        # الأرباح لا تضاف إلا مرة واحدة عند اكتمال الطلب لأول مرة.
        # حاليا، إذا تم استدعاء هذه الدالة أكثر من مرة لنفس الطلب، سيتم إضافة الربح في كل مرة.
        # للتبسيط، لن نغير سلوك إضافة الربح حالياً، لكن يجب ملاحظة ذلك.
        # يمكن إضافة flag مثل 'profit_added' في الطلب لتجنب الإضافة المتكررة.
        
        # FIX FOR IndentationError: unexpected indent (line 843)
        # تم تصحيح المسافة الزائدة هنا
        if 'profit_added' not in order:
            daily_profit += net_profit + delivery_cost_from_region # إضافة ربح المنتجات + ربح المنطقة
            orders[order_id]['profit_added'] = True # لضمان عدم تكرار إضافة الربح
            context.application.create_task(save_data_in_background(context))
        
        logger.info(f"[{chat_id}] Daily profit after processing order {order_id}: {daily_profit}")
        context.application.create_task(save_data_in_background(context)) # حفظ التغييرات على الربح اليومي


        customer_invoice_lines = []
        customer_invoice_lines.append(f"**أبو الأكبر للتوصيل**")
        customer_invoice_lines.append(f"رقم الفاتورة: {invoice}")
        customer_invoice_lines.append(f"عنوان الزبون: {order['title']}")
        customer_invoice_lines.append(f"رقم الزبون: {customer_phone}") # إضافة رقم الزبون للفاتورة
        customer_invoice_lines.append(f"\n*المواد:*")

        running_total_for_customer = 0.0
        for p in order["products"]:
            if p in pricing.get(order_id, {}) and "sell" in pricing[order_id].get(p, {}):
                sell = pricing[order_id][p]["sell"]
                running_total_for_customer += sell
                customer_invoice_lines.append(f"• {p} - {format_float(sell)} = {format_float(running_total_for_customer)}")
            else:
                customer_invoice_lines.append(f"• {p} - (لم يتم تسعيره)")

        customer_invoice_lines.append(f"* كل المنتجات ({format_float(running_total_for_customer)})")
        customer_invoice_lines.append(f"• كلفة تجهيز من - {current_places} محلات {format_float(extra_cost)} = {format_float(running_total_for_customer + extra_cost)}")
        customer_invoice_lines.append(f"• توصيل ({region_name}) {format_float(delivery_cost_from_region)} = {format_float(running_total_for_customer + extra_cost + delivery_cost_from_region)}")

        customer_invoice_lines.append(f"\n*المجموع الكلي:* {format_float(final_total)} (مع احتساب التجهيز والتوصيل)")

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

        # تحسين ترميز الـ URL لضمان عدم وجود مشاكل في الأحرف الخاصة
        # استخدام urllib.parse.quote_plus لترميز السلسلة بالكامل
        import urllib.parse
        encoded_customer_invoice = urllib.parse.quote_plus(customer_final_text.replace('*', '')) # إزالة النجوم قبل الترميز
        
        keyboard = [
            [InlineKeyboardButton("1️⃣ تعديل الأسعار", callback_data=f"edit_prices_{order_id}")],
            [InlineKeyboardButton("3️⃣ إرسال فاتورة الزبون (واتساب)", url=f"https://wa.me/{OWNER_PHONE_NUMBER}?text={encoded_customer_invoice}")],
            [InlineKeyboardButton("4️⃣ إنشاء طلب جديد", callback_data="start_new_order")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        message_text = "افعل ما تريد من الأزرار:\n\n"
        if message_prefix:
            message_text = message_prefix + "\n" + message_text
        
        owner_invoice_details = []
        owner_invoice_details.append(f"رقم الفاتورة: {invoice}")
        owner_invoice_details.append(f"عنوان الزبون: {order['title']}")
        owner_invoice_details.append(f"رقم الزبون: {customer_phone}") # إضافة رقم الزبون لفاتورة الإدارة
        owner_invoice_details.append(f"اسم المنطقة: {region_name} | سعر التوصيل: {format_float(delivery_cost_from_region)}") # تفاصيل المنطقة
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
        owner_invoice_details.append(f"الربح الكلي للمنتجات: {format_float(net_profit)}")
        owner_invoice_details.append(f"كلفة تجهيز من: {current_places} محلات (+{format_float(extra_cost)})")
        owner_invoice_details.append(f"السعر الكلي للزبون: {format_float(final_total)}")
        owner_invoice_details.append(f"الربح الصافي النهائي (منتجات + توصيل): {format_float(net_profit + delivery_cost_from_region)}")
        
        final_owner_invoice_text = "\n".join(owner_invoice_details)
        
        # تحسين ترميز الـ URL لفاتورة الإدارة أيضاً
        encoded_owner_invoice = urllib.parse.quote_plus(final_owner_invoice_text.replace('*', '')) # إزالة النجوم قبل الترميز

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
            context.user_data[user_id].pop("buy_price", None) # Clear buy_price too
            logger.info(f"[{chat_id}] Cleaned up order-specific user_data for user {user_id} after showing final options. User data after clean: {json.dumps(context.user_data.get(user_id, {}), indent=2)}")
    except Exception as e:
        logger.error(f"[{chat_id}] Error in show_final_options: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text="عذراً، حدث خطأ أثناء عرض الفاتورة النهائية. الرجاء بدء طلبية جديدة.")


async def edit_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    try:
        query = update.callback_query
        await query.answer()
        
        user_id = str(query.from_user.id)
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

        await query.message.reply_text("تمام، دز الطلبية الجديدة كلها برسالة واحدة.\n\n*السطر الأول:* عنوان الزبون.\n*السطر الثاني:* رقم الزبون.\n*الأسطر الباقية:* كل منتج بسطر واحد.", parse_mode="Markdown")
        
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in start_new_order_callback: {e}", exc_info=True)
        await update.callback_query.message.reply_text("عذراً، حدث خطأ أثناء بدء طلب جديد. الرجاء المحاولة مرة أخرى.")
        return ConversationHandler.END


async def show_profit(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    try:
        query = update.callback_query
        await query.answer()

        if str(query.from_user.id) != str(OWNER_ID):
            await query.edit_message_text("عذراً، لا تملك صلاحية لتنفيذ هذا الأمر.")
            return

        if query.data == "confirm_reset":
            global daily_profit, orders, pricing, invoice_numbers, last_button_message, delivery_pricing
            logger.info(f"Daily profit before reset: {daily_profit}")
            daily_profit = 0.0
            orders.clear()
            pricing.clear()
            invoice_numbers.clear()
            last_button_message.clear()
            delivery_pricing.clear() # مسح بيانات المناطق أيضاً
            
            try:
                with open(COUNTER_FILE, "w") as f:
                    f.write("1")
            except Exception as e:
                logger.error(f"Could not reset invoice counter file: {e}", exc_info=True)

            _save_data_to_disk()
            logger.info(f"Daily profit after reset: {daily_profit}")
            await query.edit_message_text("تم تصفير الأرباح ومسح كل الطلبات وبيانات المناطق بنجاح.")
        elif query.data == "cancel_reset":
            await query.edit_message_text("تم إلغاء عملية التصفير.")
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in confirm_reset: {e}", exc_info=True)
        await update.callback_query.message.reply_text("عذراً، حدث خطأ أثناء عملية التصفير.")

async def show_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if str(update.message.from_user.id) != str(OWNER_ID):
            await update.message.reply_text("عذراً، هذا الأمر متاح للمالك فقط.")
            return
        
        total_orders = len(orders)
        total_products = 0
        total_buy_all_orders = 0.0 
        total_sell_all_orders = 0.0 
        total_delivery_cost_all_orders = 0.0 # مجموع كلفة التوصيل
        product_counter = Counter()
        details = []

        for order_id, order in orders.items():
            invoice = invoice_numbers.get(order_id, "غير معروف")
            details.append(f"\n**فاتورة رقم:** {invoice}")
            details.append(f"**عنوان الزبون:** {order['title']}")
            details.append(f"**رقم الزبون:** {order.get('customer_phone', 'غير متوفر')}") # إضافة رقم الزبون للتقرير
            details.append(f"**المنطقة:** {order.get('region_name', 'غير محددة')} | **كلفة التوصيل:** {format_float(order.get('delivery_cost', 0.0))}") # تفاصيل المنطقة
            total_delivery_cost_all_orders += order.get('delivery_cost', 0.0)
            
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
            details.append(f"  *ربح هذه الطلبية (منتجات فقط):* {format_float(order_sell - order_buy)}")
            details.append(f"  *ربح الطلبية الكلي (منتجات + توصيل):* {format_float((order_sell - order_buy) + order.get('delivery_cost', 0.0))}")

        summary = []
        summary.append(f"**تقرير عام عن جميع الطلبات:**")
        summary.append(f"عدد الطلبات الكلي: {total_orders}")
        summary.append(f"عدد المنتجات الكلي: {total_products}")
        summary.append(f"إجمالي كلفة الشراء (للمنتجات): {format_float(total_buy_all_orders)}")
        summary.append(f"إجمالي مبلغ البيع (للمنتجات): {format_float(total_sell_all_orders)}")
        summary.append(f"إجمالي ربح المنتجات الصافي: {format_float(total_sell_all_orders - total_buy_all_orders)}")
        summary.append(f"إجمالي كلفة التوصيل المحتسبة: {format_float(total_delivery_cost_all_orders)}")
        summary.append(f"الربح التراكمي الإجمالي (منتجات + توصيل): *{format_float(daily_profit)}*")
        
        summary.append("\n**المنتجات الأكثر طلباً:**")
        if product_counter:
            for product_name, count in product_counter.most_common(5): # أعلى 5 منتجات
                summary.append(f"- {product_name}: {count} مرة")
        else:
            summary.append("- لا توجد منتجات بعد.")

        full_report_text = "\n".join(summary + details)

        await update.message.reply_text(full_report_text, parse_mode="Markdown")
        logger.info(f"[{update.effective_chat.id}] Full report sent to owner.")
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in show_report: {e}", exc_info=True)
        await update.message.reply_text("عذراً، حدث خطأ أثناء عرض التقرير.")

async def add_region_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if str(update.message.from_user.id) != str(OWNER_ID):
            await update.message.reply_text("عذراً، هذا الأمر متاح للمالك فقط.")
            return
        
        await update.message.reply_text("تمام، دزلي اسم المنطقة اللي تريد تضيف سعر توصيل الها.")
        return ASK_REGION_NAME
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in add_region_price: {e}", exc_info=True)
        await update.message.reply_text("عذراً، حدث خطأ أثناء بدء إضافة منطقة جديدة.")
        return ConversationHandler.END

async def receive_region_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.message.from_user.id)
        if user_id != str(OWNER_ID):
            await update.message.reply_text("عذراً، لا تملك صلاحية لتنفيذ هذا الأمر.")
            return ConversationHandler.END

        region_name = update.message.text.strip()
        if not region_name:
            await update.message.reply_text("اسم المنطقة ما يصير فارغ. دزلي اسم المنطقة مرة ثانية.")
            return ASK_REGION_NAME
        
        context.user_data[user_id]["current_region_name"] = region_name
        
        await update.message.reply_text(f"تمام، '*{region_name}*'. هسه دزلي سعر التوصيل لهالمنطقة (رقم فقط).", parse_mode="Markdown")
        return ASK_REGION_PRICE
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in receive_region_name: {e}", exc_info=True)
        await update.message.reply_text("عذراً، حدث خطأ أثناء استلام اسم المنطقة.")
        return ConversationHandler.END

async def receive_region_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.message.from_user.id)
        if user_id != str(OWNER_ID):
            await update.message.reply_text("عذراً، لا تملك صلاحية لتنفيذ هذا الأمر.")
            return ConversationHandler.END

        region_name = context.user_data[user_id].get("current_region_name")
        if not region_name:
            await update.message.reply_text("يبدو أن اسم المنطقة مفقود. الرجاء بدء عملية إضافة المنطقة من جديد باستخدام /add_region_price.")
            return ConversationHandler.END

        try:
            price = float(update.message.text.strip())
            if price < 0:
                await update.message.reply_text("سعر التوصيل يجب أن يكون رقماً موجباً. الرجاء إدخال سعر صحيح.")
                return ASK_REGION_PRICE
        except ValueError:
            await update.message.reply_text("الرجاء إدخال رقم صحيح لسعر التوصيل. دزلي السعر مرة ثانية.")
            return ASK_REGION_PRICE
        
        delivery_pricing[region_name] = price
        context.application.create_task(save_data_in_background(context))
        
        await update.message.reply_text(f"تم حفظ سعر التوصيل لـ '*{region_name}*' بمبلغ *{format_float(price)}* دينار بنجاح.", parse_mode="Markdown")
        context.user_data[user_id].pop("current_region_name", None)
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in receive_region_price: {e}", exc_info=True)
        await update.message.reply_text("عذراً، حدث خطأ أثناء استلام سعر المنطقة.")
        return ConversationHandler.END

async def list_regions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if str(update.message.from_user.id) != str(OWNER_ID):
            await update.message.reply_text("عذراً، هذا الأمر متاح للمالك فقط.")
            return
        
        if not delivery_pricing:
            await update.message.reply_text("ماكو أي مناطق مسجلة حالياً.")
            return
        
        message_text = "**أسعار التوصيل للمناطق المسجلة:**\n"
        for region, price in delivery_pricing.items():
            message_text += f"- *{region}*: {format_float(price)} دينار\n"
        
        await update.message.reply_text(message_text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in list_regions: {e}", exc_info=True)
        await update.message.reply_text("عذراً، حدث خطأ أثناء عرض قائمة المناطق.")

async def remove_region_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if str(update.message.from_user.id) != str(OWNER_ID):
            await update.message.reply_text("عذراً، هذا الأمر متاح للمالك فقط.")
            return
        
        if not delivery_pricing:
            await update.message.reply_text("ماكو أي مناطق مسجلة حتى تحذفها.")
            return ConversationHandler.END
        
        keyboard = []
        for region_name in delivery_pricing.keys():
            keyboard.append([InlineKeyboardButton(region_name, callback_data=f"remove_region_{region_name}")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("اختار المنطقة اللي تريد تحذفها من القائمة:", reply_markup=reply_markup)
        return REMOVE_REGION
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in remove_region_start: {e}", exc_info=True)
        await update.message.reply_text("عذراً، حدث خطأ أثناء بدء عملية حذف المنطقة.")
        return ConversationHandler.END

async def remove_region_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()

        if str(query.from_user.id) != str(OWNER_ID):
            await query.edit_message_text("عذراً، لا تملك صلاحية لتنفيذ هذا الأمر.")
            return ConversationHandler.END

        if query.data.startswith("remove_region_"):
            region_to_remove = query.data.replace("remove_region_", "")
            
            if region_to_remove in delivery_pricing:
                del delivery_pricing[region_to_remove]
                context.application.create_task(save_data_in_background(context))
                await query.edit_message_text(f"تم حذف المنطقة '*{region_to_remove}*' وأسعار توصيلها بنجاح.", parse_mode="Markdown")
                logger.info(f"Region '{region_to_remove}' removed by owner.")
            else:
                await query.edit_message_text(f"المنطقة '*{region_to_remove}*' ما موجودة أصلاً.", parse_mode="Markdown")
                logger.warning(f"Attempted to remove non-existent region '{region_to_remove}'.")
        else:
            await query.edit_message_text("عذراً، حدث خطأ في بيانات الزر.")
        
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in remove_region_confirm: {e}", exc_info=True)
        await update.callback_query.message.reply_text("عذراً، حدث خطأ أثناء حذف المنطقة.")
        return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the conversation."""
    user_id = str(update.message.from_user.id)
    logger.info(f"[{update.effective_chat.id}] User {user_id} canceled the conversation.")
    
    if user_id in context.user_data:
        context.user_data[user_id].pop("order_id", None)
        context.user_data[user_id].pop("product", None)
        context.user_data[user_id].pop("current_active_order_id", None)
        context.user_data[user_id].pop("messages_to_delete", None)
        context.user_data[user_id].pop("buy_price", None)
        context.user_data[user_id].pop("current_region_name", None) # Clean up region related data
        logger.info(f"[{update.effective_chat.id}] Cleared user_data for user {user_id} on cancel. User data after clean: {json.dumps(context.user_data.get(user_id, {}), indent=2)}")

    if update.message:
        await update.message.reply_text('تم إلغاء العملية.')
    elif update.callback_query:
        await update.callback_query.message.reply_text('تم إلغاء العملية.')
    return ConversationHandler.END


def main():
    application = ApplicationBuilder().token(TOKEN).build()

    # Conversation for receiving orders and pricing products
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_order),
            CallbackQueryHandler(start_new_order_callback, pattern=r"^start_new_order$")
        ],
        states={
            ASK_BUY: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_buy_price)],
            ASK_SELL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_sell_price)],
            # No explicit state for ASK_PLACES_COUNT in ConversationHandler anymore, handled by handle_places_count_data
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        map_to_parent={
            ConversationHandler.END: ConversationHandler.END
        }
    )

    # Conversation for adding region prices
    add_region_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("add_region_price", add_region_price)],
        states={
            ASK_REGION_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_region_name)],
            ASK_REGION_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_region_price)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # Conversation for removing region prices
    remove_region_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("remove_region", remove_region_start)],
        states={
            REMOVE_REGION: [CallbackQueryHandler(remove_region_confirm, pattern=r"^remove_region_")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(conv_handler)
    application.add_handler(add_region_conv_handler)
    application.add_handler(remove_region_conv_handler)

    # Handlers for general commands
    application.add_handler(CommandHandler("profit", show_profit))
    application.add_handler(CommandHandler("reset_all", reset_all))
    application.add_handler(CallbackQueryHandler(confirm_reset, pattern=r"^(confirm_reset|cancel_reset)$"))
    application.add_handler(CommandHandler("report", show_report))
    application.add_handler(CommandHandler("list_regions", list_regions))

    # Handlers for specific callback queries not part of a conversation flow
    application.add_handler(CallbackQueryHandler(product_selected, pattern=r"^[0-9a-fA-F]{8}\|.*$"))
    application.add_handler(CallbackQueryHandler(handle_places_count_data, pattern=r"^places_data_.*$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_places_count_data, block=False)) # For manual input of places count
    application.add_handler(CallbackQueryHandler(edit_prices, pattern=r"^edit_prices_.*$"))

    # Handler for edited messages (to re-process orders)
    application.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE & filters.TEXT, edited_message))
    
    logger.info("Bot started polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
