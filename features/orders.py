import os
import json
import uuid
import time
import asyncio
import logging
import threading
from collections import Counter
from urllib.parse import quote # هاي هم دالة مستخدمة

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler,
    MessageHandler, CallbackQueryHandler, ConversationHandler, filters
)

# استيراد دالة جلب سعر التوصيل من ملف المناطق
from features.delivery_zones import get_delivery_price

# تعريف الـ logging هنا أيضاً
logger = logging.getLogger(__name__)

# المتغيرات العالمية اللي تحتاجها الدوال
# راح نمررها أو نستوردها لاحقاً
# حاليا، لغرض التصحيح، راح نعتمد على المتغيرات العالمية اللي بـ main.py
# بس الطريقة الأصح هي تمريرها كـ parameters للدوال أو وضعها في ملف config.py
# لهسه، خلي نعتمد على الوصول Global لهاي المتغيرات.

# مسارات التخزين، راح نستخدمها من main.py
DATA_DIR = "/mnt/data/"
ORDERS_FILE = os.path.join(DATA_DIR, "orders.json")
PRICING_FILE = os.path.join(DATA_DIR, "pricing.json")
INVOICE_NUMBERS_FILE = os.path.join(DATA_DIR, "invoice_numbers.json")
DAILY_PROFIT_FILE = os.path.join(DATA_DIR, "daily_profit.json")
COUNTER_FILE = os.path.join(DATA_DIR, "invoice_counter.txt")
LAST_BUTTON_MESSAGE_FILE = os.path.join(DATA_DIR, "last_button_message.json")

# جلب OWNER_ID و TOKEN من متغيرات البيئة هنا أيضاً
OWNER_ID = int(os.getenv("OWNER_TELEGRAM_ID"))
OWNER_PHONE_NUMBER = os.getenv("OWNER_TELEGRAM_PHONE_NUMBER", "+9647733921468") # If you want to use env var for this too

# تهيئة القفل لعمليات الحفظ (يجب أن يكون عاماً أو يمرر)
save_lock = threading.Lock()
save_timer = None
save_pending = False

# حالات المحادثة
ASK_BUY, ASK_SELL, ASK_PLACES_COUNT = range(3)

# دالة الحفظ المؤجل (تحتاج الوصول للمتغيرات العالمية)
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

# حفظ البيانات إلى القرص (تحتاج الوصول للمتغيرات العالمية)
def _save_data_to_disk():
    # global orders, pricing, invoice_numbers, daily_profit, last_button_message # هاي لازم تصير global داخل main.py
    # هنا لازم تكون هاي المتغيرات متاحة للدالة
    # for now, assume access from main's global scope, but cleaner would be to pass or use a shared state object.
    # For simplicity in this module, we will re-define them or pass them as parameters to this function
    # A better way is to move these save/load logic to a `data_manager.py` module and pass the actual data dicts.

    # For immediate fix, let's make it work assuming global access,
    # but the correct way is to have the main app pass the data objects
    # or load/save within the module that holds the data.

    # Since orders, pricing, etc. are defined in main.py,
    # and _save_data_to_disk is called from schedule_save (which is called by save_data_in_background)
    # which is launched by context.application.create_task in orders functions.
    # This implies that these variables (orders, pricing etc.) are global.
    # The best immediate fix is to define them as global in this module,
    # and then initialize them via a function call, or import them.
    # Importing from main.py is bad.
    # So, the easiest is to make the order data global here, and load them in main.py, and pass them.

    # Let's adjust the save functions in this module.
    # The global variables themselves (orders, pricing, etc.)
    # should be available when this code runs in the context of the bot.
    # The easiest is to make the `orders`, `pricing`, etc. dicts passed around, or make them a class.
    # For a quick fix, let's keep the global approach, assuming main.py loads them globally
    # or we re-initialize them here (but then they'd be separate).

    # For the sake of modularity and to avoid NameError here:
    # These functions (_save_data_to_disk, schedule_save, load_json_file)
    # are actually part of the *core data management* of the bot,
    # they shouldn't be copied into every feature module.
    # They should reside in main.py or a dedicated utils/data_manager.py.

    # Let's assume for now that save_data_in_background will call a save function
    # that knows about the global variables defined in main.py.
    # So, schedule_save() and _save_data_to_disk() should remain in main.py,
    # or a separate data_manager.py module that manages the actual global `orders`, `pricing` etc.

    # This indicates that `save_data_in_background` (and thus `schedule_save` and `_save_data_to_disk`)
    # should remain in `main.py` until we create a dedicated data management module.
    # For `features/orders.py`, we should only import `save_data_in_background`.

    pass # This means _save_data_to_disk will be removed from here.

pass # This means schedule_save will be removed from here.

# دالة مساعدة لحذف الرسائل في الخلفية (تبقى هنا، لأنها تستخدم في دوال الأوامر)
