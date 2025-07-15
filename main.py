import os
import json
import uuid
import time
import asyncio
import logging
import threading
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import quote

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler,
    MessageHandler, CallbackQueryHandler, ConversationHandler, filters
)

from features.delivery_zones import (
    list_zones, get_delivery_price
)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

DATA_DIR = "/mnt/data/"

ORDERS_FILE = os.path.join(DATA_DIR, "orders.json")
PRICING_FILE = os.path.join(DATA_DIR, "pricing.json")
INVOICE_NUMBERS_FILE = os.path.join(DATA_DIR, "invoice_numbers.json")
DAILY_PROFIT_FILE = os.path.join(DATA_DIR, "daily_profit.json")
COUNTER_FILE = os.path.join(DATA_DIR, "invoice_counter.txt")
LAST_BUTTON_MESSAGE_FILE = os.path.join(DATA_DIR, "last_button_message.json")
SUPPLIER_REPORT_TIMESTAMPS_FILE = os.path.join(DATA_DIR, "supplier_report_timestamps.json")


TOKEN = os.getenv("TOKEN")

orders = {}
pricing = {}
invoice_numbers = {}
daily_profit = 0.0
last_button_message = {}
supplier_report_timestamps = {}

save_lock = threading.Lock()
save_timer = None
save_pending = False

def load_json_file(filepath, default_value, var_name):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    if os.path.exists(filepath):
        with open(filepath, "r", encoding='utf-8') as f:
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

def _save_data_to_disk_global():
    global orders, pricing, invoice_numbers, daily_profit, last_button_message, supplier_report_timestamps
    with save_lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        try:
            with open(ORDERS_FILE + ".tmp", "w", encoding='utf-8') as f:
                json.dump(orders, f, indent=4, ensure_ascii=False)
            os.replace(ORDERS_FILE + ".tmp", ORDERS_FILE)

            with open(PRICING_FILE + ".tmp", "w", encoding='utf-8') as f:
                json.dump(pricing, f, indent=4, ensure_ascii=False)
            os.replace(PRICING_FILE + ".tmp", PRICING_FILE)

            with open(INVOICE_NUMBERS_FILE + ".tmp", "w", encoding='utf-8') as f:
                json.dump(invoice_numbers, f, indent=4, ensure_ascii=False)
            os.replace(INVOICE_NUMBERS_FILE + ".tmp", INVOICE_NUMBERS_FILE)

            with open(DAILY_PROFIT_FILE + ".tmp", "w", encoding='utf-8') as f:
                json.dump(daily_profit, f, indent=4, ensure_ascii=False)
            os.replace(DAILY_PROFIT_FILE + ".tmp", DAILY_PROFIT_FILE)

            with open(LAST_BUTTON_MESSAGE_FILE + ".tmp", "w", encoding='utf-8') as f:
                json.dump(last_button_message, f, indent=4, ensure_ascii=False)
            os.replace(LAST_BUTTON_MESSAGE_FILE + ".tmp", LAST_BUTTON_MESSAGE_FILE)

            with open(SUPPLIER_REPORT_TIMESTAMPS_FILE + ".tmp", "w", encoding='utf-8') as f:
                json.dump(supplier_report_timestamps, f, indent=4, ensure_ascii=False)
            os.replace(SUPPLIER_REPORT_TIMESTAMPS_FILE + ".tmp", SUPPLIER_REPORT_TIMESTAMPS_FILE)

            logger.info("All data (global) saved to disk successfully.")
        except Exception as e:
            logger.error(f"Error saving global data to disk: {e}")

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

def load_data():
    global orders, pricing, invoice_numbers, daily_profit, last_button_message, supplier_report_timestamps

    os.makedirs(DATA_DIR, exist_ok=True)

    orders_temp = load_json_file(ORDERS_FILE, {}, "orders")
    orders.clear()
    orders.update({str(k): v for k, v in orders_temp.items()})

    pricing_temp = load_json_file(PRICING_FILE, {}, "pricing")
    pricing.clear()
    pricing.update({str(pk): pv for pk, pv in pricing_temp.items()})
    for oid in pricing:
        if isinstance(pricing[oid], dict):
            pricing[oid] = {str(pk): pv for pk, pv in pricing[oid].items()}

    invoice_numbers_temp = load_json_file(INVOICE_NUMBERS_FILE, {}, "invoice_numbers")
    invoice_numbers.clear()
    invoice_numbers.update({str(k): v for k, v in invoice_numbers_temp.items()})

    daily_profit = load_json_file(DAILY_PROFIT_FILE, 0.0, "daily_profit")
    
    last_button_message_temp = load_json_file(LAST_BUTTON_MESSAGE_FILE, {}, "last_button_message")
    last_button_message.clear()
    last_button_message.update({str(k): v for k, v in last_button_message_temp.items()})

    supplier_report_timestamps_temp = load_json_file(SUPPLIER_REPORT_TIMESTAMPS_FILE, {}, "supplier_report_timestamps")
    supplier_report_timestamps.clear()
    supplier_report_timestamps.update({str(k): v for k, v in supplier_report_timestamps_temp.items()})

    logger.info(f"Initial load complete. Orders: {len(orders)}, Pricing entries: {len(pricing)}, Daily Profit: {daily_profit}")

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

load_data()

ASK_BUY, ASK_PLACES_COUNT, ASK_PRODUCT_NAME, ASK_PRODUCT_TO_DELETE, ASK_CUSTOMER_PHONE_NUMBER_FOR_DELETION, ASK_FOR_DELETION_CONFIRMATION = range(6)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_TELEGRAM_ID")) 
OWNER_PHONE_NUMBER = os.getenv("OWNER_TELEGRAM_PHONE_NUMBER", "+9647733921468")

if TOKEN is None:
    raise ValueError("TELEGRAM_BOT_TOKEN environment variable not set.")
if OWNER_ID is None:
    raise ValueError("OWNER_TELEGRAM_ID environment variable not set.")

def format_float(value):
    formatted = f"{value:g}"
    if formatted.endswith(".0"):
        return formatted[:-2]
    return formatted

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

async def delete_message_in_background(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    try:
        await asyncio.sleep(0.1)
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.info(f"Successfully deleted message {message_id} from chat {chat_id} in background.")
    except Exception as e:
        logger.warning(f"Could not delete message {message_id} from chat {chat_id} in background: {e}.")

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
    
    if len(lines) < 3:
        if not edited:
            await message.reply_text("باعلي تاكد انك تكتب الطلبية ك التالي اول سطر هو عنوان الزبون وثاني سطر هو رقم الزبون وراها المنتجات كل سطر بي منتج يالله فر ويلك وسوي الطلب.")
        return

    title = lines[0]
    
    phone_number_raw = lines[1].strip().replace(" ", "")
    if phone_number_raw.startswith("+964"):
        phone_number = "0" + phone_number_raw[4:]
    else:
        phone_number = phone_number_raw.replace("+", "")
    
    products = [p.strip() for p in lines[2:] if p.strip()]

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
        orders[order_id] = {
            "user_id": user_id, 
            "title": title, 
            "phone_number": phone_number, 
            "products": products, 
            "places_count": 0,
            "created_at": datetime.now(timezone.utc).isoformat()
        } 
        pricing[order_id] = {p: {} for p in products}
        invoice_numbers[order_id] = invoice_no
        logger.info(f"Created new order {order_id} for user {user_id}.")
    else: 
        old_products = set(orders[order_id].get("products", []))
        new_products = set(products)
        
        orders[order_id]["title"] = title
        orders[order_id]["phone_number"] = phone_number
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
        await message.reply_text(f"طلب : *{title}*\n(الرقم: `{phone_number}` )\n(عدد المنتجات: {len(products)})", parse_mode="Markdown")
        await show_buttons(message.chat_id, context, user_id, order_id)
    else:
        await show_buttons(message.chat_id, context, user_id, order_id, confirmation_message="دهاك حدثنه الطلب. عيني دخل الاسعار الاستاذ حدث الطلب.")
        
async def show_buttons(chat_id, context, user_id, order_id, confirmation_message=None):
    orders = context.application.bot_data['orders']
    pricing = context.application.bot_data['pricing']
    last_button_message = context.application.bot_data['last_button_message']

    try:
        logger.info(f"[{chat_id}] show_buttons called for order {order_id}. User: {user_id}.")
        logger.info(f"[{chat_id}] Current pricing data for order {order_id} in show_buttons: {json.dumps(pricing.get(order_id), indent=2)}")

        if order_id not in orders:
            logger.warning(f"[{chat_id}] Attempted to show buttons for non-existent order_id: {order_id}")
            await context.bot.send_message(chat_id=chat_id, text="ترا الطلب مموجود تري سوي طلب جديد.")
            if user_id in context.user_data:
                context.user_data[user_id].pop("order_id", None)
                context.user_data[user_id].pop("product", None)
                context.user_data[user_id].pop("current_active_order_id", None)
                context.user_data[user_id].pop("messages_to_delete", None)
            return

        order = orders[order_id]

        final_buttons_list = []

        final_buttons_list.append([
            InlineKeyboardButton("➕ إضافة منتج جديد", callback_data=f"add_product_to_order_{order_id}"),
            InlineKeyboardButton("🗑️ مسح منتج", callback_data=f"delete_specific_product_{order_id}")
        ])

        completed_products_buttons = []
        pending_products_buttons = []

        for p_name in order["products"]:
            if p_name in pricing.get(order_id, {}) and 'buy' in pricing[order_id].get(p_name, {}) and 'sell' in pricing[order_id].get(p_name, {}):
                completed_products_buttons.append([InlineKeyboardButton(f"✅ {p_name}", callback_data=f"{order_id}|{p_name}")])
                logger.info(f"[{chat_id}] Product '{p_name}' in order {order_id} is completed.")
            else:
                pending_products_buttons.append([InlineKeyboardButton(p_name, callback_data=f"{order_id}|{p_name}")])
                logger.info(f"[{chat_id}] Product '{p_name}' in order {order_id} is pending. Pricing state for this product: {json.dumps(pricing.get(order_id, {}).get(p_name, {}), indent=2)}")
        
        final_buttons_list.extend(completed_products_buttons)
        final_buttons_list.extend(pending_products_buttons)
        
        markup = InlineKeyboardMarkup(final_buttons_list)

        message_text = ""
        if confirmation_message:
            message_text += f"{confirmation_message}\n\n"
        message_text += f"دوس على منتج واكتب سعره *{order['title']}*:"

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
        await context.bot.send_message(chat_id=chat_id, text="ماكدرت اعرض الازرار تريد عدل الطلب .")
        
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
            msg_error = await query.edit_message_text("زربت الطلبية مموجوده دديالله سوي طلب جديد.")
            context.user_data[user_id]['messages_to_delete'].append({
                'chat_id': msg_error.chat_id,
                'message_id': msg_error.message_id
            })
            return ConversationHandler.END

        context.user_data[user_id]["order_id"] = order_id
        context.user_data[user_id]["product"] = product

        context.user_data[user_id].pop("buy_price", None) # ما نحتاج نمسح buy_price بعد، لأنها راح تنحفظ بنفس المرة

        logger.info(f"[{query.message.chat_id}] Product '{product}' selected for order '{order_id}'. User data after product selection: {json.dumps(context.user_data.get(user_id), indent=2)}")

        current_buy = pricing.get(order_id, {}).get(product, {}).get("buy")
        current_sell = pricing.get(order_id, {}).get(product, {}).get("sell")

        message_prompt = ""
        if current_buy is not None and current_sell is not None:
            message_prompt = f"سعر *'{product}'* حالياً هو شراء: {format_float(current_buy)}، بيع: {format_float(current_sell)}.\n" \
                            f"باعلي سعر الشراء الجديد بالسطر الأول، وسعر البيع بالسطر الثاني؟ (أو دز نفس الأسعار إذا ماكو تغيير)"
        else:
            message_prompt = f"تمام، بيش اشتريت *'{product}'*؟ (بالسطر الأول)\n" \
                             f"وبييش راح تبيعه؟ (بالسطر الثاني)"

        msg = await query.message.reply_text(message_prompt, parse_mode="Markdown")
        context.user_data[user_id]['messages_to_delete'].append({
            'chat_id': msg.chat_id, 
            'message_id': msg.message_id
        })
        return ASK_BUY # راح نستخدم ASK_BUY لجمع السعرين

    except Exception as e: 
        logger.error(f"[{update.effective_chat.id}] Error in product_selected: {e}", exc_info=True)
        await update.callback_query.message.reply_text("ههه صار خطا باختيار المنتج. دياللة سوي طلب جديد.")
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

async def delete_product_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = str(query.from_user.id)
    chat_id = query.message.chat_id

    order_id = query.data.replace("delete_specific_product_", "") 

    logger.info(f"[{chat_id}] General delete product button clicked for order {order_id} by user {user_id}.")

    if order_id not in orders:
        logger.warning(f"[{chat_id}] No active order found or order_id invalid for user {user_id} when trying to display delete products.")
        await context.bot.send_message(chat_id=chat_id, text="ترا ماكو طلب فعال حتى أظهرلك منتجات للمسح. سوي طلب جديد أول.")
        return ConversationHandler.END

    order = orders[order_id]

    if not order["products"]: # إذا الطلبية ما بيها منتجات أصلاً
        await context.bot.send_message(chat_id=chat_id, text="ترا الطلبية ما بيها أي منتجات حتى تمسح منها.")
        return ConversationHandler.END

    products_to_delete_buttons = []
    for p_name in order["products"]:
        products_to_delete_buttons.append([InlineKeyboardButton(p_name, callback_data=f"confirm_delete_product_{order_id}_{p_name}")])

    # ✅ إضافة زر الإلغاء هنا
    products_to_delete_buttons.append([InlineKeyboardButton("❌ إلغاء المسح", callback_data=f"cancel_delete_product_{order_id}")])

    markup = InlineKeyboardMarkup(products_to_delete_buttons)

    # حذف رسالة الأزرار القديمة (إذا كانت موجودة)
    if query.message:
        context.application.create_task(delete_message_in_background(context, chat_id=query.message.chat_id, message_id=query.message.message_id))

    await context.bot.send_message(chat_id=chat_id, text="تمام، دوس على المنتج اللي تريد تمسحه من الطلبية:", reply_markup=markup)
    return ConversationHandler.END
    
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
    orders = context.application.bot_data['orders']
    pricing = context.application.bot_data['pricing']

    try:
        user_id = str(update.message.from_user.id)
        logger.info(f"[{update.effective_chat.id}] Received message for buy/sell prices from user {user_id}: '{update.message.text}'. User data at start of receive_buy_price: {json.dumps(context.user_data.get(user_id), indent=2)}")

        context.user_data.setdefault(user_id, {})
        if 'messages_to_delete' not in context.user_data[user_id]:
            context.user_data[user_id]['messages_to_delete'] = []

        context.user_data[user_id]['messages_to_delete'].append({
            'chat_id': update.message.chat_id,
            'message_id': update.message.message_id
        })

        data = context.user_data.get(user_id)
        if not data or "order_id" not in data or "product" not in data:
            logger.error(f"[{update.effective_chat.id}] Buy/Sell prices: Missing order_id or product in user_data for user {user_id}. User data: {json.dumps(data, indent=2)}")
            msg_error = await update.message.reply_text("عذراً، لم أتمكن من تحديد الطلبية أو المنتج. الرجاء اضغط على المنتج من القائمة أو ابدأ طلبية جديدة.", parse_mode="Markdown")
            context.user_data[user_id]['messages_to_delete'].append({
                'chat_id': msg_error.chat_id, 
                'message_id': msg_error.message_id
            })
            return ConversationHandler.END

        order_id = data["order_id"]
        product = data["product"]

        if order_id not in orders or product not in orders[order_id].get("products", []):
            logger.warning(f"[{update.effective_chat.id}] Buy/Sell prices: Order ID '{order_id}' not found or Product '{product}' not in products for order '{order_id}'.")
            msg_error = await update.message.reply_text("عذراً، الطلبية أو المنتج لم يعد موجوداً. الرجاء بدء طلبية جديدة أو التحقق من المنتجات.")
            context.user_data[user_id]['messages_to_delete'].append({
                'chat_id': msg_error.chat_id, 
                'message_id': msg_error.message_id
            })
            return ConversationHandler.END

        lines = [line.strip() for line in update.message.text.strip().split('\n') if line.strip()]
        if len(lines) != 2:
            logger.warning(f"[{update.effective_chat.id}] Buy/Sell prices: Invalid number of lines from user {user_id}: '{update.message.text}'")
            msg_error = await update.message.reply_text("الرجاء إدخال *سعر الشراء في السطر الأول* و *سعر البيع في السطر الثاني*.", parse_mode="Markdown")
            context.user_data[user_id]['messages_to_delete'].append({
                'chat_id': msg_error.chat_id, 
                'message_id': msg_error.message_id
            })
            return ASK_BUY

        buy_price_str = lines[0]
        sell_price_str = lines[1]

        try:
            buy_price = float(buy_price_str)
            sell_price = float(sell_price_str)
            if buy_price < 0 or sell_price < 0:
                logger.warning(f"[{update.effective_chat.id}] Buy/Sell prices: Negative price from user {user_id}: '{update.message.text}'")
                msg_error = await update.message.reply_text("الأسعار يجب أن تكون أرقاماً موجبة. الرجاء إدخال الأرقام بشكل صحيح.")
                context.user_data[user_id]['messages_to_delete'].append({
                    'chat_id': msg_error.chat_id, 
                    'message_id': msg_error.message_id
                })
                return ASK_BUY
        except ValueError as e: 
            logger.error(f"[{update.effective_chat.id}] Buy/Sell prices: ValueError for user {user_id} with input '{update.message.text}': {e}", exc_info=True)
            msg_error = await update.message.reply_text("الرجاء إدخال أرقام صحيحة لسعر الشراء وسعر البيع.", parse_mode="Markdown")
            context.user_data[user_id]['messages_to_delete'].append({
                'chat_id': msg_error.chat_id, 
                'message_id': msg_error.message_id
            })
            return ASK_BUY

        pricing.setdefault(order_id, {}).setdefault(product, {})["buy"] = buy_price
        pricing[order_id][product]["sell"] = sell_price
        orders[order_id]["supplier_id"] = user_id # تسجيل المجهز بالطلبية

        logger.info(f"[{update.effective_chat.id}] Pricing for order '{order_id}' and product '{product}' AFTER SAVE: {json.dumps(pricing.get(order_id, {}).get(product), indent=2)}")
        context.application.create_task(save_data_in_background(context)) 
        logger.info(f"[{update.effective_chat.id}] Buy/Sell prices for '{product}' in order '{order_id}' saved. Current user_data: {json.dumps(context.user_data.get(user_id), indent=2)}. Updated pricing for order {order_id}: {json.dumps(pricing.get(order_id), indent=2)}")

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
            logger.info(f"[{update.effective_chat.id}] Prices saved for '{product}' in order {order_id}. Showing updated buttons with confirmation. User {user_id} can select next product. Staying in conversation.")
            await show_buttons(update.effective_chat.id, context, user_id, order_id, confirmation_message=confirmation_msg)
            return ConversationHandler.END 
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in receive_buy_price (handling both prices): {e}", exc_info=True)
        await update.message.reply_text("عذراً، حدث خطأ أثناء إدخال الأسعار. الرجاء بدء طلبية جديدة.")
        return ConversationHandler.END

async def receive_new_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    chat_id = update.effective_chat.id
    new_product_name = update.message.text.strip()

    logger.info(f"[{chat_id}] Received new product name '{new_product_name}' from user {user_id}.")

    order_id = context.user_data[user_id].get("current_active_order_id")

    if not order_id or order_id not in orders:
        logger.warning(f"[{chat_id}] No active order found or order_id invalid for user {user_id} when adding new product.")
        await update.message.reply_text("ترا ماكو طلب فعال حتى أضيفله منتج. الرجاء بدء طلب جديد أولاً.")
        context.user_data[user_id].pop("adding_new_product", None)
        return ConversationHandler.END

    order = orders[order_id]

    if new_product_name in order["products"]:
        await update.message.reply_text(f"ترا المنتج '{new_product_name}' موجود بالطلبية أصلاً. اختار منتج ثاني أو كمل تسعير الموجودات.")
    else:
        order["products"].append(new_product_name)
        logger.info(f"[{chat_id}] Added new product '{new_product_name}' to order {order_id}.")
        await update.message.reply_text(f"تمت إضافة المنتج '{new_product_name}' للطلبية بنجاح.")
        context.application.create_task(save_data_in_background(context)) # حفظ البيانات بعد إضافة المنتج

    context.user_data[user_id].pop("adding_new_product", None) # إزالة العلامة
    context.user_data[user_id].pop("current_active_order_id", None) # إزالة الـ order_id بعد الانتهاء

    await show_buttons(chat_id, context, user_id, order_id) # عرض الأزرار المحدثة
    return ConversationHandler.END


async def receive_product_to_delete_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    chat_id = update.effective_chat.id
    product_name_to_delete = update.message.text.strip()

    logger.info(f"[{chat_id}] Received product name '{product_name_to_delete}' for deletion from user {user_id}.")

    order_id = context.user_data[user_id].get("current_active_order_id")

    if not order_id or order_id not in orders:
        logger.warning(f"[{chat_id}] No active order found or order_id invalid for user {user_id} when deleting product.")
        await update.message.reply_text("ترا ماكو طلب فعال حتى أمسح منه منتج. الرجاء بدء طلب جديد أولاً.")
        context.user_data[user_id].pop("deleting_product", None)
        return ConversationHandler.END

    order = orders[order_id]

    if product_name_to_delete in order["products"]:
        order["products"].remove(product_name_to_delete) # حذف المنتج من قائمة المنتجات بالطلبية
        
        # حذف سعر المنتج من الـ pricing (إذا كان موجود)
        if order_id in pricing and product_name_to_delete in pricing[order_id]:
            del pricing[order_id][product_name_to_delete]
            logger.info(f"[{chat_id}] Deleted pricing for product '{product_name_to_delete}' from order {order_id}.")
        
        logger.info(f"[{chat_id}] Product '{product_name_to_delete}' deleted from order {order_id}.")
        await update.message.reply_text(f"تم حذف المنتج '{product_name_to_delete}' من الطلبية بنجاح.")
        context.application.create_task(save_data_in_background(context)) # حفظ البيانات بعد حذف المنتج
    else:
        await update.message.reply_text(f"ترا المنتج '{product_name_to_delete}' مو موجود بالطلبية أصلاً. تأكد من الاسم.")

    context.user_data[user_id].pop("deleting_product", None) # إزالة العلامة
    context.user_data[user_id].pop("current_active_order_id", None) # إزالة الـ order_id بعد الانتهاء

    await show_buttons(chat_id, context, user_id, order_id) # عرض الأزرار المحدثة
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
            text="صلوات كللوش كل المنتجات تسعرت ديالله اختار عدد المحلات وفضني؟ (باوع ممنوع تكتب رقم لازم تختار من ذني الارقام )", 
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
        return ConversationHandler.END
        
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
                        await context.bot.send_message(chat_id=chat_id, text="عذراً، الطلبية التي تحاول تحديد عدد محلاتها غير موجودة. الرجاء بدء طلبية جديدة.")
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
                msg_error = await context.bot.send_message(chat_id=chat_id, text="الرجاء إدخال رقماً صحيحاً.")
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
                msg_error = await context.bot.send_message(chat_id=chat_id, text="الرجاء إدخال رقماً صحيحاً.")
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
        
    except Exception as e:
        logger.error(f"[{chat_id}] Error in handle_places_count_data: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text="عذراً، حدث خطأ أثناء معالجة عدد المحلات. الرجاء بدء طلبية جديدة.", parse_mode="Markdown")
        return ConversationHandler.END

from urllib.parse import quote

async def show_final_options(chat_id, context, user_id, order_id, message_prefix=None):
    orders = context.application.bot_data['orders']
    pricing = context.application.bot_data['pricing']
    invoice_numbers = context.application.bot_data['invoice_numbers']
    daily_profit_current = context.application.bot_data['daily_profit']

    try:
        logger.info(f"[{chat_id}] Showing final options for order {order_id} to user {user_id}")

        if order_id not in orders:
            logger.warning(f"[{chat_id}] Attempted to show final options for non-existent order_id: {order_id}")
            await context.bot.send_message(chat_id=chat_id, text="عذراً، الطلب غير موجود. الرجاء بدء طلبية جديدة.")
            return

        order = orders[order_id]
        invoice = invoice_numbers.get(order_id, "غير معروف")
        phone_number = order.get('phone_number', 'ماكو رقم')

        # حساب الأسعار
        total_buy = 0.0
        total_sell = 0.0
        for p_name in order["products"]:
            if p_name in pricing.get(order_id, {}) and "buy" in pricing[order_id].get(p_name, {}) and "sell" in pricing[order_id].get(p_name, {}):
                total_buy += pricing[order_id][p_name]["buy"]
                total_sell += pricing[order_id][p_name]["sell"]

        net_profit_products = total_sell - total_buy
        current_places = order.get("places_count", 0)
        extra_cost_value = calculate_extra(current_places)
        delivery_fee = get_delivery_price(order.get('title', ''))
        final_total = total_sell + extra_cost_value + delivery_fee

        # تحديث الربح اليومي
        context.application.bot_data['daily_profit'] = daily_profit_current + net_profit_products + extra_cost_value
        context.application.create_task(save_data_in_background(context))

        # فاتورة الزبون
        customer_invoice_lines = [
            "📋 أبو الأكبر للتوصيل 🚀",
            "-----------------------------------",
            f"فاتورة رقم: #{invoice}",
            f"🏠 عنوان الزبون: {order['title']}",
            f"📞 رقم الزبون: `{phone_number}`",
            "🛍️ المنتجات:",
            ""
        ]

        current_display_total_sum = 0.0
        for i, product_name in enumerate(order["products"]):
            if product_name in pricing.get(order_id, {}) and "sell" in pricing[order_id].get(product_name, {}):
                sell_price = pricing[order_id][product_name]["sell"]

                if i == 0:
                    customer_invoice_lines.append(f"– {product_name} بـ{format_float(sell_price)}")
                    customer_invoice_lines.append(f"• {format_float(sell_price)} 💵")
                else:
                    prev_total_for_display = current_display_total_sum
                    customer_invoice_lines.append(f"– {product_name} بـ{format_float(sell_price)}")
                    customer_invoice_lines.append(f"• {format_float(prev_total_for_display)}+{format_float(sell_price)}= {format_float(prev_total_for_display + sell_price)} 💵")

                current_display_total_sum += sell_price
            else:
                customer_invoice_lines.append(f"– {product_name} (لم يتم تسعيره)")

        # إضافة كلفة التجهيز
        if extra_cost_value > 0:
            prev_total_for_display = current_display_total_sum
            customer_invoice_lines.append(f"– 📦 التجهيز: من {current_places} محلات بـ {format_float(extra_cost_value)}")
            customer_invoice_lines.append(f"• {format_float(prev_total_for_display)}+{format_float(extra_cost_value)}= {format_float(prev_total_for_display + extra_cost_value)} 💵")
            current_display_total_sum += extra_cost_value

        # إضافة أجرة التوصيل
        display_delivery_fee_customer = original_delivery_fee
        if current_places in [1, 2]:
            display_delivery_fee_customer = 0

        if display_delivery_fee_customer == 0 and original_delivery_fee != 0:
            prev_total_for_display = current_display_total_sum
            customer_invoice_lines.append(f"– 🚚 التوصيل: بـ {format_float(display_delivery_fee_customer)}")
            customer_invoice_lines.append(f"• {format_float(prev_total_for_display)}+{format_float(display_delivery_fee_customer)}= {format_float(prev_total_for_display + display_delivery_fee_customer)} 💵")
            current_display_total_sum += display_delivery_fee_customer
        elif original_delivery_fee > 0:
            prev_total_for_display = current_display_total_sum
            customer_invoice_lines.append(f"– 🚚 التوصيل: بـ {format_float(original_delivery_fee)}")
            customer_invoice_lines.append(f"• {format_float(prev_total_for_display)}+{format_float(original_delivery_fee)}= {format_float(prev_total_for_display + original_delivery_fee)} 💵")
            current_display_total_sum += original_delivery_fee


        customer_invoice_lines.extend([
            "-----------------------------------",
            "✨ المجموع الكلي: ✨",
            f"بدون التوصيل = {format_float(total_sell + extra_cost_value)} 💵",
            f"مــــع التوصيل = {format_float(final_total)} 💵",
            "شكراً لاختياركم خدمة أبو الأكبر للتوصيل! ❤️"
        ])

        customer_final_text = "\n".join(customer_invoice_lines)

        # إرسال فاتورة الزبون
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=customer_final_text,
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"[{chat_id}] Could not send customer invoice: {e}")

        # فاتورة المجهز (نفس الكود السابق...)
        supplier_invoice = [
            f"**فاتورة الشراء:🧾💸**",
            f"رقم الفاتورة🔢: {invoice}",
            f"عنوان الزبون🏠: {order['title']}",
            f"رقم الزبون📞: `{phone_number}`",
            "\n*تفاصيل الشراء:🗒️💸*"
        ]

        for p_name in order["products"]:
            if p_name in pricing.get(order_id, {}) and "buy" in pricing[order_id].get(p_name, {}):
                buy = pricing[order_id][p_name]["buy"]
                supplier_invoice.append(f"  - {p_name}: {format_float(buy)}")
            else:
                supplier_invoice.append(f"  - {p_name}: (ترا ماحددت بيش اشتريت)")

        supplier_invoice.append(f"\n*مجموع كلفة الشراء للطلبية:💸* {format_float(total_buy)}")

        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="\n".join(supplier_invoice),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"[{chat_id}] Could not send supplier invoice: {e}")

        # فاتورة الإدارة (نفس الكود السابق...)
        owner_invoice = [
            f"**فاتورة الإدارة:👨🏻‍💼**",
            f"رقم الفاتورة🔢: {invoice}",
            f"رقم الزبون📞: `{phone_number}`",
            f"عنوان الزبون🏠: {order['title']}",
            "\n*تفاصيل الطلبية:🗒*"
        ]

        for p_name in order["products"]:
            if p_name in pricing.get(order_id, {}) and "buy" in pricing[order_id][p_name] and "sell" in pricing[order_id][p_name]:
                buy = pricing[order_id][p_name]["buy"]
                sell = pricing[order_id][p_name]["sell"]
                profit = sell - buy
                owner_invoice.append(f"- {p_name}: شراء {format_float(buy)} | بيع {format_float(sell)} | ربح {format_float(profit)}")
            else:
                owner_invoice.append(f"- {p_name}: (غير مسعر)")

        owner_invoice.extend([
            f"\n*إجمالي الشراء:💸* {format_float(total_buy)}",
            f"*إجمالي البيع:💵 * {format_float(total_sell)}",
            f"*ربح المنتجات:💲* {format_float(net_profit_products)}",
            f"*ربح المحلات ({current_places} محل):🏪* {format_float(extra_cost_value)}",
            f"*أجرة التوصيل:🚚* {format_float(delivery_fee)}",
            f"*المجموع الكلي:💰* {format_float(final_total)}"
        ])

        try:
            await context.bot.send_message(
                chat_id=OWNER_ID,
                text="\n".join(owner_invoice),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"[{chat_id}] Could not send owner invoice: {e}")

        # أزرار التحكم النهائية
        encoded_customer_text = quote(customer_final_text, safe='')
        keyboard = [
            [InlineKeyboardButton("1️⃣ تعدل سعر", callback_data=f"edit_prices_{order_id}")],
            [InlineKeyboardButton("2️⃣ ترفع الطلب", url="https://d.ksebstor.site/client/96f743f604a4baf145939299")],
            [InlineKeyboardButton("3️⃣ إرسال فاتورة الزبون (واتساب)", url=f"https://wa.me/{OWNER_PHONE_NUMBER}?text={encoded_customer_text}")],
            [InlineKeyboardButton("4️⃣ إنشاء طلب جديد", callback_data="start_new_order")]
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)
        message_text = "صلوات كملت 😏!\nدختار من الخيارات ابو العريف :"
        if message_prefix:
            message_text = message_prefix + "\n" + message_text

        await context.bot.send_message(
            chat_id=chat_id,
            text=message_text,
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"[{chat_id}] Error in show_final_options: {str(e)}", exc_info=True)
        await context.bot.send_message(
            chat_id=chat_id,
            text="عذراً، حدث خطأ أثناء عرض الفاتورة النهائية. الرجاء بدء طلبية جديدة."
        )

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
            logger.info(f"[{query.message.chat_id}] Cleared order-specific user_data for user {user_id} on /start command. User data after clean: {json.dumps(context.user_data.get(user_id, {}), indent=2)}")

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
    orders = context.application.bot_data['orders'] # نجيب كل الطلبيات
    pricing = context.application.bot_data['pricing'] # نحتاج الأسعار لحساب الربح

    try:
        if str(update.message.from_user.id) != str(OWNER_ID):
            await update.message.reply_text("عذراً، هذا الأمر متاح للمالك فقط.")
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
        await update.message.reply_text("عذراً، حدث خطأ أثناء عرض الأرباح.")

async def reset_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if str(update.message.from_user.id) != str(OWNER_ID):
            await update.message.reply_text("عذراً، هذا الأمر متاح للمالك فقط.")
            return
        
        keyboard = [
            [InlineKeyboardButton("اي صفر", callback_data="confirm_reset")],
            [InlineKeyboardButton("لا لاتصفر", callback_data="cancel_reset")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("يابه انته متاكد تريد تصفر راجع روحك اخذ خيره مو بعدين دكول لا حرامات ", reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in reset_all: {e}", exc_info=True)
        await update.message.reply_text("عذراً، حدث خطأ أثناء عملية التصفير.")

async def confirm_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    orders = context.application.bot_data['orders']
    pricing = context.application.bot_data['pricing']
    invoice_numbers = context.application.bot_data['invoice_numbers']
    last_button_message = context.application.bot_data['last_button_message']
    daily_profit = context.application.bot_data['daily_profit'] 
    supplier_report_timestamps = context.application.bot_data['supplier_report_timestamps']

    try:
        query = update.callback_query
        await query.answer()

        if str(query.from_user.id) != str(OWNER_ID):
            await query.edit_message_text("عذراً، لا تملك صلاحية لتنفيذ هذا الأمر.")
            return

        if query.data == "confirm_reset":
            logger.info(f"Daily profit before reset: {daily_profit}")
            
            orders.clear()
            pricing.clear()
            invoice_numbers.clear()
            last_button_message.clear()
            supplier_report_timestamps.clear()
            
            daily_profit_value = 0.0

            try:
                with open(COUNTER_FILE, "w") as f:
                    f.write("1")
            except Exception as e:
                logger.error(f"Could not reset invoice counter file: {e}", exc_info=True)
            
            context.application.bot_data['orders'] = orders
            context.application.bot_data['pricing'] = pricing
            context.application.bot_data['invoice_numbers'] = invoice_numbers
            context.application.bot_data['last_button_message'] = last_button_message
            context.application.bot_data['daily_profit'] = daily_profit_value
            context.application.bot_data['supplier_report_timestamps'] = supplier_report_timestamps

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
        total_net_profit_all_orders = 0.0
        total_extra_profit_all_orders = 0.0
        product_counter = Counter()
        details = []

        for order_id, order in orders.items():
            invoice = invoice_numbers.get(order_id, "غير معروف")
            details.append(f"\n**فاتورة رقم:🔢** {invoice}")
            details.append(f"**عنوان الزبون:🏠** {order['title']}")

            order_buy = 0.0
            order_sell = 0.0
            order_net_profit = 0.0
            order_extra_profit = 0.0

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
                        order_net_profit += profit_item
                        details.append(f"  - {p_name} | شراء💸: {format_float(buy)} | بيع💵 : {format_float(sell)} | ربح💲: {format_float(profit_item)}")
                    else:
                        details.append(f"  - {p_name}: (لم يتم تسعيره)")
            else:
                details.append(f"  (لا توجد منتجات محددة لهذا الطلب)")

            num_places = order.get("places_count", 0)
            order_extra_profit = calculate_extra(num_places)

            total_buy_all_orders += order_buy
            total_sell_all_orders += order_sell
            total_net_profit_all_orders += order_net_profit
            total_extra_profit_all_orders += order_extra_profit

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
        await update.message.reply_text("عذراً، حدث خطأ أثناء عرض التقرير.")
        
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

    await update.message.reply_text("تم تصفير تقاريرك بنجاح. أي طلبية جديدة تجهزها من الآن راح تظهر بالتقرير القادم.")

async def delete_order_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    chat_id = update.effective_chat.id

    if user_id != str(OWNER_ID):
        await update.message.reply_text("عذراً، هذا الأمر متاح للمالك فقط.")
        return ConversationHandler.END

    await update.message.reply_text("تمام، دزلي رقم الزبون للطلبية اللي تريد تمسحها:")
    context.user_data[user_id]["deleting_order"] = True
    return ASK_CUSTOMER_PHONE_NUMBER_FOR_DELETION

async def receive_customer_phone_for_deletion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    chat_id = update.effective_chat.id
    customer_phone_number = update.message.text.strip()

    logger.info(f"[{chat_id}] Received phone number '{customer_phone_number}' for order deletion from user {user_id}.")

    if user_id != str(OWNER_ID):
        await update.message.reply_text("عذراً، هذا الأمر متاح للمالك فقط.")
        context.user_data[user_id].pop("deleting_order", None)
        return ConversationHandler.END

    order_to_delete_id = None
    order_details_text = "ما لكييت طلبية لهذا الزبون."
    
    found_orders = {oid: o for oid, o in orders.items() if o.get("phone_number") == customer_phone_number}

    if found_orders:
        order_to_delete_id = list(found_orders.keys())[0]
        found_order = found_orders[order_to_delete_id]
        
        invoice = invoice_numbers.get(order_to_delete_id, "غير معروف")
        order_details_text = (
            f"لكيت طلبية رقم #{invoice} لهذا الزبون:\n"
            f"العنوان: {found_order.get('title', 'غير متوفر')}\n"
            f"المنتجات: {', '.join(found_order.get('products', []))}\n"
            f"متأكد تريد تمسح هاي الطلبية؟ هذا الإجراء ما بي رجعة."
        )
    
    if order_to_delete_id:
        context.user_data[user_id]["order_id_to_delete"] = order_to_delete_id
        confirm_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ اي، امسحها", callback_data=f"confirm_delete_order_{order_to_delete_id}")],
            [InlineKeyboardButton("❌ لا، بطلت", callback_data=f"cancel_delete_order")]
        ])
        await update.message.reply_text(order_details_text, reply_markup=confirm_keyboard, parse_mode="Markdown")
        return ASK_FOR_DELETION_CONFIRMATION
    else:
        await update.message.reply_text(order_details_text)
        context.user_data[user_id].pop("deleting_order", None)
        return ConversationHandler.END

async def confirm_delete_order_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = str(query.from_user.id)
    chat_id = query.message.chat_id

    if user_id != str(OWNER_ID):
        await query.edit_message_text("عذراً، لا تملك صلاحية لتنفيذ هذا الأمر.")
        context.user_data[user_id].pop("deleting_order", None)
        context.user_data[user_id].pop("order_id_to_delete", None)
        return ConversationHandler.END

    order_id_to_delete = context.user_data[user_id].get("order_id_to_delete")

    if not order_id_to_delete or order_id_to_delete not in orders:
        logger.warning(f"[{chat_id}] Order ID to delete not found in user_data or orders for user {user_id}.")
        await query.edit_message_text("ترا ما لكيت الطلبية اللي كنت تحاول تمسحها. يمكن انمسحت من قبل.")
        context.user_data[user_id].pop("deleting_order", None)
        context.user_data[user_id].pop("order_id_to_delete", None)
        return ConversationHandler.END

    try:
        del orders[order_id_to_delete]
        if order_id_to_delete in pricing:
            del pricing[order_id_to_delete]
        if order_id_to_delete in invoice_numbers:
            del invoice_numbers[order_id_to_delete]

        context.application.create_task(save_data_in_background(context))

        logger.info(f"[{chat_id}] Order {order_id_to_delete} deleted successfully by user {user_id}.")
        await query.edit_message_text(f"تم مسح الطلبية رقم `{invoice_numbers.get(order_id_to_delete, 'غير معروف')}` بنجاح!")
    except Exception as e:
        logger.error(f"[{chat_id}] Error deleting order {order_id_to_delete}: {e}", exc_info=True)
        await query.edit_message_text("عذراً، صار خطأ أثناء مسح الطلبية.")
    
    context.user_data[user_id].pop("deleting_order", None)
    context.user_data[user_id].pop("order_id_to_delete", None)
    return ConversationHandler.END


async def cancel_delete_order_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = str(query.from_user.id)
    chat_id = query.message.chat_id

    logger.info(f"[{chat_id}] Cancel delete order button clicked by user {user_id}.")

    await query.edit_message_text("تم إلغاء عملية مسح الطلبية.")

    context.user_data[user_id].pop("deleting_order", None)
    context.user_data[user_id].pop("order_id_to_delete", None)
    return ConversationHandler.END
    
if __name__ == "__main__":
    main()
