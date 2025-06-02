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
save_pending = False # علم جديد يبين إذا اكو حفظ معلق
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
                pricing = {str(k): v for k, v in pricing.items()}
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
            except json.JSONDecodeError:
                daily_profit = 0.0
                logger.warning("daily_profit.json is corrupted or empty, reinitializing.")
            except Exception as e:
                logger.error(f"Error loading daily_profit.json: {e}, reinitializing.")
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
            with open(LAST_BUTTON_MESSAGE_FILE, "w") as f:
                json.dump(last_button_message, f)
            logger.info("All data saved to disk successfully.")
        except Exception as e:
            logger.error(f"Error saving data to disk: {e}")
        finally:
            save_pending = False # خلص الحفظ، رجّع العلم لـ False

# دالة الحفظ المؤجل
# دالة الحفظ المؤجل - راح نغيرها
def schedule_save():
    global save_timer, save_pending
    if save_pending: # إذا اكو عملية حفظ معلقة، لا تبدي وحدة جديدة
        logger.info("Save already pending, skipping new schedule.")
        return

    if save_timer is not None:
        save_timer.cancel()

    save_pending = True # اكو عملية حفظ راح تبدي
    save_timer = threading.Timer(0.5, _save_data_to_disk) # قللت الوقت لـ 0.5 ثانية
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
ASK_BUY, ASK_SELL, ASK_PLACES = range(3)

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

# دالة مساعدة لحذف الرسائل في الخلفية
async def delete_message_in_background(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    try:
        await asyncio.sleep(0.05)
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
        del context.user_data[user_id]
        logger.info(f"Cleared user_data for user {user_id} on /start command.")
    
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
    lines = message.text.strip().split('\n')
    if len(lines) < 2:
        if not edited:
            await message.reply_text("الرجاء التأكد من كتابة عنوان الزبون في السطر الأول والمنتجات في الأسطر التالية.")
        return

    title = lines[0]
    products = [p.strip() for p in lines[1:] if p.strip()]

    if not products:
        if not edited:
            await message.reply_text("الرجاء إضافة منتجات بعد العنوان.")
        return

    existing_order_id = None
    for oid, msg_info in last_button_message.items():
        if msg_info.get("message_id") == message.message_id and str(msg_info.get("chat_id")) == str(message.chat_id):
            if oid in orders and str(orders[oid].get("user_id")) == user_id:
                existing_order_id = oid
                logger.info(f"Found existing order {existing_order_id} for user {user_id} based on message ID.")
                break
            else:
                logger.warning(f"Message ID {message.message_id} found in last_button_message but not linked to user {user_id} or order {oid} is missing. Treating as new.")
                existing_order_id = None
                break

    if existing_order_id:
        order_id = existing_order_id
        old_products = set(orders[order_id].get("products", []))
        new_products = set(products)
        added_products = list(new_products - old_products)
        removed_products = list(old_products - new_products)
        
        orders[order_id]["title"] = title
        orders[order_id]["products"] = products

        for p in added_products:
            if p not in pricing.get(order_id, {}):
                pricing.setdefault(order_id, {})[p] = {}
        
        if order_id in pricing:
            for p in removed_products:
                if p in pricing[order_id]:
                    del pricing[order_id][p]

        context.application.create_task(save_data_in_background(context))
        await show_buttons(message.chat_id, context, user_id, order_id, confirmation_message="تم تحديث الطلب.")
        return

    order_id = str(uuid.uuid4())[:8]
    invoice_no = get_invoice_number()
    orders[order_id] = {"user_id": user_id, "title": title, "products": products}
    pricing[order_id] = {p: {} for p in products}
    invoice_numbers[order_id] = invoice_no
    
    context.application.create_task(save_data_in_background(context))
    
    await message.reply_text(f"استلمت الطلب بعنوان: *{title}* (عدد المنتجات: {len(products)})", parse_mode="Markdown")
    await show_buttons(message.chat_id, context, user_id, order_id)

async def show_buttons(chat_id, context, user_id, order_id, confirmation_message=None):
    if order_id not in orders:
        logger.warning(f"Attempted to show buttons for non-existent order_id: {order_id}")
        await context.bot.send_message(chat_id=chat_id, text="عذراً، الطلب الذي تحاول الوصول إليه غير موجود أو تم حذفه. الرجاء بدء طلبية جديدة.")
        if user_id in context.user_data:
            del context.user_data[user_id]
        return

    # **الخطوة الأهم: حذف رسالة الأزرار القديمة أولاً وبسرعة**
    msg_info = last_button_message.get(order_id)
    if msg_info and str(msg_info.get("chat_id")) == str(chat_id): # تأكدنا إنها لنفس الشات
        # شغّل مهمة الحذف بالخلفية بدون تأخير
        context.application.create_task(delete_message_in_background(context, chat_id=msg_info["chat_id"], message_id=msg_info["message_id"]))
        logger.info(f"Scheduled immediate deletion of old button message {msg_info['message_id']} for order {order_id}.")
        # حذفها من القائمة حتى ما نسوي بيها مشاكل
        if order_id in last_button_message:
            del last_button_message[order_id]
            context.application.create_task(save_data_in_background(context)) # احفظ التغيير مال الحذف

    order = orders[order_id]
    
    completed_products = []
    pending_products = []
    for p in order["products"]:
        if p in pricing.get(order_id, {}) and 'buy' in pricing[order_id].get(p, {}) and 'sell' in pricing[order_id].get(p, {}):
            completed_products.append(p)
        else:
            pending_products.append(p)
            
    completed_products.sort()
    pending_products.sort()

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

    # **إرسال رسالة الأزرار الجديدة مباشرةً بعد جدولة حذف القديمة**
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=message_text,
        reply_markup=markup,
        parse_mode="Markdown"
    )
    logger.info(f"Sent new button message {msg.message_id} for order {order_id}")
    
    # حفظ معلومات الرسالة الجديدة
    last_button_message[order_id] = {"chat_id": chat_id, "message_id": msg.message_id}
    context.application.create_task(save_data_in_background(context))


# **تم إزالة app.add_handler(CommandHandler("start", start)) من هنا لأنه يجب أن يكون داخل main() فقط**

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
        if user_id in context.user_data:
            del context.user_data[user_id]
        return ConversationHandler.END
    
    context.user_data.setdefault(user_id, {})
    context.user_data[user_id].update({"order_id": order_id, "product": product})
    
    if 'messages_to_delete' not in context.user_data[user_id]:
        context.user_data[user_id]['messages_to_delete'] = [] 

    # حذف رسالة الأزرار القديمة فوراً (أسرع من قبل)
    context.application.create_task(delete_message_in_background(context, chat_id=query.message.chat_id, message_id=query.message.message_id))

    msg = await query.message.reply_text(f"تمام، كم سعر شراء *'{product}'*؟", parse_mode="Markdown")
    context.user_data[user_id]['messages_to_delete'].append({'chat_id': msg.chat_id, 'message_id': msg.message_id})
    
    return ASK_BUY

# **تم دمج هذا الجزء داخل دالة receive_buy_price الصحيحة**
# حفظ السعر فوراً دون انتظار
# pricing.setdefault(order_id, {}).setdefault(product, {})["buy"] = price
# 
# # إرسال رسالة البيع فوراً قبل أي شيء آخر
# msg = await update.message.reply_text(f"شكراً. وهسه، بيش راح تبيع *'{product}'*؟", parse_mode="Markdown")
# context.user_data[user_id]['messages_to_delete'].append({
#     'chat_id': msg.chat_id,
#     'message_id': msg.message_id
# })
# 
# # تشغيل عملية الحفظ في الخلفية بعد إرسال رسالة السؤال
# # هذا يضمن أن السؤال ينرسل بأسرع وقت ممكن
# context.application.create_task(save_data_in_background(context))
# 
# return ASK_SELL
    
async def receive_buy_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    
    context.user_data.setdefault(user_id, {})
    if 'messages_to_delete' not in context.user_data[user_id]:
        context.user_data[user_id]['messages_to_delete'] = []
    
    context.user_data[user_id]['messages_to_delete'].append({
        'chat_id': update.message.chat_id,
        'message_id': update.message.message_id
    })

    data = context.user_data.get(user_id, {})
    if not data or "order_id" not in data or "product" not in data:
        await update.message.reply_text("حدث خطأ، الرجاء البدء من جديد")
        return ConversationHandler.END
    
    order_id = data["order_id"]
    product = data["product"]
    
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
    
    # 1. إرسال رسالة "بيش راح تبيع؟" فوراً (مع الـ Markdown)
    msg = await update.message.reply_text(f"شكراً. وهسه، بيش راح تبيع *'{product}'*؟", parse_mode="Markdown")
    context.user_data[user_id]['messages_to_delete'].append({
        'chat_id': msg.chat_id,
        'message_id': msg.message_id
    })
    
    # 2. حفظ السعر بعد إرسال الرسالة
    pricing.setdefault(order_id, {}).setdefault(product, {})["buy"] = price
    
    # 3. جدولة الحفظ بالخلفية
    context.application.create_task(save_data_in_background(context))
    
    return ASK_SELL




async def receive_sell_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    
    context.user_data.setdefault(user_id, {})
    if 'messages_to_delete' not in context.user_data[user_id]:
        context.user_data[user_id]['messages_to_delete'] = []
    context.user_data[user_id]['messages_to_delete'].append({'chat_id': update.message.chat_id, 'message_id': update.message.message_id})

    data = context.user_data.get(user_id)
    if not data or "order_id" not in data or "product" not in data:
        await update.message.reply_text("عذراً، حدث خطأ. الرجاء المحاولة مرة أخرى أو بدء طلبية جديدة.")
        if user_id in context.user_data:
            del context.user_data[user_id]
        return ConversationHandler.END
    
    order_id, product = data["order_id"], data["product"]
    
    if order_id not in orders or product not in orders[order_id].get("products", []):
        await update.message.reply_text("عذراً، الطلب أو المنتج لم يعد موجوداً. الرجاء بدء طلبية جديدة.")
        if user_id in context.user_data:
            del context.user_data[user_id]
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
    context.application.create_task(save_data_in_background(context)) # حفظ البيانات مباشرة بعد التسعير

    order = orders[order_id]
    all_priced = True
    for p in order["products"]:
        if p not in pricing.get(order_id, {}) or "buy" not in pricing[order_id].get(p, {}) or "sell" not in pricing[order_id].get(p, {}):
            all_priced = False
            break
            
    if all_priced:
        context.user_data[user_id]["completed_order_id"] = order_id 
        
        # حذف رسائل المستخدم والبوت السابقة قبل إرسال السؤال الجديد
        for msg_info in context.user_data[user_id].get('messages_to_delete', []):
            context.application.create_task(delete_message_in_background(context, chat_id=msg_info['chat_id'], message_id=msg_info['message_id']))
        context.user_data[user_id]['messages_to_delete'].clear()

        # هنا أيضاً نحذف رسالة الأزرار القديمة (اللي اختفت أصلا) قبل إرسال السؤال الجديد
        # وهذا يمنع أي تأخير محتمل
        msg_info_buttons = last_button_message.get(order_id)
        if msg_info_buttons and str(msg_info_buttons.get("chat_id")) == str(update.effective_chat.id):
            context.application.create_task(delete_message_in_background(context, chat_id=msg_info_buttons["chat_id"], message_id=msg_info_buttons["message_id"]))
            if order_id in last_button_message:
                del last_button_message[order_id] # نشيلها من القائمة
                context.application.create_task(save_data_in_background(context)) # نحفظ التغيير مال الحذف

        buttons = []
        emojis = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟']
        for i in range(1, 11):
            buttons.append(InlineKeyboardButton(emojis[i-1], callback_data=f"places_{i}"))
        
        keyboard = [buttons[i:i + 5] for i in range(0, len(buttons), 5)]
        reply_markup = InlineKeyboardMarkup(keyboard)

        msg_places = await update.message.reply_text(
            "كل المنتجات تم تسعيرها. كم محل كلفتك الطلبية؟ (اختر من الأزرار أو اكتب الرقم)", 
            reply_markup=reply_markup
        )
        logger.info(f"All products priced for order {order_id}. Transitioning to ASK_PLACES. Sent new message ID: {msg_places.message_id}")

        context.user_data[user_id]['messages_to_delete'].append({'chat_id': msg_places.chat_id, 'message_id': msg_places.message_id})
        
        return ASK_PLACES 
    else:
        # **مهم جدًا: هنا نستدعي show_buttons بوضوح حتى تظهر الأزرار المحدثة**
        confirmation_msg = f"تم حفظ السعر لـ *'{product}'*."
        logger.info(f"Price saved for '{product}' in order {order_id}. Showing updated buttons with confirmation.")
        
        # قبل ما نعرض الأزرار الجديدة، نمسح رسائل الكوتش والمستخدم اللي طلعت من سؤال سعر البيع
        for msg_info in context.user_data[user_id].get('messages_to_delete', []):
            context.application.create_task(delete_message_in_background(context, chat_id=msg_info['chat_id'], message_id=msg_info['message_id']))
        context.user_data[user_id]['messages_to_delete'].clear()

        await show_buttons(update.effective_chat.id, context, user_id, order_id, confirmation_message=confirmation_msg)
        
        return ConversationHandler.END


async def receive_place_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global daily_profit
    
    places = None
    message_object = None 
    user_id = str(update.effective_user.id)
    
    context.user_data.setdefault(user_id, {})
    if 'messages_to_delete' not in context.user_data[user_id]:
        context.user_data[user_id]['messages_to_delete'] = []

    if update.callback_query:
        query = update.callback_query
        logger.info(f"Places callback query received: {query.data}")
        await query.answer()
        if query.data.startswith("places_"):
            places = int(query.data.split("_")[1])
            message_object = query.message 
        else:
            logger.error(f"Unexpected callback_query in receive_place_count: {query.data}")
            await query.edit_message_text("عذراً، حدث خطأ غير متوقع. الرجاء المحاولة مرة أخرى أو بدء طلبية جديدة.")
            if user_id in context.user_data:
                del context.user_data[user_id]
            return ConversationHandler.END
    elif update.message:
        message_object = update.message 
        context.user_data[user_id]['messages_to_delete'].append({'chat_id': update.message.chat_id, 'message_id': update.message.message_id})

        try:
            places = int(message_object.text.strip())
            if places < 0:
                msg_error = await message_object.reply_text("عدد المحلات يجب أن يكون رقماً موجباً. الرجاء إدخال عدد المحلات بشكل صحيح.")
                context.user_data[user_id]['messages_to_delete'].append({'chat_id': msg_error.chat_id, 'message_id': msg_error.message_id})
                return ASK_PLACES
        except ValueError:
            msg_error = await message_object.reply_text("الرجاء إدخال عدد صحيح لعدد المحلات.")
            context.user_data[user_id]['messages_to_delete'].append({'chat_id': msg_error.chat_id, 'message_id': msg_error.message_id})
            return ASK_PLACES 
    
    if places is None:
        logger.warning("No places count received.")
        if user_id in context.user_data:
            del context.user_data[user_id]
        return ConversationHandler.END

    order_id = context.user_data[user_id].get("completed_order_id")
    if not order_id or order_id not in orders:
        await message_object.reply_text("عذراً، لا توجد طلبية مكتملة لمعالجتها أو تم حذفها. الرجاء بدء طلبية جديدة.")
        if user_id in context.user_data:
            del context.user_data[user_id]
        return ConversationHandler.END

    order = orders[order_id]
    invoice = invoice_numbers.get(order_id, "غير معروف")
    total_buy = 0.0
    total_sell = 0.0
    
    owner_invoice_details = []
    owner_invoice_details.append(f"رقم الفاتورة: {invoice}")
    owner_invoice_details.append(f"عنوان الزبون: {order['title']}")

    for p in order["products"]:
        if p in pricing.get(order_id, {}) and "buy" in pricing[order_id].get(p, {}) and "sell" in pricing[order_id].get(p, {}):
            buy = pricing[order_id][p]["buy"]
            sell = pricing[order_id][p]["sell"] 
            profit = sell - buy
            total_buy += buy
            total_sell += sell
            owner_invoice_details.append(f"{p} - شراء: {format_float(buy)}, بيع: {format_float(sell)}, ربح: {format_float(profit)}")
        else:
            owner_invoice_details.append(f"{p} - (لم يتم تسعيره بعد)")

    net_profit = total_sell - total_buy
    daily_profit += net_profit
    context.application.create_task(save_data_in_background(context))

    extra = calculate_extra(places)
    total_with_extra = total_sell + extra

    owner_invoice_details.append(f"\nالمجموع شراء: {format_float(total_buy)}")
    owner_invoice_details.append(f"المجموع بيع: {format_float(total_sell)}")
    owner_invoice_details.append(f"الربح الكلي: {format_float(net_profit)}")
    owner_invoice_details.append(f"عدد المحلات: {places} (+{format_float(extra)})")
    owner_invoice_details.append(f"السعر الكلي: {format_float(total_with_extra)}")
    
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
        logger.info(f"Admin invoice and WhatsApp button sent to OWNER_ID: {OWNER_ID}")
    except Exception as e:
        logger.error(f"Could not send admin invoice to OWNER_ID {OWNER_ID}: {e}")
        await message_object.reply_text("عذراً، لم أتمكن من إرسال فاتورة الإدارة إلى خاصك. يرجى التأكد من أنني أستطيع مراسلتك في الخاص (قد تحتاج إلى بدء محادثة معي أولاً).")

    customer_invoice_lines = []
    customer_invoice_lines.append(f"أبو الأكبر للتوصيل")
    customer_invoice_lines.append(f"رقم الفاتورة: {invoice}")
    customer_invoice_lines.append(f"عنوان الزبون: {order['title']}")
    customer_invoice_lines.append(f"\nالمواد:")
    
    running_total_for_customer = 0.0
    for p in order["products"]:
        if p in pricing.get(order_id, {}) and "sell" in pricing[order_id].get(p, {}):
            sell = pricing[order_id][p]["sell"]
            running_total_for_customer += sell
            customer_invoice_lines.append(f"{p} - {format_float(sell)} = {format_float(running_total_for_customer)}")
        else:
            customer_invoice_lines.append(f"{p} - (لم يتم تسعيره)")
    
    customer_invoice_lines.append(f"كلفة تجهيز من - {places} محلات {format_float(extra)} = {format_float(total_with_extra)}")
    customer_invoice_lines.append(f"\nالمجموع الكلي: {format_float(total_with_extra)} (مع احتساب عدد المحلات)")
    
    customer_final_text = "\n".join(customer_invoice_lines)
    
    await message_object.reply_text("نسخة الزبون (لإرسالها للعميل):\n" + customer_final_text, parse_mode="Markdown")

    encoded_customer_invoice = customer_final_text.replace(" ", "%20").replace("\n", "%0A").replace("*", "")

    whatsapp_customer_button_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("إرسال فاتورة الزبون للواتساب", url=f"https://wa.me/{OWNER_PHONE_NUMBER}?text={encoded_customer_invoice}")]
    ])
    await message_object.reply_text("دوس على هذه الأزرار لإرسال فاتورة الزبون عبر الواتساب:", reply_markup=whatsapp_customer_button_markup)
    
    final_actions_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("تعديل الطلب الأخير", callback_data=f"edit_last_order_{order_id}")],
        [InlineKeyboardButton("إنشاء طلب جديد", callback_data="start_new_order")]
    ])
    await message_object.reply_text("شنو تريد تسوي هسه؟", reply_markup=final_actions_keyboard)

    logger.info(f"Attempting to delete {len(context.user_data[user_id].get('messages_to_delete', []))} messages for user {user_id}.")

    if user_id in context.user_data and 'messages_to_delete' in context.user_data[user_id]:
        for msg_info in context.user_data[user_id]['messages_to_delete']:
            context.application.create_task(delete_message_in_background(context, chat_id=msg_info['chat_id'], message_id=msg_info['message_id']))
        context.user_data[user_id]['messages_to_delete'].clear()

    msg_info_buttons = last_button_message.get(order_id)
    if msg_info_buttons and msg_info_buttons.get("chat_id") == message_object.chat_id:
        context.application.create_task(delete_message_in_background(context, chat_id=message_object.chat_id, message_id=msg_info_buttons["message_id"]))
        if order_id in last_button_message:
            del last_button_message[order_id] 
            context.application.create_task(save_data_in_background(context))

    if user_id in context.user_data:
        del context.user_data[user_id]
        logger.info(f"Cleared user_data for user {user_id} after successful order completion and message deletion scheduling.")
        
    return ConversationHandler.END

async def edit_last_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    if query.data.startswith("edit_last_order_"):
        order_id = query.data.replace("edit_last_order_", "")
    else:
        await query.message.reply_text("عذراً، حدث خطأ في بيانات الزر. الرجاء المحاولة مرة أخرى.")
        return ConversationHandler.END

    if order_id not in orders or str(orders[order_id].get("user_id")) != user_id:
        await query.message.reply_text("عذراً، الطلب الذي تحاول تعديله غير موجود أو ليس لك.")
        return ConversationHandler.END

    await show_buttons(query.message.chat_id, context, user_id, order_id, confirmation_message="يمكنك الآن تعديل أسعار المنتجات أو إضافة/حذف منتجات بتعديل الرسالة الأصلية.")
    
    return ConversationHandler.END

async def start_new_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    if user_id in context.user_data:
        del context.user_data[user_id]
        logger.info(f"Cleared user_data for user {user_id} after starting a new order from button.")

    await query.message.reply_text("تمام، دز الطلبية الجديدة كلها برسالة واحدة.\n\n*السطر الأول:* عنوان الزبون.\n*الأسطر الباقية:* كل منتج بسطر واحد.", parse_mode="Markdown")
    
    return ConversationHandler.END

async def show_profit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.message.from_user.id) != str(OWNER_ID):
        await update.message.reply_text("عذراً، هذا الأمر متاح للمالك فقط.")
        return
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

    # إضافة الـ Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^الارباح$|^ارباح$"), show_profit))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^صفر$|^تصفير$"), reset_all))
    app.add_handler(CallbackQueryHandler(confirm_reset, pattern="^(confirm_reset|cancel_reset)$"))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^التقارير$|^تقرير$|^تقارير$"), show_report))
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE, edited_message))

    # إضافة الهاندلرات الجديدة لأزرار ما بعد اكتمال الطلب
    app.add_handler(CallbackQueryHandler(edit_last_order, pattern="^edit_last_order_"))
    app.add_handler(CallbackQueryHandler(start_new_order, pattern="^start_new_order$"))

    # محادثة تجهيز الطلبات
    conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_order),
            CallbackQueryHandler(product_selected)
        ],
        states={
            ASK_BUY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_buy_price),
            ],
            ASK_SELL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_sell_price),
            ],
            ASK_PLACES: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_place_count),
                CallbackQueryHandler(receive_place_count, pattern="^places_")
            ],
        },
        fallbacks=[
            CommandHandler("cancel", lambda u, c: ConversationHandler.END)
        ]
    )
    app.add_handler(conv_handler)

    logger.info("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
