# ai_service.py
from openai import OpenAI
from db_manager import get_chat_history
from datetime import datetime
from config import (
    DEEPSEEK_API_KEY, 
    DEEPSEEK_API_BASE, 
    MODEL_NAME, 
    SYSTEM_PROMPT
)

client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_API_BASE,
)

def generate_ai_response(user_id, user_message, user_display_name):
    """
    Формирует промпт с памятью и личностью, вызывает DeepSeek API.
    """
    # Получаем историю последних 10 сообщений
    history = get_chat_history(user_id, limit=10)
    
    current_date = datetime.now().strftime('%d.%m.%Y')

    # Персонализированный промпт
    personalized_system_prompt = SYSTEM_PROMPT.format(
        user_name=user_display_name,
        date=current_date
    )
    
    # Изоляция пользователей
    isolation_prompt = f"\n\n[КОНТЕКСТ СЕССИИ: User ID {user_id}. Это приватный диалог только с {user_display_name}. Забудь все предыдущие разговоры с другими людьми.]"
    personalized_system_prompt += isolation_prompt

    # Формируем сообщения
    messages = [{"role": "system", "content": personalized_system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    try:
        completion = client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            temperature=0.7,
            user=f"user_{user_id}",  # Изоляция на уровне API
        )
        return completion.choices[0].message.content

    except Exception as e:
        print(f"DeepSeek API error for user {user_id}: {e}")
        raise