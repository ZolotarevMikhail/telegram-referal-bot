# -*- coding: utf-8 -*-
"""
Локальный тест логики базы — без Telegram и без сети.

Проверяет главное: пара «код ↔ пользователь» сохраняется,
двойная выдача исключена, поиск по коду и по Telegram-ID работает.

Запуск:  python3 test_local.py
"""

import os
import tempfile

DB_PATH = os.path.join(tempfile.mkdtemp(), "test.db")


def prepare_env():
    """Заглушки настроек, чтобы bot.py не требовал токен при импорте."""
    os.environ.setdefault("BOT_TOKEN", "123456789:test_token")
    os.environ.setdefault("ADMIN_IDS", "111222333")


prepare_env()

import bot  # noqa: E402  (импорт поздний: сначала нужны заглушки .env)


# Итог пройденности тестов: True, пока не провалилась хоть одна проверка
ALL_OK = True


def check(name, condition):
    """Напечатать результат одной проверки и учесть её в общем итоге."""
    global ALL_OK
    print(("  OK  " if condition else " ПРОВАЛ ") + name)
    ALL_OK = ALL_OK and condition


def run_checks():
    """Основные проверки логики базы — по порядку, с понятными подписями."""
    # 1. Сохранение пары
    bot.save_pair("PARTNER01", 1001)
    user = bot.find_by_ref("PARTNER01")
    check("пара сохранена и найдена по коду", user == (1001, 0))

    # 2. Повторный заход с тем же кодом — ничего не ломает
    bot.save_pair("PARTNER01", 1001)
    same_user = bot.find_by_ref("PARTNER01") == (1001, 0)
    check("повторное сохранение безопасно", same_user)

    # 3. Выдача доступа и защита от повтора
    bot.mark_granted(1001)
    check("выдача зафиксирована", bot.is_granted(1001))
    # без Telegram-вызова упрётся в «уже выдан»
    ok_grant, why = bot.grant_user(1001)
    repeat_blocked = (not ok_grant) and "уже выдан" in why
    check("повторная выдача заблокирована", repeat_blocked)

    # 4. Поиск по Telegram-ID (для ручной /grant)
    rows = bot.db_exec(
        "SELECT tg_id, granted FROM users WHERE tg_id = ?", (1001,)
    )
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
    second_user = bot.find_by_ref("PARTNER02") == (2002, 0)
    check("второй пользователь сохранился", second_user)


def main():
    bot.DB_PATH = DB_PATH
    bot.init_db()
    run_checks()
    print("\nИтог:", "все проверки пройдены" if ALL_OK else
          "есть провалы — смотреть выше")
    raise SystemExit(0 if ALL_OK else 1)


main()
