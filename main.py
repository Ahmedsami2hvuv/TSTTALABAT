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
import re
import urllib.parse

# تفعيل الـ logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# المسارات والملفات
DATA_DIR = "/mnt/data/"
ORDERS_FILE = os.path.join(DATA_DIR, "orders.json")
PRICING_FILE = os.path.join(DATA_DIR, "pricing.json")
INVOICE_NUMBERS_FILE = os.path.join(DATA_DIR, "invoice_numbers.json")
DAILY_PROFIT_FILE = os.path.join(DATA_DIR, "daily_profit.json")
COUNTER_FILE = os.path.join(DATA_DIR, "invoice_counter.txt")
LAST_BUTTON_MESSAGE_FILE = os.path.join(DATA_DIR, "last_button_message.json")
DELIVERY_PRICING_FILE = os.path.join(DATA_DIR, "delivery_pricing.json")

# المتغيرات العامة
orders = {}
pricing = {}
invoice_numbers = {}
daily_profit = 0.0 # سيتم تحميله من الملف
last_button_message = {}
delivery_pricing = {}

# حالات المحادثة لضبط أسعار التوصيل
ASK_REGION_NAME, ASK_REGION_PRICE, REMOVE_REGION = range(3, 6)

# التوكن ومعرف المالك
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_TELEGRAM_ID")) 
OWNER_PHONE_NUMBER = "+9647733921468"

# تحميل البيانات
def load_data():
    global orders, pricing, invoice_numbers, daily_profit, last_button_message, delivery_pricing
    os.makedirs(DATA_DIR, exist_ok=True)

    def load_json_file(filepath, default_value):
        if os.path.exists(filepath):
            try:
                with open(filepath, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, Exception) as e:
                logger.error(f"Failed to load {filepath}: {e}")
        return default_value

    orders.update(load_json_file(ORDERS_FILE, {}))
    pricing.update(load_json_file(PRICING_FILE, {}))
    invoice_numbers.update(load_json_file(INVOICE_NUMBERS_FILE, {}))
    daily_profit = load_json_file(DAILY_PROFIT_FILE, 0.0) # تحميل الربح اليومي
    last_button_message.update(load_json_file(LAST_BUTTON_MESSAGE_FILE, {}))
    delivery_pricing.update(load_json_file(DELIVERY_PRICING_FILE, {}))

# حفظ البيانات
def save_data():
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(ORDERS_FILE, "w") as f:
        json.dump(orders, f, indent=4)
    with open(PRICING_FILE, "w") as f:
        json.dump(pricing, f, indent=4)
    with open(INVOICE_NUMBERS_FILE, "w") as f:
        json.dump(invoice_numbers, f, indent=4)
    with open(DAILY_PROFIT_FILE, "w") as f:
        json.dump(daily_profit, f, indent=4)
    with open(LAST_BUTTON_MESSAGE_FILE, "w") as f:
        json.dump(last_button_message, f, indent=4)
    with open(DELIVERY_PRICING_FILE, "w") as f:
        json.dump(delivery_pricing, f, indent=4)

# تهيئة عداد الفواتير
if not os.path.exists(COUNTER_FILE):
    with open(COUNTER_FILE, "w") as f:
        f.write("1")

def get_invoice_number():
    with open(COUNTER_FILE, "r+") as f:
        current = int(f.read().strip())
        f.seek(0)
        f.write(str(current + 1))
        f.truncate() # لضمان عدم وجود بيانات زائدة
        return current

# تحميل البيانات الأولية عند بدء تشغيل البوت
load_data()

# الدوال المساعدة
def clean_phone_number(phone):
    cleaned = re.sub(r'[^0-9]', '', phone)
    if cleaned.startswith('964'):
        return '0' + cleaned[3:]
    elif cleaned.startswith('+964'):
        return '0' + cleaned[4:]
    return cleaned

def format_float(value):
    formatted = f"{value:g}"
    return formatted[:-2] if formatted.endswith(".0") else formatted

def calculate_extra(places):
    return min(8, max(0, places - 2)) if places > 2 else 0

async def delete_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logger.warning(f"Could not delete message {message_id} in chat {chat_id}: {e}")

# فلتر للتمييز بين أنواع الرسائل
def is_new_order_message(update: Update):
    if not update.message or not update.message.text:
        return False
    text = update.message.text.strip()
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    
    # يجب ألا يبدأ الأمر بعلامة / لكي لا يتعارض مع الـ CommandHandler
    if text.startswith('/'):
        return False
        
    return len(lines) >= 3 and re.match(r'^[\s\d\+]+$', lines[1]) # السطر الثاني رقم هاتف

def is_price_input(update: Update):
    if not update.message or not update.message.text:
        return False
    try:
        float(update.message.text.strip())
        return True
    except ValueError:
        return False

# Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    # مسح حالة المستخدم عند بدء أمر جديد
    if user_id in context.user_data:
        context.user_data.pop(user_id)
    await update.message.reply_text(
        "أهلاً بك! لإعداد طلبية:\n"
        "*السطر الأول:* عنوان الزبون\n"
        "*السطر الثاني:* رقم الزبون\n"
        "*الأسطر الباقية:* كل منتج بسطر واحد",
        parse_mode="Markdown"
    )

async def receive_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    # للتأكد من أننا لسنا في سياق تسعير منتج أو ضبط أسعار توصيل
    if context.user_data.get(user_id, {}).get('in_pricing') or \
       context.user_data.get(user_id, {}).get('in_delivery_price_setup'):
        return ConversationHandler.END # إنهاء المحادثة الحالية إن وجدت

    lines = [line.strip() for line in update.message.text.strip().split('\n') if line.strip()]
    if len(lines) < 3:
        await update.message.reply_text(
            "الرجاء التأكد من كتابة:\n"
            "1. العنوان في السطر الأول\n"
            "2. رقم الهاتف في السطر الثاني\n"
            "3. المنتجات في الأسطر التالية"
        )
        return
    
    title = lines[0]
    phone = clean_phone_number(lines[1])
    products = [p.strip() for p in lines[2:] if p.strip()]
    
    order_id = str(uuid.uuid4())[:8]
    invoice_no = get_invoice_number()
    
    # تحديد المنطقة
    region_name = "غير محددة"
    delivery_cost = 0.0
    title_lower = title.lower()
    for region, price in delivery_pricing.items():
        if region.lower() in title_lower:
            region_name = region
            delivery_cost = price
            break
    
    orders[order_id] = {
        "user_id": user_id,
        "title": title,
        "customer_phone": phone,
        "products": products,
        "places_count": 0,
        "delivery_cost": delivery_cost,
        "region_name": region_name,
        "profit_added": False
    }
    
    pricing[order_id] = {p: {} for p in products}
    invoice_numbers[order_id] = invoice_no
    
    save_data()
    
    await update.message.reply_text(f"تم استلام الطلبية ({len(products)} منتجات)")
    await show_products_buttons(update.message.chat_id, context, user_id, order_id)
    # نرجع ASK_BUY عشان يبقى في حالة محادثة التسعير
    context.user_data[user_id]['state'] = ASK_BUY 
    return ASK_BUY

async def show_products_buttons(chat_id, context, user_id, order_id):
    order = orders.get(order_id)
    if not order:
        await context.bot.send_message(chat_id, "عذراً، لم يتم العثور على الطلبية.")
        return
    
    buttons = []
    
    for p in order["products"]:
        # إذا تم تسعير المنتج، نضع علامة صح
        if pricing.get(order_id, {}).get(p, {}).get("buy") is not None and \
           pricing.get(order_id, {}).get(p, {}).get("sell") is not None:
            buttons.append([InlineKeyboardButton(f"✅ {p}", callback_data=f"price_{order_id}_{p}")])
        else:
            buttons.append([InlineKeyboardButton(p, callback_data=f"price_{order_id}_{p}")])
    
    markup = InlineKeyboardMarkup(buttons)
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text="اختر المنتج لتسعيره:",
        reply_markup=markup
    )
    last_button_message[order_id] = {"chat_id": chat_id, "message_id": msg.message_id}
    save_data()

async def product_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer() # يجب الرد على الـ CallbackQuery
    
    user_id = str(query.from_user.id)
    _, order_id, product = query.data.split('_', 2)
    
    context.user_data[user_id] = {
        "order_id": order_id,
        "product": product,
        "in_pricing": True, # تحديد أن المستخدم في عملية تسعير
        "state": ASK_BUY # لتحديد حالة المحادثة
    }
    
    await query.edit_message_text(f"كم سعر شراء '{product}'؟")
    return ASK_BUY

async def receive_buy_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    user_data = context.user_data.get(user_id, {})
    
    if not user_data.get("in_pricing") or user_data.get("state") != ASK_BUY:
        # إذا لم يكن المستخدم في عملية تسعير أو الحالة غير صحيحة، نعود
        await update.message.reply_text("الرجاء البدء بتسعير منتج أولاً أو أكمل الطلبية.")
        return ConversationHandler.END # ننهي المحادثة هنا لتجنب المشاكل
    
    try:
        price = float(update.message.text.strip())
        if price < 0:
            await update.message.reply_text("السعر يجب أن يكون موجباً")
            return ASK_BUY # نرجع لنفس الحالة لإعادة الإدخال
        
        order_id = user_data["order_id"]
        product = user_data["product"]
        
        pricing[order_id][product]["buy"] = price
        save_data()
        
        await update.message.reply_text(f"كم سعر بيع '{product}'؟")
        context.user_data[user_id]["state"] = ASK_SELL # تغيير الحالة لسعر البيع
        return ASK_SELL
        
    except ValueError:
        await update.message.reply_text("الرجاء إدخال رقم صحيح")
        return ASK_BUY # نرجع لنفس الحالة لإعادة الإدخال

async def receive_sell_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    user_data = context.user_data.get(user_id, {})
    
    if not user_data.get("in_pricing") or user_data.get("state") != ASK_SELL:
        await update.message.reply_text("الرجاء البدء بتسعير منتج أولاً أو أكمل الطلبية.")
        return ConversationHandler.END
    
    try:
        price = float(update.message.text.strip())
        if price < 0:
            await update.message.reply_text("السعر يجب أن يكون موجباً")
            return ASK_SELL
        
        order_id = user_data["order_id"]
        product = user_data["product"]
        
        pricing[order_id][product]["sell"] = price
        save_data()
        
        # حذف حالة المستخدم بعد إتمام التسعير للمنتج
        context.user_data[user_id].pop("in_pricing", None)
        context.user_data[user_id].pop("state", None)
        
        # التحقق إذا كانت جميع المنتجات مسعرة
        order = orders.get(order_id)
        if not order:
            await update.message.reply_text("عذراً، الطلبية غير موجودة.")
            return ConversationHandler.END

        all_priced = True
        for p in order["products"]:
            if not (pricing.get(order_id, {}).get(p, {}).get("buy") is not None and 
                    pricing.get(order_id, {}).get(p, {}).get("sell") is not None):
                all_priced = False
                break
        
        # حذف رسالة الأزرار القديمة لتجنب تكرارها
        if order_id in last_button_message:
            msg_info = last_button_message.pop(order_id)
            save_data() # حفظ التغيير بعد حذف الرسالة من last_button_message
            await delete_message(context, msg_info["chat_id"], msg_info["message_id"])

        if all_priced:
            await ask_places_count(update.message.chat_id, context, user_id, order_id)
            return ConversationHandler.END # ننهي المحادثة
        else:
            await show_products_buttons(update.message.chat_id, context, user_id, order_id)
            return ConversationHandler.END # ننهي المحادثة هنا ونترك الـ callback لإعادة بدء التسعير
            
    except ValueError:
        await update.message.reply_text("الرجاء إدخال رقم صحيح")
        return ASK_SELL

async def ask_places_count(chat_id, context, user_id, order_id):
    buttons = [
        [InlineKeyboardButton(str(i), callback_data=f"places_{order_id}_{i}") 
        for i in range(1, 6)]
    ]
    markup = InlineKeyboardMarkup(buttons)
    await context.bot.send_message(
        chat_id=chat_id,
        text="كم محل كلفتك الطلبية؟",
        reply_markup=markup
    )
    # نخزن order_id في user_data لتحديد الطلبية عند استلام الجواب
    context.user_data[user_id] = {"order_id": order_id, "state": ASK_PLACES_COUNT}

async def handle_places_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    _, order_id, count = query.data.split('_')
    
    if order_id not in orders:
        await query.edit_message_text("عذراً، لم يتم العثور على الطلبية.")
        return ConversationHandler.END

    orders[order_id]["places_count"] = int(count)
    save_data()
    
    await query.edit_message_text("تم حفظ عدد المحلات. جارٍ إعداد الفاتورة...")
    
    # حذف رسالة الأزرار بعد اختيار عدد المحلات
    try:
        await delete_message(context, query.message.chat_id, query.message.message_id)
    except Exception as e:
        logger.warning(f"Could not delete places count message: {e}")

    # مسح حالة المستخدم بعد إتمام العملية
    if user_id in context.user_data:
        context.user_data.pop(user_id)
        
    await show_final_invoice(query.message.chat_id, context, order_id)
    return ConversationHandler.END

async def show_final_invoice(chat_id, context, order_id):
    try:
        order = orders.get(order_id)
        if not order:
            await context.bot.send_message(chat_id, "عذراً، حدث خطأ أثناء عرض الفاتورة النهائية. الرجاء بدء طلبية جديدة.")
            logger.error(f"Order {order_id} not found when trying to show final invoice.")
            return

        invoice_no = invoice_numbers.get(order_id)
        if not invoice_no:
            await context.bot.send_message(chat_id, "عذراً، حدث خطأ أثناء عرض الفاتورة النهائية. الرجاء بدء طلبية جديدة.")
            logger.error(f"Invoice number for order {order_id} not found.")
            return
        
        # حساب المجموع
        total_buy = sum(pricing[order_id][p]["buy"] for p in order["products"])
        total_sell = sum(pricing[order_id][p]["sell"] for p in order["products"])
        extra = calculate_extra(order["places_count"])
        delivery = order["delivery_cost"]
        final_total = total_sell + extra + delivery
        
        # فاتورة الزبون
        customer_msg = [
            f"فاتورة رقم: {invoice_no}",
            f"العنوان: {order['title']}",
            f"رقم الهاتف: {order['customer_phone']}",
            "",
            "المنتجات:"
        ]
        
        for p in order["products"]:
            customer_msg.append(f"- {p}: {format_float(pricing[order_id][p]['sell'])}")
        
        customer_msg.extend([
            "",
            f"كلفة التجهيز: {format_float(extra)}",
            f"سعر التوصيل: {format_float(delivery)}",
            f"المجموع الكلي: {format_float(final_total)}"
        ])
        
        await context.bot.send_message(chat_id, "\n".join(customer_msg))
        
        # فاتورة الإدارة
        admin_msg = [
            f"فاتورة إدارة رقم: {invoice_no}",
            f"الزبون: {order['title']}",
            f"المنطقة: {order['region_name']}",
            f"عدد المحلات: {order['places_count']}",
            "",
            "التفاصيل:"
        ]
        
        for p in order["products"]:
            admin_msg.append(
                f"- {p}: شراء {format_float(pricing[order_id][p]['buy'])} | بيع {format_float(pricing[order_id][p]['sell'])}"
            )
        
        profit = total_sell - total_buy - extra + delivery # تم تعديل حساب الربح هنا ليشمل الـ extra و delivery
        admin_msg.extend([
            "",
            f"الربح: {format_float(profit)}",
            f"المجموع النهائي: {format_float(final_total)}"
        ])
        
        await context.bot.send_message(OWNER_ID, "\n".join(admin_msg))
        
        # تحديث الربح اليومي
        if not order["profit_added"]:
            global daily_profit
            daily_profit += profit
            orders[order_id]["profit_added"] = True
            save_data()

    except Exception as e:
        logger.error(f"Error in show_final_invoice for order {order_id}: {e}", exc_info=True)
        await context.bot.send_message(chat_id, "عذراً، حدث خطأ أثناء إعداد أو إرسال الفاتورة. الرجاء التواصل مع الدعم الفني.")
        # نرسل رسالة خطأ للمالك أيضاً
        await context.bot.send_message(OWNER_ID, f"حدث خطأ فني أثناء إعداد الفاتورة رقم {invoice_numbers.get(order_id, 'غير معروف')}:\n{e}")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id in context.user_data:
        # إزالة البيانات الخاصة بالمستخدم لإنهاء أي محادثة جارية
        context.user_data.pop(user_id) 
    await update.message.reply_text("تم الإلغاء. يمكنك البدء بطلبية جديدة في أي وقت.")
    return ConversationHandler.END

# Handlers لأوامر الإدارة
async def daily_profit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("أنت غير مخول لاستخدام هذا الأمر.")
        return
    await update.message.reply_text(f"الربح اليومي الحالي هو: {format_float(daily_profit)} دينار عراقي.")

async def reset_daily_profit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("أنت غير مخول لاستخدام هذا الأمر.")
        return
    global daily_profit
    daily_profit = 0.0
    save_data()
    await update.message.reply_text("تم تصفير الربح اليومي بنجاح.")

# --- أوامر إدارة مناطق التوصيل ---
async def add_delivery_region_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("أنت غير مخول لاستخدام هذا الأمر.")
        return ConversationHandler.END
    context.user_data[str(update.effective_user.id)] = {"in_delivery_price_setup": True, "state": ASK_REGION_NAME}
    await update.message.reply_text("الرجاء إدخال اسم المنطقة الجديدة:")
    return ASK_REGION_NAME

async def receive_region_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    context.user_data[user_id]['region_name'] = update.message.text.strip()
    await update.message.reply_text(f"الرجاء إدخال سعر توصيل لـ '{context.user_data[user_id]['region_name']}':")
    context.user_data[user_id]['state'] = ASK_REGION_PRICE
    return ASK_REGION_PRICE

async def receive_region_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    try:
        price = float(update.message.text.strip())
        if price < 0:
            await update.message.reply_text("السعر يجب أن يكون موجباً.")
            return ASK_REGION_PRICE
        
        region_name = context.user_data[user_id]['region_name']
        delivery_pricing[region_name] = price
        save_data()
        await update.message.reply_text(f"تم إضافة/تحديث سعر توصيل '{region_name}' إلى {format_float(price)} دينار.")
        context.user_data.pop(user_id, None) # مسح حالة المستخدم
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("الرجاء إدخال رقم صحيح للسعر.")
        return ASK_REGION_PRICE

async def remove_delivery_region_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("أنت غير مخول لاستخدام هذا الأمر.")
        return ConversationHandler.END
    
    if not delivery_pricing:
        await update.message.reply_text("لا توجد مناطق توصيل معرفة حالياً.")
        return ConversationHandler.END

    buttons = []
    for region in delivery_pricing.keys():
        buttons.append([InlineKeyboardButton(region, callback_data=f"remove_region_{region}")])
    
    markup = InlineKeyboardMarkup(buttons)
    await update.message.reply_text("اختر المنطقة التي تريد حذفها:", reply_markup=markup)
    context.user_data[str(update.effective_user.id)] = {"in_delivery_price_setup": True, "state": REMOVE_REGION}
    return REMOVE_REGION

async def confirm_remove_region(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    if not context.user_data.get(user_id, {}).get("in_delivery_price_setup") or \
       context.user_data.get(user_id, {}).get("state") != REMOVE_REGION:
        await query.edit_message_text("الرجاء البدء بأمر حذف المنطقة أولاً.")
        return ConversationHandler.END

    _, region_name = query.data.split('remove_region_')
    
    if region_name in delivery_pricing:
        del delivery_pricing[region_name]
        save_data()
        await query.edit_message_text(f"تم حذف منطقة '{region_name}' بنجاح.")
    else:
        await query.edit_message_text("المنطقة غير موجودة.")
    
    context.user_data.pop(user_id, None)
    return ConversationHandler.END

async def show_delivery_prices_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("أنت غير مخول لاستخدام هذا الأمر.")
        return
    
    if not delivery_pricing:
        await update.message.reply_text("لا توجد مناطق توصيل معرفة حالياً.")
        return

    msg = ["أسعار التوصيل للمناطق:"]
    for region, price in delivery_pricing.items():
        msg.append(f"- {region}: {format_float(price)} دينار")
    
    await update.message.reply_text("\n".join(msg))

async def handle_unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # هذا الـ handler سيستقبل أي رسالة تبدأ بـ / ولكنها ليست أمر معرف
    if update.message.text.startswith('/'):
        await update.message.reply_text("عذراً، هذا الأمر غير معروف. الرجاء التأكد من الأمر المدخل.")
    # لا داعي لإضافة أي منطق هنا للرسائل التي لا تبدأ بـ /
    # لأن الـ filters.create(is_new_order_message) سيتولى التعامل معها

def main():
    application = ApplicationBuilder().token(TOKEN).build()

    # ConversationHandler للتسعير (نطاق أضيق)
    # يجب أن يكون له نقطة دخول خاصة به وليس من خلال MessageHandler عام
    pricing_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(product_selected, pattern=r"^price_")],
        states={
            ASK_BUY: [MessageHandler(filters.TEXT & ~filters.COMMAND & filters.create(is_price_input), receive_buy_price)],
            ASK_SELL: [MessageHandler(filters.TEXT & ~filters.COMMAND & filters.create(is_price_input), receive_sell_price)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        map_to_parent={
            ConversationHandler.END: ConversationHandler.END # لإنهاء المحادثة الأصلية إذا تم الانتهاء من التسعير
        }
    )

    # ConversationHandler لإدارة أسعار التوصيل (نطاق أضيق)
    delivery_price_conv = ConversationHandler(
        entry_points=[CommandHandler("add_region", add_delivery_region_start)],
        states={
            ASK_REGION_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_region_name)],
            ASK_REGION_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND & filters.create(is_price_input), receive_region_price)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        map_to_parent={
            ConversationHandler.END: ConversationHandler.END
        }
    )

    remove_region_conv = ConversationHandler(
        entry_points=[CommandHandler("remove_region", remove_delivery_region_start)],
        states={
            REMOVE_REGION: [CallbackQueryHandler(confirm_remove_region, pattern=r"^remove_region_")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        map_to_parent={
            ConversationHandler.END: ConversationHandler.END
        }
    )

    # إضافة الـ Handlers بالترتيب الصحيح (الأوامر قبل الرسائل النصية العامة)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("daily_profit", daily_profit_command))
    application.add_handler(CommandHandler("reset_profit", reset_daily_profit_command))
    application.add_handler(CommandHandler("show_regions", show_delivery_prices_command))

    # إضافة ConversationHandlers
    application.add_handler(pricing_conv)
    application.add_handler(delivery_price_conv)
    application.add_handler(remove_region_conv)

    # Handler لعدد المحلات (كولباك فقط)
    application.add_handler(CallbackQueryHandler(handle_places_count, pattern=r"^places_"))
    
    # Handler للطلبات الجديدة (رسائل نصية تستوفي الشروط)
    # هذا الـ handler يجب أن يكون بعد الـ CommandHandlers والـ ConversationHandlers اللي تبدأ بـ CommandHandlers
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.create(is_new_order_message),
        receive_order
    ))

    # Handler لأي أمر غير معروف
    application.add_handler(MessageHandler(filters.COMMAND, handle_unknown_command))

    logger.info("Bot started polling...")
    application.run_polling()

if __name__ == "__main__":
    main()
