# -*- coding: utf-8 -*-
"""
المنطق القديم: الطلبات العادية (أول سطر = عنوان/منطقة، ثم رقم، ثم المنتجات).
main يوجّه الرسائل اللي بدايتها مو «اسم الزبون: » إلى هذا الملف ويستدعي handle_old_order
اللي بدوره يستدعي receive_order (المعرّف في main).
"""
from telegram import Update
from telegram.ext import ContextTypes


async def handle_old_order(update: Update, context: ContextTypes.DEFAULT_TYPE, receive_order_fn):
    """
    تشغيل المنطق القديم (استلام الطلبية بصيغة: عنوان، رقم، منتجات).
    receive_order_fn = دالة receive_order من main.
    """
    if receive_order_fn is None:
        return
    await receive_order_fn(update, context)
