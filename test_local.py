# -*- coding: utf-8 -*-
"""
Локальный тест логики базы — без Telegram и без сети.

Проверяет главное: пара «код ↔ пользователь» сохраняется,
двойная выдача исключена, поиск по коду и по Telegram-ID работает.

Запуск:  python3 test_local.py
"""

import os
import sqlite3
import tempfile

DB_PATH = os.path.join(tempfile.mkdtemp(), "test.db")

# Заглушки настроек, чтобы bot.py не требовал токен при импорте
os.environ.setdefault("BOT_TOKEN", "123456789:test_token")
os.environ.setdefault("ADMIN_IDS", "111222333")

import importlib
import bot  # noqa: E402

bot.DB_PATH = DB_PATH
bot.init_db()

ok = True

def check(name, condition):
    global ok
    print(("  OK  " if condition else " ПРОВАЛ ") + name)
    ok = ok and condition

# 1. Сохранение пары
bot.save_pair("PARTNER01", 1001)
user = bot.find_by_ref("PARTNER01")
check("пара сохранена и найдена по коду", user == (1001, 0))

# 2. Повторный заход с тем же кодом — ничего не ломает
bot.save_pair("PARTNER01", 1001)
check("повторное сохранение безопасно", bot.find_by_ref("PARTNER01") == (1001, 0))

# 3. Выдача доступа и защита от повтора
bot.mark_granted(1001)
check("выдача зафиксирована", bot.is_granted(1001))
ok_grant, why = bot.grant_user(1001)  # без Telegram-вызова упрётся в «уже выдан»
check("повторная выдача заблокирована", (not ok_grant) and "уже выдан" in why)

# 4. Поиск по Telegram-ID (для ручной /grant)
rows = bot.db_exec("SELECT tg_id, granted FROM users WHERE tg_id = ?", (1001,))
check("поиск по Telegram-ID работает", rows and rows[0][0] == 1001)

# 5. Постбек с отклонённым действием — доступ не выдаётся
answer = bot.process_postback("PARTNER01", "0")
check("отклонённый postback не выдаёт доступ", "не выдан" in answer)

# 6. Постбек по неизвестному коду — без ошибки
answer = bot.process_postback("NOBODY1", "1")
check("неизвестный код обрабатывается без падения", "не найден" in answer)

# 7. Свои настройки группы
bot.set_group_id(-1001234567890)
check("группа сохраняется", bot.get_group_id() == -1001234567890)

# 8. Разные пользователи — разные коды
bot.save_pair("PARTNER02", 2002)
check("второй пользователь сохранился", bot.find_by_ref("PARTNER02") == (2002, 0))

print("\nИтог:", "все проверки пройдены" if ok else "есть провалы — смотреть выше")
raise SystemExit(0 if ok else 1)