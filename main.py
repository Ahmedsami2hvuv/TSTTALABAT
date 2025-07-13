---
async def show_final_options(chat_id, context, user_id, order_id, message_prefix=None):
    orders = context.application.bot_data['orders']
    pricing = context.application.bot_data['pricing']
    invoice_numbers = context.application.bot_data['invoice_numbers']
    daily_profit_current = context.application.bot_data['daily_profit']

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
        phone_number = order.get('phone_number', 'لا يوجد رقم')

        total_buy = 0.0
        total_sell = 0.0
        for p in order["products"]:
            if p in pricing.get(order_id, {}) and "buy" in pricing[order_id].get(p, {}) and "sell" in pricing[order_id].get(p, {}):
                total_buy += pricing[order_id][p]["buy"]
                total_sell += pricing[order_id][p]["sell"]

        net_profit = total_sell - total_buy

        current_places = orders[order_id].get("places_count", 0)
        extra_cost = calculate_extra(current_places)

        delivery_fee = get_delivery_price(order.get('title', ''))

        total_before_delivery_fee = total_sell + extra_cost

        final_total = total_before_delivery_fee + delivery_fee

        context.application.bot_data['daily_profit'] = daily_profit_current + net_profit
        logger.info(f"[{chat_id}] Daily profit after adding {net_profit} for order {order_id}: {context.application.bot_data['daily_profit']}")
        context.application.create_task(save_data_in_background(context))

        # فاتورة الزبون الجديدة حسب الطلب
        customer_invoice_lines = [
            "📋 أبو الأكبر للتوصيل 🚀",
            "-----------------------------------",
            f"فاتورة رقم: #{invoice}",
            f"🏠 عنوان الزبون: {order['title']}",
            f"📞 رقم الزبون: {phone_number}",
            "🛍️ المنتجات:",
            ""
        ]

        current_total = 0.0
        for i, product in enumerate(order["products"]):
            if product in pricing.get(order_id, {}) and "sell" in pricing[order_id][product]:
                sell_price = pricing[order_id][product]["sell"]
                
                if i == 0:  # المنتج الأول
                    customer_invoice_lines.append(f"– {product} بـ{format_float(sell_price)}")
                    customer_invoice_lines.append(f"• {format_float(sell_price)} 💵")
                else:  # المنتجات التالية
                    prev_total = current_total
                    customer_invoice_lines.append(f"– {product} بـ{format_float(sell_price)}")
                    customer_invoice_lines.append(f"• {format_float(prev_total)}+{format_float(sell_price)}= {format_float(prev_total + sell_price)} 💵")
                
                current_total += sell_price
            else:
                customer_invoice_lines.append(f"– {product} (لم يتم تسعيره)")

        # إضافة كلفة التجهيز
        if extra_cost > 0:
            prev_total = current_total
            customer_invoice_lines.append(f"– 📦 التجهيز: من {current_places} محلات بـ {format_float(extra_cost)}")
            customer_invoice_lines.append(f"• {format_float(prev_total)}+{format_float(extra_cost)}= {format_float(prev_total + extra_cost)} 💵")
            current_total += extra_cost

        # إضافة أجرة التوصيل
        if delivery_fee > 0:
            prev_total = current_total
            customer_invoice_lines.append(f"– 🚚 التوصيل: بـ {format_float(delivery_fee)}")
            customer_invoice_lines.append(f"• {format_float(prev_total)}+{format_float(delivery_fee)}= {format_float(prev_total + delivery_fee)} 💵")
            current_total += delivery_fee

        customer_invoice_lines.extend([
            "-----------------------------------",
            "✨ المجموع الكلي: ✨",
            f"بدون التوصيل = {format_float(total_before_delivery_fee)} 💵",
            f"مــــع التوصيل = {format_float(final_total)} 💵",
            "شكراً لاختياركم خدمة أبو الأكبر للتوصيل! ❤️"
        ])

        customer_final_text = "\n".join(customer_invoice_lines)

        # ✅ فاتورة الزبون المخصصة للواتساب (مختصرة)
        customer_whatsapp_invoice_lines = [
            "📋 فاتورتك من أبو الأكبر 🚀",
            "-------------------------------",
            f"فاتورة رقم: #{invoice}",
            f"🏠 العنوان: {order['title']}",
            f"📞 الرقم: {phone_number}",
            "🛍️ المنتجات:",
        ]

        for product in order["products"]:
            if product in pricing.get(order_id, {}) and "sell" in pricing[order_id][product]:
                sell_price = pricing[order_id][product]["sell"]
                customer_whatsapp_invoice_lines.append(f"- {product}: {format_float(sell_price)}")
            else:
                customer_whatsapp_invoice_lines.append(f"- {product} (لم يتم تسعيره)")

        if extra_cost > 0:
            customer_whatsapp_invoice_lines.append(f"📦 تجهيز ({current_places} محلات): {format_float(extra_cost)}")
        if delivery_fee > 0:
            customer_whatsapp_invoice_lines.append(f"🚚 توصيل: {format_float(delivery_fee)}")

        customer_whatsapp_invoice_lines.extend([
            "-------------------------------",
            f"✨ المجموع الكلي: {format_float(final_total)} 💵",
            "شكراً لاختياركم أبو الأكبر! ❤️"
        ])
        customer_whatsapp_final_text = "\n".join(customer_whatsapp_invoice_lines)

        # باقي الدوال كما هي بدون تغيير
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

        # فاتورة المجهز
        supplier_invoice_details = [
            f"**فاتورة شراء طلبية (لك):**",
            f"رقم الفاتورة: {invoice}",
            f"عنوان الزبون: {order['title']}",
            f"رقم الزبون: `{phone_number}`",
            "\n*تفاصيل الشراء:*"
        ]
        supplier_total_buy = 0.0
        for p in order["products"]:
            if p in pricing.get(order_id, {}) and "buy" in pricing[order_id].get(p, {}):
                buy = pricing[order_id][p]["buy"]
                supplier_total_buy += buy
                supplier_invoice_details.append(f"  - {p}: {format_float(buy)}")
            else:
                supplier_invoice_details.append(f"  - {p}: (لم يتم تحديد سعر الشراء)")

        supplier_invoice_details.append(f"\n*مجموع كلفة الشراء للطلبية:* {format_float(supplier_total_buy)}")
        final_supplier_invoice_text = "\n".join(supplier_invoice_details)

        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=final_supplier_invoice_text,
                parse_mode="Markdown"
            )
            logger.info(f"[{chat_id}] Sent supplier purchase invoice to private chat of user {user_id}.")
        except Exception as e:
            logger.error(f"[{chat_id}] Could not send supplier purchase invoice to private chat of user {user_id}: {e}")
            await context.bot.send_message(chat_id=chat_id, text="عذراً، لم أتمكن من إرسال فاتورة الشراء لخاص المجهز.")

        # فاتورة الإدارة
        owner_invoice_details = [
            f"رقم الفاتورة: {invoice}",
            f"رقم الزبون: `{phone_number}`",
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
            f"الــربـــح الكلي: {format_float(net_profit)}",
            f"التــجـهيز ({current_places}) : {format_float(extra_cost)}",
            f"مـــــجموع بيع: {format_float(total_sell + extra_cost)}"
        ])
        if delivery_fee > 0:
            owner_invoice_details.append(f"أجرة التوصيل: {format_float(delivery_fee)}")
        owner_invoice_details.append(f"الــســعر الكلي: {format_float(final_total)}")

        final_owner_invoice_text = "\n".join(owner_invoice_details)

        try:
            invoice_filename = os.path.join(invoices_dir, f"invoice_{invoice}_admin.txt")
            with open(invoice_filename, "w", encoding="utf-8") as f:
                f.write("فاتورة الإدارة\n" + "="*40 + "\n" + final_owner_invoice_text)
            logger.info(f"[{chat_id}] Saved invoice to {invoice_filename}")
        except Exception as e:
            logger.error(f"[{chat_id}] Failed to save admin invoice to file: {e}")

        encoded_owner_invoice = quote(final_owner_invoice_text, safe='')
        encoded_customer_whatsapp_text = quote(customer_whatsapp_final_text, safe='') 

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

        # أزرار التحكم
        keyboard = [
            [InlineKeyboardButton("1️⃣ تعديل الأسعار", callback_data=f"edit_prices_{order_id}")],
            [InlineKeyboardButton("2️⃣ رفع الطلبية", url="https://d.ksebstor.site/client/96f743f604a4baf145939298")],
            [InlineKeyboardButton("3️⃣ إرسال فاتورة الزبون (واتساب)", url=f"https://wa.me/{phone_number}?text={encoded_customer_whatsapp_text}")], 
            [InlineKeyboardButton("4️⃣ إنشاء طلب جديد", callback_data="start_new_order")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        message_text = "افعل ما تريد من الأزرار:\n\n"
        if message_prefix:
            message_text = message_prefix + "\n" + message_text

        # ✅ هنا المشكلة كانت تصير!
        # تأكد إنو الـ chat_id هذا صحيح ومو جاي يسبب مشكلة.
        # وأيضاً تأكد من انه لا يوجد delete_message_in_background للرسالة اللي بيها الأزرار النهائية.

        # قبل إرسال الرسالة النهائية بالأزرار، تأكد من تنظيف رسائل الأزرار السابقة لنفس الطلبية
        # (لو كان اكو زر سابق انعرض للطلب نفسه)
        last_button_message_info = context.application.bot_data['last_button_message'].get(order_id)
        if last_button_message_info and last_button_message_info['chat_id'] == chat_id:
            try:
                await context.bot.delete_message(chat_id=last_button_message_info['chat_id'], message_id=last_button_message_info['message_id'])
                logger.info(f"[{chat_id}] Deleted old final options message {last_button_message_info['message_id']} for order {order_id}.")
            except Exception as e:
                logger.warning(f"[{chat_id}] Could not delete old final options message {last_button_message_info['message_id']}: {e}")
            finally:
                context.application.bot_data['last_button_message'].pop(order_id, None) # نظفها بعد الحذف

        sent_message = await context.bot.send_message(chat_id=chat_id, text=message_text, reply_markup=reply_markup, parse_mode="Markdown")
        # حفظ معلومات الرسالة النهائية الجديدة لغرض التعديل أو الحذف مستقبلاً
        context.application.bot_data['last_button_message'][order_id] = {
            'chat_id': chat_id,
            'message_id': sent_message.message_id
        }
        context.application.create_task(save_data_in_background(context))
        logger.info(f"[{chat_id}] Sent final options message {sent_message.message_id} with buttons for order {order_id}.")


        # تنظيف بيانات المستخدم (بعد ما انعرضت الأزرار بنجاح)
        if user_id in context.user_data:
            context.user_data[user_id].pop("order_id", None)
            context.user_data[user_id].pop("product", None)
            context.user_data[user_id].pop("current_active_order_id", None)
            context.user_data[user_id].pop("messages_to_delete", None)

    except Exception as e:
        logger.error(f"[{chat_id}] Error in show_final_options: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text="عذراً، حدث خطأ أثناء عرض الفاتورة النهائية. الرجاء بدء طلبية جديدة.")
