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
daily_profit = 0.0
last_button_message = {}
delivery_pricing = {}

# حالات المحادثة
ASK_BUY, ASK_SELL, ASK_PLACES_COUNT = range(3)
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
            except (json.JSONDecodeError, Exception):
                pass
        return default_value

    orders.update(load_json_file(ORDERS_FILE, {}))
    pricing.update(load_json_file(PRICING_FILE, {}))
    invoice_numbers.update(load_json_file(INVOICE_NUMBERS_FILE, {}))
    daily_profit = load_json_file(DAILY_PROFIT_FILE, 0.0)
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
        return current

# تحميل البيانات الأولية
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

async def delete_message(context, chat_id, message_id):
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass

# فلتر للتمييز بين أنواع الرسائل
def is_new_order(update: Update):
    lines = [line.strip() for line in update.message.text.strip().split('\n') if line.strip()]
    return len(lines) >= 3

def is_price(update: Update):
    try:
        float(update.message.text.strip())
        return True
    except ValueError:
        return False

# Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    context.user_data[user_id] = {}
    await update.message.reply_text(
        "أهلاً بك! لإعداد طلبية:\n"
        "*السطر الأول:* عنوان الزبون\n"
        "*السطر الثاني:* رقم الزبون\n"
        "*الأسطر الباقية:* كل منتج بسطر واحد",
        parse_mode="Markdown"
    )
    return ConversationHandler.END

async def receive_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    if context.user_data.get(user_id, {}).get('in_pricing'):
        return
    
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
    return ASK_BUY

async def show_products_buttons(chat_id, context, user_id, order_id):
    order = orders[order_id]
    buttons = []
    
    for p in order["products"]:
        if p in pricing[order_id] and pricing[order_id][p].get("buy") is not None and pricing[order_id][p].get("sell") is not None:
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
    await query.answer()
    
    user_id = str(query.from_user.id)
    _, order_id, product = query.data.split('_', 2)
    
    context.user_data[user_id] = {
        "order_id": order_id,
        "product": product,
        "in_pricing": True
    }
    
    await query.edit_message_text(f"كم سعر شراء '{product}'؟")
    return ASK_BUY

async def receive_buy_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    user_data = context.user_data.get(user_id, {})
    
    if not user_data.get("in_pricing"):
        return await receive_order(update, context)
    
    try:
        price = float(update.message.text.strip())
        if price < 0:
            await update.message.reply_text("السعر يجب أن يكون موجباً")
            return ASK_BUY
        
        order_id = user_data["order_id"]
        product = user_data["product"]
        
        pricing[order_id][product]["buy"] = price
        save_data()
        
        await update.message.reply_text(f"كم سعر بيع '{product}'؟")
        return ASK_SELL
        
    except ValueError:
        await update.message.reply_text("الرجاء إدخال رقم صحيح")
        return ASK_BUY

async def receive_sell_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    user_data = context.user_data.get(user_id, {})
    
    try:
        price = float(update.message.text.strip())
        if price < 0:
            await update.message.reply_text("السعر يجب أن يكون موجباً")
            return ASK_SELL
        
        order_id = user_data["order_id"]
        product = user_data["product"]
        
        pricing[order_id][product]["sell"] = price
        save_data()
        
        # التحقق إذا كانت جميع المنتجات مسعرة
        order = orders[order_id]
        all_priced = all(
            p in pricing[order_id] and 
            "buy" in pricing[order_id][p] and 
            "sell" in pricing[order_id][p]
            for p in order["products"]
        )
        
        if all_priced:
            await ask_places_count(update.message.chat_id, context, user_id, order_id)
            return ConversationHandler.END
        else:
            await show_products_buttons(update.message.chat_id, context, user_id, order_id)
            return ConversationHandler.END
            
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

async def handle_places_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    _, order_id, count = query.data.split('_')
    orders[order_id]["places_count"] = int(count)
    save_data()
    
    await query.edit_message_text("تم حفظ عدد المحلات")
    await show_final_invoice(query.message.chat_id, context, order_id)

async def show_final_invoice(chat_id, context, order_id):
    order = orders[order_id]
    invoice_no = invoice_numbers[order_id]
    
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
        customer_msg.append(f"- {p}: {pricing[order_id][p]['sell']}")
    
    customer_msg.extend([
        "",
        f"كلفة التجهيز: {extra}",
        f"سعر التوصيل: {delivery}",
        f"المجموع الكلي: {final_total}"
    ])
    
    await context.bot.send_message(chat_id, "\n".join(customer_msg))
    
    # فاتورة الإدارة
    admin_msg = [
        f"فاتورة إدارة رقم: {invoice_no}",
        f"الزبون: {order['title']}",
        f"المنطقة: {order['region_name']}",
        "",
        "التفاصيل:"
    ]
    
    for p in order["products"]:
        admin_msg.append(
            f"- {p}: شراء {pricing[order_id][p]['buy']} | بيع {pricing[order_id][p]['sell']}"
        )
    
    profit = total_sell - total_buy + delivery
    admin_msg.extend([
        "",
        f"الربح: {profit}",
        f"المجموع النهائي: {final_total}"
    ])
    
    await context.bot.send_message(OWNER_ID, "\n".join(admin_msg))
    
    # تحديث الربح اليومي
    if not order["profit_added"]:
        global daily_profit
        daily_profit += profit
        orders[order_id]["profit_added"] = True
        save_data()

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    context.user_data[user_id] = {}
    await update.message.reply_text("تم الإلغاء")
    return ConversationHandler.END

def main():
    application = ApplicationBuilder().token(TOKEN).build()

    # Handler للطلبات الجديدة
    order_handler = MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.create(is_new_order),
        receive_order
    )

    # Conversation للتسعير
    pricing_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(product_selected, pattern=r"^price_")],
        states={
            ASK_BUY: [MessageHandler(filters.TEXT & ~filters.COMMAND & filters.create(is_price), receive_buy_price)],
            ASK_SELL: [MessageHandler(filters.TEXT & ~filters.COMMAND & filters.create(is_price), receive_sell_price)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # Handler لعدد المحلات
    places_handler = CallbackQueryHandler(handle_places_count, pattern=r"^places_")

    application.add_handler(order_handler)
    application.add_handler(pricing_conv)
    application.add_handler(places_handler)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cancel", cancel))

    application.run_polling()

if __name__ == "__main__":
    main()
