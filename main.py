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

# تفعيل الـ logging للحصول على تفاصيل الأخطاء والعمليات
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)


# المسار الثابت لحفظ البيانات داخل وحدة التخزين (Volume)
DATA_DIR = "/mnt/data/"

# أسماء ملفات حفظ البيانات، الآن ستُحفظ داخل مجلد DATA_DIR
ORDERS_FILE = os.path.join(DATA_DIR, "orders.json")
PRICING_FILE = os.path.join(DATA_DIR, "pricing.json")
INVOICE_NUMBERS_FILE = os.path.join(DATA_DIR, "invoice_numbers.json")
DAILY_PROFIT_FILE = os.path.join(DATA_DIR, "daily_profit.json")
COUNTER_FILE = os.path.join(DATA_DIR, "invoice_counter.txt")
# ملف لحفظ IDs رسائل الأزرار لكي لا يتم حذفها عند إعادة التشغيل
LAST_BUTTON_MESSAGE_FILE = os.path.join(DATA_DIR, "last_button_message.json")

# تهيئة المتغيرات العامة في النطاق العلوي لضمان وجودها
orders = {}
pricing = {}
invoice_numbers = {}
daily_profit = 0.0
last_button_message = {} 
current_product = {} 


# تحميل البيانات عند بدء تشغيل البوت
def load_data():
    global orders, pricing, invoice_numbers, daily_profit, last_button_message, current_product

    os.makedirs(DATA_DIR, exist_ok=True)

    # تم تعديل طريقة التحميل لاستخدام .clear() و .update() للحفاظ على المراجع
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
                pricing = {str(k): v for pk, pv in pricing.items()} # Ensure string keys
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
    
    # تحميل آخر رسائل الأزرار
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
def save_data():
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(ORDERS_FILE, "w") as f:
        json.dump(orders, f)
    with open(PRICING_FILE, "w") as f:
        json.dump(pricing, f)
    with open(INVOICE_NUMBERS_FILE, "w") as f:
        json.dump(invoice_numbers, f)
    with open(DAILY_PROFIT_FILE, "w") as f:
        json.dump(daily_profit, f)
    # حفظ IDs رسائل الأزرار
    with open(LAST_BUTTON_MESSAGE_FILE, "w") as f:
        json.dump(last_button_message, f)

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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أهلاً بك! لإعداد طلبية، دز الطلبية كلها برسالة واحدة.\n\n*السطر الأول:* عنوان الزبون.\n*الأسطر الباقية:* كل منتج بسطر واحد.", parse_mode="Markdown")

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
    # البحث عن طلبية موجودة لنفس المستخدم ونفس العنوان
    for oid, order in orders.items():
        if str(order.get("user_id")) == user_id and order.get("title") == title:
            existing_order_id = oid
            break

    if edited:
        # البحث عن طلبية موجودة مرتبطة بهذه الرسالة المعدلة
        for oid, msg_info in last_button_message.items():
            if msg_info.get("message_id") == message.message_id and str(msg_info.get("chat_id")) == str(message.chat_id):
                if oid in orders and str(orders[oid].get("user_id")) != user_id: # إذا كانت الرسالة المعدلة لمستخدم آخر، تجاهلها كرسالة تعديل وعاملها كطلب جديد
                    existing_order_id = None
                    break
                else:
                    existing_order_id = oid
                    break
        
        # إذا تم تعديل رسالة ليس لها طلب موجود أو ملك لمستخدم آخر، عاملها كطلب جديد
        if existing_order_id and existing_order_id not in orders:
            existing_order_id = None
        if existing_order_id and str(orders[existing_order_id].get("user_id")) != user_id:
            existing_order_id = None

    if existing_order_id:
        order_id = existing_order_id
        old_products = set(orders[order_id].get("products", []))
        new_products = set(products)
        added_products = list(new_products - old_products)
        removed_products = list(old_products - new_products)
        
        orders[order_id]["title"] = title
        orders[order_id]["products"] = products # تحديث قائمة المنتجات بالكامل لتعكس الإزالة والإضافة

        # إضافة المنتجات الجديدة لـ pricing
        for p in added_products:
            if p not in pricing.get(order_id, {}):
                pricing.setdefault(order_id, {})[p] = {}
        
        # إزالة المنتجات المحذوفة من pricing
        if order_id in pricing:
            for p in removed_products:
                if p in pricing[order_id]:
                    del pricing[order_id][p]

        save_data()
        await show_buttons(message.chat_id, context, user_id, order_id)
        return

    # إنشاء طلبية جديدة
    order_id = str(uuid.uuid4())[:8]
    invoice_no = get_invoice_number()
    orders[order_id] = {"user_id": user_id, "title": title, "products": products}
    pricing[order_id] = {p: {} for p in products}
    invoice_numbers[order_id] = invoice_no
    
    save_data()
    
    await message.reply_text(f"استلمت الطلب بعنوان: *{title}* (عدد المنتجات: {len(products)})", parse_mode="Markdown")
    await show_buttons(message.chat_id, context, user_id, order_id)

async def show_buttons(chat_id, context, user_id, order_id, is_final_buttons=False):
    if order_id not in orders:
        logger.warning(f"Attempted to show buttons for non-existent order_id: {order_id}")
        await context.bot.send_message(chat_id=chat_id, text="عذراً، الطلب الذي تحاول الوصول إليه غير موجود أو تم حذفه. الرجاء بدء طلبية جديدة.")
        return

    order = orders[order_id]
    
    # فصل المنتجات المكتملة عن غير المكتملة لغرض الترتيب
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
    
    # محاولة حذف الرسالة القديمة وتجاهل الأخطاء
    msg_info = last_button_message.get(order_id)
    if msg_info and msg_info.get("chat_id") == chat_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_info["message_id"])
            logger.info(f"Deleted old button message {msg_info['message_id']} for order {order_id}.")
        except Exception as e:
            # تجاهل الخطأ إذا الرسالة لم تعد موجودة أو لا يمكن حذفها
            logger.warning(f"Could not delete old button message {msg_info.get('message_id', 'N/A')} for order {order_id}: {e}. It might have been deleted already or is inaccessible.")
        finally:
            # إزالة الإشارة للرسالة القديمة من الذاكرة والملف
            if order_id in last_button_message:
                del last_button_message[order_id]
                save_data() # حفظ التغيير لضمان عدم الرجوع للرسالة المحذوفة بعد إعادة تشغيل البوت

    # إرسال الرسالة الجديدة
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=f"اضغط على منتج لتحديد سعره من *{order['title']}*:",
        reply_markup=markup,
        parse_mode="Markdown"
    )
    logger.info(f"Sent new button message {msg.message_id} for order {order_id}")
    
    last_button_message[order_id] = {"chat_id": chat_id, "message_id": msg.message_id}
    save_data() # حفظ الـ ID والـ chat_id للرسالة الجديدة


async def product_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    logger.info(f"Callback query received: {query.data}")
    await query.answer()

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
        return ConversationHandler.END
    
    context.user_data[user_id] = {"order_id": order_id, "product": product} # استخدام user_data لتخزين البيانات الخاصة بالمستخدم
    
    # ارسال رسالة السؤال عن سعر الشراء وحفظ الـ ID الخاص بها
    msg = await query.message.reply_text(f"تمام، كم سعر شراء *'{product}'*؟", parse_mode="Markdown")
    context.user_data[user_id]['ask_buy_message_id'] = msg.message_id
    
    return ASK_BUY

async def receive_buy_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    
    # حفظ رسالة المستخدم الحالية لحذفها لاحقاً
    context.user_data.setdefault(user_id, {})
    context.user_data[user_id]['user_buy_message_id'] = update.message.message_id # <--- Changed name to be specific

    data = context.user_data.get(user_id) # استخدام context.user_data
    if not data or "order_id" not in data or "product" not in data:
        await update.message.reply_text("عذراً، حدث خطأ. الرجاء المحاولة مرة أخرى أو بدء طلبية جديدة.")
        # Attempt to delete the user's message even on error
        if 'user_buy_message_id' in context.user_data[user_id]:
            try:
                await context.bot.delete_message(chat_id=update.message.chat_id, message_id=context.user_data[user_id]['user_buy_message_id'])
                del context.user_data[user_id]['user_buy_message_id']
            except Exception as e:
                logger.warning(f"Could not delete user's buy message on error in receive_buy_price: {e}")
        return ConversationHandler.END
    
    order_id, product = data["order_id"], data["product"]
    
    if order_id not in orders or product not in orders[order_id].get("products", []):
        await update.message.reply_text("عذراً، الطلب أو المنتج لم يعد موجوداً. الرجاء بدء طلبية جديدة.")
        if 'user_buy_message_id' in context.user_data[user_id]:
            try:
                await context.bot.delete_message(chat_id=update.message.chat_id, message_id=context.user_data[user_id]['user_buy_message_id'])
                del context.user_data[user_id]['user_buy_message_id']
            except Exception as e:
                logger.warning(f"Could not delete user's buy message on error in receive_buy_price: {e}")
        return ConversationHandler.END

    try:
        price = float(update.message.text.strip())
        if price < 0:
            await update.message.reply_text("سعر الشراء يجب أن يكون رقماً إيجابياً. بيش اشتريت بالضبط؟")
            # We don't delete the user's message here if the input is invalid, they might want to correct it.
            return ASK_BUY 
    except ValueError:
        await update.message.reply_text("الرجاء إدخال رقم صحيح لسعر الشراء. بيش اشتريت؟")
        # We don't delete the user's message here if the input is invalid, they might want to correct it.
        return ASK_BUY 
    
    pricing.setdefault(order_id, {}).setdefault(product, {})["buy"] = price
    save_data()

    # ****** قم بإرسال الرد أولاً (السؤال عن سعر البيع) وحفظ الـ ID الخاص بها ******
    msg = await update.message.reply_text(f"شكراً. وهسه، بيش راح تبيع *'{product}'*؟", parse_mode="Markdown")
    context.user_data[user_id]['ask_sell_message_id'] = msg.message_id
    
    # We will delete the user's buy message and the bot's ask-buy message in receive_sell_price.

    return ASK_SELL

async def receive_sell_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    
    # حفظ رسالة المستخدم الحالية (سعر البيع) لحذفها لاحقاً
    context.user_data.setdefault(user_id, {})
    context.user_data[user_id]['user_sell_message_id'] = update.message.message_id # <--- Changed name to be specific

    data = context.user_data.get(user_id) # استخدام context.user_data
    if not data or "order_id" not in data or "product" not in data:
        # Delete user message on error
        if 'user_sell_message_id' in context.user_data[user_id]:
            try:
                await context.bot.delete_message(chat_id=update.message.chat_id, message_id=context.user_data[user_id]['user_sell_message_id'])
                del context.user_data[user_id]['user_sell_message_id']
            except Exception as e:
                logger.warning(f"Could not delete user's sell message on error in receive_sell_price: {e}")
        await update.message.reply_text("عذراً، حدث خطأ. الرجاء المحاولة مرة أخرى أو بدء طلبية جديدة.")
        return ConversationHandler.END
    
    order_id, product = data["order_id"], data["product"]
    
    if order_id not in orders or product not in orders[order_id].get("products", []):
        # Delete user message on error
        if 'user_sell_message_id' in context.user_data[user_id]:
            try:
                await context.bot.delete_message(chat_id=update.message.chat_id, message_id=context.user_data[user_id]['user_sell_message_id'])
                del context.user_data[user_id]['user_sell_message_id']
            except Exception as e:
                logger.warning(f"Could not delete user's sell message on error in receive_sell_price: {e}")
        await update.message.reply_text("عذراً، الطلب أو المنتج لم يعد موجوداً. الرجاء بدء طلبية جديدة.")
        return ConversationHandler.END

    try:
        price = float(update.message.text.strip())
        if price < 0:
            await update.message.reply_text("سعر البيع يجب أن يكون رقماً إيجابياً. بيش راح تبيع بالضبط؟")
            # Don't delete message here, user needs to re-enter
            return ASK_SELL 
    except ValueError:
        await update.message.reply_text("الرجاء إدخال رقم صحيح لسعر البيع. بيش حتبيع؟")
        # Don't delete message here, user needs to re-enter
        return ASK_SELL 
    
    pricing.setdefault(order_id, {}).setdefault(product, {})["sell"] = price
    save_data()

    # ****** الآن سنقوم بحذف جميع الرسائل المتعلقة بتسعير هذا المنتج ******
    # 1. حذف رسالة سؤال الشراء من البوت
    if 'ask_buy_message_id' in context.user_data[user_id]:
        try:
            await context.bot.delete_message(chat_id=update.message.chat_id, message_id=context.user_data[user_id]['ask_buy_message_id'])
            del context.user_data[user_id]['ask_buy_message_id']
        except Exception as e:
            logger.warning(f"Could not delete 'ask buy' message ({context.user_data[user_id].get('ask_buy_message_id', 'N/A')}): {e}")
    
    # 2. حذف رسالة إدخال سعر الشراء من المستخدم
    if 'user_buy_message_id' in context.user_data[user_id]:
        try:
            await context.bot.delete_message(chat_id=update.message.chat_id, message_id=context.user_data[user_id]['user_buy_message_id'])
            del context.user_data[user_id]['user_buy_message_id']
        except Exception as e:
            logger.warning(f"Could not delete user's buy message ({context.user_data[user_id].get('user_buy_message_id', 'N/A')}): {e}")

    # 3. حذف رسالة سؤال البيع من البوت
    if 'ask_sell_message_id' in context.user_data[user_id]:
        try:
            await context.bot.delete_message(chat_id=update.message.chat_id, message_id=context.user_data[user_id]['ask_sell_message_id'])
            del context.user_data[user_id]['ask_sell_message_id']
        except Exception as e:
            logger.warning(f"Could not delete 'ask sell' message ({context.user_data[user_id].get('ask_sell_message_id', 'N/A')}): {e}")

    # 4. حذف رسالة إدخال سعر البيع من المستخدم
    if 'user_sell_message_id' in context.user_data[user_id]:
        try:
            await context.bot.delete_message(chat_id=update.message.chat_id, message_id=context.user_data[user_id]['user_sell_message_id'])
            del context.user_data[user_id]['user_sell_message_id']
        except Exception as e:
            logger.warning(f"Could not delete user's sell message ({context.user_data[user_id].get('user_sell_message_id', 'N/A')}): {e}")


    if order_id not in orders: # تحقق مرة أخرى في حال تم حذف الطلب بشكل غير متوقع
        await update.message.reply_text("عذراً، الطلب لم يعد موجوداً بعد حفظ السعر. الرجاء بدء طلبية جديدة.")
        return ConversationHandler.END

    order = orders[order_id]
    all_priced = True
    for p in order["products"]:
        if p not in pricing.get(order_id, {}) or "buy" not in pricing[order_id].get(p, {}) or "sell" not in pricing[order_id].get(p, {}):
            all_priced = False
            break
            
    if all_priced:
        context.user_data["completed_order_id"] = order_id
        
        buttons = []
        emojis = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟']
        for i in range(1, 11):
            buttons.append(InlineKeyboardButton(emojis[i-1], callback_data=f"places_{i}"))
        
        keyboard = [buttons[i:i + 5] for i in range(0, len(buttons), 5)]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text("كل المنتجات تم تسعيرها. كم محل كلفتك الطلبية؟ (اختر من الأزرار أو اكتب الرقم)", reply_markup=reply_markup)
        
        return ASK_PLACES
    else:
        await update.message.reply_text(f"تم حفظ السعر لـ *'{product}'*.", parse_mode="Markdown")
        await show_buttons(update.effective_chat.id, context, user_id, order_id)
        
        return ASK_BUY


def calculate_extra(places):
    extra_fees = {
        1: 0,
        2: 0,
        3: 1,
        4: 2,
        5: 3,
        6: 4
    }
    return extra_fees.get(places, places - 2)

async def receive_place_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global daily_profit
    
    places = None
    message_object = None 
    user_id = str(update.effective_user.id) # استخدم effective_user للحصول على ID المستخدم

    # تخزين ID رسالة المجهز هنا فقط إذا كانت رسالة نصية، وليس من كولباك
    if update.message:
        context.user_data.setdefault(user_id, {})
        context.user_data[user_id]['last_user_message_to_delete_places'] = update.message.message_id # New key for places input

    if update.callback_query:
        # هذا الجزء يتعامل مع النقر على الأزرار، لا يوجد نص من المستخدم ليتم حذفه
        query = update.callback_query
        logger.info(f"Places callback query received: {query.data}")
        await query.answer()
        if query.data.startswith("places_"):
            places = int(query.data.split("_")[1])
            message_object = query.message 
            try:
                # هذا يحذف رسالة البوت نفسها التي تحتوي على الأزرار
                await context.bot.edit_message_reply_markup(
                    chat_id=query.message.chat_id,
                    message_id=query.message.message_id,
                    reply_markup=None
                )
            except Exception as e:
                logger.warning(f"Could not remove places buttons: {e}")
                pass
        else:
            logger.error(f"Unexpected callback_query in receive_place_count: {query.data}")
            await query.edit_message_text("عذراً، حدث خطأ غير متوقع. الرجاء المحاولة مرة أخرى أو بدء طلبية جديدة.")
            return ConversationHandler.END
    elif update.message:
        message_object = update.message 
        
        try:
            places = int(message_object.text.strip())
            if places < 0:
                await message_object.reply_text("عدد المحلات يجب أن يكون رقماً موجباً. الرجاء إدخال عدد المحلات بشكل صحيح.")
                return ASK_PLACES # لا نحذف الرسالة هنا
        except ValueError:
            await message_object.reply_text("الرجاء إدخال عدد صحيح لعدد المحلات.")
            return ASK_PLACES # لا نحذف الرسالة هنا
    
    if places is None:
        logger.warning("No places count received.")
        return ConversationHandler.END

    order_id = context.user_data.get("completed_order_id")
    if not order_id or order_id not in orders:
        await message_object.reply_text("عذراً، لا توجد طلبية مكتملة لمعالجتها أو تم حذفها. الرجاء بدء طلبية جديدة.")
        return ConversationHandler.END

    order = orders[order_id]
    invoice = invoice_numbers.get(order_id, "غير معروف")
    total_buy = 0.0
    total_sell = 0.0
    
    # --- بناء فاتورة الإدارة (للمالك فقط) ---
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
    save_data()

    extra = calculate_extra(places)
    total_with_extra = total_sell + extra

    owner_invoice_details.append(f"\nالمجموع شراء: {format_float(total_buy)}")
    owner_invoice_details.append(f"المجموع بيع: {format_float(total_sell)}")
    owner_invoice_details.append(f"الربح الكلي: {format_float(net_profit)}")
    owner_invoice_details.append(f"عدد المحلات: {places} (+{format_float(extra)})")
    owner_invoice_details.append(f"السعر الكلي: {format_float(total_with_extra)}")
    
    final_owner_invoice_text = "\n".join(owner_invoice_details)
    
    # ENCODED ADMIN INVOICE FOR WHATSAPP
    encoded_owner_invoice = final_owner_invoice_text.replace(" ", "%20").replace("\n", "%0A").replace("*", "")
    whatsapp_owner_button_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("إرسال فاتورة الإدارة للواتساب", url=f"https://wa.me/{OWNER_PHONE_NUMBER}?text={encoded_owner_invoice}")]
    ])

    # إرسال فاتورة الإدارة وزر الواتساب الخاص بها إلى الخاص بالمالك (OWNER_ID)
    try:
        await context.bot.send_message(
            chat_id=OWNER_ID, # <--- يتم الإرسال إلى ID المالك فقط
            text=f"**فاتورة طلبية (الإدارة):**\n{final_owner_invoice_text}",
            parse_mode="Markdown",
            reply_markup=whatsapp_owner_button_markup # <--- إرسال زر الواتساب الخاص بمالك هنا
        )
        logger.info(f"Admin invoice and WhatsApp button sent to OWNER_ID: {OWNER_ID}")
    except Exception as e:
        logger.error(f"Could not send admin invoice to OWNER_ID {OWNER_ID}: {e}")
        # إذا لم يتمكن من إرسالها للمالك، يخبر المستخدم في المحادثة الأصلية (الكروب)
        await message_object.reply_text("عذراً، لم أتمكن من إرسال فاتورة الإدارة إلى خاصك. يرجى التأكد من أنني أستطيع مراسلتك في الخاص (قد تحتاج إلى بدء محادثة معي أولاً).")


    # --- بناء فاتورة الزبون (للكروب فقط) ---
    customer_invoice_lines = []
    customer_invoice_lines.append(f"أبو الأكبر للتوصيل")
    customer_invoice_lines.append(f"رقم الفاتورة: {invoice}")
    customer_invoice_lines.append(f"عنوان الزبون: {order['title']}")
    customer_invoice_lines.append(f"\nالمواد:")
    
    running_total_for_customer = 0.0 # مجموع البيع للزبون
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
    
    # نسخة الزبون (ستظل في المحادثة العامة في الكروب)
    await message_object.reply_text("نسخة الزبون (لإرسالها للعميل):\n" + customer_final_text, parse_mode="Markdown")

    encoded_customer_invoice = customer_final_text.replace(" ", "%20").replace("\n", "%0A").replace("*", "")

    # زر الواتساب الخاص بالزبون (سيبقى في المحادثة العامة في الكروب)
    whatsapp_customer_button_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("إرسال فاتورة الزبون للواتساب", url=f"https://wa.me/{OWNER_PHONE_NUMBER}?text={encoded_customer_invoice}")]
    ])
    await message_object.reply_text("دوس على هذه الأزرار لإرسال فاتورة الزبون عبر الواتساب:", reply_markup=whatsapp_customer_button_markup)
    
    final_actions_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("تعديل الطلب الأخير", callback_data=f"edit_last_order_{order_id}")],
        [InlineKeyboardButton("إنشاء طلب جديد", callback_data="start_new_order")]
    ])
    await message_object.reply_text("شنو تريد تسوي هسه؟", reply_markup=final_actions_keyboard)

    # حذف رسالة المستخدم بعد المعالجة بنجاح (فقط إذا كانت رسالة نصية)
    if update.message and 'last_user_message_to_delete_places' in context.user_data[user_id]:
        try:
            await context.bot.delete_message(chat_id=update.message.chat_id, message_id=context.user_data[user_id]['last_user_message_to_delete_places'])
            del context.user_data[user_id]['last_user_message_to_delete_places']
        except Exception as e:
            logger.warning(f"Could not delete user message in receive_place_count: {e}")

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

    await show_buttons(query.message.chat_id, context, user_id, order_id)
    
    return ASK_BUY

async def start_new_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
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

        save_data()
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
                CallbackQueryHandler(product_selected) 
            ],
            ASK_SELL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_sell_price),
                CallbackQueryHandler(product_selected) 
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

