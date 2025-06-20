from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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

# تهيئة logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# مسارات الملفات
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

# متغيرات الحفظ المؤجل
save_timer = None
save_pending = False
save_lock = threading.Lock()

# تحميل البيانات
def load_data():
    global orders, pricing, invoice_numbers, daily_profit, last_button_message, delivery_pricing
    os.makedirs(DATA_DIR, exist_ok=True)

    def load_json_file(filepath, default_value):
        if os.path.exists(filepath):
            try:
                with open(filepath, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error loading {filepath}: {e}")
        return default_value

    orders = load_json_file(ORDERS_FILE, {})
    pricing = load_json_file(PRICING_FILE, {})
    invoice_numbers = load_json_file(INVOICE_NUMBERS_FILE, {})
    daily_profit = load_json_file(DAILY_PROFIT_FILE, 0.0)
    last_button_message = load_json_file(LAST_BUTTON_MESSAGE_FILE, {})
    delivery_pricing = load_json_file(DELIVERY_PRICING_FILE, {})

# حفظ البيانات
def _save_data_to_disk():
    global save_pending
    with save_lock:
        try:
            def save_to_temp(filepath, data):
                with open(filepath + ".tmp", "w") as f:
                    json.dump(data, f, indent=4)
                os.replace(filepath + ".tmp", filepath)

            save_to_temp(ORDERS_FILE, orders)
            save_to_temp(PRICING_FILE, pricing)
            save_to_temp(INVOICE_NUMBERS_FILE, invoice_numbers)
            save_to_temp(DAILY_PROFIT_FILE, daily_profit)
            save_to_temp(LAST_BUTTON_MESSAGE_FILE, last_button_message)
            save_to_temp(DELIVERY_PRICING_FILE, delivery_pricing)
        except Exception as e:
            logger.error(f"Error saving data: {e}")
        finally:
            save_pending = False

def schedule_save():
    global save_timer, save_pending
    if not save_pending:
        save_pending = True
        if save_timer:
            save_timer.cancel()
        save_timer = threading.Timer(0.5, _save_data_to_disk)
        save_timer.start()

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

# حالات المحادثة
ASK_BUY, ASK_SELL, ASK_PLACES_COUNT = range(3)
ASK_REGION_NAME, ASK_REGION_PRICE, REMOVE_REGION = range(3, 6)

# بيانات البوت
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_TELEGRAM_ID", 0))
OWNER_PHONE_NUMBER = "+9647733921468"

# دوال مساعدة
def format_float(value):
    return f"{value:g}".replace(".0", "")

def clean_phone_number(phone):
    cleaned = re.sub(r'[^0-9]', '', phone)
    if cleaned.startswith('964'):
        return '0' + cleaned[3:]
    elif cleaned.startswith('+964'):
        return '0' + cleaned[4:]
    return cleaned

def calculate_extra(places_count):
    return max(0, min(8, places_count - 2))

async def delete_message_in_background(context, chat_id, message_id):
    try:
        await asyncio.sleep(0.1)
        await context.bot.delete_message(chat_id, message_id)
    except Exception:
        pass

async def save_data_in_background(context):
    schedule_save()

# الدوال الرئيسية
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    context.user_data[user_id] = {}
    await update.message.reply_text(
        "أهلاً بك! لإعداد طلبية:\n"
        "1. عنوان الزبون\n"
        "2. رقم الزبون\n"
        "3. المنتجات (كل منتج بسطر)",
        parse_mode="Markdown"
    )
    return ConversationHandler.END

async def receive_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.effective_user.id)
        if context.user_data.get(user_id, {}).get('in_conversation'):
            return await handle_conversation_state(update, context)
        
        return await process_order(update, context, update.message)
    except Exception as e:
        logger.error(f"Error in receive_order: {e}")
        await update.message.reply_text("حدث خطأ، الرجاء المحاولة مرة أخرى")
        return ConversationHandler.END

async def process_order(update, context, message, edited=False):
    user_id = str(message.from_user.id)
    lines = [line.strip() for line in message.text.strip().split('\n') if line.strip()]
    
    if len(lines) < 3:
        if not edited:
            await message.reply_text("الرجاء إدخال العنوان، الرقم، والمنتجات")
        return

    title = lines[0]
    phone = clean_phone_number(lines[1])
    products = [p for p in lines[2:] if p]

    # تحديد المنطقة وسعر التوصيل
    region_name = "غير محددة"
    delivery_cost = 0.0
    for region, price in delivery_pricing.items():
        if region.lower() in title.lower():
            region_name = region
            delivery_cost = price
            break

    order_id = None
    if edited:
        for oid, msg_info in last_button_message.items():
            if msg_info and msg_info.get("message_id") == message.message_id:
                order_id = oid
                break

    if not order_id:
        order_id = str(uuid.uuid4())[:8]
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
        invoice_numbers[order_id] = get_invoice_number()
    else:
        old_products = set(orders[order_id].get("products", []))
        new_products = set(products)
        
        orders[order_id].update({
            "title": title,
            "customer_phone": phone,
            "products": products,
            "delivery_cost": delivery_cost,
            "region_name": region_name
        })

        for p in new_products:
            if p not in pricing.get(order_id, {}):
                pricing.setdefault(order_id, {})[p] = {}
        
        for p in old_products - new_products:
            if p in pricing.get(order_id, {}):
                del pricing[order_id][p]

    await save_data_in_background(context)
    confirmation = "تم تحديث الطلب" if order_id in orders else f"استلمت الطلب: {title}"
    await show_buttons(message.chat_id, context, user_id, order_id, confirmation)

async def show_buttons(chat_id, context, user_id, order_id, confirmation=None):
    try:
        order = orders[order_id]
        buttons = []
        for p in order["products"]:
            is_priced = p in pricing.get(order_id, {}) and 'buy' in pricing[order_id][p] and 'sell' in pricing[order_id][p]
            buttons.append([InlineKeyboardButton(
                f"{'✅ ' if is_priced else ''}{p}",
                callback_data=f"{order_id}|{p}"
            )])

        markup = InlineKeyboardMarkup(buttons)

        if last_button_message.get(order_id):
            await delete_message_in_background(
                context,
                last_button_message[order_id]["chat_id"],
                last_button_message[order_id]["message_id"]
            )

        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=f"{confirmation or ''}\n\nاختر منتجاً لتسعيره:",
            reply_markup=markup,
            parse_mode="Markdown"
        )
        last_button_message[order_id] = {"chat_id": chat_id, "message_id": msg.message_id}
    except Exception as e:
        logger.error(f"Error in show_buttons: {e}")
        await context.bot.send_message(chat_id, "حدث خطأ في عرض الأزرار")

async def show_final_options(chat_id, context, user_id, order_id, message_prefix=None):
    try:
        global daily_profit
        order = orders[order_id]
        invoice = invoice_numbers.get(order_id, "غير معروف")
        
        # إنشاء فاتورة الزبون
        customer_lines = [
            "**فاتورة طلبية**",
            f"رقم الفاتورة: {invoice}",
            f"العنوان: {order['title']}",
            f"رقم الزبون: {order.get('customer_phone', 'غير متوفر')}",
            f"المنطقة: {order.get('region_name', 'غير محددة')}",
            "\n*المنتجات:*"
        ]
        
        total_sell = 0
        for p in order["products"]:
            if p in pricing.get(order_id, {}) and 'sell' in pricing[order_id][p]:
                sell = pricing[order_id][p]['sell']
                customer_lines.append(f"- {p}: {format_float(sell)}")
                total_sell += sell
            else:
                customer_lines.append(f"- {p}: (لم يتم التسعير)")
        
        extra_cost = calculate_extra(order.get("places_count", 0))
        delivery_cost = order.get("delivery_cost", 0)
        final_total = total_sell + extra_cost + delivery_cost
        
        customer_lines.extend([
            f"\n*المجموع:* {format_float(total_sell)}",
            f"كلفة التجهيز: {format_float(extra_cost)}",
            f"سعر التوصيل: {format_float(delivery_cost)}",
            f"*المجموع النهائي:* {format_float(final_total)}"
        ])
        
        customer_invoice = "\n".join(customer_lines)

        # محاولة إرسال الفاتورة في الكروب
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=customer_invoice,
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to send invoice to group {chat_id}: {e}")
            await context.bot.send_message(
                chat_id=chat_id,
                text="⚠️ تم إنشاء الفاتورة ولكن حدث خطأ في عرضها بالمجموعة"
            )
            # إرسال نسخة للخاص
            await context.bot.send_message(
                chat_id=user_id,
                text=f"نسخة من الفاتورة:\n\n{customer_invoice}",
                parse_mode="Markdown"
            )

        # إنشاء فاتورة الإدارة
        admin_lines = [
            "**فاتورة الإدارة**",
            f"رقم الفاتورة: {invoice}",
            f"العنوان: {order['title']}",
            f"المنطقة: {order.get('region_name', 'غير محددة')}",
            f"سعر التوصيل: {format_float(delivery_cost)}",
            "\n*تفاصيل الأسعار:*"
        ]
        
        total_buy = 0
        total_sell = 0
        for p in order["products"]:
            if p in pricing.get(order_id, {}) and 'buy' in pricing[order_id][p] and 'sell' in pricing[order_id][p]:
                buy = pricing[order_id][p]['buy']
                sell = pricing[order_id][p]['sell']
                admin_lines.append(f"- {p}: شراء {format_float(buy)} | بيع {format_float(sell)} | ربح {format_float(sell - buy)}")
                total_buy += buy
                total_sell += sell
        
        net_profit = total_sell - total_buy + delivery_cost
        admin_lines.extend([
            f"\n*المجموع شراء:* {format_float(total_buy)}",
            f"*المجموع بيع:* {format_float(total_sell)}",
            f"*الربح الصافي:* {format_float(net_profit)}"
        ])
        
        admin_invoice = "\n".join(admin_lines)

        # إرسال فاتورة الإدارة
        await context.bot.send_message(
            chat_id=OWNER_ID,
            text=admin_invoice,
            parse_mode="Markdown"
        )

        # إرسال أزرار التحكم
        keyboard = [
            [InlineKeyboardButton("تعديل الأسعار", callback_data=f"edit_prices_{order_id}")],
            [InlineKeyboardButton("إنشاء طلب جديد", callback_data="start_new_order")]
        ]
        await context.bot.send_message(
            chat_id=chat_id,
            text="اختر الإجراء التالي:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
            
    except Exception as e:
        logger.error(f"Error in show_final_options: {e}")
        await context.bot.send_message(chat_id, "حدث خطأ في عرض الخيارات النهائية")

# [يتبع باقي الدوال بنفس النسق مع الحفاظ على جميع الوظائف]

def main():
    if not TOKEN:
        raise ValueError("يجب تعيين TELEGRAM_BOT_TOKEN")
    
    app = ApplicationBuilder().token(TOKEN).build()
    
    # معالجة الطلبات
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_order)
        ],
        states={
            ASK_BUY: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_buy_price)],
            ASK_SELL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_sell_price)],
            ASK_PLACES_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_places_count_data)]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )
    
    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(product_selected, pattern=r"^[^_]+\|[^_]+$"))
    app.add_handler(CallbackQueryHandler(handle_places_count_data, pattern=r"^places_data_.*"))
    app.add_handler(CallbackQueryHandler(edit_prices, pattern=r"^edit_prices_.*"))
    app.add_handler(CallbackQueryHandler(start_new_order_callback, pattern=r"^start_new_order$"))
    
    # الأوامر الإدارية
    app.add_handler(CommandHandler("profit", show_profit))
    app.add_handler(CommandHandler("reset_all", reset_all))
    app.add_handler(CommandHandler("report", show_report))
    app.add_handler(CommandHandler("list_regions", list_regions))
    app.add_handler(CommandHandler("add_region_price", add_region_price))
    app.add_handler(CommandHandler("remove_region", remove_region_start))
    
    app.add_handler(CallbackQueryHandler(confirm_reset, pattern=r"^(confirm_reset|cancel_reset)$"))
    app.add_handler(CallbackQueryHandler(remove_region_confirm, pattern=r"^remove_region_.*"))
    
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
