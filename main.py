# ... (الكود السابق) ...

    # إرسال الرسالة الجديدة أولاً
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=message_text,
        reply_markup=markup,
        parse_mode="Markdown"
    )
    logger.info(f"Sent new button message {msg.message_id} for order {order_id}")
    
    # محاولة حذف الرسالة القديمة للأزرار فقط، وتجاهل الأخطاء بعد إرسال الرسالة الجديدة
    msg_info = last_button_message.get(order_id)
    if msg_info and msg_info.get("chat_id") == chat_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_info["message_id"])
            logger.info(f"Deleted old button message {msg_info['message_id']} for order {order_id}.")
        except Exception as e:
            logger.warning(f"Could not delete old button message {msg_info.get('message_id', 'N/A')} for order {order_id}: {e}. It might have been deleted already or is inaccessible.")
        finally:
            # إزالة الإشارة للرسالة القديمة من الذاكرة والملف فقط بعد محاولة الحذف
            if order_id in last_button_message:
                del last_button_message[order_id]
                save_data() # حفظ التغيير لضمان عدم الرجوع للرسالة المحذوفة بعد إعادة تشغيل البوت

    last_button_message[order_id] = {"chat_id": chat_id, "message_id": msg.message_id}
    save_data() # حفظ الـ ID والـ chat_id للرسالة الجديدة

# ... (باقي الكود) ...
