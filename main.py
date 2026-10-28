import os
import sqlite3
import telebot
from telebot import types
import duckdb
import urllib.request
import zipfile
import glob
import requests
import time
import re
from datetime import datetime

# ===== НАСТРОЙКИ =====
TOKEN = os.environ.get("TOKEN")
CREATOR_ID = int(os.environ.get("CREATOR_ID", 0))
ZIP_URL = os.environ.get("ZIP_URL")
CRYPTO_PAY_TOKEN = os.environ.get("CRYPTO_PAY_TOKEN")
BOT_USERNAME = "WhitlessBot" # ⚠️ ЕГЕР БОТТЫҢ USERNAME БАСҚАША БОЛСА, ОСЫ ЖЕРДІ ӨЗГЕРТІҢІЗ (@сіздің_ботыңыз)

USERS_DB = "users.db"
CSV_FILE = "data.csv"
ZIP_FILE = "data.zip"

bot = telebot.TeleBot(TOKEN)

# ===== ФАЙЛДЫ ЖҮКТЕУ ЖӘНЕ АШУ =====
def prepare_data():
    global CSV_FILE
    existing_csv = glob.glob("*.csv") + glob.glob("*/*.csv")
    if existing_csv:
        CSV_FILE = existing_csv[0]
        print(f"✅ База данных найдена: {CSV_FILE} (Размер: {os.path.getsize(CSV_FILE)} байт)")
        return

    print("⏳ Загрузка файла базы данных с GitHub...")
    try:
        urllib.request.urlretrieve(ZIP_URL, ZIP_FILE)
        print("✅ Загрузка завершена. Распаковка...")
        with zipfile.ZipFile(ZIP_FILE, 'r') as zip_ref:
            zip_ref.extractall(".") 
            
        existing_csv = glob.glob("*.csv") + glob.glob("*/*.csv")
        if existing_csv:
            CSV_FILE = existing_csv[0]
            print(f"✅ База данных готова: {CSV_FILE}")
        os.remove(ZIP_FILE)
    except Exception as e:
        print(f"❌ Ошибка загрузки: {e}")

# ===== БАЗА ДАННЫХ ПОЛЬЗОВАТЕЛЕЙ =====
def init_db():
    conn = sqlite3.connect(USERS_DB)
    cursor = conn.cursor()
    # Негізгі кесте
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        first_name TEXT,
        is_premium INTEGER DEFAULT 0,
        premium_until INTEGER DEFAULT 0,
        is_admin INTEGER DEFAULT 0,
        search_limit INTEGER DEFAULT 1,
        referrer_id INTEGER DEFAULT 0,
        ref_earnings INTEGER DEFAULT 0
    )''')
    
    # Ескі база болса, жаңа бағандарды қосу (Миграция)
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN referrer_id INTEGER DEFAULT 0")
        cursor.execute("ALTER TABLE users ADD COLUMN ref_earnings INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass # Бағандар бұрыннан бар

    # Төленген счеттер кестесі
    cursor.execute('''CREATE TABLE IF NOT EXISTS processed_invoices (
        invoice_id TEXT PRIMARY KEY
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

def register_user(message, referrer_id=0):
    user = message.from_user
    conn = sqlite3.connect(USERS_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user.id,))
    if not cursor.fetchone():
        # Жаңа адам. Егер реферал сілтемесімен келсе, оны сақтаймыз
        cursor.execute("INSERT INTO users (user_id, first_name, referrer_id) VALUES (?, ?, ?)", 
                       (user.id, user.first_name, referrer_id))
        conn.commit()
    conn.close()

def update_user_field(user_id, field, value):
    conn = sqlite3.connect(USERS_DB)
    cursor = conn.cursor()
    cursor.execute(f"UPDATE users SET {field} = ? WHERE user_id = ?", (value, user_id))
    conn.commit()
    conn.close()

def check_premium_status(user_id):
    user = get_user(user_id)
    if user[3] > int(time.time()):
        return True
    return False

def get_ref_count(user_id):
    conn = sqlite3.connect(USERS_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users WHERE referrer_id = ?", (user_id,))
    count = cursor.fetchone()[0]
    conn.close()
    return count

# ===== РЕФЕРАЛДЫҚ СИСТЕМА (БОНУС БЕРУ) =====
def process_referral_bonus(buyer_id, purchase_type):
    user = get_user(buyer_id)
    if not user: return
    
    referrer_id = user[6] # referrer_id индексі
    if referrer_id == 0 or referrer_id == buyer_id: return
    
    bonus = 0
    if purchase_type == "req_10":
        bonus = 1
    elif purchase_type == "req_20":
        bonus = 2
    elif purchase_type.startswith("custom_"):
        reqs = int(purchase_type.split('_')[1])
        bonus = int(reqs * 0.1) # 10% округленный вниз
    elif purchase_type.startswith("prem_"):
        bonus = 10 # Премиум алса, 10 запрос
        
    if bonus > 0:
        ref_user = get_user(referrer_id)
        if ref_user:
            # Рефералға запрос пен статистиканы қосамыз
            update_user_field(referrer_id, "search_limit", ref_user[5] + bonus)
            update_user_field(referrer_id, "ref_earnings", ref_user[7] + bonus)
            
            # Рефералға хабарлама жібереміз
            try:
                bot.send_message(
                    referrer_id, 
                    f"🎉 <b>Реферальный бонус!</b>\n\nВаш приглашенный друг совершил покупку.\nВам начислено: <b>+{bonus} запросов</b>!",
                    parse_mode="HTML"
                )
            except:
                pass # Егер ол ботты бұғаттап тастаса

# ===== CRYPTOPAY API =====
def create_crypto_invoice(amount, description, payload):
    url = "https://pay.crypt.bot/api/createInvoice"
    headers = {"Crypto-Pay-API-Token": CRYPTO_PAY_TOKEN}
    data = {
        "asset": "USDT",
        "amount": str(amount),
        "description": description,
        "payload": payload
    }
    try:
        response = requests.post(url, headers=headers, json=data).json()
        if response.get("ok"):
            return response["result"]
    except Exception as e:
        print("CryptoPay Error:", e)
    return None

def check_crypto_invoice(invoice_id):
    url = f"https://pay.crypt.bot/api/getInvoices?invoice_ids={invoice_id}"
    headers = {"Crypto-Pay-API-Token": CRYPTO_PAY_TOKEN}
    try:
        response = requests.get(url, headers=headers).json()
        if response.get("ok") and response["result"]["items"]:
            invoice = response["result"]["items"][0]
            if invoice["status"] == "paid":
                return invoice.get("payload", "")
    except Exception as e:
        print("CryptoPay Check Error:", e)
    return None

# ===== КЛАВИАТУРЫ =====
def welcome_buttons(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(types.KeyboardButton("🔍 Поиск"), types.KeyboardButton("💎 Купить доступ"))
    markup.row(types.KeyboardButton("👥 Рефералы"), types.KeyboardButton("🆔 Мой профиль"))
    
    user = get_user(user_id)
    if user and (user[4] == 1 or user_id == CREATOR_ID):
        markup.row(types.KeyboardButton("⚙️ Админ-панель"), types.KeyboardButton("👁 Информация"))
    else:
        markup.row(types.KeyboardButton("👁 Информация"))
    return markup

def back_button():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("🔙 Назад"))
    return markup

# ===== СТАРТ (РЕФЕРАЛ СІЛТЕМЕСІН ТЕКСЕРУ) =====
@bot.message_handler(commands=["start"])
def start(message):
    referrer_id = 0
    # Егер сілтеме арқылы келсе (мысалы: /start 123456789)
    args = message.text.split()
    if len(args) > 1 and args[1].isdigit():
        referrer_id = int(args[1])
    
    register_user(message, referrer_id)
    text = "👋 Добро пожаловать в <b>Whitless Company</b>!\n\n✅ Бот готов к поиску."
    bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=welcome_buttons(message.from_user.id))

@bot.message_handler(func=lambda m: m.text == "👁 Информация")
def info(message):
    text = "ℹ️ <b>ИНФОРМАЦИЯ</b>\n\n🏢 Компания: <b>Whitless Company</b>\n🤖 Умный поиск по базам данных.\n\n💎 Пополнить баланс можно через CryptoBot."
    bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=back_button())

@bot.message_handler(func=lambda m: m.text == "🆔 Мой профиль")
def my_account(message):
    register_user(message)
    user = get_user(message.from_user.id)
    
    is_prem = check_premium_status(user[0])
    status = "👤 Обычный"
    limit_text = f"{user[5]} запросов"
    
    if is_prem:
        status = "🌟 Премиум"
        limit_text = "♾ Безлимит"
        prem_date = datetime.fromtimestamp(user[3]).strftime('%d.%m.%Y %H:%M')
        limit_text += f" (до {prem_date})"
    elif user[4] == 1 or user[0] == CREATOR_ID:
        status = "👑 Создатель"

    text = (
        f"🆔 <b>МОЙ ПРОФИЛЬ</b>\n\n"
        f"👤 Имя: {user[1]}\n"
        f"🆔 ID: <code>{user[0]}</code>\n"
        f"📊 Статус: {status}\n"
        f"🔍 Осталось запросов: {limit_text}\n\n"
        f"👥 Приглашено друзей: <b>{get_ref_count(user[0])}</b>\n"
        f"💰 Заработано с рефералов: <b>{user[7]}</b>"
    )
    bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=back_button())

# ===== РЕФЕРАЛДЫҚ БӨЛІМ =====
@bot.message_handler(func=lambda m: m.text == "👥 Рефералы")
def referral_menu(message):
    user_id = message.from_user.id
    user = get_user(user_id)
    ref_link = f"https://t.me/{BOT_USERNAME}?start={user_id}"
    
    text = (
        "👥 <b>РЕФЕРАЛЬНАЯ СИСТЕМА</b>\n\n"
        "Приглашайте друзей и получайте бонусы!\n\n"
        "🎁 <b>Ваши бонусы:</b>\n"
        "• Если друг купит 10 запросов, вы получите <b>10%</b> (1 запрос).\n"
        "• Если друг купит Премиум, вы получите <b>10 запросов</b>.\n\n"
        f"👥 <b>Приглашено друзей:</b> {get_ref_count(user_id)}\n"
        f"💰 <b>Заработано запросов:</b> {user[7]}\n\n"
        f"🔗 <b>Ваша ссылка для приглашения:</b>\n<code>{ref_link}</code>"
    )
    
    markup = types.InlineKeyboardMarkup()
    share_btn = types.InlineKeyboardButton("📢 Поделиться ссылкой", url=f"https://t.me/share/url?url={ref_link}&text=Присоединяйся к лучшему боту для поиска!")
    markup.add(share_btn)
    
    bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=markup)

# ===== МАГАЗИН (CRYPTOPAY) =====
@bot.message_handler(func=lambda m: m.text == "💎 Купить доступ")
def shop(message):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("10 запросов - $1", callback_data="buy_req_10"),
        types.InlineKeyboardButton("20 запросов - $2", callback_data="buy_req_20")
    )
    markup.add(types.InlineKeyboardButton("✍️ Свое количество ($0.1/шт)", callback_data="buy_custom"))
    markup.add(
        types.InlineKeyboardButton("🌟 Премиум 7 дней - $5", callback_data="buy_prem_7"),
        types.InlineKeyboardButton("🌟 Премиум 1 месяц - $20", callback_data="buy_prem_30")
    )
    bot.send_message(message.chat.id, "💎 <b>Магазин возможностей</b>\n\nВыберите, что хотите приобрести (оплата в USDT):", parse_mode="HTML", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('buy_'))
def process_buy(call):
    action = call.data
    amount = 0
    description = ""
    payload = ""
    
    if action == "buy_req_10":
        amount = 1
        description = "10 запросов к базе"
        payload = "req_10"
    elif action == "buy_req_20":
        amount = 2
        description = "20 запросов к базе"
        payload = "req_20"
    elif action == "buy_prem_7":
        amount = 5
        description = "Премиум на 7 дней"
        payload = "prem_7"
    elif action == "buy_prem_30":
        amount = 20
        description = "Премиум на 30 дней"
        payload = "prem_30"
    elif action == "buy_custom":
        msg = bot.send_message(call.message.chat.id, "Введите желаемое количество запросов:")
        bot.register_next_step_handler(msg, process_custom_buy)
        return

    send_invoice(call.message.chat.id, amount, description, payload)

def process_custom_buy(message):
    try:
        reqs = int(message.text.strip())
        if reqs < 1: raise ValueError
        amount = round(reqs * 0.1, 2)
        if amount < 0.1: amount = 0.1
        
        description = f"{reqs} запросов к базе"
        payload = f"custom_{reqs}"
        send_invoice(message.chat.id, amount, description, payload)
    except:
        bot.send_message(message.chat.id, "❌ Ошибка. Введите целое число больше 0.", reply_markup=back_button())

def send_invoice(chat_id, amount, description, payload):
    invoice = create_crypto_invoice(amount, description, payload)
    if invoice:
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("💳 Оплатить", url=invoice["bot_invoice_url"]))
        markup.add(types.InlineKeyboardButton("✅ Проверить оплату", callback_data=f"check_{invoice['invoice_id']}"))
        
        text = f"🧾 <b>Счет создан!</b>\n\n📦 Товар: <b>{description}</b>\n💰 Сумма: <b>{amount} USDT</b>\n\n<i>Оплатите и нажмите 'Проверить оплату'.</i>"
        bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)
    else:
        bot.send_message(chat_id, "❌ Ошибка создания счета.")

@bot.callback_query_handler(func=lambda call: call.data.startswith('check_'))
def check_payment(call):
    invoice_id = call.data.split('_')[1]
    user_id = call.from_user.id
    
    conn = sqlite3.connect(USERS_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM processed_invoices WHERE invoice_id = ?", (invoice_id,))
    already_processed = cursor.fetchone()
    conn.close()

    if already_processed:
        bot.answer_callback_query(call.id, "✅ Вы уже получили товар по этому счету!", show_alert=True)
        bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
        return

    payload = check_crypto_invoice(invoice_id)
    
    if payload:
        user = get_user(user_id)
        
        # Төлем түрін анықтау
        if payload.startswith("req_") or payload.startswith("custom_"):
            reqs = int(payload.split('_')[1])
            update_user_field(user_id, "search_limit", user[5] + reqs)
            success_text = f"✅ Успешно оплачено! Вам добавлено {reqs} запросов."
        elif payload.startswith("prem_"):
            days = int(payload.split('_')[1])
            new_prem_until = int(time.time()) + (days * 24 * 60 * 60)
            if user[3] > int(time.time()):
                new_prem_until = user[3] + (days * 24 * 60 * 60)
            update_user_field(user_id, "premium_until", new_prem_until)
            success_text = f"✅ Успешно оплачено! Премиум активирован на {days} дней."
        
        # РЕФЕРАЛҒА БОНУС БЕРУ
        process_referral_bonus(user_id, payload)
        
        conn = sqlite3.connect(USERS_DB)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO processed_invoices (invoice_id) VALUES (?)", (invoice_id,))
        conn.commit()
        conn.close()
        
        bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
        bot.send_message(call.message.chat.id, success_text)
    else:
        bot.answer_callback_query(call.id, "❌ Оплата еще не поступила.", show_alert=True)

# ===== ПОИСК =====
@bot.message_handler(func=lambda m: m.text == "🔍 Поиск")
def search(message):
    register_user(message)
    user = get_user(message.from_user.id)
    is_prem = check_premium_status(user[0])
    
    if not is_prem and user[5] <= 0:
        bot.send_message(message.chat.id, "❌ У вас закончились запросы.\nПополните баланс в разделе '💎 Купить доступ'.", reply_markup=back_button())
        return
        
    msg = bot.send_message(message.chat.id, "🔍 Введите номер телефона, ФИО или ИИН для поиска:", reply_markup=back_button())
    bot.register_next_step_handler(msg, process_search)

def process_search(message):
    if not message.text or message.text == "🔙 Назад":
        bot.send_message(message.chat.id, "Главное меню:", reply_markup=welcome_buttons(message.from_user.id))
        return

    raw_query = message.text.strip()
    user_id = message.from_user.id
    user = get_user(user_id)
    is_prem = check_premium_status(user_id)

    if not os.path.isfile(CSV_FILE):
        bot.send_message(message.chat.id, "⏳ База данных все еще загружается на сервер.", reply_markup=welcome_buttons(user_id))
        return

    wait_msg = bot.send_message(message.chat.id, "⏳ Идет поиск...")

    try:
        con = duckdb.connect(database=':memory:')
        digits_only = re.sub(r'[^0-9]', '', raw_query)
        
        if digits_only and len(digits_only) > 5:
            sql_query = f"""
                SELECT * FROM read_csv('{CSV_FILE}', columns={{'line': 'VARCHAR'}}, delim='\n', header=False, quote='', escape='') 
                WHERE regexp_replace(line, '[^0-9]', '', 'g') LIKE ? LIMIT 5
            """
            params = [f"%{digits_only}%"]
        else:
            words = [w.lower() for w in raw_query.split() if w]
            if not words:
                bot.send_message(message.chat.id, "❌ Введите корректный запрос.", reply_markup=welcome_buttons(user_id))
                return
            conditions = " AND ".join(["line ILIKE ?"] * len(words))
            params = [f"%{w}%" for w in words]
            sql_query = f"""
                SELECT * FROM read_csv('{CSV_FILE}', columns={{'line': 'VARCHAR'}}, delim='\n', header=False, quote='', escape='') 
                WHERE {conditions} LIMIT 5
            """
        
        result = con.execute(sql_query, params).fetchall()
        con.close()

        bot.delete_message(message.chat.id, wait_msg.message_id)

        if not result:
            bot.send_message(message.chat.id, "❌ Совпадений не найдено.\n(Запрос не был потрачен).", reply_markup=welcome_buttons(user_id))
            return

        if not is_prem:
            update_user_field(user_id, "search_limit", user[5] - 1)

        results_texts = []
        for row in result:
            line = row[0]
            parts = line.split(',')
            formatted_lines = [f"▫️ <b>Данные {j+1}:</b> {p.strip().replace('\"', '')}" for j, p in enumerate(parts) if p.strip().replace('\"', '')]
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
    if not user or (user[4] == 0 and user_id != CREATOR_ID):
        bot.send_message(message.chat.id, "❌ У вас нет доступа.")
        return

    # Статистика
    conn = sqlite3.connect(USERS_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    conn.close()

    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_prem = types.InlineKeyboardButton("🌟 Выдать Премиум", callback_data="admin_premium")
    btn_searches = types.InlineKeyboardButton("🔍 Выдать запросы", callback_data="admin_give_searches")
    btn_admin = types.InlineKeyboardButton("🛡 Сделать Админом", callback_data="admin_add_admin")
    
    markup.add(btn_prem, btn_searches)
    if user_id == CREATOR_ID:
        markup.add(btn_admin)

    text = f"⚙️ <b>Админ-панель</b>\n\n👥 Всего пользователей: <b>{total_users}</b>\n\nВыберите действие:"
    bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('admin_'))
def admin_callbacks(call):
    action = call.data
    msg = bot.send_message(call.message.chat.id, "Введите <code>ID</code> пользователя:", parse_mode="HTML")
    if action == "admin_premium":
        bot.register_next_step_handler(msg, give_premium)
    elif action == "admin_add_admin":
        bot.register_next_step_handler(msg, add_admin)
    elif action == "admin_give_searches":
        bot.register_next_step_handler(msg, ask_search_amount)

def give_premium(message):
    try:
        target_id = int(message.text.strip())
        new_prem_until = int(time.time()) + (30 * 24 * 60 * 60)
        update_user_field(target_id, "premium_until", new_prem_until)
        bot.send_message(message.chat.id, f"✅ Пользователю {target_id} выдан Премиум на 30 дней!", reply_markup=welcome_buttons(message.from_user.id))
    except:
        bot.send_message(message.chat.id, "❌ Ошибка. Введите цифры.", reply_markup=welcome_buttons(message.from_user.id))

def add_admin(message):
    try:
        target_id = int(message.text.strip())
        update_user_field(target_id, "is_admin", 1)
        bot.send_message(message.chat.id, f"✅ Пользователь {target_id} теперь Админ!", reply_markup=welcome_buttons(message.from_user.id))
    except:
        bot.send_message(message.chat.id, "❌ Ошибка. Введите цифры.", reply_markup=welcome_buttons(message.from_user.id))

def ask_search_amount(message):
    try:
        target_id = int(message.text.strip())
        msg = bot.send_message(message.chat.id, f"Пользователь {target_id}.\nСколько запросов добавить?")
        bot.register_next_step_handler(msg, apply_searches, target_id)
    except:
        bot.send_message(message.chat.id, "❌ Ошибка.", reply_markup=welcome_buttons(message.from_user.id))

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
            bot.send_message(message.chat.id, f"✅ Пользователю {target_id} добавлено {amount} запросов.\nТеперь у него: {new_limit}.", reply_markup=welcome_buttons(message.from_user.id))
        else:
            bot.send_message(message.chat.id, "❌ Пользователь не найден.", reply_markup=welcome_buttons(message.from_user.id))
        conn.close()
    except:
        bot.send_message(message.chat.id, "❌ Ошибка.", reply_markup=welcome_buttons(message.from_user.id))

# ===== НАЗАД =====
@bot.message_handler(func=lambda m: m.text == "🔙 Назад")
def go_back(message):
    bot.send_message(message.chat.id, "Главное меню:", reply_markup=welcome_buttons(message.from_user.id))

# ===== ЗАПУСК =====
if __name__ == "__main__":
    if not all([TOKEN, CREATOR_ID, ZIP_URL, CRYPTO_PAY_TOKEN]):
        print("❌ ОШИБКА: Не все переменные окружения заданы в Railway!")
    else:
        init_db()
        prepare_data()
        print("Whitless Company Bot запущен!")
        bot.infinity_polling(skip_pending=True, timeout=30)
