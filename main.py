from features.delivery_zones import zone_handlers, get_delivery_price
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
AREAS_FILE = os.path.join(DATA_DIR, "areas.json")

# تهيئة المتغيرات العامة
orders = {}
pricing = {}
invoice_numbers = {}
daily_profit = 0.0
last_button_message = {}
areas = {}

# متغيرات الحفظ المؤجل
save_timer = None
save_pending = False
save_lock = threading.Lock()

# تحميل البيانات عند بدء تشغيل البوت
def load_data():
    global orders, pricing, invoice_numbers, daily_profit, last_button_message, areas
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
            pricing[oid] = {str(pk): pv for pk, pv in pricing[oid].items()}  # Ensure inner keys are strings too
    invoice_numbers_temp = load_json_file(INVOICE_NUMBERS_FILE, {}, "invoice_numbers")
    invoice_numbers.clear()
    invoice_numbers.update({str(k): v for k, v in invoice_numbers_temp.items()})
    daily_profit = load_json_file(DAILY_PROFIT_FILE, 0.0, "daily_profit")
    last_button_message_temp = load_json_file(LAST_BUTTON_MESSAGE_FILE, {}, "last_button_message")
    last_button_message.clear()
    last_button_message.update({str(k): v for k, v in last_button_message_temp.items()})
    areas = load_json_file(AREAS_FILE, {}, "areas")
    logger.info(f"Initial load complete. Orders: {len(orders)}, Pricing entries: {len(pricing)}, Daily Profit: {daily_profit}, Areas: {len(areas)}")

# حفظ البيانات
def _save_data_to_disk():
    global save_pending
    with save_lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        try:
            # Save to temporary files first, then rename to prevent data corruption
            with open(ORDERS_FILE + ".tmp", "w") as f:
                json.dump(orders, f, indent=4)  # Use indent for readability in files
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
            with open(AREAS_FILE + ".tmp", "w") as f:
                json.dump(areas, f, indent=4)
            os.replace(AREAS_FILE + ".tmp", AREAS_FILE)
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

# تحميل البيانات عند بدء البوت
load_data()

# حالات المحادثة
ASK_BUY, ASK_SELL, ASK_PLACES_COUNT, ADD_AREA, REMOVE_AREA = range(5)

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

# دالة لعرض قائمة المناطق
async def show_areas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not areas:
        await update.message.reply_text("لا توجد مناطق مضافة حالياً.")
        return
    areas_list = "\n".join([f"{area}: {price}" for area, price in areas.items()])
    await update.message.reply_text(f"قائمة المناطق وأسعار التوصيل:\n{areas_list}")

# دالة لإضافة منطقة جديدة
async def add_area(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    if user_id != str(OWNER_ID):
        await update.message.reply_text("عذراً، هذا الأمر متاح للمالك فقط.")
        return ConversationHandler.END
    await update.message.reply_text("أرسل اسم المنطقة وسعر التوصيل مفصولة بمسافة (مثال: المنطقة 5).")
    return ADD_AREA

# معالجة إضافة المنطقة
async def process_add_area(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        input_data = update.message.text.strip().split()
        if len(input_data) != 2:
            await update.message.reply_text("الرجاء إرسال اسم المنطقة وسعر التوصيل بشكل صحيح (مثال: المنطقة 5).")
            return ADD_AREA
        area_name, price = input_data
        if not price.isdigit() or int(price) <= 0:
            await update.message.reply_text("السعر يجب أن يكون رقماً موجباً.")
            return ADD_AREA
        areas[area_name] = int(price)
        _save_data_to_disk()
        await update.message.reply_text(f"تمت إضافة المنطقة '{area_name}' بسعر توصيل {price} بنجاح.")
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Error adding area: {e}")
        await update.message.reply_text("عذراً، حدث خطأ أثناء إضافة المنطقة.")
        return ConversationHandler.END

# دالة لإزالة منطقة
async def remove_area(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    if user_id != str(OWNER_ID):
        await update.message.reply_text("عذراً، هذا الأمر متاح للمالك فقط.")
        return ConversationHandler.END
    if not areas:
        await update.message.reply_text("لا توجد مناطق مضافة حالياً.")
        return ConversationHandler.END
    keyboard = []
    for area in areas.keys():
        keyboard.append([InlineKeyboardButton(area, callback_data=f"remove_area_{area}")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("اختر المنطقة التي تريد إزالتها:", reply_markup=reply_markup)
    return REMOVE_AREA

# معالجة إزالة المنطقة
async def process_remove_area(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    area_name = query.data.replace("remove_area_", "")
    if area_name in areas:
        del areas[area_name]
        _save_data_to_disk()
        await query.edit_message_text(f"تمت إزالة المنطقة '{area_name}' بنجاح.")
    else:
        await query.edit_message_text("هذه المنطقة غير موجودة.")
    return ConversationHandler.END

# دالة للحصول على سعر التوصيل بناءً على العنوان
def get_delivery_price(title):
    for area, price in areas.items():
        if title.startswith(area):
            return price
    return 0

# تحديث دالة إنشاء الفاتورة لإضافة سعر التوصيل
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
        delivery_price = get_delivery_price(order['title'])  # الحصول على سعر التوصيل بناءً على العنوان
        final_total = total_sell + extra_cost + delivery_price
        daily_profit += net_profit
        context.application.create_task(schedule_save())
        customer_invoice_lines = []
        customer_invoice_lines.append(f"**أبو الأكبر للتوصيل**")
        customer_invoice_lines.append(f"رقم الفاتورة: {invoice}")
        customer_invoice_lines.append(f"عنوان الزبون: {order['title']}")
        customer_invoice_lines.append("\n*المواد:*")
        running_total_for_customer = 0.0
        for p in order["products"]:
            if p in pricing.get(order_id, {}) and "sell" in pricing[order_id].get(p, {}):
                sell = pricing[order_id][p]["sell"]
                running_total_for_customer += sell
                customer_invoice_lines.append(f"{p} - {format_float(sell)} = {format_float(running_total_for_customer)}")
            else:
                customer_invoice_lines.append(f"{p} - (لم يتم تسعيره)")
        customer_invoice_lines.append(f"كلفة تجهيز من - {current_places} محلات {format_float(extra_cost)}")
        customer_invoice_lines.append(f"سعر التوصيل: {delivery_price}")
        customer_invoice_lines.append(f"*المجموع الكلي:* {format_float(final_total)} (مع احتساب عدد المحلات وسعر التوصيل)")
        customer_final_text = "\n".join(customer_invoice_lines)
        await context.bot.send_message(
            chat_id=chat_id,
            text=customer_final_text,
            parse_mode="Markdown"
        )
        logger.info(f"[{chat_id}] Customer invoice sent as a separate message for order {order_id}.")
        keyboard = [
            [InlineKeyboardButton("1️⃣ تعديل الأسعار", callback_data=f"edit_prices_{order_id}")],
            [InlineKeyboardButton("3️⃣ إرسال فاتورة الزبون (واتساب)", url=f"https://wa.me/{OWNER_PHONE_NUMBER}?text={customer_final_text.replace(' ', '%20').replace('\n', '%0A').replace('*', '')}")],
            [InlineKeyboardButton("4️⃣ إنشاء طلب جديد", callback_data="start_new_order")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        message_text = "افعل ما تريد من الأزرار:\n"
        if message_prefix:
            message_text = message_prefix + "\n" + message_text
        await context.bot.send_message(chat_id=chat_id, text=message_text, reply_markup=reply_markup, parse_mode="Markdown")
        if user_id in context.user_data:
            if 'messages_to_delete' in context.user_data[user_id]:
                for msg_info in context.user_data[user_id]['messages_to_delete']:
                    context.application.create_task(delete_message_in_background(context, chat_id=msg_info['chat_id'], message_id=msg_info['message_id']))
                context.user_data[user_id]['messages_to_delete'].clear()
            context.user_data[user_id].pop("order_id", None)
            context.user_data[user_id].pop("product", None)
            context.user_data[user_id].pop("current_active_order_id", None)
            context.user_data[user_id].pop("buy_price", None)
            logger.info(f"[{chat_id}] Cleaned up order-specific user_data for user {user_id} after showing final options. User data after clean: {json.dumps(context.user_data.get(user_id, {}), indent=2)}")
    except Exception as e:
        logger.error(f"[{chat_id}] Error in show_final_options: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text="عذراً، حدث خطأ أثناء عرض الفاتورة النهائية. الرجاء بدء طلبية جديدة.")

# تحديث الـ main function لإضافة handlers الجديدة 
def main():
    app = ApplicationBuilder().token(TOKEN).build()
    
# إضافة الهاندلرات الخاصة بالمناطق
for handler in zone_handlers:
    app.add_handler(handler)
    
    # Handlers لا تدخل في أي ConversationHandler
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^الارباح$|^ارباح$"), show_profit))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^صفر$|^تصفير$"), reset_all))
    app.add_handler(CallbackQueryHandler(confirm_reset, pattern="^(confirm_reset|cancel_reset)$"))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^التقارير$|^تقرير$|^تقارير$"), show_report))
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE, edited_message))
    app.add_handler(CallbackQueryHandler(edit_prices, pattern="^edit_prices_"))
    app.add_handler(CallbackQueryHandler(start_new_order_callback, pattern="^start_new_order$"))

    # Handlers لإدارة المناطق
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^المناطق$|^مناطق$"), lambda u, c: asyncio.run(show_areas(u, c))))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^اضافة منطقة$"), add_area))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^ازالة منطقة$"), remove_area))
    app.add_handler(CallbackQueryHandler(process_remove_area, pattern="^remove_area_"))

    # ConversationHandlers

app.add_handler(CommandHandler("المناطق", list_zones))
    
    places_conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(handle_places_count_data, pattern=r"^places_data_[a-f0-9]{8}_\d+$"),
        ],
        states={
            ASK_PLACES_COUNT: [
                MessageHandler(filters.TEXT & filters.Regex(r"^\d+(\.\d+)?$") & ~filters.COMMAND, handle_places_count_data),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_places_count_data),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", lambda u, c: ConversationHandler.END),
            MessageHandler(filters.ALL, lambda u, c: ConversationHandler.END)
        ]
    )
    app.add_handler(places_conv_handler)

    # ConversationHandler لإضافة وإزالة المناطق
    areas_conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.TEXT & filters.Regex("^اضافة منطقة$"), add_area),
            MessageHandler(filters.TEXT & filters.Regex("^ازالة منطقة$"), remove_area),
        ],
        states={
            ADD_AREA: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_add_area),
            ],
            REMOVE_AREA: [
                CallbackQueryHandler(process_remove_area, pattern="^remove_area_"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", lambda u, c: ConversationHandler.END),
            MessageHandler(filters.ALL, lambda u, c: ConversationHandler.END)
        ]
    )
    app.add_handler(areas_conv_handler)

    # ConversationHandler لإنشاء الطلب وتسعير المنتجات
    order_creation_conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_order),
            CallbackQueryHandler(product_selected, pattern=r"^[a-f0-9]{8}\|.+$")
        ],
        states={
            ASK_BUY: [
                MessageHandler(filters.TEXT & filters.Regex(r"^\d+(\.\d+)?$") & ~filters.COMMAND, receive_buy_price),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_buy_price),
            ],
            ASK_SELL: [
                MessageHandler(filters.TEXT & filters.Regex(r"^\d+(\.\d+)?$") & ~filters.COMMAND, receive_sell_price),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_sell_price),
            ]
        },
        fallbacks=[
            CommandHandler("cancel", lambda u, c: ConversationHandler.END),
            MessageHandler(filters.ALL, lambda u, c: ConversationHandler.END)
        ]
    )
    app.add_handler(order_creation_conv_handler)

    logger.info("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
