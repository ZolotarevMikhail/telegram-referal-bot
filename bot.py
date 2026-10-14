# -*- coding: utf-8 -*-
"""
Реферальный бот Telegram.

Схема: пользователь заходит по ссылке с кодом (deep link),
бот запоминает пару «код ↔ Telegram-ID». Когда партнёрская система
присылает postback «код подтвердил действие», бот выдаёт
одноразовую ссылку-приглашение в приватную группу.

Повторная выдача исключена: факт выдачи хранится в базе.
Админ может выдать доступ вручную командой /grant.

Токен и настройки — только в файле .env, в коде их нет.
"""

import os
import re
import sqlite3
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv
import telebot

# --- Настройки из .env ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
STATUS_OK = os.getenv("STATUS_OK", "1").strip()
POSTBACK_PORT = int(os.getenv("POSTBACK_PORT", "8000"))
POSTBACK_SECRET = os.getenv("POSTBACK_SECRET", "").strip()

# Админы — список Telegram-ID через запятую, например: 123456789,987654321
ADMIN_IDS = {
    int(raw_id) for raw_id in os.getenv("ADMIN_IDS", "")
    .replace(" ", "").split(",") if raw_id.strip().isdigit()
}

if not BOT_TOKEN:
    print("Ошибка: не указан BOT_TOKEN в файле .env")
    sys.exit(1)

bot = telebot.TeleBot(BOT_TOKEN)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "refbot.db")
db_lock = threading.Lock()  # база — один файл, обращения из двух потоков
# надо блокировать

# Код из реферальной ссылки: латиница/цифры/дефис/подчерк, 3-64 символа
REF_CODE_PATTERN = r"[A-Za-z0-9_-]{3,64}"


# --- База (SQLite, один файл рядом с ботом) ---

SQL_CREATE_USERS = (
    "CREATE TABLE IF NOT EXISTS users ("
    " tg_id INTEGER PRIMARY KEY,"
    " ref_id TEXT NOT NULL UNIQUE,"
    " granted INTEGER NOT NULL DEFAULT 0,"
    " granted_at TEXT)"
)
SQL_CREATE_SETTINGS = (
    "CREATE TABLE IF NOT EXISTS settings "
    "(key TEXT PRIMARY KEY, value TEXT)"
)


def init_db():
    """Создать таблицы базы, если их ещё нет."""
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(SQL_CREATE_USERS)
        conn.execute(SQL_CREATE_SETTINGS)
        conn.commit()
        conn.close()


def db_exec(query, params=()):
    """Один запрос к базе: выполнить, зафиксировать, вернуть строки."""
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.execute(query, params)
        conn.commit()
        result = cursor.fetchall()
        conn.close()
        return result


def save_pair(ref_id, tg_id):
    """Запомнить пару «код ↔ пользователь».

    Если пара уже есть — ничего не менять (INSERT OR IGNORE).
    """
    db_exec(
        "INSERT OR IGNORE INTO users (tg_id, ref_id) VALUES (?, ?)",
        (tg_id, ref_id),
    )


def find_by_ref(ref_id):
    """Найти пользователя по реферальному коду (или None)."""
    rows = db_exec("SELECT tg_id, granted FROM users WHERE ref_id = ?",
                   (ref_id,))
    return rows[0] if rows else None


def find_by_tg_id(tg_id):
    """Найти пользователя по Telegram-ID (или None). Нужно для /grant."""
    rows = db_exec("SELECT tg_id, granted FROM users WHERE tg_id = ?",
                   (int(tg_id),))
    return rows[0] if rows else None


def is_granted(tg_id):
    """Выдан ли уже доступ этому пользователю."""
    rows = db_exec("SELECT granted FROM users WHERE tg_id = ?", (tg_id,))
    return bool(rows and rows[0][0])


def mark_granted(tg_id):
    """Отметить пользователя как получившего доступ.

    Условие «AND granted = 0» защищает от перезаписи даты повторной
    выдачей.
    """
    granted_at = datetime.now().isoformat(timespec="seconds")
    db_exec(
        "UPDATE users SET granted = 1, granted_at = ? "
        "WHERE tg_id = ? AND granted = 0",
        (granted_at, tg_id),
    )


def get_group_id():
    """ID группы для приглашений (None, если ещё не задан)."""
    rows = db_exec("SELECT value FROM settings WHERE key = 'group_id'")
    return int(rows[0][0]) if rows else None


def set_group_id(group_id):
    """Запомнить ID группы (вставка или обновление)."""
    db_exec(
        "INSERT INTO settings (key, value) VALUES ('group_id', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(group_id),),
    )


# --- Выдача доступа ---

def grant_user(tg_id):
    """Выдать доступ пользователю: одноразовая ссылка в группу.

    Возвращает (успех, текст для админа/лога).
    """
    if is_granted(tg_id):
        return False, "доступ уже выдан ранее"
    group_id = get_group_id()
    if group_id is None:
        return False, ("группа не задана — "
                       "админ должен прислать /setgroup в группе")
    try:
        invite_link = bot.create_chat_invite_link(
            group_id, member_limit=1
        ).invite_link
        bot.send_message(
            tg_id,
            "Условие выполнено! Приглашение в группу "
            "(ссылка одноразовая):\n" + invite_link,
        )
    except Exception as error:
        return False, f"ошибка выдачи: {error}"
    mark_granted(tg_id)
    return True, "доступ выдан"


def process_postback(ref_id, status):
    """Обработать postback от партнёрской системы. Возвращает текст-ответ."""
    if status != STATUS_OK:
        return ("postback по коду {}: действие отклонено "
                "(status={}), доступ не выдан").format(ref_id, status)
    user = find_by_ref(ref_id)
    if user is None:
        return ("postback по коду {}: пользователь не найден "
                "(не заходил в бота)").format(ref_id)
    tg_id = user[0]
    ok, why = grant_user(tg_id)
    return f"postback по коду {ref_id}: {why}"


# --- Команды бота ---

def split_start_payload(message_text):
    """Код из текста /start: всё после первого пробела (или пусто)."""
    parts = message_text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


@bot.message_handler(commands=["start"], chat_types=["private"])
def cmd_start(m):
    payload = split_start_payload(m.text)
    if not re.fullmatch(REF_CODE_PATTERN, payload):
        bot.reply_to(
            m,
            "Привет! Это бот-привратник приватной группы.\n"
            "Заходите по своей персональной ссылке — "
            "дальше всё автоматически.",
        )
        return
    save_pair(payload, m.from_user.id)
    bot.reply_to(
        m,
        f"Принято! Ваш код: {payload}.\n"
        "Когда партнёрская система подтвердит действие, "
        "я пришлю приглашение в группу автоматически.",
    )


@bot.message_handler(commands=["help"], chat_types=["private"])
def cmd_help(m):
    bot.reply_to(
        m,
        "Я выдаю доступ в приватную группу автоматически.\n"
        "• Зайдите по персональной ссылке — я запомню вас.\n"
        "• После подтверждения действия придёт "
        "одноразовая ссылка-приглашение.",
    )


def is_admin(tg_id):
    """Является ли отправитель админом (список в .env)."""
    return tg_id in ADMIN_IDS


@bot.message_handler(commands=["setgroup"])
def cmd_setgroup(m):
    """Админ присылает /setgroup прямо в целевой группе — бот запоминает."""
    if not is_admin(m.from_user.id):
        return  # молча игнорируем чужих
    if m.chat.type not in ("group", "supergroup"):
        bot.reply_to(m, "Эту команду нужно прислать в самой группе.")
        return
    set_group_id(m.chat.id)
    bot.reply_to(
        m,
        "Группа запомнена. Теперь я выдаю сюда "
        "одноразовые приглашения.",
    )


@bot.message_handler(commands=["grant"], chat_types=["private"])
def cmd_grant(m):
    """Ручная выдача: /grant <код или Telegram-ID пользователя>"""
    if not is_admin(m.from_user.id):
        return
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(m, "Формат: /grant <код или ID пользователя>")
        return
    key = parts[1].strip()
    user = find_by_ref(key)
    if user is None and key.isdigit():
        user = find_by_tg_id(key)
    if user is None:
        bot.reply_to(
            m,
            f"Пользователь {key} не найден — "
            "он не заходил в бота.",
        )
        return
    tg_id = user[0]
    ok, why = grant_user(tg_id)
    bot.reply_to(m, f"Пользователь {key}: {why}")


# --- HTTP-сервер для postback (партнёрская система стучится по ссылке) ---

class PostbackHandler(BaseHTTPRequestHandler):
    """Обрабатывает GET /postback?ref_id=...&status=..."""

    def do_GET(self):
        url = urlparse(self.path)
        if url.path != "/postback":
            self.send_json(404, {"error": "unknown path"})
            return
        query = parse_qs(url.query)
        ref_id = query.get("ref_id", [""])[0].strip()
        status = query.get("status", [""])[0].strip()
        has_bad_secret = (
            POSTBACK_SECRET
            and query.get("secret", [""])[0].strip() != POSTBACK_SECRET
        )
        if has_bad_secret:
            self.send_json(403, {"error": "bad secret"})
            return
        if not ref_id:
            self.send_json(400, {"error": "ref_id required"})
            return
        answer = process_postback(ref_id, status)
        print("POSTBACK:", answer)
        self.send_json(200, {"result": answer})

    def send_json(self, code, obj):
        """Ответ в одном формате: {"result": "..."}; заголовки как были."""
        result_text = obj.get("result", obj.get("error", ""))
        body = f'{{"result": "{result_text}"}}'.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # без стандартного журнала — свой print в do_GET


def start_postback_server():
    """Поднять фоновый HTTP-сервер для postback."""
    server = ThreadingHTTPServer(("0.0.0.0", POSTBACK_PORT), PostbackHandler)
    server_thread = threading.Thread(
        target=server.serve_forever, daemon=True
    )
    server_thread.start()
    print(f"Postback-сервер слушает порт {POSTBACK_PORT}")


# --- Запуск ---

if __name__ == "__main__":
    init_db()
    start_postback_server()
    print("Бот запущен. Для остановки: Ctrl+C")
    bot.infinity_polling(skip_pending=True)
