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
AREAS_FILE = os.path.join(DATA_DIR, "areas.json")  # New file for areas

# تهيئة المتغيرات العامة
orders = {}
pricing = {}
invoice_numbers = {}
daily_profit = 0.0
last_button_message = {}
areas = {}  # New dictionary to store areas and their prices

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
    
    areas_temp = load_json_file(AREAS_FILE, {}, "areas")  # Load areas
    areas.clear()
    areas.update({str(k): v for k, v in areas_temp.items()})
    
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
            
            with open(AREAS_FILE + ".tmp", "w") as f:  # Save areas
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

# قائمة المناطق والأسعار
def load_areas():
    global areas
    areas = {
        "الاسمدة": 3,
        "جيكور حزبه1": 3,
        "جيكور حزبه2": 3,
        "جيكور": 3,
        "العصفورية": 3,
        "باب سليمان": 3,
        "باب طويل": 3,
        "باب العريض": 3,
        "باب عباس": 3,
        "كوت بازل": 3,
        "باب دباغ": 3,
        "باب ميدان": 3,
        "بلد سلطان": 3,
        "ام الصخر": 3,
        "باب رمانه": 3,
        "اهل عيد": 3,
        "الباني": 3,
        "نهر خوز": 3,
        "ابو مغيرة": 3,
        "مجيبرة": 3,
        "السبيليات": 3,
        "الصنكر": 3,
        "محيلة قبل دورة ام زباله": 3,
        "طريق الوسطي": 3,
        "العاگولية": 3,
        "الصحراء": 3,
        "ابو كوصرة": 3,
        "طريزاوية": 3,
        "العوجة": 3,
        "المقيمين": 3,
        "الابطاح": 3,
        "اللكطة": 3,
        "الشجرة الطيبة": 3,
        "شيخ ابراهيم": 3,
        "نزيلة": 3,
        "عميرية": 3,
        "بلد": 3,
        "كوت البلجاني": 3,
        "الحوطة": 3,
        "السوق": 3,
        "الصنكر": 3,
        "محيلة الوسطي": 3,
        "محيلة قرب الجسر": 3,
        "محيلة السوق": 3,
        "محيلة قرب السيطرة": 3,
        "محيلة شارع المشروع": 5,
        "محيلة شارع سيد حامد": 5,
        "محيلة شارع الاندلس": 5,
        "محيلة الصكاروة": 5,
        "المعهد الصناعي": 5,
        "الدورة": 5,
        "الاندلس": 5,
        "الجديدة": 5,
        "الرومية": 5,
        "الصكاروة": 5,
        "كوت الصلحي": 5,
        "كوت الفداغ": 5,
        "جامع الشهيد": 5,
        "يوسفان": 5,
        "حمدان": 5,
        "كوت ثويني": 5,
        "البهادرية": 5,
        "محولة الزهير": 5,
        "كوت الحمداني": 5,
        "عويسيان": 5,
        "مهيجران": 5,
        "السراجي": 5,
        "عبد اليان": 5,
        "معمل الثرمستون": 3
    }
    schedule_save()

# تحميل المناطق عند بدء البوت
load_areas()

# دالة لإظهار قائمة المناطق
async def show_areas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    if user_id != str(OWNER_ID):
        await update.message.reply_text("عذراً، هذا الأمر متاح للمالك فقط.")
        return
    
    keyboard = []
    for area, price in areas.items():
        keyboard.append([InlineKeyboardButton(f"{area} ({price})", callback_data=f"area_{area}")])
    
    keyboard.append([InlineKeyboardButton("اضافة منطقة", callback_data="add_area"), InlineKeyboardButton("ازالة منطقة", callback_data="remove_area")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text("قائمة المناطق:", reply_markup=reply_markup)

# دالة لإضافة منطقة جديدة
async def add_area_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("ادخل اسم المنطقة الجديدة:")
    return ADD_AREA

async def add_area_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    area_name = update.message.text.strip()
    if area_name in areas:
        await update.message.reply_text("هذه المنطقة موجودة بالفعل.")
        return ConversationHandler.END
    
    context.user_data["new_area_name"] = area_name
    await update.message.reply_text(f"ادخل سعر التوصيل للمنطقة '{area_name}':")
    return ADD_AREA

async def add_area_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    area_name = context.user_data.get("new_area_name")
    try:
        price = float(update.message.text.strip())
        if price < 0:
            await update.message.reply_text("السعر يجب أن يكون موجباً.")
            return ADD_AREA
    except ValueError:
        await update.message.reply_text("الرجاء إدخال رقم صحيح.")
        return ADD_AREA
    
    areas[area_name] = price
    schedule_save()
    await update.message.reply_text(f"تمت إضافة المنطقة '{area_name}' بسعر {price}.")
    return ConversationHandler.END

# دالة لحذف منطقة
async def remove_area_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    keyboard = []
    for area in areas.keys():
        keyboard.append([InlineKeyboardButton(area, callback_data=f"remove_area_{area}")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text("اختر المنطقة التي تريد حذفها:", reply_markup=reply_markup)
    return REMOVE_AREA

async def remove_area_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    area_name = query.data.replace("remove_area_", "")
    if area_name in areas:
        del areas[area_name]
        schedule_save()
        await query.edit_message_text(f"تم حذف المنطقة '{area_name}'.")
    else:
        await query.edit_message_text("هذه المنطقة غير موجودة.")
    
    return ConversationHandler.END

# تعديل show_final_options لتشمل سعر التوصيل
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
        
        # استخراج سعر التوصيل بناءً على العنوان
        delivery_area = order['title'].split("\n")[0].strip()
        delivery_price = areas.get(delivery_area, 0)
        final_total = total_sell + extra_cost + delivery_price
        
        daily_profit += net_profit
        context.application.create_task(save_data_in_background(context))
        
        customer_invoice_lines = []
        customer_invoice_lines.append(f"**أبو الأكبر للتوصيل**")
        customer_invoice_lines.append(f"رقم الفاتورة: {invoice}")
        customer_invoice_lines.append(f"عنوان الزبون: {order['title']}")
        customer_invoice_lines.append(f"\n*المواد:*")
        
        running_total_for_customer = 0.0
        for p in order["products"]:
            if p in pricing.get(order_id, {}) and "sell" in pricing[order_id].get(p, {}):
                sell = pricing[order_id][p]["sell"]
                running_total_for_customer += sell
                customer_invoice_lines.append(f"{p} - {format_float(sell)} = {format_float(running_total_for_customer)}")
            else:
                customer_invoice_lines.append(f"{p} - (لم يتم تسعيره)")
        
        customer_invoice_lines.append(f"كلفة تجهيز من - {current_places} محلات {format_float(extra_cost)}")
        customer_invoice_lines.append(f"سعر التوصيل: {format_float(delivery_price)}")
        customer_invoice_lines.append(f"\n*المجموع الكلي:* {format_float(final_total)} (مع احتساب عدد المحلات وسعر التوصيل)")
        
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
        
        keyboard = [
            [InlineKeyboardButton("1️⃣ تعديل الأسعار", callback_data=f"edit_prices_{order_id}")],
            [InlineKeyboardButton("3️⃣ إرسال فاتورة الزبون (واتساب)", url=f"https://wa.me/{OWNER_PHONE_NUMBER}?text={customer_final_text.replace(' ', '%20').replace('\n', '%0A').replace('*', '')}")],
            [InlineKeyboardButton("4️⃣ إنشاء طلب جديد", callback_data="start_new_order")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message_text = "افعل ما تريد من الأزرار:\n"
        if message_prefix:
            message_text = message_prefix + "\n" + message_text
        
        owner_invoice_details = []
        owner_invoice_details.append(f"رقم الفاتورة: {invoice}")
        owner_invoice_details.append(f"عنوان الزبون: {order['title']}")
        
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
        owner_invoice_details.append(f"الربح الكلي: {format_float(net_profit)}")
        owner_invoice_details.append(f"عدد المحلات: {current_places} (+{format_float(extra_cost)})")
        owner_invoice_details.append(f"سعر التوصيل: {format_float(delivery_price)}")
        owner_invoice_details.append(f"السعر الكلي: {format_float(final_total)}")
        
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
            context.user_data[user_id].pop("buy_price", None)
            logger.info(f"[{chat_id}] Cleaned up order-specific user_data for user {user_id} after showing final options. User data after clean: {json.dumps(context.user_data.get(user_id, {}), indent=2)}")
    except Exception as e:
        logger.error(f"[{chat_id}] Error in show_final_options: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text="عذراً، حدث خطأ أثناء عرض الفاتورة النهائية. الرجاء بدء طلبية جديدة.")

# تحديث main() لتشمل الأوامر الجديدة 
def main():
    app = ApplicationBuilder().token(TOKEN).build()
    
    # Handlers لا تدخل في أي ConversationHandler (مثل الـ /start والأوامر الإدارية)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^الارباح$|^ارباح$"), show_profit))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^صفر$|^تصفير$"), reset_all))
    app.add_handler(CallbackQueryHandler(confirm_reset, pattern="^(confirm_reset|cancel_reset)$"))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^التقارير$|^تقرير$|^تقارير$"), show_report))
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE, edited_message))
    app.add_handler(CallbackQueryHandler(edit_prices, pattern="^edit_prices_"))
    app.add_handler(CallbackQueryHandler(start_new_order_callback, pattern="^start_new_order$"))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^المناطق$|^مناطق$"), show_areas))  # New handler for areas
    
    # ConversationHandler لإدارة المناطق
    areas_conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(add_area_start, pattern="^add_area$"),
            CallbackQueryHandler(remove_area_start, pattern="^remove_area$")
        ],
        states={
            ADD_AREA: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_area_process),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_area_finish)
            ],
            REMOVE_AREA: [
                CallbackQueryHandler(remove_area_process, pattern="^remove_area_")
            ]
        },
        fallbacks=[
            CommandHandler("cancel", lambda u, c: ConversationHandler.END),
            MessageHandler(filters.ALL, lambda u, c: ConversationHandler.END)
        ]
    )
    app.add_handler(areas_conv_handler)
    
    # ConversationHandler لعدد المحلات
    places_conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(handle_places_count_data, pattern=r"^places_data_[a-f0-9]{8}_\d+$")
        ],
        states={
            ASK_PLACES_COUNT: [
                MessageHandler(filters.TEXT & filters.Regex(r"^\d+(\.\d+)?$") & ~filters.COMMAND, handle_places_count_data),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_places_count_data)
            ]
        },
        fallbacks=[
            CommandHandler("cancel", lambda u, c: ConversationHandler.END),
            MessageHandler(filters.ALL, lambda u, c: ConversationHandler.END)
        ]
    )
    app.add_handler(places_conv_handler)
    
    # ConversationHandler لإنشاء الطلب وتسعير المنتجات
    order_creation_conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_order),
            CallbackQueryHandler(product_selected, pattern=r"^[a-f0-9]{8}\|.+$")
        ],
        states={
            ASK_BUY: [
                MessageHandler(filters.TEXT & filters.Regex(r"^\d+(\.\d+)?$") & ~filters.COMMAND, receive_buy_price),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_buy_price)
            ],
            ASK_SELL: [
                MessageHandler(filters.TEXT & filters.Regex(r"^\d+(\.\d+)?$") & ~filters.COMMAND, receive_sell_price),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_sell_price)
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
