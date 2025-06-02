# bot.py
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler,
    MessageHandler, CallbackQueryHandler, ConversationHandler, filters
)
import uuid
import os
import asyncio
import threading
import json
import logging
from collections import Counter
from typing import Dict, Any

# ... [الاستيرادات والإعدادات الأولية] ...

# تحميل البيانات عند بدء التشغيل
data = utils.load_data()
orders = data['orders']
pricing = data['pricing']
invoice_numbers = data['invoice_numbers']
daily_profit = data['daily_profit']
last_button_message = data['last_button_message']

# حالات المحادثة
ASK_BUY, ASK_SELL, ASK_PLACES = range(3)

class OrderManager:
    def __init__(self):
        self.save_lock = threading.Lock()
        self.save_timer = None
    
    async def save_data_async(self, context: ContextTypes.DEFAULT_TYPE):
        with self.save_lock:
            data = {
                'orders': orders,
                'pricing': pricing,
                'invoice_numbers': invoice_numbers,
                'daily_profit': daily_profit,
                'last_button_message': last_button_message
            }
            utils.save_data(data)
    
    def schedule_save(self, context: ContextTypes.DEFAULT_TYPE):
        if self.save_timer is not None:
            self.save_timer.cancel()
        
        self.save_timer = threading.Timer(1.0, lambda: asyncio.run(self.save_data_async(context)))
        self.save_timer.start()

order_manager = OrderManager()

async def receive_buy_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة سعر الشراء مع تحسين الأداء"""
    user_id = str(update.message.from_user.id)
    
    # إرسال رد فوري
    processing_msg = await update.message.reply_text("⏳ جاري معالجة سعر الشراء...")
    
    # إعداد بيانات المستخدم
    context.user_data.setdefault(user_id, {})
    context.user_data[user_id].setdefault('messages_to_delete', [])
    context.user_data[user_id]['messages_to_delete'].append({
        'chat_id': update.message.chat_id,
        'message_id': update.message.message_id
    })

    # التحقق من صحة البيانات
    data = context.user_data.get(user_id, {})
    if not data.get('order_id') or not data.get('product'):
        await processing_msg.edit_text("❌ خطأ في البيانات. الرجاء بدء طلب جديد.")
        return ConversationHandler.END
    
    order_id, product = data['order_id'], data['product']
    
    # التحقق من وجود الطلب والمنتج
    if order_id not in orders or product not in orders.get(order_id, {}).get('products', []):
        await processing_msg.edit_text("❌ الطلب غير موجود. الرجاء بدء طلب جديد.")
        return ConversationHandler.END

    # معالجة سعر الشراء
    try:
        buy_price = float(update.message.text.strip())
        if buy_price < 0:
            error_msg = await update.message.reply_text("❌ السعر يجب أن يكون موجباً")
            context.user_data[user_id]['messages_to_delete'].append({
                'chat_id': error_msg.chat_id,
                'message_id': error_msg.message_id
            })
            return ASK_BUY
    except ValueError:
        error_msg = await update.message.reply_text("❌ الرجاء إدخال رقم صحيح")
        context.user_data[user_id]['messages_to_delete'].append({
            'chat_id': error_msg.chat_id,
            'message_id': error_msg.message_id
        })
        return ASK_BUY
    
    # حفظ السعر بدون تأخير
    pricing.setdefault(order_id, {}).setdefault(product, {})['buy'] = buy_price
    
    # إرسال سؤال سعر البيع فوراً
    sell_msg = await update.message.reply_text(
        f"✅ تم حفظ سعر الشراء: {buy_price}\n"
        f"↪️ الرجاء إدخال سعر البيع لـ *{product}*",
        parse_mode="Markdown"
    )
    
    # جدولة الحذف
    context.user_data[user_id]['messages_to_delete'].extend([
        {'chat_id': processing_msg.chat_id, 'message_id': processing_msg.message_id},
        {'chat_id': sell_msg.chat_id, 'message_id': sell_msg.message_id}
    ])
    
    return ASK_SELL

async def receive_sell_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة سعر البيع مع تحسين الأداء"""
    user_id = str(update.message.from_user.id)
    
    # ... [بنفس نمط receive_buy_price مع تعديلات سعر البيع] ...
    
    # حفظ البيانات بعد اكتمال السعرين
    order_manager.schedule_save(context)
    
    # ... [بقية المنطق كما هو] ...

# ... [جميع الدوال الأخرى تبقى كما هي مع استبدال save_data_in_background بـ order_manager.schedule_save] ...

def main():
    """الدالة الرئيسية مع تحسين إدارة الذاكرة"""
    app = ApplicationBuilder().token(TOKEN).build()
    
    # إضافة Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_order))
    app.add_handler(CallbackQueryHandler(product_selected))
    
    # محادثة تجهيز الطلبات
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, receive_order)],
        states={
            ASK_BUY: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_buy_price)],
            ASK_SELL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_sell_price)],
            ASK_PLACES: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_place_count),
                CallbackQueryHandler(receive_place_count, pattern="^places_")
            ],
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
        allow_reentry=True
    )
    
    app.add_handler(conv_handler)
    app.run_polling()

if __name__ == "__main__":
    main()
