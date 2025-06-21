import os
import json
import uuid
import time
import asyncio
import logging
import threading
from collections import Counter
from urllib.parse import quote 

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler,
    MessageHandler, CallbackQueryHandler, ConversationHandler, filters
)

# استيراد دالة جلب سعر التوصيل من ملف المناطق
from features.delivery_zones import get_delivery_price

# تعريف الـ logging
logger = logging.getLogger(__name__)

# المتغيرات العامة الأساسية (يجب أن يتم تمريرها أو الوصول إليها بشكل عام)
# لغرض التجزئة هنا، سنفترض أنها ستُعالج كـ global variables
# (main.py سيعرفها كـ global، وهذه الدوال ستستخدمها)
# الطريقة الأفضل للمشاريع الكبيرة هي تمريرها كمعاملات (parameters) للدوال
# أو استخدام context.bot_data في python-telegram-bot

# مسارات التخزين (ثابتة لمجلد /mnt/data في Railway)
DATA_DIR = "/mnt/data/"
ORDERS_FILE = os.path.join(DATA_DIR, "orders.json")
PRICING_FILE = os.path.join(DATA_DIR, "pricing.json")
INVOICE_NUMBERS_FILE = os.path.join(DATA_DIR, "invoice_numbers.json")
DAILY_PROFIT_FILE = os.path.join(DATA_DIR, "daily_profit.json")
COUNTER_FILE = os.path.join(DATA_DIR, "invoice_counter.txt") # عداد الفواتير
LAST_BUTTON_MESSAGE_FILE = os.path.join(DATA_DIR, "last_button_message.json")

# جلب OWNER_ID ورقم الهاتف (للواتساب) من متغيرات البيئة
# هذا لضمان أن الدوال في هذا الملف يمكنها الوصول لهذه المعلومات
OWNER_ID = int(os.getenv("OWNER_TELEGRAM_ID"))
OWNER_PHONE_NUMBER = os.getenv("OWNER_TELEGRAM_PHONE_NUMBER", "+9647733921468")

# هذه الدوال (schedule_save, _save_data_to_disk, get_invoice_number)
# هي في الأساس وظائف مساعدة عامة تتعامل مع البيانات
# يجب أن تبقى في main.py أو يتم نقلها إلى ملف 'utils.py' أو 'data_manager.py'
# ومن ثم يتم استيرادها في main.py وفي هذا الملف.
# لغرض هذه الخطوة، سنفترض أنها ستكون متاحة من main.py.

# حالات المحادثة
ASK_BUY, ASK_SELL, ASK_PLACES_COUNT = range(3)

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
        await asyncio.sleep(0.1) 
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.info(f"Successfully deleted message {message_id} from chat {chat_id} in background.")
    except Exception as e:
        logger.warning(f"Could not delete message {message_id} from chat {chat_id} in background: {e}.")

# دالة مساعدة لحفظ البيانات في الخلفية
# هذه الدالة ستعتمد على دالة schedule_save الموجودة في main.py
async def save_data_in_background(context: ContextTypes.DEFAULT_TYPE):
    # للوصول إلى دالة schedule_save المعرفة في main.py
    # الطريقة الأفضل هي تمريرها أو استيرادها من ملف utils.py مشترك
    # حاليا، نفترض أن context.application يمكنها الوصول إليها، أو أننا نعتمد على أن main.py
    # سيعرف هذه الدوال بشكل عام قبل تنفيذ app.run_polling.
    # لتجنب NameError، يمكننا إما تمريرها أو جعل main.py يضعها في bot_data
    # أو أن save_data_in_background نفسها تبقى في main.py وتستدعي الدوال العالمية.
    # بما أننا الآن نجعلها في module منفصل، يجب أن يكون هناك اتصال بالدوال الرئيسية.
    # للحفاظ على البساطة، سنقوم بإعادة تنفيذ logic schedule_save هنا أو افتراض أنها ستُستدعى من main.py.
    # الأبسط هو أن save_data_in_background كانت تستدعي schedule_save
    # سنقوم بتغيير هذا ليتناسب مع نموذج حيث يتم تمرير البيانات.
    # ولكن لمتابعة النمط القديم: هذا يتطلب الوصول إلى المتغيرات orders, pricing, etc.
    # التي هي معرفة في main.py.
    # سنضع `global` للوصول إليها مباشرة هنا.
    
    # هذه يجب أن تكون موجودة في main.py فقط أو في ملف إدارة بيانات مركزي
    # وسنقوم بتوفير هذه الدالة في main.py ونتأكد من استدعائها من هناك.
    # لذلك، هذه الدالة ستُزال من orders.py بعد هذه الخطوة.
    pass # هذه الدالة سيتم إزالتها لاحقاً.

# دالة لجلب رقم الفاتورة (يجب أن تكون في main.py أو data_manager.py)
# لغرض هذا الملف، سنعتبر أنها تستخدم المتغيرات العامة التي ستكون متاحة
# أو يتم تمريرها إلى الدالة.
# سيتم إزالتها لاحقاً أيضاً.
def get_invoice_number():
    # هذا الكود يستخدم COUNTER_FILE الذي يجب أن يكون معرفا كـ global
    # أو يتم تمرير مساره.
    # لتبسيط الأمر، سنعتبر أن COUNTER_FILE معرف عالمياً في main.py
    # أو في ملف مشترك.
    # الأفضل هو أن تكون هذه الدالة في ملف DataManager منفصل.
    pass # سيتم إزالتها لاحقاً.


async def receive_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # هذه الدوال ستعمل على المتغيرات orders, pricing, invoice_numbers, last_button_message
    # التي ستُعرف كـ global في main.py ويتم الوصول إليها هنا عبر global keyword.
    global orders, pricing, invoice_numbers, last_button_message # يجب تعريفها كـ global هنا
    print("📩 تم استقبال رسالة جديدة داخل receive_order")
    try:
        logger.info(f"[{update.effective_chat.id}] Processing order from: {update.effective_user.id} - Message ID: {update.message.message_id}. User data: {json.dumps(context.user_data.get(str(update.effective_user.id), {}), indent=2)}")
        await process_order(update, context, update.message)
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in receive_order: {e}", exc_info=True)
        await update.message.reply_text("عذراً، حدث خطأ أثناء معالجة الطلب. الرجاء المحاولة مرة أخرى أو بدء طلبية جديدة.")
        return ConversationHandler.END

async def edited_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global orders, pricing, invoice_numbers, last_button_message
    try:
        if not update.edited_message:
            return
        logger.info(f"[{update.effective_chat.id}] Processing edited order from: {update.effective_user.id} - Message ID: {update.edited_message.message_id}. User data: {json.dumps(context.user_data.get(str(update.effective_user.id), {}), indent=2)}")
        await process_order(update, context, update.edited_message, edited=True)
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in edited_message: {e}", exc_info=True)
        await update.edited_message.reply_text("عذراً، حدث خطأ أثناء معالجة التعديل. الرجاء المحاولة مرة أخرى.")

async def process_order(update, context, message, edited=False):
    global orders, pricing, invoice_numbers, last_button_message # تأكيد الوصول كـ global
    user_id = str(message.from_user.id)
    lines = [line.strip() for line in message.text.strip().split('\n') if line.strip()]
    
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
        # دالة get_invoice_number يجب أن تكون متاحة هنا.
        # حاليا، نفترض أنها معرفة كـ global أو يتم استيرادها.
        # للحفاظ على التجزئة، يجب نقلها إلى ملف utils أو data_manager.
        # لغرض هذا الملف الآن، سنتركها كما هي ونفترض أنها global من main.py
        invoice_no = context.application.get_invoice_number() # الأفضل أن تكون بهذه الطريقة
        
        orders[order_id] = {"user_id": user_id, "title": title, "products": products, "places_count": 0} 
        pricing[order_id] = {p: {} for p in products}
        invoice_numbers[order_id] = invoice_no
        logger.info(f"Created new order {order_id} for user {user_id}.")
    else: 
        old_products = set(orders[order_id].get("products", []))
        new_products = set(products)
        
        orders[order_id]["title"] = title
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
        
    context.application.create_task(context.application.save_data_in_background(context))
    
    if is_new_order:
        await message.reply_text(f"استلمت الطلب بعنوان: *{title}* (عدد المنتجات: {len(products)})", parse_mode="Markdown")
        await show_buttons(message.chat_id, context, user_id, order_id)
    else:
        await show_buttons(message.chat_id, context, user_id, order_id, confirmation_message="تم تحديث الطلب. الرجاء التأكد من تسعير أي منتجات جديدة.")
        
async def show_buttons(chat_id, context, user_id, order_id, confirmation_message=None):
    global orders, pricing, last_button_message
    try:
        logger.info(f"[{chat_id}] show_buttons called for order {order_id}. User: {user_id}.")
        logger.info(f"[{chat_id}] Current pricing data for order {order_id} in show_buttons: {json.dumps(pricing.get(order_id), indent=2)}")

        if order_id not in orders:
            logger.warning(f"[{chat_id}] Attempted to show buttons for non-existent order_id: {order_id}")
            await context.bot.send_message(chat_id=chat_id, text="عذراً، الطلب الذي تحاول الوصول إليه غير موجود أو تم حذفه. الرجاء بدء طلبية جديدة.")
            if user_id in context.user_data:
                context.user_data[user_id].pop("order_id", None)
                context.user_data[user_id].pop("product", None)
                context.user_data[user_id].pop("current_active_order_id", None)
                context.user_data[user_id].pop("messages_to_delete", None)
            return

        order = orders[order_id]
        
        completed_products = []
        pending_products = []
        
        for p in order["products"]:
            if p in pricing.get(order_id, {}) and 'buy' in pricing[order_id].get(p, {}) and 'sell' in pricing[order_id].get(p, {}):
                completed_products.append(p)
                logger.info(f"[{chat_id}] Product '{p}' in order {order_id} is completed.")
            else:
                pending_products.append(p)
                logger.info(f"[{chat_id}] Product '{p}' in order {order_id} is pending. Pricing state for this product: {json.dumps(pricing.get(order_id, {}).get(p, {}), indent=2)}")
        
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
        context.application.create_task(context.application.save_data_in_background(context)) # استدعاء save_data_in_background

        if user_id in context.user_data and 'messages_to_delete' in context.user_data[user_id]:
            logger.info(f"[{chat_id}] Scheduling deletion of {len(context.user_data[user_id].get('messages_to_delete', []))} old messages after showing new buttons for user {user_id}.")
            for msg_info in context.user_data[user_id]['messages_to_delete']:
                context.application.create_task(delete_message_in_background(context, chat_id=msg_info['chat_id'], message_id=msg_info['message_id']))
            context.user_data[user_id]['messages_to_delete'].clear()
    except Exception as e:
        logger.error(f"[{chat_id}] Error in show_buttons: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text="عذراً، حدث خطأ أثناء عرض الأزرار. الرجاء بدء طلبية جديدة.")


async def product_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global orders, pricing # تأكيد الوصول كـ global
    try: # تصحيح: إضافة try هنا
        query = update.callback_query
        await query.answer()
        
        user_id = str(query.from_user.id)
        logger.info(f"[{query.message.chat_id}] Product selected callback from user {user_id}: {json.dumps(query.data)}. User data at product_selected start: {json.dumps(context.user_data.get(user_id, {}), indent=2)}")

        context.user_data.setdefault(user_id, {}).setdefault('messages_to_delete', []).append({
            'chat_id': query.message.chat_id,
            'message_id': query.message.message_id
        })
        logger.info(f"[{query.message.chat_id}] Added product selection button message {query.message.message_id} to delete queue.")
        
        order_id, product = query.data.split('|', 1)
        
        if order_id not in orders:
            logger.warning(f"[{query.message.chat_id}] Product selected: Order ID '{order_id}' not found.")
            msg_error = await query.edit_message_text("عذراً، الطلبية لم تعد موجودة. الرجاء بدء طلبية جديدة.")
            context.user_data[user_id]['messages_to_delete'].append({
                'chat_id': msg_error.chat_id,
                'message_id': msg_error.message_id
            })
            return ConversationHandler.END

        context.user_data[user_id]["order_id"] = order_id
        context.user_data[user_id]["product"] = product
        
        context.user_data[user_id].pop("buy_price", None)

        logger.info(f"[{query.message.chat_id}] Product '{product}' selected for order '{order_id}'. User data after product selection: {json.dumps(context.user_data.get(user_id), indent=2)}")
        
        current_buy = pricing.get(order_id, {}).get(product, {}).get("buy")
        current_sell = pricing.get(order_id, {}).get(product, {}).get("sell")

        if current_buy is not None and current_sell is not None:
            msg_edit = await query.message.reply_text(
                f"سعر *'{product}'* حالياً هو شراء: {format_float(current_buy)}، بيع: {format_float(current_sell)}.\n"
                "شنو سعر الشراء الجديد؟ (أو دز نفس السعر إذا ماكو تغيير)", parse_mode="Markdown"
            )
            context.user_data[user_id]['messages_to_delete'].append({
                'chat_id': msg_edit.chat_id, 
                'message_id': msg_edit.message_id
            })
            return ASK_BUY 
        else:
            msg_new = await query.message.reply_text(f"تمام، بيش اشتريت *'{product}'*؟", parse_mode="Markdown")
            context.user_data[user_id]['messages_to_delete'].append({
                'chat_id': msg_new.chat_id, 
                'message_id': msg_new.message_id
            })
            return ASK_BUY 

    except Exception as e: # تصحيح: إضافة Exception e
        logger.error(f"[{update.effective_chat.id}] Error in product_selected: {e}", exc_info=True)
        await update.callback_query.message.reply_text("عذراً، حدث خطأ أثناء اختيار المنتج. الرجاء بدء طلبية جديدة.")
        return ConversationHandler.END
    
async def receive_buy_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global orders, pricing # تأكيد الوصول كـ global
    try:
        user_id = str(update.message.from_user.id)
        logger.info(f"[{update.effective_chat.id}] Received message for buy price from user {user_id}: '{update.message.text}'. User data at start of receive_buy_price: {json.dumps(context.user_data.get(user_id), indent=2)}")

        context.user_data.setdefault(user_id, {})
        if 'messages_to_delete' not in context.user_data[user_id]:
            context.user_data[user_id]['messages_to_delete'] = []
        
        context.user_data[user_id]['messages_to_delete'].append({
            'chat_id': update.message.chat_id,
            'message_id': update.message.message_id
        })

        data = context.user_data.get(user_id)
        if not data or "order_id" not in data or "product" not in data:
            logger.error(f"[{update.effective_chat.id}] Buy price: Missing order_id or product in user_data for user {user_id}. User data: {json.dumps(data, indent=2)}")
            msg_error = await update.message.reply_text("عذراً، لم أتمكن من تحديد الطلبية أو المنتج لتسعيره. الرجاء اضغط على المنتج من القائمة أولاً لتحديد سعره، أو ابدأ طلبية جديدة.", parse_mode="Markdown")
            context.user_data[user_id]['messages_to_delete'].append({
                'chat_id': msg_error.chat_id, 
                'message_id': msg_error.message_id
            })
            return ConversationHandler.END
        
        order_id = data["order_id"]
        product = data["product"]
        
        if order_id not in orders or product not in orders[order_id].get("products", []):
            logger.warning(f"[{update.effective_chat.id}] Buy price: Order ID '{order_id}' not found or Product '{product}' not in products for order '{order_id}'.")
            msg_error = await update.message.reply_text("عذراً، الطلبية أو المنتج لم يعد موجوداً. الرجاء بدء طلبية جديدة أو التحقق من المنتجات.")
            context.user_data[user_id]['messages_to_delete'].append({
                'chat_id': msg_error.chat_id, 
                'message_id': msg_error.message_id
            })
            return ConversationHandler.END
        
        if not filters.Regex(r"^\d+(\.\d+)?$").check_update(update): 
            logger.warning(f"[{update.effective_chat.id}] Buy price: Non-numeric input from user {user_id}: '{update.message.text}'")
            msg_error = await update.message.reply_text("الرجاء إدخال *رقم* صحيح لسعر الشراء.")
            context.user_data[user_id]['messages_to_delete'].append({
                'chat_id': msg_error.chat_id, 
                'message_id': msg_error.message_id
            })
            return ASK_BUY 

        try:
            price = float(update.message.text.strip())
            if price < 0:
                logger.warning(f"[{update.effective_chat.id}] Buy price: Negative price from user {user_id}: '{update.message.text}'")
                msg_error = await update.message.reply_text("السعر يجب أن يكون موجباً")
                context.user_data[user_id]['messages_to_delete'].append({
                    'chat_id': msg_error.chat_id, 
                    'message_id': msg_error.message_id
                })
                return ASK_BUY
        except ValueError as e: 
            logger.error(f"[{update.effective_chat.id}] Buy price: ValueError for user {user_id} with input '{update.message.text}': {e}", exc_info=True)
            msg_error = await update.message.reply_text("الرجاء إدخال رقم صحيح")
            context.user_data[user_id]['messages_to_delete'].append({
                    'chat_id': msg_error.chat_id, 
                    'message_id': msg_error.message_id
                })
            return ASK_BUY
        
        context.user_data[user_id]["buy_price"] = price 
        logger.info(f"[{update.effective_chat.id}] Buy price '{price}' stored in user_data for product '{product}'. User data after storing buy_price: {json.dumps(context.user_data.get(user_id), indent=2)}")

        msg = await update.message.reply_text(f"شكراً. وهسه، بيش راح تبيع *'{product}'*؟", parse_mode="Markdown")
        context.user_data[user_id]['messages_to_delete'].append({'chat_id': msg.chat_id, 'message_id': msg.message_id})
        logger.info(f"[{update.effective_chat.id}] Asking for sell price for '{product}'. Next state: ASK_SELL. Current user_data: {json.dumps(context.user_data.get(user_id), indent=2)}")
        
        return ASK_SELL
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in receive_buy_price: {e}", exc_info=True)
        await update.message.reply_text("عذراً، حدث خطأ أثناء إدخال سعر الشراء. الرجاء بدء طلبية جديدة.")
        return ConversationHandler.END


async def receive_sell_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global orders, pricing # تأكيد الوصول كـ global
    try:
        user_id = str(update.message.from_user.id)
        logger.info(f"[{update.effective_chat.id}] Received message for sell price from user {user_id}: '{update.message.text}'. User data at start of receive_sell_price: {json.dumps(context.user_data.get(user_id), indent=2)}")

        context.user_data.setdefault(user_id, {})
        if 'messages_to_delete' not in context.user_data[user_id]:
            context.user_data[user_id]['messages_to_delete'] = []
        context.user_data[user_id]['messages_to_delete'].append({'chat_id': update.message.chat_id, 'message_id': update.message.message_id})

        data = context.user_data.get(user_id)
        if not data or "order_id" not in data or "product" not in data or "buy_price" not in data: 
            logger.error(f"[{update.effective_chat.id}] Sell price: Missing order_id, product, or buy_price in user_data for user {user_id}. User data: {json.dumps(data, indent=2)}")
            msg_error = await update.message.reply_text("عذراً، لم أتمكن من تحديد الطلبية أو المنتج لتسعيره. الرجاء اضغط على المنتج من القائمة أولاً لتحديد سعره، أو ابدأ طلبية جديدة.", parse_mode="Markdown")
            context.user_data[user_id]['messages_to_delete'].append({
                'chat_id': msg_error.chat_id, 
                'message_id': msg_error.message_id
            })
            return ConversationHandler.END
        
        order_id, product, buy_price_from_user_data = data["order_id"], data["product"], data["buy_price"]
        
        if order_id not in orders or product not in orders[order_id].get("products", []):
            logger.warning(f"[{update.effective_chat.id}] Sell price: Order ID '{order_id}' not found or Product '{product}' not in products for order '{order_id}'.")
            msg_error = await update.message.reply_text("عذراً، الطلبية أو المنتج لم يعد موجوداً. الرجاء بدء طلبية جديدة.")
            context.user_data[user_id]['messages_to_delete'].append({
                'chat_id': msg_error.chat_id, 
                'message_id': msg_error.message_id
            })
            return ConversationHandler.END

        if not filters.Regex(r"^\d+(\.\d+)?$").check_update(update): 
            logger.warning(f"[{update.effective_chat.id}] Sell price: Non-numeric input from user {user_id}: '{update.message.text}'")
            msg_error = await update.message.reply_text("الرجاء إدخال *رقم* صحيح لسعر البيع.")
            context.user_data[user_id]['messages_to_delete'].append({'chat_id': msg_error.chat_id, 'message_id': msg_error.message_id})
            return ASK_SELL 

        try:
            sell_price = float(update.message.text.strip())
            if sell_price < 0:
                logger.warning(f"[{update.effective_chat.id}] Sell price: Negative price from user {user_id}: '{update.message.text}'")
                msg_error = await update.message.reply_text("سعر البيع يجب أن يكون رقماً إيجابياً. بيش راح تبيع بالضبط؟")
                context.user_data[user_id]['messages_to_delete'].append({'chat_id': msg_error.chat_id, 'message_id': msg_error.message_id})
                return ASK_SELL 
        except ValueError as e:
            logger.error(f"[{update.effective_chat.id}] Sell price: ValueError for user {user_id} with input '{update.message.text}': {e}", exc_info=True)
            msg_error = await update.message.reply_text("الرجاء إدخال رقم صحيح لسعر البيع. بيش حتبيع؟")
            context.user_data[user_id]['messages_to_delete'].append({'chat_id': msg_error.chat_id, 'message_id': msg_error.message_id})
            return ASK_SELL 
        
        pricing.setdefault(order_id, {}).setdefault(product, {})["buy"] = buy_price_from_user_data
        pricing[order_id][product]["sell"] = sell_price
        
        logger.info(f"[{update.effective_chat.id}] Pricing for order '{order_id}' and product '{product}' AFTER SAVE: {json.dumps(pricing.get(order_id, {}).get(product), indent=2)}")
        context.application.create_task(context.application.save_data_in_background(context)) # استدعاء save_data_in_background
        logger.info(f"[{update.effective_chat.id}] Sell price for '{product}' in order '{order_id}' saved. Current user_data: {json.dumps(context.user_data.get(user_id), indent=2)}. Updated pricing for order {order_id}: {json.dumps(pricing.get(order_id), indent=2)}")

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
            logger.info(f"[{update.effective_chat.id}] Price saved for '{product}' in order {order_id}. Showing updated buttons with confirmation. User {user_id} can select next product. Staying in conversation.")
            await show_buttons(update.effective_chat.id, context, user_id, order_id, confirmation_message=confirmation_msg)
            return ConversationHandler.END 
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in receive_sell_price: {e}", exc_info=True)
        await update.message.reply_text("عذراً، حدث خطأ أثناء إدخال سعر البيع. الرجاء بدء طلبية جديدة.")
        return ConversationHandler.END

async def request_places_count_standalone(chat_id, context: ContextTypes.DEFAULT_TYPE, user_id: str, order_id: str):
    global orders # تأكيد الوصول كـ global
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
            text="تمام، كل المنتجات تسعّرت. هسه، كم محل كلفتك الطلبية؟ (اختر من الأزرار أو اكتب الرقم)", 
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
        
        return # لا نرجع ConversationHandler.END هنا لأن المحادثة يجب أن تستمر
    except Exception as e:
        logger.error(f"[{chat_id}] Error in request_places_count_standalone: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text="عذراً، حدث خطأ أثناء طلب عدد المحلات. الرجاء بدء طلبية جديدة.")
        return ConversationHandler.END
        
async def handle_places_count_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global daily_profit, orders # تأكيد الوصول كـ global
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
                        await context.bot.send_message(chat_id=chat_id, text="عذراً، الطلبية اللي حاول تختار عدد محلاتها ما موجودة عندي. الرجاء بدء طلبية جديدة.")
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
                msg_error = await context.bot.send_message(chat_id=chat_id, text="الرجاء إدخال *رقم صحيح* لعدد المحلات.")
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
                msg_error = await context.bot.send_message(chat_id=chat_id, text="الرجاء إدخال عدد صحيح لعدد المحلات.")
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
        context.application.create_task(context.application.save_data_in_background(context)) # استدعاء save_data_in_background
        logger.info(f"[{chat_id}] Places count {places} saved for order {order_id_to_process}. Current user_data: {json.dumps(context.user_data.get(user_id), indent=2)}")

        if user_id in context.user_data and 'messages_to_delete' in context.user_data[user_id]:
            logger.info(f"[{chat_id}] Scheduling deletion of {len(context.user_data[user_id].get('messages_to_delete', []))} old messages after showing final options for user {user_id}.")
            for msg_info in context.user_data[user_id]['messages_to_delete']:
                context.application.create_task(delete_message_in_background(context, chat_id=msg_info['chat_id'], message_id=msg_info['message_id']))
            context.user_data[user_id]['messages_to_delete'].clear()
        
        await show_final_options(chat_id, context, user_id, order_id_to_process, message_prefix="تم تحديث عدد المحلات بنجاح.")
        
        if user_id in context.user_data and "current_active_order_id" in context.user_data[user_id]:
            del context.user_data[user_id]["current_active_order_id"]
            logger.info(f"[{chat_id}] Cleared current_active_order_id for user {user_id} after processing places count.")

        return ConversationHandler.END 
    except Exception as e:
        logger.error(f"[{chat_id}] Error in handle_places_count_data: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text="عذراً، حدث خطأ أثناء معالجة عدد المحلات. الرجاء بدء طلبية جديدة.")
        return ConversationHandler.END

from urllib.parse import quote

async def show_final_options(chat_id, context, user_id, order_id, message_prefix=None):
    global daily_profit, orders, invoice_numbers, pricing, OWNER_ID, OWNER_PHONE_NUMBER
    try:
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

        import re
        delivery_fee = 0.0
        title = order.get('title', '')
        delivery_fee = get_delivery_price(title) 

        final_total = total_sell + extra_cost + delivery_fee

        logger.info(f"[{chat_id}] Daily profit before addition for order {order_id}: {daily_profit}")
        daily_profit += net_profit
        logger.info(f"[{chat_id}] Daily profit after adding {net_profit} for order {order_id}: {daily_profit}")
        context.application.create_task(context.application.save_data_in_background(context)) # استدعاء save_data_in_background

        customer_invoice_lines = [
            "**أبو الأكبر للتوصيل**",
            f"رقم الفاتورة: {invoice}",
            f"عنوان الزبون: {order['title']}",
            "\n*المواد:*"
        ]
        running_total_for_customer = 0.0
        for p in order["products"]:
            if p in pricing.get(order_id, {}) and "sell" in pricing[order_id][p]:
                sell = pricing[order_id][p]["sell"]
                running_total_for_customer += sell
                customer_invoice_lines.append(f"{p} - {format_float(sell)} = {format_float(running_total_for_customer)}")
            else:
                customer_invoice_lines.append(f"{p} - (لم يتم تسعيره)")

        customer_invoice_lines.append(f"كلفة تجهيز من - {current_places} محلات {format_float(extra_cost)}")
        if delivery_fee > 0:
            customer_invoice_lines.append(f"أجرة التوصيل: {format_float(delivery_fee)}")
        customer_invoice_lines.append(f"\n*المجموع الكلي:* {format_float(final_total)} (مع احتساب عدد المحلات والتوصيل)")
        customer_final_text = "\n".join(customer_invoice_lines)

        invoices_dir = "invoices"
        os.makedirs(invoices_dir, exist_ok=True)
        try:
            customer_invoice_filename = os.path.join(invoices_dir, f"invoice_{invoice}_customer.txt")
            with open(customer_invoice_filename, "w", encoding="utf-8") as f:
                f.write("فاتورة الزبون\n" + "="*40 + "\n" + customer_final_text)
            logger.info(f"[{chat_id}] Saved customer invoice to {customer_invoice_filename}")
        except Exception as e:
            logger.error(f"[{chat_id}] Failed to save customer invoice to file: {e}")

        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=customer_final_text,
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"[{chat_id}] Could not send customer invoice as message: {e}")

        owner_invoice_details = [
            f"رقم الفاتورة: {invoice}",
            f"عنوان الزبون: {order['title']}"
        ]
        for p in order["products"]:
            if p in pricing.get(order_id, {}) and "buy" in pricing[order_id][p] and "sell" in pricing[order_id][p]:
                buy = pricing[order_id][p]["buy"]
                sell = pricing[order_id][p]["sell"]
                profit_item = sell - buy
                owner_invoice_details.append(f"{p} - شراء: {format_float(buy)}, بيع: {format_float(sell)}, ربح: {format_float(profit_item)}")
            else:
                owner_invoice_details.append(f"{p} - (لم يتم تسعيره بعد)")

        owner_invoice_details.extend([
            f"\nالمجموع شراء: {format_float(total_buy)}",
            f"المجموع بيع: {format_float(total_sell)}",
            f"الربح الكلي: {format_float(net_profit)}",
            f"عدد المحلات: {current_places} (+{format_float(extra_cost)})"
        ])
        if delivery_fee > 0:
            owner_invoice_details.append(f"أجرة التوصيل: {format_float(delivery_fee)}")
        owner_invoice_details.append(f"السعر الكلي: {format_float(final_total)}")

        final_owner_invoice_text = "\n".join(owner_invoice_details)

        try:
            invoice_filename = os.path.join(invoices_dir, f"invoice_{invoice}_admin.txt")
            with open(invoice_filename, "w", encoding="utf-8") as f:
                f.write("فاتورة الإدارة\n" + "="*40 + "\n" + final_owner_invoice_text)
            logger.info(f"[{chat_id}] Saved invoice to {invoice_filename}")
        except Exception as e:
            logger.error(f"[{chat_id}] Failed to save admin invoice to file: {e}")

        encoded_owner_invoice = quote(final_owner_invoice_text, safe='')
        encoded_customer_text = quote(customer_final_text, safe='')

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
        except Exception as e:
            logger.error(f"[{chat_id}] Could not send admin invoice to OWNER_ID {OWNER_ID}: {e}")
            await context.bot.send_message(chat_id=chat_id, text="عذراً، لم أتمكن من إرسال فاتورة الإدارة إلى خاصك.")

        keyboard = [
            [InlineKeyboardButton("1️⃣ تعديل الأسعار", callback_data=f"edit_prices_{order_id}")],
            [InlineKeyboardButton("3️⃣ إرسال فاتورة الزبون (واتساب)", url=f"https://wa.me/{OWNER_PHONE_NUMBER}?text={encoded_customer_text}")],
            [InlineKeyboardButton("4️⃣ إنشاء طلب جديد", callback_data="start_new_order")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        message_text = "افعل ما تريد من الأزرار:\n\n"
        if message_prefix:
            message_text = message_prefix + "\n" + message_text

        await context.bot.send_message(chat_id=chat_id, text=message_text, reply_markup=reply_markup, parse_mode="Markdown")

        if user_id in context.user_data:
            context.user_data[user_id].pop("order_id", None)
            context.user_data[user_id].pop("product", None)
            context.user_data[user_id].pop("current_active_order_id", None)
            context.user_data[user_id].pop("messages_to_delete", None)

    except Exception as e:
        logger.error(f"[{chat_id}] Error in show_final_options: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text="عذراً، حدث خطأ أثناء عرض الفاتورة النهائية. الرجاء بدء طلبية جديدة.")
    
async def edit_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global orders, pricing # تأكيد الوصول كـ global
    try:
        query = update.callback_query
        await query.answer()
        
        user_id = str(query.from_user.id)
        logger.info(f"[{query.message.chat_id}] Edit prices callback from user {user_id}: {json.dumps(context.user_data.get(user_id, {}), indent=2)}")
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
            context.user_data[user_id].pop("buy_price", None) 
            logger.info(f"Cleared order-specific user_data for user {user_id} on /start command. User data after clean: {json.dumps(context.user_data.get(user_id, {}), indent=2)}")

        if query.message:
            context.application.create_task(delete_message_in_background(context, chat_id=query.message.chat_id, message_id=query.message.message_id))

        await query.message.reply_text("تمام، دز الطلبية الجديدة كلها برسالة واحدة.\n\n*السطر الأول:* عنوان الزبون.\n*الأسطر الباقية:* كل منتج بسطر واحد.", parse_mode="Markdown")
        
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in start_new_order_callback: {e}", exc_info=True)
        await update.callback_query.message.reply_text("عذراً، حدث خطأ أثناء بدء طلب جديد. الرجاء المحاولة مرة أخرى.")
        return ConversationHandler.END


async def show_profit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global daily_profit, OWNER_ID
    try:
        if str(update.message.from_user.id) != str(OWNER_ID):
            await update.message.reply_text("عذراً، هذا الأمر متاح للمالك فقط.")
            return
        logger.info(f"Current daily_profit requested by user {update.message.from_user.id}: {daily_profit}")
        await update.message.reply_text(f"الربح التراكمي الإجمالي: *{format_float(daily_profit)}* دينار", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in show_profit: {e}", exc_info=True)
        await update.message.reply_text("عذراً، حدث خطأ أثناء عرض الأرباح.")

async def reset_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global OWNER_ID
    try:
        if str(update.message.from_user.id) != str(OWNER_ID):
            await update.message.reply_text("عذراً، هذا الأمر متاح للمالك فقط.")
            return
        
        keyboard = [
            [InlineKeyboardButton("نعم، متأكد", callback_data="confirm_reset")],
            [InlineKeyboardButton("لا، إلغاء", callback_data="cancel_reset")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("هل أنت متأكد من تصفير جميع الأرباح ومسح كل الطلبات؟ هذا الإجراء لا يمكن التراجع عنه.", reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in reset_all: {e}", exc_info=True)
        await update.message.reply_text("عذراً، حدث خطأ أثناء محاولة التصفير.")

async def confirm_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global daily_profit, orders, pricing, invoice_numbers, last_button_message, OWNER_ID
    try:
        query = update.callback_query
        await query.answer()

        if str(query.from_user.id) != str(OWNER_ID):
            await query.edit_message_text("عذراً، لا تملك صلاحية لتنفيذ هذا الأمر.")
            return

        if query.data == "confirm_reset":
            logger.info(f"Daily profit before reset: {daily_profit}")
            daily_profit = 0.0
            orders.clear()
            pricing.clear()
            invoice_numbers.clear()
            last_button_message.clear()
            
            try:
                with open(COUNTER_FILE, "w") as f:
                    f.write("1")
            except Exception as e:
                logger.error(f"Could not reset invoice counter file: {e}", exc_info=True)

            _save_data_to_disk()
            logger.info(f"Daily profit after reset: {daily_profit}")
            await query.edit_message_text("تم تصفير الأرباح ومسح كل الطلبات بنجاح.")
        elif query.data == "cancel_reset":
            await query.edit_message_text("تم إلغاء عملية التصفير.")
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in confirm_reset: {e}", exc_info=True)
        await update.callback_query.message.reply_text("عذراً، حدث خطأ أثناء عملية التصفير.")

async def show_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global orders, pricing, invoice_numbers, OWNER_ID, daily_profit
    try:
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
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in show_report: {e}", exc_info=True)
        await update.message.reply_text("عذراً، حدث خطأ أثناء عرض التقرير.")

def main():
    app = ApplicationBuilder().token(TOKEN).build()

    # ✅ ConversationHandler لإدارة المناطق (إضافة وحذف باستخدام الدوال الجديدة)
    zone_management_conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(ask_zone_name, pattern="^add_zone$"),
            CallbackQueryHandler(ask_zone_name, pattern="^remove_zone$"),
        ],
        states={
            "WAITING_FOR_ZONE_ACTION_INPUT": [ 
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_zone_edit)
            ],
        },
        fallbacks=[
            CommandHandler("cancel", lambda u, c: ConversationHandler.END),
            MessageHandler(filters.ALL, lambda u, c: ConversationHandler.END)
        ]
    )
    app.add_handler(zone_management_conv_handler)


    # ✅ Handlers خارج المحادثة
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("profit", show_profit))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^الارباح$|^ارباح$"), show_profit))
    app.add_handler(CommandHandler("reset", reset_all))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^صفر$|^تصفير$"), reset_all))
    app.add_handler(CallbackQueryHandler(confirm_reset, pattern="^(confirm_reset|cancel_reset)$"))
    app.add_handler(CommandHandler("report", show_report))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^التقارير$|^تقرير$|^تقارير$"), show_report))
    
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE, edited_message))
    app.add_handler(CallbackQueryHandler(edit_prices, pattern="^edit_prices_"))
    app.add_handler(CallbackQueryHandler(start_new_order_callback, pattern="^start_new_order$"))
    
    app.add_handler(CommandHandler("zones", list_zones))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"^(مناطق|المناطق)$"), list_zones))
    # ✅ إضافة أمر جديد لإضافة المناطق دفعة واحدة
    app.add_handler(CommandHandler("add_zones_bulk", add_zones_bulk))


    # ✅ ConversationHandler لعدد المحلات
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

    # ✅ ConversationHandler لإنشاء وتسعير الطلبات
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

    # ✅ تشغيل البوت
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
