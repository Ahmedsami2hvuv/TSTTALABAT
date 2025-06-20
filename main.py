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

            logger.info("Data saved successfully.")
        except Exception as e:
            logger.error(f"Error saving data: {e}", exc_info=True)


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
    global orders, pricing, invoice_numbers, daily_profit, areas
    os.makedirs(DATA_DIR, exist_ok=True)

    # تحميل البيانات عند التشغيل
    try:
        with open(ORDERS_FILE, "r") as f:
            orders = json.load(f)
    except Exception as e:
        logger.warning(f"Could not load orders: {e}")

    try:
        with open(PRICING_FILE, "r") as f:
            pricing = json.load(f)
    except Exception as e:
        logger.warning(f"Could not load pricing: {e}")

    try:
        with open(INVOICE_NUMBERS_FILE, "r") as f:
            invoice_numbers = json.load(f)
    except Exception as e:
        logger.warning(f"Could not load invoice numbers: {e}")

    try:
        with open(DAILY_PROFIT_FILE, "r") as f:
            daily_profit = float(json.load(f))
    except Exception as e:
        logger.warning(f"Could not load daily profit: {e}")

    try:
        with open(AREAS_FILE, "r") as f:
            areas.update(json.load(f))
    except Exception as e:
        logger.warning(f"Could not load delivery areas: {e}")

    # إعداد التطبيق
    app = ApplicationBuilder().token(TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^الارباح$|^ارباح$"), show_profit))

    # تشغيل البوت
    logger.info("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
