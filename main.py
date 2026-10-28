import os
import psycopg2
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
ZIP_URL_1 = os.environ.get("ZIP_URL")
ZIP_URL_2 = os.environ.get("ZIP_URL_2")
CRYPTO_PAY_TOKEN = os.environ.get("CRYPTO_PAY_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
BOT_USERNAME = "WhitlessNet_Bot" # ⚠️ Егер боттың аты басқаша болса, осында өзгертіңіз

bot = telebot.TeleBot(TOKEN)

# ===== ФАЙЛДАРДЫ ЖҮКТЕУ ЖӘНЕ АШУ =====
def prepare_data():
    if not os.path.exists("db1"): os.makedirs("db1")
    if not glob.glob("db1/*.csv"):
        print("⏳ Загрузка Базы 1...")
        try:
            urllib.request.urlretrieve(ZIP_URL_1, "db1.zip")
            with zipfile.ZipFile("db1.zip", 'r') as zip_ref: zip_ref.extractall("db1")
            os.remove("db1.zip")
            print("✅ База 1 готова!")
        except Exception as e: print(f"❌ Ошибка Базы 1: {e}")

    if ZIP_URL_2:
        if not os.path.exists("db2"): os.makedirs("db2")
        if not glob.glob("db2/*.csv"):
            print("⏳ Загрузка Базы 2...")
            try:
                urllib.request.urlretrieve(ZIP_URL_2, "db2.zip")
                with zipfile.ZipFile("db2.zip", 'r') as zip_ref: zip_ref.extractall("db2")
                os.remove("db2.zip")
                print("✅ База 2 готова!")
            except Exception as e: print(f"❌ Ошибка Базы 2: {e}")

# ===== БАЗА ДАННЫХ ПОЛЬЗОВАТЕЛЕЙ (PostgreSQL) =====
def get_db_connection():
    clean_url = DATABASE_URL.split('?')[0]
    return psycopg2.connect(clean_url, sslmode='require')

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT PRIMARY KEY, first_name TEXT, is_premium INTEGER DEFAULT 0,
        premium_until INTEGER DEFAULT 0, is_admin INTEGER DEFAULT 0, search_limit INTEGER DEFAULT 1,
        referrer_id BIGINT DEFAULT 0, ref_earnings INTEGER DEFAULT 0, is_banned INTEGER DEFAULT 0
    )''')
    try: cursor.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_banned INTEGER DEFAULT 0")
    except: pass
    cursor.execute('''CREATE TABLE IF NOT EXISTS processed_invoices (invoice_id TEXT PRIMARY KEY)''')
    conn.commit(); cursor.close(); conn.close()

def get_user(user_id):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
    user = cursor.fetchone(); cursor.close(); conn.close()
    return user

def register_user(message, referrer_id=0):
    user = message.from_user
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE user_id = %s", (user.id,))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (user_id, first_name, referrer_id) VALUES (%s, %s, %s)", (user.id, user.first_name, referrer_id))
        conn.commit()
    cursor.close(); conn.close()

def update_user_field(user_id, field, value):
    conn = get_db_connection(); cursor = conn.cursor()
    allowed_fields = ['is_premium', 'premium_until', 'is_admin', 'search_limit', 'referrer_id', 'ref_earnings', 'is_banned']
    if field in allowed_fields:
        cursor.execute(f"UPDATE users SET {field} = %s WHERE user_id = %s", (value, user_id))
        conn.commit()
    cursor.close(); conn.close()

def check_premium_status(user_id):
    user = get_user(user_id)
    if user and user[3] > int(time.time()): return True
    return False

# ===== РЕФЕРАЛДЫҚ СИСТЕМА =====
def process_referral_bonus(buyer_id, purchase_type):
    user = get_user(buyer_id)
    if not user: return
    referrer_id = user[6]
    if referrer_id == 0 or referrer_id == buyer_id: return
    bonus = 0
    if purchase_type == "req_10": bonus = 1
    elif purchase_type == "req_20": bonus = 2
    elif purchase_type.startswith("custom_"): bonus = int(int(purchase_type.split('_')[1]) * 0.1)
    elif purchase_type.startswith("prem_"): bonus = 10
    if bonus > 0:
        ref_user = get_user(referrer_id)
        if ref_user:
            update_user_field(referrer_id, "search_limit", ref_user[5] + bonus)
            update_user_field(referrer_id, "ref_earnings", ref_user[7] + bonus)
            try: bot.send_message(referrer_id, f"🎉 <b>Реферальный бонус!</b>\n\nВам начислено: <b>+{bonus} запросов</b>!", parse_mode="HTML")
            except: pass

# ===== CRYPTOPAY API =====
def create_crypto_invoice(amount, description, payload):
    url = "https://pay.crypt.bot/api/createInvoice"
    headers = {"Crypto-Pay-API-Token": CRYPTO_PAY_TOKEN}
    data = {"asset": "USDT", "amount": str(amount), "description": description, "payload": payload}
    try:
        res = requests.post(url, headers=headers, json=data).json()
        if res.get("ok"): return res["result"]
    except: pass
    return None

def check_crypto_invoice(invoice_id):
    url = f"https://pay.crypt.bot/api/getInvoices?invoice_ids={invoice_id}"
    headers = {"Crypto-Pay-API-Token": CRYPTO_PAY_TOKEN}
    try:
        res = requests.get(url, headers=headers).json()
        if res.get("ok") and res["result"]["items"]:
            inv = res["result"]["items"][0]
            if inv["status"] == "paid": return inv.get("payload", "")
    except: pass
    return None

# ===== КЛАВИАТУРАЛАР =====
def welcome_buttons(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(types.KeyboardButton("🔍 База 1"), types.KeyboardButton("🔍 База 2"))
    markup.row(types.KeyboardButton("🌟 Обе Базы (Premium)"))
    markup.row(types.KeyboardButton("💎 Купить доступ"), types.KeyboardButton("👥 Рефералы"))
    markup.row(types.KeyboardButton("👤 Мой профиль"), types.KeyboardButton("ℹ️ Информация"))
    
    user = get_user(user_id)
    if user and (user[4] == 1 or user_id == CREATOR_ID):
        markup.row(types.KeyboardButton("⚙️ Админ-панель"))
    return markup

def back_button():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("🔙 Назад"))
    return markup

# ===== СТАРТ =====
@bot.message_handler(commands=["start"])
def start(message):
    referrer_id = 0
    args = message.text.split()
    if len(args) > 1 and args[1].isdigit(): referrer_id = int(args[1])
    register_user(message, referrer_id)
    
    user = get_user(message.from_user.id)
    if user and user[8] == 1:
        bot.send_message(message.chat.id, "⛔ Вы заблокированы в этом боте.")
        return

    text = "👋 Добро пожаловать в <b>Whitless Company</b>!\n\n✅ Выберите базу для поиска:"
    bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=welcome_buttons(message.from_user.id))

# ===== ПРОФИЛЬ =====
@bot.message_handler(func=lambda m: m.text == "👤 Мой профиль")
def my_account(message):
    user = get_user(message.from_user.id)
    if user[8] == 1: return
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

    text = f"👤 <b>МОЙ ПРОФИЛЬ</b>\n\n🆔 ID: <code>{user[0]}</code>\n📊 Статус: {status}\n🔍 Осталось: {limit_text}\n\n👥 Рефералов: <b>{user[7]}</b>"
    bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=back_button())

@bot.message_handler(func=lambda m: m.text == "ℹ️ Информация")
def info(message):
    text = "ℹ️ <b>ИНФОРМАЦИЯ</b>\n\n🏢 Компания: <b>Whitless Company</b>\n🤖 Умный поиск по базам данных."
    bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=back_button())

@bot.message_handler(func=lambda m: m.text == "👥 Рефералы")
def referral_menu(message):
    user_id = message.from_user.id
    ref_link = f"https://t.me/{BOT_USERNAME}?start={user_id}"
    text = f"👥 <b>РЕФЕРАЛЫ</b>\n\n🎁 10% от покупок друга.\n\n🔗 <b>Ваша ссылка:</b>\n<code>{ref_link}</code>"
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("📢 Поделиться", url=f"https://t.me/share/url?url={ref_link}"))
    bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=markup)

# ===== МАГАЗИН =====
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
    bot.send_message(message.chat.id, "💎 <b>Магазин</b>\n\nВыберите товар:", parse_mode="HTML", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('buy_'))
def process_buy(call):
    action = call.data
    amount, desc, payload = 0, "", ""
    if action == "buy_req_10": amount=1; desc="10 запросов"; payload="req_10"
    elif action == "buy_req_20": amount=2; desc="20 запросов"; payload="req_20"
    elif action == "buy_prem_7": amount=5; desc="Премиум 7 дней"; payload="prem_7"
    elif action == "buy_prem_30": amount=20; desc="Премиум 30 дней"; payload="prem_30"
    elif action == "buy_custom":
        msg = bot.send_message(call.message.chat.id, "Введите количество запросов:")
        bot.register_next_step_handler(msg, process_custom_buy)
        return
    send_invoice(call.message.chat.id, amount, desc, payload)

def process_custom_buy(message):
    try:
        reqs = int(message.text.strip())
        if reqs < 1: raise ValueError
        amount = round(reqs * 0.1, 2)
        send_invoice(message.chat.id, amount, f"{reqs} запросов", f"custom_{reqs}")
    except: bot.send_message(message.chat.id, "❌ Ошибка.", reply_markup=back_button())

def send_invoice(chat_id, amount, description, payload):
    invoice = create_crypto_invoice(amount, description, payload)
    if invoice:
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("💳 Оплатить", url=invoice["bot_invoice_url"]))
        markup.add(types.InlineKeyboardButton("✅ Проверить оплату", callback_data=f"check_{invoice['invoice_id']}"))
        bot.send_message(chat_id, f"🧾 <b>Счет создан!</b>\n\n📦 {description}\n💰 <b>{amount} USDT</b>", parse_mode="HTML", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('check_'))
def check_payment(call):
    invoice_id = call.data.split('_')[1]
    user_id = call.from_user.id
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM processed_invoices WHERE invoice_id = %s", (invoice_id,))
    if cursor.fetchone():
        cursor.close(); conn.close()
        bot.answer_callback_query(call.id, "✅ Вы уже получили товар!", show_alert=True)
        return
    cursor.close(); conn.close()

    payload = check_crypto_invoice(invoice_id)
    if payload:
        user = get_user(user_id)
        if payload.startswith("req_") or payload.startswith("custom_"):
            reqs = int(payload.split('_')[1])
            update_user_field(user_id, "search_limit", user[5] + reqs)
            success_text = f"✅ +{reqs} запросов."
        elif payload.startswith("prem_"):
            days = int(payload.split('_')[1])
            new_prem = int(time.time()) + (days * 24 * 60 * 60)
            if user[3] > int(time.time()): new_prem = user[3] + (days * 24 * 60 * 60)
            update_user_field(user_id, "premium_until", new_prem)
            success_text = f"✅ Премиум на {days} дней."
        process_referral_bonus(user_id, payload)
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("INSERT INTO processed_invoices (invoice_id) VALUES (%s)", (invoice_id,))
        conn.commit(); cursor.close(); conn.close()
        bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
        bot.send_message(call.message.chat.id, success_text)
    else: bot.answer_callback_query(call.id, "❌ Оплата не поступила.", show_alert=True)

# ===== ІЗДЕУ ЛОГИКАСЫ =====
@bot.message_handler(func=lambda m: m.text in ["🔍 База 1", "🔍 База 2", "🌟 Обе Базы (Premium)"])
def search_init(message):
    user_id = message.from_user.id
    user = get_user(user_id)
    if user[8] == 1: return
    
    is_prem = check_premium_status(user_id)
    if message.text == "🌟 Обе Базы (Premium)" and not is_prem:
        bot.send_message(message.chat.id, "❌ Поиск по двум базам доступен только для <b>Премиум</b> пользователей!", parse_mode="HTML", reply_markup=back_button())
        return
    if not is_prem and user[5] <= 0:
        bot.send_message(message.chat.id, "❌ Закончились запросы.", reply_markup=back_button())
        return

    msg = bot.send_message(message.chat.id, "🔍 Введите номер, ФИО или ИИН:", reply_markup=back_button())
    bot.register_next_step_handler(msg, process_search, message.text)

# АҚЫЛДЫ ФОРМАТТАУШЫ ФУНКЦИЯ
def format_smart_data(raw_line):
    # Бөлгішті анықтау (үтір немесе нүктелі үтір)
    delimiter = ';' if raw_line.count(';') > raw_line.count(',') else ','
    parts = [p.strip().replace('"', '') for p in raw_line.split(delimiter) if p.strip()]
    
    formatted = []
    for p in parts:
        p_upper = p.upper()
        
        # 1. Мекенжай (Область, Город, Улица т.б.)
        if any(w in p_upper for w in ["ОБЛАСТЬ", "ГОРОД", "РАЙОН", "УЛИЦА", "ДОМ", "МИКРОРАЙОН", "АУЛ", "СЕЛО", "РЕСПУБЛИКА", "КНР", "КАЗАХСТАН", "ПРОСПЕКТ", "ПЕР."]):
            formatted.append(f"📍 <b>Адрес:</b> {p}")
            
        # 2. Жынысы
        elif p_upper in ["МУЖСКОЙ", "ЖЕНСКИЙ", "М", "Ж"]:
            formatted.append(f"🚻 <b>Пол:</b> {p}")
            
        # 3. Телефон нөмірі (+7, 8, скобка немесе сызықша бар сандар)
        elif re.match(r'^[\+\d][\d\s\-\(\)]{8,}$', p) and not re.match(r'^\d{10,14}$', p):
            formatted.append(f"📞 <b>Номер:</b> <code>{p}</code>")
            
        # 4. Туған күні (Дата)
        elif re.match(r'^\d{4}-\d{2}-\d{2}', p) or re.match(r'^\d{2}[\.\-\/]\d{2}[\.\-\/]\d{4}', p):
            formatted.append(f"📅 <b>Дата рож:</b> {p}")
            
        # 5. ИИН / Паспорт (Тек қана 10-14 саннан тұрса)
        elif p.isdigit() and 10 <= len(p) <= 14:
            formatted.append(f"🆔 <b>ИИН/Паспорт:</b> <code>{p}</code>")
            
        # 6. Жүйелік деректер (TRUE/FALSE немесе уақыты бар дата)
        elif "TRUE" in p_upper or "FALSE" in p_upper or re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}', p):
            formatted.append(f"⚙️ <b>Статус:</b> {p}")
            
        # 7. ФИО (Егер әріптерден тұрса және цифр болмаса)
        elif re.match(r'^[А-Яа-яЁёҚқҒғҮүҰұӨөӘәІіҢң\s\-]+$', p) and not any(c.isdigit() for c in p):
            formatted.append(f"👤 <b>ФИО/Имя:</b> {p}")
            
        # 8. Қалғандары
        else:
            formatted.append(f"📌 <b>Данные:</b> {p}")
            
    return "\n".join(formatted)

def process_search(message, search_mode):
    if not message.text or message.text == "🔙 Назад":
        bot.send_message(message.chat.id, "Главное меню:", reply_markup=welcome_buttons(message.from_user.id))
        return

    raw_query = message.text.strip()
    user_id = message.from_user.id
    user = get_user(user_id)
    is_prem = check_premium_status(user_id)

    if search_mode == "🔍 База 1": files_to_search = glob.glob("db1/*.csv")
    elif search_mode == "🔍 База 2": files_to_search = glob.glob("db2/*.csv")
    else: files_to_search = glob.glob("db1/*.csv") + glob.glob("db2/*.csv")

    if not files_to_search:
        bot.send_message(message.chat.id, "⏳ База загружается...", reply_markup=welcome_buttons(user_id))
        return

    wait_msg = bot.send_message(message.chat.id, f"⏳ Идет поиск ({search_mode})...")

    try:
        con = duckdb.connect(database=':memory:')
        digits_only = re.sub(r'[^0-9]', '', raw_query)
        files_str = "[" + ", ".join([f"'{f}'" for f in files_to_search]) + "]"
        
        if digits_only and len(digits_only) > 5:
            sql_query = f"SELECT * FROM read_csv({files_str}, columns={{'line': 'VARCHAR'}}, delim='\n', header=False, quote='', escape='') WHERE regexp_replace(line, '[^0-9]', '', 'g') LIKE ? LIMIT 5"
            params = [f"%{digits_only}%"]
        else:
            words = [w.lower() for w in raw_query.split() if w]
            if not words:
                bot.send_message(message.chat.id, "❌ Введите корректный запрос.", reply_markup=welcome_buttons(user_id))
                return
            conditions = " AND ".join(["line ILIKE ?"] * len(words))
            params = [f"%{w}%" for w in words]
            sql_query = f"SELECT * FROM read_csv({files_str}, columns={{'line': 'VARCHAR'}}, delim='\n', header=False, quote='', escape='') WHERE {conditions} LIMIT 5"
        
        result = con.execute(sql_query, params).fetchall()
        con.close()
        bot.delete_message(message.chat.id, wait_msg.message_id)

        if not result:
            bot.send_message(message.chat.id, "❌ Не найдено.\n(Запрос не потрачен).", reply_markup=welcome_buttons(user_id))
            return

        if not is_prem: update_user_field(user_id, "search_limit", user[5] - 1)

        results_texts = []
        for row in result:
            # Жаңа ақылды функцияны шақырамыз
            results_texts.append(format_smart_data(row[0]))

        final_text = f"✅ <b>РЕЗУЛЬТАТЫ ({search_mode}):</b>\n\n" + "\n\n➖➖➖➖➖➖➖➖➖\n\n".join(results_texts)
        bot.send_message(message.chat.id, final_text, parse_mode="HTML", reply_markup=welcome_buttons(user_id))

    except Exception as e:
        print("Ошибка DuckDB:", e)
        bot.send_message(message.chat.id, "❌ Ошибка при поиске.", reply_markup=welcome_buttons(message.from_user.id))

# ===== АДМИН ПАНЕЛЬ =====
@bot.message_handler(func=lambda m: m.text == "⚙️ Админ-панель")
def admin_panel(message):
    user_id = message.from_user.id
    user = get_user(user_id)
    if not user or (user[4] == 0 and user_id != CREATOR_ID): return

    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    cursor.close(); conn.close()

    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🌟 Выдать Премиум", callback_data="adm_give_prem"),
        types.InlineKeyboardButton("❌ Забрать Премиум", callback_data="adm_remove_prem")
    )
    markup.add(
        types.InlineKeyboardButton("🔍 Выдать запросы", callback_data="adm_give_searches"),
        types.InlineKeyboardButton("⛔ В Черный список", callback_data="adm_ban")
    )
    markup.add(
        types.InlineKeyboardButton("✅ Из ЧС (Разбанить)", callback_data="adm_unban"),
        types.InlineKeyboardButton("🛡 Сделать Админом", callback_data="adm_add_admin")
    )
    markup.add(types.InlineKeyboardButton("🚫 Снять Админа", callback_data="adm_remove_admin"))

    bot.send_message(message.chat.id, f"⚙️ <b>Админ-панель</b>\n\n👥 Пользователей: <b>{total_users}</b>", parse_mode="HTML", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('adm_'))
def admin_actions(call):
    action = call.data
    msg = bot.send_message(call.message.chat.id, "Введите <b>ID</b> пользователя (только цифры):", parse_mode="HTML")
    bot.register_next_step_handler(msg, admin_process_id, action)

def admin_process_id(message, action):
    try:
        target_id = int(message.text.strip())
        target_user = get_user(target_id)
        if not target_user:
            bot.send_message(message.chat.id, "❌ Пользователь не найден.", reply_markup=welcome_buttons(message.from_user.id))
            return

        if action == "adm_give_prem":
            new_prem_until = int(time.time()) + (30 * 24 * 60 * 60)
            update_user_field(target_id, "premium_until", new_prem_until)
            bot.send_message(message.chat.id, f"✅ Пользователю {target_id} выдан Премиум на 30 дней!", reply_markup=welcome_buttons(message.from_user.id))
        elif action == "adm_remove_prem":
            update_user_field(target_id, "premium_until", 0)
            bot.send_message(message.chat.id, f"✅ У пользователя {target_id} забрали Премиум.", reply_markup=welcome_buttons(message.from_user.id))
        elif action == "adm_ban":
            update_user_field(target_id, "is_banned", 1)
            bot.send_message(message.chat.id, f"⛔ Пользователь {target_id} заблокирован (ЧС).", reply_markup=welcome_buttons(message.from_user.id))
        elif action == "adm_unban":
            update_user_field(target_id, "is_banned", 0)
            bot.send_message(message.chat.id, f"✅ Пользователь {target_id} разблокирован.", reply_markup=welcome_buttons(message.from_user.id))
        elif action == "adm_add_admin":
            update_user_field(target_id, "is_admin", 1)
            bot.send_message(message.chat.id, f"🛡 Пользователь {target_id} теперь Админ!", reply_markup=welcome_buttons(message.from_user.id))
        elif action == "adm_remove_admin":
            update_user_field(target_id, "is_admin", 0)
            bot.send_message(message.chat.id, f"🚫 Пользователь {target_id} больше не Админ.", reply_markup=welcome_buttons(message.from_user.id))
        elif action == "adm_give_searches":
            msg = bot.send_message(message.chat.id, f"Пользователь {target_id}.\nСколько запросов добавить?")
            bot.register_next_step_handler(msg, apply_searches, target_id)
    except:
        bot.send_message(message.chat.id, "❌ Ошибка. Введите корректный ID.", reply_markup=welcome_buttons(message.from_user.id))

def apply_searches(message, target_id):
    try:
        amount = int(message.text.strip())
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT search_limit FROM users WHERE user_id = %s", (target_id,))
        result = cursor.fetchone()
        if result:
            new_limit = result[0] + amount
            cursor.execute("UPDATE users SET search_limit = %s WHERE user_id = %s", (new_limit, target_id))
            conn.commit()
            bot.send_message(message.chat.id, f"✅ {target_id} добавлено {amount} запросов. Всего: {new_limit}.", reply_markup=welcome_buttons(message.from_user.id))
        cursor.close(); conn.close()
    except:
        bot.send_message(message.chat.id, "❌ Ошибка.", reply_markup=welcome_buttons(message.from_user.id))

@bot.message_handler(func=lambda m: m.text == "🔙 Назад")
def go_back(message):
    bot.send_message(message.chat.id, "Главное меню:", reply_markup=welcome_buttons(message.from_user.id))

if __name__ == "__main__":
    if not all([TOKEN, CREATOR_ID, ZIP_URL_1, ZIP_URL_2, CRYPTO_PAY_TOKEN, DATABASE_URL]):
        print("❌ ОШИБКА: Не хватает переменных в Railway!")
    else:
        init_db()
        prepare_data()
        print("Whitless Company Bot запущен (Умный поиск)!")
        bot.infinity_polling(skip_pending=True, timeout=30)
