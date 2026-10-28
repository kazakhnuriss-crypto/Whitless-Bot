import os
import sqlite3
import telebot
from telebot import types
import duckdb
import urllib.request
import zipfile

# ===== НАСТРОЙКИ (Барлығы Railway-дің Variables ішінен алынады) =====
TOKEN = os.environ.get("TOKEN")
CREATOR_ID = int(os.environ.get("CREATOR_ID", 0))
ZIP_URL = os.environ.get("ZIP_URL")

USERS_DB = "users.db"
CSV_FILE = "base2.csv"
ZIP_FILE = "base2.zip"

bot = telebot.TeleBot(TOKEN)

# ===== ФАЙЛДЫ ЖҮКТЕУ ЖӘНЕ АШУ =====
def prepare_data():
    if os.path.exists(CSV_FILE):
        print("✅ Файл базасы дайын.")
        return

    print("⏳ Файл GitHub-тан жүктелуде (бұл бірнеше минут алуы мүмкін)...")
    try:
        urllib.request.urlretrieve(ZIP_URL, ZIP_FILE)
        print("✅ Жүктеу аяқталды. Архив ашылуда...")
        
        with zipfile.ZipFile(ZIP_FILE, 'r') as zip_ref:
            zip_ref.extractall(".") 
            
        print("✅ Архив ашылды! База дайын.")
        os.remove(ZIP_FILE)
    except Exception as e:
        print(f"❌ Файлды жүктеу қатесі: {e}")

# ===== ҚОЛДАНУШЫЛАР БАЗАСЫ =====
def init_db():
    conn = sqlite3.connect(USERS_DB)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        first_name TEXT,
        is_premium INTEGER DEFAULT 0,
        is_admin INTEGER DEFAULT 0,
        search_limit INTEGER DEFAULT 1
    )''')
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect(USERS_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user

def register_user(message):
    user = message.from_user
    conn = sqlite3.connect(USERS_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user.id,))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (user_id, first_name) VALUES (?, ?)", (user.id, user.first_name))
        conn.commit()
    conn.close()

def update_user_field(user_id, field, value):
    conn = sqlite3.connect(USERS_DB)
    cursor = conn.cursor()
    cursor.execute(f"UPDATE users SET {field} = ? WHERE user_id = ?", (value, user_id))
    conn.commit()
    conn.close()

# ===== КЛАВИАТУРАЛАР =====
def welcome_buttons(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(types.KeyboardButton("🔍 Поиск"), types.KeyboardButton("👁 Информация"))
    markup.row(types.KeyboardButton("🆔 Мой аккаунт"))
    
    user = get_user(user_id)
    if user and (user[3] == 1 or user_id == CREATOR_ID):
        markup.row(types.KeyboardButton("⚙️ Админ-панель"))
    return markup

def back_button():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("🔙 Назад"))
    return markup

# ===== СТАРТ =====
@bot.message_handler(commands=["start"])
def start(message):
    register_user(message)
    text = "👋 Добро пожаловать в <b>Whitless Company</b>!\n\n✅ Бот готов к поиску."
    bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=welcome_buttons(message.from_user.id))

@bot.message_handler(func=lambda m: m.text == "👁 Информация")
def info(message):
    text = "ℹ️ <b>ИНФОРМАЦИЯ</b>\n\n🏢 Компания: <b>Whitless Company</b>\n🤖 Умный поиск по базе."
    bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=back_button())

@bot.message_handler(func=lambda m: m.text == "🆔 Мой аккаунт")
def my_account(message):
    register_user(message)
    user = get_user(message.from_user.id)
    
    status = "👤 Обычный"
    if user[2] == 1: status = "🌟 Премиум"
    if user[3] == 1 or user[0] == CREATOR_ID: status = "👑 Создатель"

    limit_text = "♾ Шексіз" if user[2] == 1 else f"{user[4]} рет"
    
    text = (
        f"🆔 <b>МОЙ АККАУНТ</b>\n\n"
        f"👤 Имя: {user[1]}\n"
        f"🆔 ID: <code>{user[0]}</code>\n"
        f"📊 Статус: {status}\n"
        f"🔍 Осталось поисков: {limit_text}"
    )
    bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=back_button())

# ===== ПОИСК =====
@bot.message_handler(func=lambda m: m.text == "🔍 Поиск")
def search(message):
    register_user(message)
    user = get_user(message.from_user.id)
    if user[2] == 0 and user[4] <= 0:
        bot.send_message(message.chat.id, "❌ У вас закончились бесплатные поиски.", reply_markup=back_button())
        return
    msg = bot.send_message(message.chat.id, "🔍 Введите номер, ФИО или ИИН для поиска:", reply_markup=back_button())
    bot.register_next_step_handler(msg, process_search)

def process_search(message):
    if not message.text or message.text == "🔙 Назад":
        bot.send_message(message.chat.id, "Главное меню:", reply_markup=welcome_buttons(message.from_user.id))
        return

    query = message.text.strip().lower()
    user_id = message.from_user.id
    user = get_user(user_id)

    if not os.path.isfile(CSV_FILE):
        bot.send_message(message.chat.id, "⏳ Файл базы все еще загружается на сервер. Подождите 2-3 минуты.", reply_markup=welcome_buttons(user_id))
        return

    wait_msg = bot.send_message(message.chat.id, "⏳ Идет поиск по базе... (2-5 секунд)")

    try:
        con = duckdb.connect(database=':memory:')
        sql_query = f"""
            SELECT * FROM read_csv('{CSV_FILE}', 
            columns={{'line': 'VARCHAR'}}, 
            delim='\n', 
            header=False, 
            quote='', 
            escape='') 
            WHERE line ILIKE ? LIMIT 5
        """
        search_pattern = f"%{query}%"
        result = con.execute(sql_query, [search_pattern]).fetchall()
        con.close()

        bot.delete_message(message.chat.id, wait_msg.message_id)

        if not result:
            bot.send_message(message.chat.id, "❌ Совпадений не найдено.\n(Лимит не потрачен).", reply_markup=welcome_buttons(user_id))
            return

        if user[2] == 0:
            update_user_field(user_id, "search_limit", user[4] - 1)

        results_texts = []
        for row in result:
            line = row[0]
            parts = line.split(',')
            formatted_lines = [f"▫️ <b>Дерек {j+1}:</b> {p.strip().replace('\"', '')}" for j, p in enumerate(parts) if p.strip().replace('\"', '')]
            results_texts.append("\n".join(formatted_lines))

        final_text = "✅ <b>РЕЗУЛЬТАТЫ ПОИСКА:</b>\n\n" + "\n\n➖➖➖➖➖➖➖➖➖\n\n".join(results_texts)
        bot.send_message(message.chat.id, final_text, parse_mode="HTML", reply_markup=welcome_buttons(user_id))

    except Exception as e:
        print("Ошибка DuckDB:", e)
        bot.send_message(message.chat.id, "❌ Ошибка при поиске.", reply_markup=welcome_buttons(user_id))

# ===== АДМИН ПАНЕЛЬ =====
@bot.message_handler(func=lambda m: m.text == "⚙️ Админ-панель")
def admin_panel(message):
    user_id = message.from_user.id
    user = get_user(user_id)
    if not user or (user[3] == 0 and user_id != CREATOR_ID):
        bot.send_message(message.chat.id, "❌ У вас нет доступа.")
        return

    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_prem = types.InlineKeyboardButton("🌟 Выдать Премиум", callback_data="admin_premium")
    btn_searches = types.InlineKeyboardButton("🔍 Выдать поиски", callback_data="admin_give_searches")
    btn_admin = types.InlineKeyboardButton("🛡 Сделать Админом", callback_data="admin_add_admin")
    
    markup.add(btn_prem, btn_searches)
    if user_id == CREATOR_ID:
        markup.add(btn_admin)

    bot.send_message(message.chat.id, "⚙️ <b>Админ-панель</b>\n\nВыберите действие:", parse_mode="HTML", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('admin_'))
def admin_callbacks(call):
    action = call.data
    msg = bot.send_message(call.message.chat.id, "Введите <code>ID</code> пользователя (только цифры):", parse_mode="HTML")
    if action == "admin_premium":
        bot.register_next_step_handler(msg, give_premium)
    elif action == "admin_add_admin":
        bot.register_next_step_handler(msg, add_admin)
    elif action == "admin_give_searches":
        bot.register_next_step_handler(msg, ask_search_amount)

def give_premium(message):
    try:
        target_id = int(message.text.strip())
        update_user_field(target_id, "is_premium", 1)
        bot.send_message(message.chat.id, f"✅ Пользователю {target_id} выдан Премиум!", reply_markup=welcome_buttons(message.from_user.id))
    except:
        bot.send_message(message.chat.id, "❌ Ошибка. Введите только цифры.", reply_markup=welcome_buttons(message.from_user.id))

def add_admin(message):
    try:
        target_id = int(message.text.strip())
        update_user_field(target_id, "is_admin", 1)
        bot.send_message(message.chat.id, f"✅ Пользователь {target_id} теперь Админ!", reply_markup=welcome_buttons(message.from_user.id))
    except:
        bot.send_message(message.chat.id, "❌ Ошибка. Введите только цифры.", reply_markup=welcome_buttons(message.from_user.id))

def ask_search_amount(message):
    try:
        target_id = int(message.text.strip())
        msg = bot.send_message(message.chat.id, f"Пользователь {target_id}.\nСколько поисков добавить? (Введите число):")
        bot.register_next_step_handler(msg, apply_searches, target_id)
    except:
        bot.send_message(message.chat.id, "❌ Ошибка. Введите только цифры.", reply_markup=welcome_buttons(message.from_user.id))

def apply_searches(message, target_id):
    try:
        amount = int(message.text.strip())
        conn = sqlite3.connect(USERS_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT search_limit FROM users WHERE user_id = ?", (target_id,))
        result = cursor.fetchone()
        if result:
            new_limit = result[0] + amount
            cursor.execute("UPDATE users SET search_limit = ? WHERE user_id = ?", (new_limit, target_id))
            conn.commit()
            bot.send_message(message.chat.id, f"✅ Пользователю {target_id} добавлено {amount} поисков.\nТеперь у него: {new_limit} поисков.", reply_markup=welcome_buttons(message.from_user.id))
        else:
            bot.send_message(message.chat.id, "❌ Пользователь не найден.", reply_markup=welcome_buttons(message.from_user.id))
        conn.close()
    except:
        bot.send_message(message.chat.id, "❌ Ошибка. Введите только цифры.", reply_markup=welcome_buttons(message.from_user.id))

# ===== НАЗАД =====
@bot.message_handler(func=lambda m: m.text == "🔙 Назад")
def go_back(message):
    bot.send_message(message.chat.id, "Главное меню:", reply_markup=welcome_buttons(message.from_user.id))

# ===== ЗАПУСК =====
if __name__ == "__main__":
    if not TOKEN or not ZIP_URL or not CREATOR_ID:
        print("❌ ҚАТЕ: Railway-де TOKEN, CREATOR_ID немесе ZIP_URL өзгермелілері қосылмаған!")
    else:
        init_db()
        prepare_data()
        print("Whitless Company Bot запущен!")
        bot.infinity_polling(skip_pending=True, timeout=30)
