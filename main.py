# main.py
# Compatible with python-telegram-bot == 20.8
# Main bot file for "أبو الأكبر للتوصيل" features:
# - orders/pricing storage in /mnt/data/
# - products stored as {"id","name"} (auto-migrate strings)
# - add/delete product buttons above products
# - price input flow
# - choose places count (1..10)
# - compute shop fee and delivery price from data/delivery_zones.json
# - build/send invoices to group & private (supplier/admin)
# - WebApp button to open external dashboard link
# - safe background save / delete message utilities

import os
import json
import uuid
import time
import threading
import logging
import asyncio
from datetime import datetime
from typing import Dict, Any

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
)

# ---------- CONFIG ----------
DATA_DIR = "/mnt/data"
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

ORDERS_FILE = os.path.join(DATA_DIR, "orders.json")
PRICING_FILE = os.path.join(DATA_DIR, "pricing.json")
LAST_BUTTON_MESSAGE_FILE = os.path.join(DATA_DIR, "last_button_message.json")
INVOICE_COUNTER_FILE = os.path.join(DATA_DIR, "invoice_counter.json")

DELIVERY_ZONES_FILE = os.path.join("data", "delivery_zones.json")  # as provided

TOKEN = os.getenv("TOKEN")  # must be set in Railway

# ---------- LOGGING ----------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------- IN-MEMORY DATA ----------
# these will be loaded from disk at startup
orders: Dict[str, Dict[str, Any]] = {}
pricing: Dict[str, Dict[str, Dict[str, float]]] = {}
last_button_message: Dict[str, Dict[str, int]] = {}
invoice_counter: Dict[str, int] = {}

# ---------- STATE ----------
ASK_BUY = 1

# ---------- SAVE LOCK / TIMER ----------
save_lock = threading.Lock()
_save_timer = None
_save_pending = False


# ---------- UTIL: load / save ----------
def safe_load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Could not load JSON {path}: {e}", exc_info=True)
    return default


def safe_save_json(path, data):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception as e:
        logger.error(f"Could not save JSON {path}: {e}", exc_info=True)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except:
            pass
    return False


def load_all_data():
    global orders, pricing, last_button_message, invoice_counter
    orders = safe_load_json(ORDERS_FILE, {})
    pricing = safe_load_json(PRICING_FILE, {})
    last_button_message = safe_load_json(LAST_BUTTON_MESSAGE_FILE, {})
    invoice_counter = safe_load_json(INVOICE_COUNTER_FILE, {"counter": 0})
    logger.info("Loaded data: orders=%d pricing=%d", len(orders), len(pricing))


def save_all_data_now():
    global orders, pricing, last_button_message, invoice_counter
    with save_lock:
        safe_save_json(ORDERS_FILE, orders)
        safe_save_json(PRICING_FILE, pricing)
        safe_save_json(LAST_BUTTON_MESSAGE_FILE, last_button_message)
        safe_save_json(INVOICE_COUNTER_FILE, invoice_counter)
        logger.info("All data (global) saved to disk successfully.")


def schedule_save_data(context: ContextTypes.DEFAULT_TYPE, delay: float = 0.5):
    """
    Schedule background save: call context.application.create_task(save_data_in_background(context))
    Use this convenience to avoid race conditions.
    """
    # We will use context.application.create_task(save_data_in_background(context)) where needed
    context.application.create_task(save_data_in_background(context))


async def save_data_in_background(context: ContextTypes.DEFAULT_TYPE):
    # run blocking save in thread to avoid blocking event loop
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, save_all_data_now)


# ---------- UTIL: delivery zones ----------
def get_delivery_price_for_address(address: str) -> int:
    try:
        path = DELIVERY_ZONES_FILE
        with open(path, "r", encoding="utf-8") as f:
            zones = json.load(f)
    except Exception as e:
        logger.error(f"Could not read delivery zones file: {e}", exc_info=True)
        return 0
    if not address:
        return 0
    # match longest keys first
    keys = sorted(zones.keys(), key=lambda x: -len(x))
    addr_text = address.strip()
    for k in keys:
        if k and k in addr_text:
            try:
                return int(zones[k])
            except Exception:
                continue
    return 0


def shop_fee_from_places(places: int) -> int:
    try:
        p = int(places)
    except Exception:
        return 0
    return max(0, p - 2)


# ---------- UTIL: messages cleanup ----------
async def delete_message_in_background(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        # Message might be already deleted or can't be removed
        logger.warning(f"Could not delete message {message_id} from chat {chat_id} in background: {e}")


async def delete_previous_messages(context: ContextTypes.DEFAULT_TYPE, user_id: str):
    # delete messages stored under user_data[user_id]['messages_to_delete']
    try:
        msgs = context.user_data.get(user_id, {}).get("messages_to_delete", [])
        for m in msgs:
            try:
                await delete_message_in_background(context, chat_id=m.get("chat_id"), message_id=m.get("message_id"))
            except Exception:
                pass
        context.user_data.setdefault(user_id, {})["messages_to_delete"] = []
    except Exception:
        pass


# ---------- UTIL: invoice numbering ----------
def next_invoice_number():
    invoice_counter.setdefault("counter", 0)
    invoice_counter["counter"] += 1
    safe_save_json(INVOICE_COUNTER_FILE, invoice_counter)
    return invoice_counter["counter"]


# ---------- BUILD INVOICE TEXTS ----------
def build_group_invoice_text(order_id: str, orders_map: dict, pricing_map: dict, places_count: int) -> str:
    order = orders_map.get(order_id, {})
    products = order.get("products", [])
    customer_name = order.get("title", "بدون عنوان")
    customer_phone = order.get("phone") or order.get("customer_phone") or ""
    invoice_num = order.get("invoice_number", order_id)
    header = f"📋\n-----------------------------------\nفاتورة رقم: #{invoice_num}\n🏠 عنوان الزبون: {customer_name}\n📞 رقم الزبون: {customer_phone}\n🛍️ المنتجات:\n"
    lines = []
    cumulative = 0
    for p in products:
        if isinstance(p, dict):
            pid = p.get("id")
            pname = p.get("name")
            qty = p.get("qty", 1)
        else:
            pid = None
            pname = str(p)
            qty = 1
        pr = pricing_map.get(order_id, {}).get(pid, {}) if pid else {}
        sell = pr.get("sell", 0) or 0
        line_value = sell * qty
        prev = cumulative
        cumulative = cumulative + line_value
        lines.append(f" – {pname} بـ{sell}• {prev}+{line_value}= {cumulative} 💵")
    shop_fee = shop_fee_from_places(order.get("places_count", 1))
    prev = cumulative
    cumulative += shop_fee
    shop_line = f" – 📦 التجهيز: من {order.get('places_count', 1)} محلات بـ {shop_fee}• {prev}+{shop_fee}= {cumulative} 💵"
    delivery_price = get_delivery_price_for_address(order.get("title", ""))
    prev2 = cumulative
    cumulative_with_delivery = cumulative + delivery_price
    delivery_line = f" – 🚚 التوصيل: بـ {delivery_price}• {prev2}+{delivery_price}= {cumulative_with_delivery} 💵"
    footer = "-----------------------------------\n"
    footer += f"✨ المجموع الكلي: ✨\nبدون التوصيل = {cumulative} 💵\nمــــع التوصيل = {cumulative_with_delivery} 💵\nشكراً لاختياركم! ❤️"
    return header + "\n".join(lines) + "\n" + shop_line + "\n" + delivery_line + "\n" + footer


def build_supplier_invoice_text(order_id: str, orders_map: dict, pricing_map: dict, places_count: int) -> str:
    order = orders_map.get(order_id, {})
    products = order.get("products", [])
    customer_name = order.get("title", "بدون عنوان")
    customer_phone = order.get("phone") or order.get("customer_phone") or ""
    invoice_num = order.get("invoice_number", order_id)
    header = "فاتورة الشراء:🧾💸\n"
    header += f"رقم الفاتورة🔢: {invoice_num}\n"
    header += f"عنوان الزبون🏠: {customer_name}\n"
    header += f"رقم الزبون📞:{customer_phone}\n"
    header += "تفاصيل الشراء:🗒️💸\n"
    total_buy = 0
    lines = []
    for p in products:
        if isinstance(p, dict):
            pid = p.get("id")
            pname = p.get("name")
            qty = p.get("qty", 1)
        else:
            pid = None
            pname = str(p)
            qty = 1
        pr = pricing_map.get(order_id, {}).get(pid, {}) if pid else {}
        buy = pr.get("buy", 0) or 0
        total_buy += buy * qty
        lines.append(f" - {pname}: {buy}")
    footer = f"\nمجموع كلفة الشراء للطلبية:💸 {total_buy}\n"
    return header + "\n".join(lines) + footer


def build_admin_invoice_text(order_id: str, orders_map: dict, pricing_map: dict, places_count: int) -> str:
    order = orders_map.get(order_id, {})
    products = order.get("products", [])
    customer_phone = order.get("phone") or order.get("customer_phone") or ""
    customer_name = order.get("title", "بدون عنوان")
    invoice_num = order.get("invoice_number", order_id)
    header = f"فاتورة الإدارة:👨🏻‍💼\nرقم الفاتورة🔢: {invoice_num}\nرقم الزبون📞: {customer_phone}\nعنوان الزبون🏠: {customer_name}\n**تفاصيل الطلبية:🗒**\n"
    total_buy = 0
    total_sell = 0
    lines = []
    for p in products:
        if isinstance(p, dict):
            pid = p.get("id")
            pname = p.get("name")
            qty = p.get("qty", 1)
        else:
            pid = None
            pname = str(p)
            qty = 1
        pr = pricing_map.get(order_id, {}).get(pid, {}) if pid else {}
        buy = pr.get("buy", 0) or 0
        sell = pr.get("sell", 0) or 0
        profit = sell - buy
        total_buy += buy * qty
        total_sell += sell * qty
        lines.append(f"- {pname}: شراء {buy} | بيع {sell} | ربح {profit}")
    shop_fee = shop_fee_from_places(order.get("places_count", 1))
    delivery_price = get_delivery_price_for_address(order.get("title", ""))
    total_profit_products = total_sell - total_buy
    admin_total = total_sell + shop_fee + delivery_price
    footer = f"**إجمالي الشراء:💸** {total_buy}\n**إجمالي البيع:💵** {total_sell}\n**ربح المنتجات:💲** {total_profit_products}\n**ربح المحلات ( {order.get('places_count', 1)} محل):🏪** {shop_fee}\n**أجرة التوصيل:🚚** {delivery_price}\n**المجموع الكلي:💰** {admin_total}\n"
    return header + "\n".join(lines) + "\n" + footer


# ---------- CORE UI: show_buttons ----------
async def show_buttons(chat_id: int, context: ContextTypes.DEFAULT_TYPE, user_id: str, order_id: str, confirmation_message: str = None):
    orders_map = context.application.bot_data.setdefault("orders", orders)
    pricing_map = context.application.bot_data.setdefault("pricing", pricing)
    last_msg_map = context.application.bot_data.setdefault("last_button_message", last_button_message)
    try:
        if order_id not in orders_map:
            await context.bot.send_message(chat_id, "الطلبية مموجودة.")
            return

        order = orders_map[order_id]

        # migrate old products (string -> dict)
        new_products = []
        for product in order.get("products", []):
            if isinstance(product, str):
                new_products.append({"id": uuid.uuid4().hex[:8], "name": product})
            elif isinstance(product, dict):
                # ensure has id/name
                if "id" not in product:
                    product["id"] = uuid.uuid4().hex[:8]
                if "name" not in product:
                    product["name"] = str(product)
                new_products.append(product)
            else:
                new_products.append({"id": uuid.uuid4().hex[:8], "name": str(product)})
        order["products"] = new_products

        buttons = []

        # add / delete buttons on top
        buttons.append([InlineKeyboardButton("➕ إضافة منتج", callback_data=f"add_product_to_order_{order_id}")])
        buttons.append([InlineKeyboardButton("🗑️ حذف منتج", callback_data=f"delete_specific_product_{order_id}")])

        completed = []
        pending = []
        for product in order["products"]:
            p_id = product["id"]
            p_name = product["name"]
            if p_id in pricing_map.get(order_id, {}) and "buy" in pricing_map[order_id][p_id] and "sell" in pricing_map[order_id][p_id]:
                completed.append([InlineKeyboardButton(f"✅ {p_name}", callback_data=f"{order_id}|{p_id}")])
            else:
                pending.append([InlineKeyboardButton(f"{p_name}", callback_data=f"{order_id}|{p_id}")])

        buttons.extend(completed)
        buttons.extend(pending)

        # final options bottom
        buttons.append([InlineKeyboardButton("📄 عرض الفاتورة", callback_data=f"final_options_{order_id}")])

        markup = InlineKeyboardMarkup(buttons)

        text = ""
        if confirmation_message:
            text += confirmation_message + "\n\n"
        text += f"دوس على منتج واكتب سعره *{order.get('title','')}*:"
        # delete previous last button message if exists
        prev = last_msg_map.get(order_id)
        if prev:
            try:
                context.application.create_task(delete_message_in_background(context, chat_id=prev.get("chat_id"), message_id=prev.get("message_id")))
            except Exception:
                pass

        msg = await context.bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")
        last_msg_map[order_id] = {"chat_id": chat_id, "message_id": msg.message_id}
        context.application.create_task(save_data_in_background(context))
    except Exception as e:
        logger.error(f"[{chat_id}] Error in show_buttons: {e}", exc_info=True)
        await context.bot.send_message(chat_id, "خطأ بعرض الأزرار.")


# ---------- Handler: when pressing a product ----------
async def product_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    orders_map = context.application.bot_data.setdefault("orders", orders)
    pricing_map = context.application.bot_data.setdefault("pricing", pricing)
    try:
        query = update.callback_query
        await query.answer()
        user_id = str(query.from_user.id)
        context.user_data.setdefault(user_id, {})
        context.user_data[user_id].setdefault("messages_to_delete", [])

        # record that the current buttons message can be deleted later
        context.user_data[user_id]["messages_to_delete"].append({
            "chat_id": query.message.chat_id,
            "message_id": query.message.message_id
        })

        # parse callback_data: order_id|product_id
        parts = query.data.split("|", 1)
        if len(parts) != 2:
            await query.edit_message_text("صيغة زر المنتج غير صحيحة.")
            return ConversationHandler.END
        order_id, product_id = parts

        if order_id not in orders_map:
            await query.edit_message_text("الطلبية مموجودة.")
            return ConversationHandler.END

        # find product object in order (migrate strings if needed)
        product_obj = None
        for p in orders_map[order_id].get("products", []):
            if isinstance(p, dict) and p.get("id") == product_id:
                product_obj = p
                break
            if isinstance(p, str) and p == product_id:
                new_p = {"id": uuid.uuid4().hex[:8], "name": p}
                idx = orders_map[order_id]["products"].index(p)
                orders_map[order_id]["products"][idx] = new_p
                product_obj = new_p
                product_id = new_p["id"]
                break

        if product_obj is None:
            # try matching by id string inside a dict item
            for p in orders_map[order_id].get("products", []):
                if isinstance(p, dict) and str(p.get("id")) == str(product_id):
                    product_obj = p
                    product_id = p.get("id")
                    break

        if product_obj is None:
            await query.edit_message_text("هذا المنتج مموجود أو صار خلل.")
            return ConversationHandler.END

        p_name = product_obj.get("name", str(product_id))

        # ensure user_data structures
        context.user_data.setdefault(user_id, {})
        context.user_data[user_id]["order_id"] = order_id
        context.user_data[user_id]["product"] = product_id
        context.user_data[user_id].pop("buy_price", None)

        # prompt for buy/sell
        current_buy = pricing_map.get(order_id, {}).get(product_id, {}).get("buy")
        current_sell = pricing_map.get(order_id, {}).get(product_id, {}).get("sell")
        if current_buy is not None and current_sell is not None:
            message_prompt = f"سعر *'{p_name}'* حالياً هو شراء: {current_buy}، بيع: {current_sell}.\nابعث سعر الشراء الجديد بالسطر الأول، وسعر البيع بالسطر الثاني؟ (أو دز نفس الأسعار إذا ماكو تغيير)"
        else:
            message_prompt = (
                f"تمام، بيش اشتريت *'{p_name}'*؟ (بالسطر الأول)\n"
                f"وبييش راح تبيعه؟ (بالسطر الثاني)\n\n"
                f"💡 **إذا كان سعر الشراء هو نفسه سعر البيع،** اكتب الرقم مرة واحدة فقط."
            )

        msg = await query.message.reply_text(message_prompt, parse_mode="Markdown")
        context.user_data[user_id]["messages_to_delete"].append({
            "chat_id": msg.chat_id,
            "message_id": msg.message_id
        })
        return ASK_BUY
    except Exception as e:
        logger.error(f"product_selected error: {e}", exc_info=True)
        try:
            await query.message.reply_text("حدث خطأ باختيار المنتج. رجاءً جرّب مرة ثانية.")
        except:
            pass
        return ConversationHandler.END


# ---------- Handler: receive buy/sell prices ----------
async def receive_buy_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    chat_id = update.effective_chat.id
    orders_map = context.application.bot_data.setdefault("orders", orders)
    pricing_map = context.application.bot_data.setdefault("pricing", pricing)
    try:
        # remove previous messages if any
        try:
            await delete_previous_messages(context, user_id)
        except Exception:
            pass

        context.user_data.setdefault(user_id, {})
        context.user_data[user_id].setdefault("messages_to_delete", [])

        order_id = context.user_data[user_id].get("order_id")
        product_ref = context.user_data[user_id].get("product")
        if not order_id or order_id not in orders_map:
            await update.message.reply_text("❌ لم يتم تحديد طلبية أو الطلبية قديمة. أرسل الطلب من جديد.")
            return ConversationHandler.END
        if not product_ref:
            await update.message.reply_text("❌ لم يتم تحديد المنتج. اضغط على اسم المنتج من الأزرار أولاً.")
            return ConversationHandler.END

        # save this message to delete later
        context.user_data[user_id]["messages_to_delete"].append({
            "chat_id": update.message.chat_id,
            "message_id": update.message.message_id
        })

        lines = [line.strip() for line in update.message.text.split("\n") if line.strip()]
        buy_price_str = None
        sell_price_str = None
        if len(lines) == 2:
            buy_price_str, sell_price_str = lines[0], lines[1]
        elif len(lines) == 1:
            parts = [p.strip() for p in lines[0].split() if p.strip()]
            if len(parts) == 2:
                buy_price_str, sell_price_str = parts[0], parts[1]
            elif len(parts) == 1:
                buy_price_str = sell_price_str = parts[0]

        if buy_price_str is None or sell_price_str is None:
            msg_error = await update.message.reply_text("😒 دخل سعر الشراء بالسطر الأول وسعر البيع بالسطر الثاني، أو قيمة واحدة إذا متساويين.")
            context.user_data[user_id]["messages_to_delete"].append({"chat_id": msg_error.chat_id, "message_id": msg_error.message_id})
            return ASK_BUY

        try:
            buy_price = float(buy_price_str)
            sell_price = float(sell_price_str)
            if buy_price < 0 or sell_price < 0:
                raise ValueError("الأسعار لا يمكن أن تكون سالبة.")
        except Exception:
            msg_error = await update.message.reply_text("😒 دخّل أرقام صحيحة للشراء والبيع.")
            context.user_data[user_id]["messages_to_delete"].append({"chat_id": msg_error.chat_id, "message_id": msg_error.message_id})
            return ASK_BUY

        # resolve product object inside order
        product_obj = None
        product_id = None
        for p in orders_map[order_id].get("products", []):
            if isinstance(p, dict) and p.get("id") == product_ref:
                product_obj = p
                product_id = p["id"]
                break

        if product_obj is None:
            for p in orders_map[order_id].get("products", []):
                if isinstance(p, dict) and p.get("name") == product_ref:
                    product_obj = p
                    product_id = p["id"]
                    break
                if isinstance(p, str) and p == product_ref:
                    new_p = {"id": uuid.uuid4().hex[:8], "name": p}
                    idx = orders_map[order_id]["products"].index(p)
                    orders_map[order_id]["products"][idx] = new_p
                    product_obj = new_p
                    product_id = new_p["id"]
                    break

        if product_obj is None:
            for p in orders_map[order_id].get("products", []):
                if isinstance(p, dict) and str(p.get("id")) == str(product_ref):
                    product_obj = p
                    product_id = p["id"]
                    break

        if product_obj is None:
            logger.error(f"[{chat_id}] Could not resolve product '{product_ref}' in order {order_id}. Products: {orders_map[order_id].get('products')}")
            await update.message.reply_text("هذا المنتج مموجود أو صار خلل. حاول تحميل الطلبية من جديد أو أضف المنتج مرة ثانية.")
            return ConversationHandler.END

        pricing_map.setdefault(order_id, {})
        name_key = product_obj.get("name")
        if name_key in pricing_map[order_id] and product_id not in pricing_map[order_id]:
            pricing_map[order_id][product_id] = pricing_map[order_id].pop(name_key)
            logger.info(f"Migrated pricing key for order {order_id}: '{name_key}' -> '{product_id}'")

        pricing_map[order_id].setdefault(product_id, {})
        pricing_map[order_id][product_id]["buy"] = buy_price
        pricing_map[order_id][product_id]["sell"] = sell_price

        # store supplier id
        orders_map[order_id]["supplier_id"] = user_id

        logger.info(f"[{chat_id}] Saved pricing for order '{order_id}', product_id '{product_id}': buy={buy_price}, sell={sell_price}")
        context.application.create_task(save_data_in_background(context))

        # cleanup user_data
        context.user_data[user_id].pop("order_id", None)
        context.user_data[user_id].pop("product", None)

        # check completion
        is_order_complete = True
        for p in orders_map[order_id].get("products", []):
            pid = p["id"] if isinstance(p, dict) else None
            if pid is None or pid not in pricing_map.get(order_id, {}) or "buy" not in pricing_map[order_id].get(pid, {}):
                is_order_complete = False
                break

        if is_order_complete:
            # ask for places count
            await request_places_count_standalone(chat_id, context, user_id, order_id)
            return ConversationHandler.END
        else:
            await show_buttons(chat_id, context, user_id, order_id, confirmation_message="تم إدخال السعر. بقي منتجات أخرى؟")
            return ConversationHandler.END

    except Exception as e:
        logger.error(f"[{chat_id}] Critical error in receive_buy_price: {e}", exc_info=True)
        try:
            msg_error = await update.message.reply_text("كسها صار خطا مدري وين؛ رجع سوي طلب جديد أو أضف المنتج مرة ثانية.")
            context.user_data.setdefault(user_id, {}).setdefault("messages_to_delete", []).append({
                "chat_id": msg_error.chat_id,
                "message_id": msg_error.message_id
            })
        except:
            pass
        return ConversationHandler.END


# ---------- request places count ----------
async def request_places_count_standalone(chat_id: int, context: ContextTypes.DEFAULT_TYPE, user_id: str, order_id: str):
    orders_map = context.application.bot_data.setdefault("orders", orders)
    try:
        if order_id not in orders_map:
            await context.bot.send_message(chat_id, "⚠️ الطلب غير موجود.")
            return
        user_data = context.user_data.setdefault(user_id, {})
        user_data["current_active_order_id"] = order_id
        emojis = ['1️⃣','2️⃣','3️⃣','4️⃣','5️⃣','6️⃣','7️⃣','8️⃣','9️⃣','🔟']
        buttons = [InlineKeyboardButton(emojis[i-1], callback_data=f"places_data_{order_id}_{i}") for i in range(1,11)]
        keyboard = [buttons[i:i+5] for i in range(0, len(buttons), 5)]
        reply_markup = InlineKeyboardMarkup(keyboard)
        msg_places = await context.bot.send_message(chat_id=chat_id, text="✔️ كملت تسعير كل المنتجات\nاختار عدد المحلات من الأزرار التالية:", reply_markup=reply_markup)
        user_data['places_count_message'] = {'chat_id': msg_places.chat_id, 'message_id': msg_places.message_id}
        messages_to_delete = user_data.get("messages_to_delete", [])
        if messages_to_delete:
            for msg_info in messages_to_delete:
                context.application.create_task(delete_message_in_background(context, chat_id=msg_info['chat_id'], message_id=msg_info['message_id']))
            user_data["messages_to_delete"] = []
    except Exception as e:
        logger.error(f"Error in request_places_count_standalone: {e}", exc_info=True)
        await context.bot.send_message(chat_id, "❌ صار خلل أثناء اختيار عدد المحلات.\nرجاءً سوّي طلب جديد.")


# ---------- handle places count callback ----------
async def handle_places_count_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    orders_map = context.application.bot_data.setdefault("orders", orders)
    try:
        query = update.callback_query
        await query.answer()
        parts = query.data.split("_")
        if len(parts) >= 4 and parts[0] == "places" and parts[1] == "data":
            order_id = parts[2]
            places = int(parts[3])
        else:
            await query.message.reply_text("صيغة البيانات خاطئة.")
            return
        if order_id not in orders_map:
            await query.message.reply_text("الطلب مفقود.")
            return
        orders_map[order_id]['places_count'] = places
        # delete places message if present
        user_id = str(query.from_user.id)
        try:
            pcm = context.user_data.get(user_id, {}).pop('places_count_message', None)
            if pcm:
                context.application.create_task(delete_message_in_background(context, chat_id=pcm.get('chat_id'), message_id=pcm.get('message_id')))
        except Exception:
            pass
        # show final options
        await show_final_options(query.message.chat_id, context, user_id, order_id, message_prefix=None)
    except Exception as e:
        logger.error(f"Error in handle_places_count_data: {e}", exc_info=True)
        try:
            await query.message.reply_text("عذرا حدث خطأ أثناء معالجة عدد المحلات")
        except:
            pass


# ---------- show final options ----------
async def show_final_options(chat_id: int, context: ContextTypes.DEFAULT_TYPE, user_id: str, order_id: str, message_prefix: str = None):
    orders_map = context.application.bot_data.setdefault("orders", orders)
    pricing_map = context.application.bot_data.setdefault("pricing", pricing)
    try:
        if order_id not in orders_map:
            await context.bot.send_message(chat_id, "⚠️ الطلب غير موجود.")
            return
        order = orders_map[order_id]
        products = order.get("products", [])
        text = ""
        if message_prefix:
            text += f"{message_prefix}\n\n"
        text += f"📦 *الطلب:* {order.get('title','')}\n"
        text += "━━━━━━━━━━━━━━\n"
        total_profit = 0
        for product in products:
            p_id = product.get("id") if isinstance(product, dict) else None
            p_name = product.get("name") if isinstance(product, dict) else str(product)
            if p_id and p_id in pricing_map.get(order_id, {}):
                pr = pricing_map[order_id][p_id]
                if "buy" in pr and "sell" in pr:
                    profit = pr["sell"] - pr["buy"]
                    total_profit += profit
                    text += f"🔹 {p_name}\n"
                    text += f"   شراء: {pr['buy']}\n"
                    text += f"   بيع: {pr['sell']}\n"
                    text += f"   ربح: {profit}\n\n"
                else:
                    text += f"❗ {p_name} — لم يتم تسعيره بالكامل.\n\n"
            else:
                text += f"❗ {p_name} — لم يتم تسعيره.\n\n"
        text += "━━━━━━━━━━━━━━\n"
        text += f"💰 *الربح الكلي:* {total_profit}"
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 إرسال الفاتورة", callback_data=f"send_invoice_{order_id}")],
            [InlineKeyboardButton("🗑️ حذف منتج", callback_data=f"delete_specific_product_{order_id}")],
            [InlineKeyboardButton("🔙 رجوع", callback_data=f"back_to_order_{order_id}")]
        ])
        await context.bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"[{chat_id}] Error in show_final_options: {e}", exc_info=True)
        await context.bot.send_message(chat_id, "❌ خطأ أثناء عرض خيارات الفاتورة.")


# ---------- send invoice handler ----------
async def send_invoice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        data = query.data
        _, order_id = data.split("_", 1)
        orders_map = context.application.bot_data.setdefault("orders", orders)
        pricing_map = context.application.bot_data.setdefault("pricing", pricing)
        if order_id not in orders_map:
            await query.message.reply_text("الطلبية مموجودة.")
            return
        order = orders_map[order_id]
        places_count = order.get("places_count", 1)
        group_chat_id = order.get("group_id") or query.message.chat_id
        # ensure invoice number
        if "invoice_number" not in order:
            order["invoice_number"] = next_invoice_number()
        group_text = build_group_invoice_text(order_id, orders_map, pricing_map, places_count)
        supplier_text = build_supplier_invoice_text(order_id, orders_map, pricing_map, places_count)
        admin_text = build_admin_invoice_text(order_id, orders_map, pricing_map, places_count)
        # send to group
        await context.bot.send_message(group_chat_id, group_text)
        # send supplier invoice privately if supplier_id exists
        supplier_id = order.get("supplier_id")
        if supplier_id:
            markup = InlineKeyboardMarkup([[
                InlineKeyboardButton("✏️ تعديل الطلبية", callback_data=f"edit_order_{order_id}"),
                InlineKeyboardButton("📤 رفع الطلبية", web_app=WebAppInfo(url="https://d.ksebstor.site/dashboard/client_order"))
            ]])
            try:
                await context.bot.send_message(int(supplier_id), supplier_text, reply_markup=markup)
            except Exception as e:
                logger.error(f"Failed to send supplier invoice to {supplier_id}: {e}", exc_info=True)
        # send admin invoice to admin_id if present in bot_data
        admin_id = context.application.bot_data.get("admin_id")
        try:
            if admin_id:
                await context.bot.send_message(int(admin_id), admin_text)
        except Exception as e:
            logger.error(f"Failed to send admin invoice: {e}", exc_info=True)
        await query.message.reply_text("تم إرسال الفواتير. ✅")
        order["status"] = "invoiced"
        context.application.create_task(save_data_in_background(context))
    except Exception as e:
        logger.error(f"send_invoice_handler error: {e}", exc_info=True)
        try:
            await update.callback_query.message.reply_text("عذراً، حدث خطأ أثناء إرسال الفواتير.")
        except:
            pass


# ---------- delete specific product (callback) ----------
async def delete_specific_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        parts = query.data.split("_", 2)
        if len(parts) < 3:
            await query.message.reply_text("صيغة الطلب للحذف خاطئة.")
            return
        _, order_id, p_id = parts
        orders_map = context.application.bot_data.setdefault("orders", orders)
        pricing_map = context.application.bot_data.setdefault("pricing", pricing)
        if order_id not in orders_map:
            await query.message.reply_text("ماكو طلب.")
            return
        order = orders_map[order_id]
        order["products"] = [p for p in order.get("products", []) if not (isinstance(p, dict) and p.get("id") == p_id)]
        if order_id in pricing_map and p_id in pricing_map[order_id]:
            del pricing_map[order_id][p_id]
        await query.message.reply_text("تم حذف المنتج.")
        await show_buttons(query.message.chat_id, context, str(query.from_user.id), order_id)
    except Exception as e:
        logger.error(f"Error in delete_specific_product: {e}", exc_info=True)
        try:
            await query.message.reply_text("حدث خطأ أثناء حذف المنتج.")
        except:
            pass


# ---------- add product flow (simple) ----------
async def add_product_to_order_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        data = query.data  # add_product_to_order_{order_id}
        parts = data.split("_", 4)
        order_id = parts[-1]
        # prompt user to send product name
        user_id = str(query.from_user.id)
        context.user_data.setdefault(user_id, {})
        context.user_data[user_id]["adding_new_product"] = order_id
        await query.message.reply_text("أرسل اسم المنتج الذي تريد إضافته الآن:")
    except Exception as e:
        logger.error(f"Error in add_product_to_order_callback: {e}", exc_info=True)
        try:
            await query.message.reply_text("خطأ أثناء بدء إضافة المنتج.")
        except:
            pass


async def receive_new_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    text = update.message.text.strip()
    order_id = context.user_data.get(user_id, {}).get("adding_new_product")
    if not order_id:
        await update.message.reply_text("ماكو طلب مرتبط. اضغط زر الإضافة أولاً.")
        return
    orders_map = context.application.bot_data.setdefault("orders", orders)
    if order_id not in orders_map:
        await update.message.reply_text("الطلبية مموجودة.")
        context.user_data.get(user_id, {}).pop("adding_new_product", None)
        return
    new_id = uuid.uuid4().hex[:8]
    orders_map[order_id].setdefault("products", []).append({"id": new_id, "name": text})
    await update.message.reply_text(f"تمت إضافة المنتج '{text}'.")
    context.user_data[user_id].pop("adding_new_product", None)
    context.application.create_task(save_data_in_background(context))
    # show buttons again
    await show_buttons(update.effective_chat.id, context, user_id, order_id)


# ---------- edit order placeholder ----------
async def edit_order_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        # placeholder: in future, could open webapp with order_id param
        await query.message.reply_text("زر تعديل الطلبية — مهيأ للعمل لاحقاً.")
    except Exception as e:
        logger.error(f"edit_order_callback error: {e}", exc_info=True)


# ---------- receive new order messages (from group) ----------
async def receive_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    When someone posts an order in a group, this handler creates a new order entry and shows buttons.
    We assume the message text is the order title + product lines (you can adapt parsing).
    """
    try:
        chat = update.effective_chat
        user = update.effective_user
        chat_id = chat.id
        text = update.message.text or ""
        # create order id
        order_id = uuid.uuid4().hex[:8]
        # basic parsing: first line as title, remaining lines as product names
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        title = lines[0] if lines else f"طلب من {user.full_name}"
        products = []
        if len(lines) > 1:
            for l in lines[1:]:
                products.append({"id": uuid.uuid4().hex[:8], "name": l})
        else:
            # If only one line, we still may want to create an empty product list
            products = []

        orders.setdefault(order_id, {})
        orders[order_id] = {
            "title": title,
            "products": products,
            "created_at": datetime.utcnow().isoformat(),
            "group_id": chat_id,
            "status": "new",
        }

        # store orders back to bot_data too
        context.application.bot_data["orders"] = orders
        context.application.bot_data["pricing"] = pricing

        logger.info(f"[{chat_id}] Processing order from: {user.id} - Created new order {order_id}.")
        context.application.create_task(save_data_in_background(context))

        # show buttons in the same chat
        await show_buttons(chat_id, context, str(user.id), order_id)
    except Exception as e:
        logger.error(f"receive_order error: {e}", exc_info=True)
        try:
            await update.message.reply_text("حدث خطأ أثناء استقبال الطلب.")
        except:
            pass


# ---------- show incomplete orders (example handler) ----------
async def show_incomplete_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # list orders that are not fully priced
    try:
        orders_map = context.application.bot_data.setdefault("orders", orders)
        text_lines = []
        for oid, o in orders_map.items():
            incomplete = False
            for p in o.get("products", []):
                pid = p.get("id") if isinstance(p, dict) else None
                if not pid or pid not in pricing.get(oid, {}) or "buy" not in pricing.get(oid, {}).get(pid, {}):
                    incomplete = True
                    break
            if incomplete:
                text_lines.append(f"- {o.get('title','')} (#{oid})")
        if not text_lines:
            await update.message.reply_text("ماكو طلبات غير مكتملة.")
        else:
            await update.message.reply_text("الطلبات غير المكتملة:\n" + "\n".join(text_lines))
    except Exception as e:
        logger.error(f"show_incomplete_orders error: {e}", exc_info=True)
        await update.message.reply_text("حدث خطأ بعرض الطلبات.")


# ---------- start command ----------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("هلا! بوت تجهيز الطلبات شغّال. أرسل رسالة تحتوي الطلب داخل الكروب والبيها المنتجات.")


# ---------- register handlers and run ----------
def build_app():
    load_all_data()
    application = ApplicationBuilder().token(TOKEN).build()

    # store loaded data into bot_data for shared access
    application.bot_data["orders"] = orders
    application.bot_data["pricing"] = pricing
    application.bot_data["last_button_message"] = last_button_message
    application.bot_data["admin_id"] = application.bot_data.get("admin_id")  # optional

    # handlers
    application.add_handler(CommandHandler("start", start_cmd))
    # receive order messages (from groups) - non-command message handler
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), receive_order))

    # product selection & callbacks
    application.add_handler(CallbackQueryHandler(product_selected, pattern=r"^\w+\|"))
    application.add_handler(CallbackQueryHandler(add_product_to_order_callback, pattern=r"^add_product_to_order_"))
    application.add_handler(CallbackQueryHandler(delete_specific_product, pattern=r"^delete_specific_product_"))
    application.add_handler(CallbackQueryHandler(handle_places_count_data, pattern=r"^places_data_"))
    application.add_handler(CallbackQueryHandler(send_invoice_handler, pattern=r"^send_invoice_"))
    application.add_handler(CallbackQueryHandler(edit_order_callback, pattern=r"^edit_order_"))
    application.add_handler(CallbackQueryHandler(lambda u, c: None, pattern=r"^final_options_"))  # final options handled elsewhere by show_final_options call

    # conversation for pricing (ASK_BUY)
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(product_selected, pattern=r"^\w+\|\w+")],
        states={
            ASK_BUY: [MessageHandler(filters.TEXT & (~filters.COMMAND), receive_buy_price)]
        },
        fallbacks=[],
        allow_reentry=True,
    )
    application.add_handler(conv)

    # add product simple flow
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), receive_new_product_name))

    return application


if __name__ == "__main__":
    if not TOKEN:
        logger.error("TOKEN not set in environment variables. Exiting.")
        raise SystemExit("TOKEN not set")
    app = build_app()
    logger.info("Application starting...")
    app.run_polling(poll_interval=1.0, timeout=20)
