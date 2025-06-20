from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
)
import uuid
import os
import json
import logging
import asyncio

# تفعيل الـ logging للحصول على تفاصيل الأخطاء والعمليات
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
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
AREAS_FILE = os.path.join(DATA_DIR, "areas.json")  # ملف جديد لحفظ المناطق وأسعارها

# تهيئة المتغيرات العامة
orders = {}
pricing = {}
invoice_numbers = {}
daily_profit = 0.0
areas = {
    "المنطقة الجديدة": 5,
    "المعلمين": 6,
    "الكرادة": 7,
    "البياع": 8,
    "شارع فلسطين": 4,
    "المرادية": 5,
}
save_pending = False
save_lock = asyncio.Lock()

# تحميل بيانات من ملف JSON
def load_json_file(filename, default_data, log_name="data"):
    try:
        if os.path.exists(filename):
            with open(filename, "r") as f:
                data = json.load(f)
                logger.info(f"Loaded {log_name} from {filename}")
                return data
        else:
            logger.warning(f"{filename} not found, initializing to default.")
    except Exception as e:
        logger.error(f"Error loading {filename}: {e}", exc_info=True)
    return default_data.copy()


# حفظ البيانات إلى الملفات
async def save_data_in_background(context: ContextTypes.DEFAULT_TYPE):
    global save_pending
    async with save_lock:
        if not save_pending:
            save_pending = True
            threading.Thread(target=_save_data_to_disk).start()
            await asyncio.sleep(0.5)  # انتظار قصير قبل إعادة تمكين الحفظ
            save_pending = False


def _save_data_to_disk():
    global save_pending
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

            with open(AREAS_FILE + ".tmp", "w") as f:
                json.dump(areas, f, indent=4)
            os.replace(AREAS_FILE + ".tmp", AREAS_FILE)

            with open(LAST_BUTTON_MESSAGE_FILE + ".tmp", "w") as f:
                json.dump({}, f)  # لا يتم استخدام هذا الملف حالياً
            os.replace(LAST_BUTTON_MESSAGE_FILE + ".tmp", LAST_BUTTON_MESSAGE_FILE)

            logger.info("Data saved successfully.")
        except Exception as e:
            logger.error(f"Error saving data: {e}", exc_info=True)


# تهيئة البيانات عند التشغيل
def initialize_data():
    global orders, pricing, invoice_numbers, daily_profit, areas
    orders = load_json_file(ORDERS_FILE, {}, "orders")
    pricing = load_json_file(PRICING_FILE, {}, "pricing")
    invoice_numbers = load_json_file(INVOICE_NUMBERS_FILE, {}, "invoice_numbers")
    daily_profit = float(load_json_file(DAILY_PROFIT_FILE, 0.0, "daily_profit"))
    areas_temp = load_json_file(AREAS_FILE, areas, "areas")
    areas.clear()
    areas.update({str(k): v for k, v in areas_temp.items()})
    logger.info(
        f"Initial load complete. Orders: {len(orders)}, Pricing entries: {len(pricing)}, Daily Profit: {daily_profit}, Areas: {len(areas)}"
    )


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
        await asyncio.sleep(0.1)  # زيادة التأخير لضمان ظهور الرسالة الجديدة
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logger.warning(f"Could not delete message {message_id} in chat {chat_id}: {e}")


# دالة لعرض الأرباح اليومية
async def show_profit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = str(update.effective_user.id)

    # التأكد من أن الأمر من المالك فقط
    if user_id != str(OWNER_ID):
        await update.message.reply_text("عذراً، هذا الأمر متاح للمالك فقط.")
        return

    global daily_profit
    await update.message.reply_text(f"💰 الأرباح اليومية حتى الآن: {daily_profit} دينار")


# دالة لبدء الطلبية
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    chat_id = update.effective_chat.id

    # تنظيف بيانات المستخدم القديمة
    if user_id in context.user_data:
        context.user_data[user_id].clear()
        logger.info(
            f"[{chat_id}] Cleaned up order-specific user_data for user {user_id} on /start command. User data after clearing: {json.dumps(context.user_data.get(user_id, {}), indent=2)}"
        )

    await update.message.reply_text(
        "أهلاً بك يا أبا الأكبر! لإعداد طلبية، دز الطلبية كلها برسالة واحدة.\n*السطر الأول:* عنوان الزبون (يمكن أن يبدأ باسم المنطقة).\n*الأسطر الباقية:* كل منتج بسطر واحد.",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


# دالة لتلقي الطلبية
async def receive_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = str(update.effective_user.id)

    logger.info(
        f"[{chat_id}] Processing order from: {update.effective_user.id} - Message ID: {update.message.message_id}. User data: {json.dumps(context.user_data.get(str(update.effective_user.id), {}), indent=2)}"
    )

    text = update.message.text.strip().splitlines()
    if len(text) < 2:
        await update.message.reply_text("الرجاء إدخال الطلبية كاملة كما هو مطلوب.")
        return ConversationHandler.END

    title = text[0].strip()
    products = [line.strip() for line in text[1:] if line.strip()]

    order_id = str(uuid.uuid4())
    orders[order_id] = {"title": title, "products": products, "customer_messages": []}

    logger.info(f"[{chat_id}] Created new order {order_id} for user {user_id}")

    # إنشاء رسالة ترحيبية مع خيارات
    keyboard = [
        [InlineKeyboardButton("➕ إضافة منتج", callback_data=f"add_product_{order_id}")],
        [InlineKeyboardButton("✏️ تسعير منتج", callback_data=f"price_product_{order_id}")],
        [InlineKeyboardButton("🔢 تحديد عدد المحلات", callback_data=f"request_places_{order_id}")],
        [InlineKeyboardButton("🖨️ عرض الفاتورة", callback_data=f"show_invoice_{order_id}")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("اختر ما تريد من الخيارات:", reply_markup=reply_markup)

    return ConversationHandler.END


# دالة لعرض الفاتورة النهائية
async def show_final_options(chat_id, context, user_id, order_id, message_prefix=None):
    global daily_profit
    if order_id not in orders:
        logger.warning(f"[{chat_id}] Attempted to show final options for non-existent order_id: {order_id}")
        await context.bot.send_message(chat_id=chat_id, text="عذراً، الطلب الذي تحاول الوصول إليه غير موجود أو تم حذفه. الرجاء بدء طلبية جديدة.")
        return

    order = orders[order_id]
    invoice = invoice_numbers.get(order_id, "غير معروف")

    total_buy = 0.0
    total_sell = 0.0
    current_places = 0
    extra_cost = 0
    delivery_cost = 0

    if order_id in pricing:
        for product, prices in pricing[order_id].items():
            if "buy" in prices and "sell" in prices:
                total_buy += prices["buy"]
                total_sell += prices["sell"]

    if "places" in order:
        current_places = order["places"]
        extra_cost = calculate_extra(current_places)

    if "delivery_area" in order and order["delivery_area"] in areas:
        delivery_cost = areas[order["delivery_area"]]

    net_profit = total_sell - total_buy
    final_total = total_sell + extra_cost + delivery_cost

    daily_profit += net_profit
    await save_data_in_background(context)

    # رسالة الزبون
    customer_invoice_lines = []
    customer_invoice_lines.append("*فاتورة الزبون*")
    customer_invoice_lines.append(f"رقم الفاتورة: {invoice}")
    customer_invoice_lines.append(f"عنوان الزبون: {order['title']}")
    running_total_for_customer = 0.0
    for p in order["products"]:
        if p in pricing.get(order_id, {}) and "sell" in pricing[order_id][p]:
            sell = pricing[order_id][p]["sell"]
            running_total_for_customer += sell
            customer_invoice_lines.append(f"{p} - {format_float(sell)} = {format_float(running_total_for_customer)}")
        else:
            customer_invoice_lines.append(f"{p} - (لم يتم تسعيره)")

    customer_invoice_lines.append(f"كلفة التجهيز: {format_float(extra_cost)}")
    if delivery_cost > 0:
        customer_invoice_lines.append(f"أجرة التوصيل: {format_float(delivery_cost)}")
    customer_invoice_lines.append(f"*المجموع الكلي: {format_float(final_total)}*")
    customer_final_text = "\n".join(customer_invoice_lines)

    try:
        await context.bot.send_message(chat_id=chat_id, text=customer_final_text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"[{chat_id}] Error sending customer invoice: {e}", exc_info=True)

    # رسالة الإدارة
    owner_invoice_details = []
    owner_invoice_details.append("*فاتورة الإدارة*")
    owner_invoice_details.append(f"رقم الفاتورة: {invoice}")
    owner_invoice_details.append(f"عنوان الزبون: {order['title']}")
    owner_invoice_details.append(f"عدد المحلات: {current_places} (+{format_float(extra_cost)})")
    if delivery_cost > 0:
        owner_invoice_details.append(f"سعر التوصيل: {format_float(delivery_cost)}")
    for p in order["products"]:
        if p in pricing.get(order_id, {}) and "buy" in pricing[order_id][p] and "sell" in pricing[order_id][p]:
            buy = pricing[order_id][p]["buy"]
            sell = pricing[order_id][p]["sell"]
            profit_item = sell - buy
            owner_invoice_details.append(f"{p} - شراء: {format_float(buy)}, بيع: {format_float(sell)}, ربح: {format_float(profit_item)}")
        else:
            owner_invoice_details.append(f"{p} - (لم يتم تسعيره)")
    owner_invoice_details.append(f"المجموع شراء: {format_float(total_buy)}")
    owner_invoice_details.append(f"المجموع بيع: {format_float(total_sell)}")
    owner_invoice_details.append(f"الربح الكلي: {format_float(net_profit)}")
    owner_invoice_details.append(f"السعر الكلي: {format_float(final_total)}")
    owner_final_text = "\n".join(owner_invoice_details)

    try:
        await context.bot.send_message(chat_id=chat_id, text=owner_final_text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"[{chat_id}] Error sending owner invoice: {e}", exc_info=True)

    # خيارات إضافية
    keyboard = [
        [InlineKeyboardButton("🔁 تحرير الأسعار", callback_data=f"edit_prices_{order_id}")],
        [InlineKeyboardButton("📤 إرسال الفاتورة عبر واتساب", url=f"https://wa.me/{OWNER_PHONE_NUMBER}?text={owner_final_text.replace(' ', '%20').replace('\n', '%0A')}")],
        [InlineKeyboardButton("🆕 إنشاء طلب جديد", callback_data="start_new_order")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    message_text = "افعل ما تريد من الأزرار:"
    if message_prefix:
        message_text = message_prefix + "\n\n" + message_text
    await context.bot.send_message(chat_id=chat_id, text=message_text, reply_markup=reply_markup)


# تعيين توكن البوت ومعرف المالك 
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_TELEGRAM_ID"))
OWNER_PHONE_NUMBER = "+9647733921468"

if TOKEN is None:
    raise ValueError("TELEGRAM_BOT_TOKEN environment variable not set.")
if OWNER_ID is None:
    raise ValueError("OWNER_TELEGRAM_ID environment variable not set.")


# الدالة الرئيسية
def main():
    initialize_data()

    app = ApplicationBuilder().token(TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^الارباح$|^ارباح$"), show_profit))

    # تشغيل البوت
    logger.info("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
