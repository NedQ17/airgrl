# db_manager.py - PostgreSQL Version
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import SimpleConnectionPool
from datetime import datetime, date, timedelta
from config import DB_CONFIG, DAILY_LIMIT

# Connection pool for better performance
connection_pool = None

def init_db():
    """Создает connection pool и необходимые таблицы в PostgreSQL."""
    global connection_pool
    
    # Create connection pool
    connection_pool = SimpleConnectionPool(
        minconn=1,
        maxconn=10,
        host=DB_CONFIG['host'],
        database=DB_CONFIG['database'],
        user=DB_CONFIG['user'],
        password=DB_CONFIG['password'],
        port=DB_CONFIG['port']
    )
    
    conn = connection_pool.getconn()
    cursor = conn.cursor()

    # Messages table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Index for faster queries
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_messages_user_id 
        ON messages(user_id, id DESC)
    """)

    # Limits table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS limits (
            user_id BIGINT PRIMARY KEY,
            date DATE NOT NULL,
            count INTEGER NOT NULL DEFAULT 0
        )
    """)

    # Subscriptions table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            user_id BIGINT PRIMARY KEY,
            start_date TIMESTAMP NOT NULL,
            end_date TIMESTAMP NOT NULL
        )
    """)

    conn.commit()
    cursor.close()
    connection_pool.putconn(conn)
    print("✅ PostgreSQL database initialized successfully")


def get_connection():
    """Получает соединение из пула."""
    return connection_pool.getconn()


def return_connection(conn):
    """Возвращает соединение в пул."""
    connection_pool.putconn(conn)


def is_user_subscribed(user_id):
    """Проверяет, активна ли подписка у пользователя."""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute(
        "SELECT end_date FROM subscriptions WHERE user_id = %s",
        (user_id,)
    )
    result = cursor.fetchone()
    
    cursor.close()
    return_connection(conn)

    if result:
        end_date = result[0]
        return end_date > datetime.now()
    return False


def activate_subscription(user_id, duration_days=30):
    """Активирует или продлевает подписку на N дней."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT end_date FROM subscriptions WHERE user_id = %s",
        (user_id,)
    )
    result = cursor.fetchone()
    now = datetime.now()

    if result and result[0] > now:
        start_from = result[0]
    else:
        start_from = now

    new_end = start_from + timedelta(days=duration_days)
    
    cursor.execute("""
        INSERT INTO subscriptions (user_id, start_date, end_date)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id) 
        DO UPDATE SET end_date = EXCLUDED.end_date
    """, (user_id, now, new_end))
    
    conn.commit()
    cursor.close()
    return_connection(conn)


def get_chat_history(user_id, limit=5):
    """Возвращает последние N сообщений."""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT role, content 
        FROM messages 
        WHERE user_id = %s 
        ORDER BY id DESC 
        LIMIT %s
    """, (user_id, limit))
    
    history_raw = cursor.fetchall()
    cursor.close()
    return_connection(conn)

    history = [{"role": row[0], "content": row[1]} for row in reversed(history_raw)]
    return history


def save_message(user_id, role, content):
    """Сохраняет сообщение в историю."""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO messages (user_id, role, content)
        VALUES (%s, %s, %s)
    """, (user_id, role, content))
    
    conn.commit()
    cursor.close()
    return_connection(conn)


def check_and_increment_limit(user_id, daily_limit):
    """Проверяет и инкрементирует дневной лимит."""
    conn = get_connection()
    cursor = conn.cursor()
    today = date.today()

    cursor.execute(
        "SELECT count, date FROM limits WHERE user_id = %s",
        (user_id,)
    )
    result = cursor.fetchone()

    if result and result[1] == today:
        current_count = result[0]
        if current_count >= daily_limit:
            cursor.close()
            return_connection(conn)
            return False
        
        cursor.execute(
            "UPDATE limits SET count = count + 1 WHERE user_id = %s",
            (user_id,)
        )
    else:
        current_count = 0
        if 1 <= daily_limit:
            cursor.execute("""
                INSERT INTO limits (user_id, date, count)
                VALUES (%s, %s, 1)
                ON CONFLICT (user_id)
                DO UPDATE SET date = EXCLUDED.date, count = 1
            """, (user_id, today))
        else:
            cursor.close()
            return_connection(conn)
            return False

    conn.commit()
    cursor.close()
    return_connection(conn)
    return True


def increase_limit(user_id, count_to_add):
    """Сбрасывает часть счетчика, effectively добавляя лимит."""
    conn = get_connection()
    cursor = conn.cursor()
    today = date.today()

    cursor.execute(
        "SELECT count, date FROM limits WHERE user_id = %s",
        (user_id,)
    )
    result = cursor.fetchone()

    if result and result[1] == today:
        current_count = result[0]
    else:
        current_count = 0

    new_count = current_count - count_to_add

    cursor.execute("""
        INSERT INTO limits (user_id, date, count)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id)
        DO UPDATE SET date = EXCLUDED.date, count = EXCLUDED.count
    """, (user_id, today, new_count))
    
    conn.commit()
    cursor.close()
    return_connection(conn)


def clear_user_history(user_id):
    """Удаляет всю историю сообщений пользователя."""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM messages WHERE user_id = %s", (user_id,))
    
    conn.commit()
    cursor.close()
    return_connection(conn)
    print(f"[DEBUG] История сообщений пользователя {user_id} успешно очищена.")


def get_user_status(user_id):
    """
    Возвращает статус подписки (дни до конца или None) 
    и оставшееся количество сообщений на сегодня.
    """
    conn = get_connection()
    cursor = conn.cursor()
    today_date = datetime.now()

    # 1. Получаем статус подписки
    cursor.execute(
        "SELECT end_date FROM subscriptions WHERE user_id = %s",
        (user_id,)
    )
    sub_result = cursor.fetchone()

    days_left = None
    if sub_result:
        sub_end_date = sub_result[0]
        if sub_end_date > today_date:
            time_left = sub_end_date - today_date
            days_left = time_left.days + 1

    # 2. Получаем оставшиеся сообщения
    cursor.execute(
        "SELECT count, date FROM limits WHERE user_id = %s",
        (user_id,)
    )
    limit_result = cursor.fetchone()

    current_count = 0
    if limit_result and limit_result[1] == date.today():
        current_count = limit_result[0]

    messages_left = None

    if days_left is not None and days_left > 0:
        messages_left = "∞ (Безлимит)"
    else:
        messages_left_count = DAILY_LIMIT - current_count
        messages_left = max(0, messages_left_count)

        if messages_left_count > DAILY_LIMIT:
             messages_left = messages_left_count

    cursor.close()
    return_connection(conn)
    return days_left, messages_left