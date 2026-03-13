# -*- coding: utf-8 -*-
"""
المنطق القديم: الطلبات العادية (أول سطر = عنوان/منطقة، ثاني سطر = رقم، ثم المنتجات).
يحتوي على استلام الطلب ومعالجته وعرض الأزرار. main يوجّه الرسائل اللي بدايتها مو «اسم الزبون: » إلى هذا الملف فقط.
"""
import json
import logging
import uuid
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

logger = logging.getLogger(__name__)


def _get_invoice_number(context):
    """استدعاء دالة رقم الفاتورة من bot_data (معرّفة في main)."""
    fn = context.application.bot_data.get("get_invoice_number")
    return fn() if fn else 0


def _save_data_in_background(context):
    """جدولة الحفظ من bot_data (معرّف في main)."""
    fn = context.application.bot_data.get("save_data_in_background")
    if fn:
        context.application.create_task(fn(context))


def _delete_message_in_background(context, chat_id, message_id):
    """حذف رسالة في الخلفية من bot_data (معرّف في main)."""
    fn = context.application.bot_data.get("delete_message_in_background")
    if fn:
        context.application.create_task(fn(context, chat_id=chat_id, message_id=message_id))


async def receive_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استلام رسالة الطلبية بالصيغة القديمة (عنوان، رقم، منتجات)."""
    try:
        logger.info(
            f"[{update.effective_chat.id}] Processing order from: {update.effective_user.id} - "
            f"Message ID: {update.message.message_id}. User data: {json.dumps(context.user_data.get(str(update.effective_user.id), {}), indent=2)}"
        )
        await process_order(update, context, update.message)
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"[{update.effective_chat.id}] Error in receive_order: {e}", exc_info=True)
        await update.message.reply_text("ماكدرت اعالج الطلب عاجبك لوتحاول مره ثانيه لو ادز طلب جديد ولا تصفن.")
        return ConversationHandler.END


async def process_order(update, context, message, edited=False):
    """معالجة نص الطلبية: عنوان، رقم، قائمة منتجات."""
    orders = context.application.bot_data["orders"]
    pricing = context.application.bot_data["pricing"]
    invoice_numbers = context.application.bot_data["invoice_numbers"]
    last_button_message = context.application.bot_data["last_button_message"]

    user_id = str(message.from_user.id)
    lines = [line.strip() for line in message.text.strip().split("\n") if line.strip()]

    if len(lines) < 3:
        if not edited:
            await message.reply_text(
                "باعلي تاكد انك تكتب الطلبية ك التالي اول سطر هو عنوان الزبون وثاني سطر هو رقم الزبون وراها المنتجات كل سطر بي منتج يالله فر ويلك وسوي الطلب."
            )
        return

    title = lines[0]
    phone_number_raw = lines[1].strip().replace(" ", "")
    if phone_number_raw.startswith("+964"):
        phone_number = "0" + phone_number_raw[4:]
    else:
        phone_number = phone_number_raw.replace("+", "")
    products = [p.strip() for p in lines[2:] if p.strip()]

    if not products:
        if not edited:
            await message.reply_text("يابه لازم المنتجات ورا رقم الهاتف .")
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
        invoice_no = _get_invoice_number(context)
        orders[order_id] = {
            "user_id": user_id,
            "title": title,
            "phone_number": phone_number,
            "products": products,
            "places_count": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        pricing[order_id] = {p: {} for p in products}
        invoice_numbers[order_id] = invoice_no
        logger.info(f"Created new order {order_id} for user {user_id}.")
    else:
        old_products = set(orders[order_id].get("products", []))
        new_products = set(products)
        orders[order_id]["title"] = title
        orders[order_id]["phone_number"] = phone_number
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

    _save_data_in_background(context)

    if is_new_order:
        await message.reply_text(
            f"طلب : *{title}*\n(الرقم: `{phone_number}` )\n(عدد المنتجات: {len(products)})",
            parse_mode="Markdown",
        )
        await show_buttons(message.chat_id, context, user_id, order_id)
    else:
        await show_buttons(
            message.chat_id, context, user_id, order_id,
            confirmation_message="دهاك حدثنه الطلب. عيني دخل الاسعار الاستاذ حدث الطلب.",
        )


async def show_buttons(chat_id, context, user_id, order_id, confirmation_message=None):
    """عرض أزرار تسعير الطلبية (المنطق القديم)."""
    orders = context.application.bot_data["orders"]
    pricing = context.application.bot_data["pricing"]
    last_button_message = context.application.bot_data["last_button_message"]

    try:
        if order_id not in orders:
            await context.bot.send_message(chat_id=chat_id, text="❌ الطلب غير موجود.")
            return

        order = orders[order_id]
        final_buttons_list = []
        final_buttons_list.append([
            InlineKeyboardButton("➕ إضافة منتج", callback_data=f"add_product_to_order_{order_id}"),
            InlineKeyboardButton("🗑️ مسح منتج", callback_data=f"delete_specific_product_{order_id}"),
        ])

        completed_products_buttons = []
        pending_products_buttons = []
        user_data = context.user_data.get(user_id, {})
        edited_list = user_data.get("edited_products_list", [])
        editing_mode = user_data.get("editing_mode", False)

        for i, p_name in enumerate(order["products"]):
            callback_data_for_product = f"{order_id}|{i}"
            is_priced = p_name in pricing.get(order_id, {}) and "buy" in pricing[order_id].get(p_name, {})
            if is_priced:
                button_text = f"✏️✅ {p_name}" if p_name in edited_list else f"✅ {p_name}"
                completed_products_buttons.append([InlineKeyboardButton(button_text, callback_data=callback_data_for_product)])
            else:
                pending_products_buttons.append([InlineKeyboardButton(p_name, callback_data=callback_data_for_product)])

        final_buttons_list.extend(completed_products_buttons)
        final_buttons_list.extend(pending_products_buttons)

        if editing_mode:
            final_buttons_list.append([
                InlineKeyboardButton("🏪 تعديل المحلات", callback_data=f"done_editing_{order_id}"),
            ])
            final_buttons_list.append([
                InlineKeyboardButton("💾 حفظ واكتمل التعديل", callback_data=f"cancel_edit_{order_id}"),
            ])

        markup = InlineKeyboardMarkup(final_buttons_list)
        message_text = f"{confirmation_message}\n\n" if confirmation_message else ""
        status_text = "🔧 وضع التعديل حالياً" if editing_mode else "📝 تسعير الطلب"
        message_text += f"*{status_text}* ({order['title']}):\nاختر منتجاً لتعديل سعره:"

        msg_info = last_button_message.get(order_id)
        if msg_info:
            _delete_message_in_background(context, chat_id=msg_info["chat_id"], message_id=msg_info["message_id"])

        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=message_text,
            reply_markup=markup,
            parse_mode="Markdown",
        )
        last_button_message[order_id] = {"chat_id": chat_id, "message_id": msg.message_id}
        _save_data_in_background(context)

        if "messages_to_delete" in user_data:
            for m_info in user_data["messages_to_delete"]:
                _delete_message_in_background(context, chat_id=m_info["chat_id"], message_id=m_info["message_id"])
            user_data["messages_to_delete"].clear()

    except Exception as e:
        logger.error(f"Error in show_buttons: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text="⚠️ حدث خطأ في عرض قائمة المنتجات.")
