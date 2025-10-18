from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, BotCommand 
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    PreCheckoutQueryHandler,
    filters,
    ContextTypes
)
import asyncio

# Импортируем конфиг, базу данных и AI
from config import *
from db_manager import (
    init_db,
    check_and_increment_limit,
    save_message,
    is_user_subscribed,
    activate_subscription,
    increase_limit,
    clear_user_history,
    get_user_status,
    create_payment_intent,
    verify_and_consume_payment
)
from ai_service import generate_ai_response


# ========================== СЕРВИСНЫЕ ФУНКЦИИ ==========================

async def set_bot_commands(application):
    """Устанавливает команды меню для бота."""
    commands = [
        BotCommand("start", "Начать диалог"),
        BotCommand("mysubsc", "Моя подписка и лимиты"),
        BotCommand("subscribe", "👑Безлимит на 30 дней"), 
        BotCommand("buy_messages", "🎁Дополнительные сообщения"), 
        BotCommand("reset", "Очистить историю"),
    ]
    await application.bot.set_my_commands(commands)
    print("Меню команд успешно установлено.")


# ========================== ПЛАТЕЖИ И ИНВОЙСЫ ==========================

async def pre_checkout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверяет, можно ли обработать платеж."""
    query = update.pre_checkout_query
    await query.answer(ok=True)


async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает успешную оплату с проверкой токена."""
    user_id = update.message.from_user.id
    payment_token = update.message.successful_payment.invoice_payload
    
    # ✅ КРИТИЧНО: Верифицируем платеж
    valid, payment_data = verify_and_consume_payment(payment_token, user_id)
    
    if not valid:
        print(f"⚠️ SECURITY ALERT: Invalid payment attempt by user {user_id}, token: {payment_token}")
        await update.message.reply_text(
            "❌ Ошибка обработки платежа. Пожалуйста, обратитесь в поддержку."
        )
        return
    
    # Обрабатываем платеж
    if payment_data['payment_type'] == 'subscription':
        activate_subscription(user_id, duration_days=30)
        await update.message.reply_text(SUCCESS_PAYMENT_MESSAGE)
    
    elif payment_data['payment_type'] == 'messages':
        count = payment_data['package_details']['count']
        increase_limit(user_id, count_to_add=count)
        await update.message.reply_text(
            f"✅ **Успешная покупка!** Вам добавлено {count} сообщений.",
            parse_mode='Markdown'
        )
    
    print(f"✅ Valid payment processed for user {user_id}: {payment_data}")


async def send_subscription_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет инвойс для покупки подписки с защищенным payload."""
    user_id = update.effective_user.id
    
    # ✅ Создаем защищенный токен
    payment_token = create_payment_intent(
        user_id=user_id,
        payment_type='subscription',
        amount=SUBSCRIPTION_PRICE_STARS
    )
    
    title = "👑 Безлимитная подписка на 30 дней"
    description = "Получите неограниченное общение с Алиной на 30 дней."
    
    await context.bot.send_invoice(
        chat_id=user_id,
        title=title,
        description=description,
        payload=payment_token,  # ✅ Используем безопасный токен
        provider_token=PAYMENT_PROVIDER_TOKEN,
        currency="XTR",
        prices=[LabeledPrice("Подписка на 30 дней", SUBSCRIPTION_PRICE_STARS)],  # ✅ БЕЗ умножения на 100
        start_parameter='monthly_sub',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(f"Купить за {SUBSCRIPTION_PRICE_STARS} ⭐", pay=True)]
        ])
    )


async def _send_message_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE, count: int, price: int, payload_key: str):
    """Отправляет инвойс для покупки сообщений с защищенным payload."""
    user_id = update.effective_user.id
    
    # ✅ Создаем защищенный токен
    payment_token = create_payment_intent(
        user_id=user_id,
        payment_type='messages',
        amount=price,
        package_details={'count': count}
    )
    
    title = f"🎁 Разовая покупка {count} сообщений"
    description = f"Получите {count} дополнительных сообщений для Алины. Действует бессрочно."

    await context.bot.send_invoice(
        chat_id=user_id,
        title=title,
        description=description,
        payload=payment_token,  # ✅ Используем безопасный токен
        provider_token=PAYMENT_PROVIDER_TOKEN,
        currency="XTR", 
        prices=[LabeledPrice(f"Сообщения ({count})", price)],  # ✅ БЕЗ умножения на 100
        start_parameter=payload_key.replace('_', '-'), 
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(f"Купить за {price} ⭐", pay=True)]
        ])
    )


# ========================== НАВИГАЦИЯ ==========================

async def show_subscription_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает детали подписки с кнопкой "Купить" и "Назад".
    Работает как для кнопки в чате, так и для команды /subscribe."""
    
    message_text = (
        f"👑 **Безлимитная подписка на 30 дней** \n\n"
        f"Стоимость: **{SUBSCRIPTION_PRICE_STARS} ⭐**\n\n"
        f"✅ **Неограниченное общение:** Отправляйте столько сообщений, сколько захотите.\n"
        f"✅ **Приоритет в очереди:** Ваши запросы обрабатываются быстрее.\n"
        f"✅ **Поддержка развития:** Вы помогаете проекту становиться лучше!\n"
    )
    
    keyboard = [
        [InlineKeyboardButton(f"Купить безлимит за {SUBSCRIPTION_PRICE_STARS} ⭐", callback_data="final_buy_subscription")], 
        [InlineKeyboardButton("⬅️ Назад к статусу", callback_data="back_to_status")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Определяем, как отправить/отредактировать сообщение
    if update.message:
        await update.message.reply_text(message_text, reply_markup=reply_markup, parse_mode='Markdown')
    elif update.callback_query:
        await update.callback_query.edit_message_text(
            message_text, 
            reply_markup=reply_markup, 
            parse_mode='Markdown'
        )


async def show_message_packages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отображает меню с пакетами сообщений для покупки (ответ на /buy_messages или кнопку)."""
    
    keyboard = []
    
    # Сборка кнопок из MESSAGE_PACKAGES
    for key, package in MESSAGE_PACKAGES.items():
        button_text = f"🎁 {package['count']} сообщений — {package['price']} ⭐"
        callback_data = f"buy_msg_{key}" 
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    
    # Добавляем кнопку "Назад"
    keyboard.append([InlineKeyboardButton("⬅️ Назад к статусу", callback_data="back_to_status")])
        
    reply_markup = InlineKeyboardMarkup(keyboard)

    message_text = (
        "🌟 **Выберите пакет сообщений для покупки:**\n\n"
        "Купленные сообщения суммируются с Вашим дневным лимитом и действуют бессрочно."
    )
    
    # Определяем, как отправить/отредактировать сообщение
    if update.message:
        await update.message.reply_text(message_text, reply_markup=reply_markup, parse_mode='Markdown')
    elif update.callback_query:
        await update.callback_query.edit_message_text(
            message_text, 
            reply_markup=reply_markup, 
            parse_mode='Markdown'
        )


# ========================== ХЕНДЛЕРЫ КОМАНД ==========================

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сбрасывает историю сообщений (память) пользователя."""
    user_id = update.message.from_user.id
    clear_user_history(user_id)
    await update.message.reply_text("🧠 Память сброшена! Начнем наш разговор с чистого листа.")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветственное сообщение и отображение статуса подписки при /start или кнопке 'Назад'."""
    
    source = None
    user_id = None

    if update.message:
        user_id = update.message.from_user.id
        source = update.message
    elif update.callback_query:
        user_id = update.callback_query.from_user.id
        source = update.callback_query
    else:
        return
    
    # --- 1. Получаем статус ---
    days_left, messages_left = get_user_status(user_id) 

    welcome_message = (
        "Привет! Я Алина и я здесь для тебя! 💕\n"
        "Поддержу, утешу, а могу и просто заболтать 😊\n\n"
    )

    if days_left is not None and days_left > 0:
        status_text = (
            f"✅ **У Вас активна подписка (30 дней)!**\n"
            f"До конца осталось: **{days_left}** дней.\n"
            f"Лимит: **Безлимит**."
        )
    else:
        status_text = (
            f"🆓 **Ваш дневной лимит:** {messages_left}/{DAILY_LIMIT} сообщений.\n"
            f"Чтобы продолжить общение, Вы можете:\n"
        )
    
    # --- 2. Создаем кнопки для покупки ---
    keyboard = [
        [InlineKeyboardButton(f"⭐ Купить безлимит ({SUBSCRIPTION_PRICE_STARS} ⭐/30 дней)", callback_data="show_sub_details")],
        
        [InlineKeyboardButton(
            f"🎁 Купить сообщения от {SMALLEST_PACKAGE_PRICE} ⭐", 
            callback_data="show_message_packages_menu"
        )]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # --- 3. Отправляем или редактируем сообщение ---
    if source is update.message:
        await source.reply_text(
            welcome_message + status_text, 
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    elif source is update.callback_query:
        await source.edit_message_text(
            welcome_message + status_text, 
            reply_markup=reply_markup, 
            parse_mode='Markdown'
        )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает нажатие кнопок (подписка / разовое пополнение / навигация)."""
    query = update.callback_query
    await query.answer()
    
    data = query.data

    # НАВИГАЦИЯ (Кнопка Назад)
    if data == 'back_to_status':
        await start_command(update, context) 
        return

    # ДЕТАЛИЗАЦИЯ
    elif data == 'show_sub_details':
        await show_subscription_details(update, context)
        
    elif data == 'show_message_packages_menu':
        await show_message_packages(update, context) 

    # ПОКУПКА ПОДПИСКИ (ФИНАЛЬНЫЙ ШАГ)
    elif data == 'final_buy_subscription': 
        await send_subscription_invoice(update, context)
        
    # ПОКУПКА ПАКЕТА СООБЩЕНИЙ (ФИНАЛЬНЫЙ ШАГ)
    elif data.startswith('buy_msg_'):
        package_key = data[8:] 
        package = MESSAGE_PACKAGES.get(package_key)
        
        if package:
            payload_key = f"messages_{package['count']}_stars_{package['price']}"
            
            await _send_message_invoice(
                update, 
                context, 
                count=package['count'], 
                price=package['price'], 
                payload_key=payload_key
            )
        else:
            await query.edit_message_text("Ошибка: Неизвестный пакет сообщений.", reply_markup=None)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает входящие текстовые сообщения."""
    user_id = update.message.from_user.id
    user_message = update.message.text
    user_display_name = update.message.from_user.first_name

    # 1. Проверка подписки и лимита
    if not is_user_subscribed(user_id) and not check_and_increment_limit(user_id, DAILY_LIMIT):
        keyboard = [
            [InlineKeyboardButton(f"⭐ Купить безлимит ({SUBSCRIPTION_PRICE_STARS} ⭐/30 дней)", callback_data="show_sub_details")],
            [InlineKeyboardButton(
                f"🎁 Купить сообщения от {SMALLEST_PACKAGE_PRICE} ⭐", 
                callback_data="show_message_packages_menu"
            )]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            LIMIT_EXCEEDED_MESSAGE, 
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return
    
    # 2. Индикатор "печатает..."
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action="typing"
    )
    
    # 3. Сохраняем сообщение пользователя
    save_message(user_id, "user", user_message)

    # 4. Получаем ответ от AI
    try:
        ai_response = generate_ai_response(user_id, user_message, user_display_name)
    except Exception as e:
        print(f"Критическая ошибка при вызове AI для user {user_id}: {e}")
        ai_response = "Извини, произошел технический сбой 💔 Попробуй чуть позже."

    # 5. Естественная задержка перед ответом
    typing_time = len(ai_response) / 80  # 80 символов/сек
    typing_time = min(typing_time, 4)  # Максимум 4 секунды
    typing_time = max(typing_time, 0.5)  # Минимум 0.5 секунды
    
    await asyncio.sleep(typing_time)

    # 6. Отправляем ответ
    await update.message.reply_text(ai_response)
    save_message(user_id, "assistant", ai_response)


# ========================== MAIN ==========================

def main():
    """Инициализация и запуск Telegram-бота."""
    init_db()

    application = Application.builder().token(TOKEN_TG).build()

    # Команды
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("mysubsc", start_command))
    application.add_handler(CommandHandler("subscribe", show_subscription_details)) 
    application.add_handler(CommandHandler("buy_messages", show_message_packages))
    application.add_handler(CommandHandler("reset", reset_command))
    
    # Сообщения
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Callback и платежи
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(PreCheckoutQueryHandler(pre_checkout_callback))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))
    
    print("🚀 AIGirl bot is running...")
    
    # Устанавливаем команды меню после запуска (с обработкой ошибок)
    async def post_init(app):
        try:
            await set_bot_commands(app)
        except Exception as e:
            print(f"⚠️ Не удалось установить команды меню: {e}")
            print("Бот продолжит работу без меню команд")
    
    application.post_init = post_init
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()