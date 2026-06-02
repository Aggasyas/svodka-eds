#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram-бот сводок ЕДДС Солнечногорска.

Что делает:
  • Принимает .docx со сводкой за сутки (как документ в чат).
  • Прогоняет конвейер build_all.build_site():
        парсинг → метрики → история → HTML (сводка, аналитика, индекс).
  • Публикует папку site/ на GitHub Pages (git push).
  • Отвечает ссылкой на свежую сводку + краткой динамикой по ключевым числам.

Команды:
  /start, /help            — справка.
  /last                    — ссылки на последнюю сводку + аналитику + динамика.
  /svodka YYYY-MM-DD       — ссылка на сводку за конкретный день.
  /analitika               — ссылка на аналитику за последний день.

Доступ: только Telegram-ID из ALLOWED_IDS (защита от чужих).

Работа в группе:
  • Бот можно добавить в рабочий чат — он сам увидит присланный .docx и
    обработает его (нужно отключить privacy mode у @BotFather, см. инструкцию).
  • В группе бот реагирует ТОЛЬКО на файлы с именем вида Svodka-*.docx
    (NAME_FILTER), чтобы не мешать обычной переписке.
  • Разрешённые группы перечисляются в ALLOWED_CHAT_IDS (id чатов, отрицательные).
  • Результат (ссылка + динамика) уходит в REPORT_CHAT_ID — обычно ваша личка,
    чтобы не светить сводку с ПДн в общем чате. В личном чате ответ приходит
    прямо в диалог, как раньше.

Запускается как systemd-сервис. Все настройки — через переменные окружения
(см. .env.example и инструкцию INSTRUKCIYA.md).
"""
import os
import re
import sys
import asyncio
import logging
import subprocess
import tempfile
from datetime import datetime

# --- путь к пайплайну (build_all.py и render_*.py лежат на уровень выше) ---
PIPELINE_DIR = os.environ.get("PIPELINE_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PIPELINE_DIR not in sys.path:
    sys.path.insert(0, PIPELINE_DIR)

import build_all          # noqa: E402
import metrics as M       # noqa: E402

from aiogram import Bot, Dispatcher, F            # noqa: E402
from aiogram.filters import Command, CommandStart  # noqa: E402
from aiogram.types import Message                  # noqa: E402

# ------------------------- КОНФИГ -------------------------
BOT_TOKEN   = os.environ["BOT_TOKEN"]                       # от @BotFather
SITE_DIR    = os.environ.get("SITE_DIR",    os.path.join(PIPELINE_DIR, "site"))
HISTORY     = os.environ.get("HISTORY",     os.path.join(PIPELINE_DIR, "history.jsonl"))
PAGES_URL   = os.environ.get("PAGES_URL",   "").rstrip("/")  # напр. https://aggasyas.github.io/svodka-eds
GIT_REPO    = os.environ.get("GIT_REPO_DIR", PIPELINE_DIR)   # где лежит git-репозиторий с site/
MASK_PII    = os.environ.get("MASK_PII", "0") == "1"

# Список разрешённых пользователей (Telegram numeric id), через запятую.
ALLOWED_IDS = {
    int(x) for x in os.environ.get("ALLOWED_IDS", "").replace(" ", "").split(",") if x
}

# Разрешённые групповые чаты (id обычно отрицательные), через запятую.
# Если пусто — бот в группах файлы не обрабатывает (только личка).
ALLOWED_CHAT_IDS = {
    int(x) for x in os.environ.get("ALLOWED_CHAT_IDS", "").replace(" ", "").split(",") if x
}

# Куда присылать результат при обработке из группы (обычно ваша личка —
# numeric id). Если не задан, в группе бот ответит в сам чат.
_report = os.environ.get("REPORT_CHAT_ID", "").strip()
REPORT_CHAT_ID = int(_report) if _report.lstrip("-").isdigit() else None

# Шаблон имени файла, на который бот реагирует в ГРУППЕ (регистронезависимо).
# В личке принимаем любой .docx. По умолчанию — "svodka...".
NAME_FILTER = re.compile(os.environ.get("NAME_FILTER", r"svodka.*\.docx$"), re.IGNORECASE)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("svodka_bot")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()


# ------------------------- ВСПОМОГАТЕЛЬНОЕ -------------------------
def allowed(msg: Message) -> bool:
    """Пропускаем только разрешённых пользователей (если список задан)."""
    if not ALLOWED_IDS:
        return True  # список пуст — пускаем всех (на свой страх; лучше задать)
    return msg.from_user and msg.from_user.id in ALLOWED_IDS


def url_svodka(date: str) -> str:
    return f"{PAGES_URL}/svodka-{date}.html" if PAGES_URL else f"svodka-{date}.html"


def url_analytics(date: str) -> str:
    return f"{PAGES_URL}/analytics-{date}.html" if PAGES_URL else f"analytics-{date}.html"


def url_index() -> str:
    return f"{PAGES_URL}/" if PAGES_URL else "index.html"


def last_date() -> str | None:
    hist = M.load_history(HISTORY)
    dates = sorted(r["date"] for r in hist if r.get("date"))
    return dates[-1] if dates else None


def dynamics_text(date: str) -> str:
    """Короткая текстовая динамика по 4 ключевым показателям."""
    hist = M.load_history(HISTORY)
    keys = ["inc_total", "tech_total", "appeals_total", "calls_112"]
    cmp = M.compare(hist, date, keys=keys)
    if not cmp:
        return ""
    lines = []
    for k in keys:
        row = cmp.get(k)
        if not row:
            continue
        _, txt = M.verdict(row)
        # стрелка по дельте к вчера
        d1 = row.get("delta1")
        arrow = "•"
        if d1 is not None:
            arrow = "▲" if d1 > 0 else ("▼" if d1 < 0 else "▪")
        lines.append(f"{arrow} {row['label']}: {row['value']} ({txt})")
    return "\n".join(lines)


def git_publish(commit_msg: str) -> tuple[bool, str]:
    """Коммитим site/ и пушим в origin. Возвращает (успех, текст)."""
    try:
        # имя папки сайта внутри репозитория (на сервере это docs, локально site)
        site_rel = os.path.relpath(SITE_DIR, GIT_REPO)
        # добавляем сайт и историю
        subprocess.run(["git", "-C", GIT_REPO, "add", site_rel, "history.jsonl"],
                       check=True, capture_output=True, text=True)
        # есть ли что коммитить
        diff = subprocess.run(["git", "-C", GIT_REPO, "diff", "--cached", "--quiet"])
        if diff.returncode == 0:
            return True, "изменений нет (уже опубликовано)"
        subprocess.run(["git", "-C", GIT_REPO, "commit", "-m", commit_msg],
                       check=True, capture_output=True, text=True)
        push = subprocess.run(["git", "-C", GIT_REPO, "push", "origin", "HEAD"],
                              check=True, capture_output=True, text=True)
        return True, "опубликовано"
    except subprocess.CalledProcessError as e:
        return False, (e.stderr or e.stdout or str(e))[-500:]


# ------------------------- ХЕНДЛЕРЫ -------------------------
@dp.message(CommandStart())
@dp.message(Command("help"))
async def cmd_help(msg: Message):
    if not allowed(msg):
        await msg.answer("⛔ Доступ запрещён. Обратитесь к администратору.")
        return
    await msg.answer(
        "🟢 <b>Бот сводок ЕДДС</b>\n\n"
        "Пришлите мне файл <b>.docx</b> со сводкой за сутки — "
        "я соберу HTML, обновлю аналитику и опубликую в интернете, "
        "а в ответ дам ссылку и краткую динамику.\n\n"
        "<b>Команды:</b>\n"
        "/last — последняя сводка + аналитика + динамика\n"
        "/svodka ГГГГ-ММ-ДД — сводка за конкретный день\n"
        "/analitika — аналитика за последний день\n",
        parse_mode="HTML",
    )


@dp.message(Command("last"))
async def cmd_last(msg: Message):
    if not allowed(msg):
        return
    d = last_date()
    if not d:
        await msg.answer("Истории пока нет — пришлите первый .docx.")
        return
    dyn = dynamics_text(d)
    await msg.answer(
        f"📄 <b>Последняя сводка за {d}</b>\n"
        f"{url_svodka(d)}\n\n"
        f"📊 Аналитика: {url_analytics(d)}\n"
        f"🗂 Все сводки: {url_index()}\n\n"
        + (f"<b>Динамика к вчера:</b>\n{dyn}" if dyn else ""),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@dp.message(Command("svodka"))
async def cmd_svodka(msg: Message):
    if not allowed(msg):
        return
    parts = (msg.text or "").split()
    if len(parts) < 2:
        await msg.answer("Укажите дату: <code>/svodka 2026-06-01</code>", parse_mode="HTML")
        return
    date = parts[1].strip()
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        await msg.answer("Формат даты: ГГГГ-ММ-ДД, например <code>/svodka 2026-06-01</code>", parse_mode="HTML")
        return
    hist = M.load_history(HISTORY)
    if not any(r.get("date") == date for r in hist):
        await msg.answer(f"За {date} сводки в истории нет.")
        return
    await msg.answer(
        f"📄 Сводка за {date}:\n{url_svodka(date)}\n"
        f"📊 Аналитика: {url_analytics(date)}",
        disable_web_page_preview=True,
    )


@dp.message(Command("analitika"))
async def cmd_analitika(msg: Message):
    if not allowed(msg):
        return
    d = last_date()
    if not d:
        await msg.answer("Истории пока нет.")
        return
    await msg.answer(
        f"📊 Аналитика за {d}:\n{url_analytics(d)}",
        disable_web_page_preview=True,
    )


def is_private(msg: Message) -> bool:
    return msg.chat and msg.chat.type == "private"


def group_allowed(msg: Message) -> bool:
    """Разрешён ли этот групповой чат для обработки сводок."""
    if not ALLOWED_CHAT_IDS:
        return False  # группы не сконфигурированы — игнорируем
    return msg.chat and msg.chat.id in ALLOWED_CHAT_IDS


@dp.message(F.document)
async def on_document(msg: Message):
    doc = msg.document
    name = (doc.file_name or "")
    private = is_private(msg)

    # --- Контроль доступа и фильтрация ---
    if private:
        # Личка: проверяем пользователя; принимаем любой .docx
        if not allowed(msg):
            await msg.answer("⛔ Доступ запрещён.")
            return
        if not name.lower().endswith(".docx"):
            await msg.answer("Нужен файл <b>.docx</b> со сводкой ЕДДС.", parse_mode="HTML")
            return
    else:
        # Группа: молча игнорируем чужие чаты и посторонние файлы
        if not group_allowed(msg):
            return
        if not (name.lower().endswith(".docx") and NAME_FILTER.search(name)):
            return  # не сводка — не реагируем, чтобы не мешать переписке

    # Куда слать прогресс и результат:
    #  • из лички — отвечаем в тот же диалог;
    #  • из группы — результат в REPORT_CHAT_ID (личка), а в группе не шумим.
    report_to = msg.chat.id if private else (REPORT_CHAT_ID or msg.chat.id)

    async def report(text: str):
        await bot.send_message(report_to, text, parse_mode="HTML",
                               disable_web_page_preview=True)

    if private:
        status = await msg.answer("⏳ Принял файл, собираю сводку…")
    else:
        # из группы — короткая реакция-уведомление автору в личку, что взяли в работу
        await report(f"⏳ Принял сводку из чата «{esc_chat(msg)}», собираю…")
        status = None

    # 1. скачиваем во временный файл
    tmpdir = tempfile.mkdtemp(prefix="svodka_")
    local_path = os.path.join(tmpdir, doc.file_name)
    try:
        tg_file = await bot.get_file(doc.file_id)
        await bot.download_file(tg_file.file_path, destination=local_path)
    except Exception as e:
        await _set(status, report, f"❌ Не смог скачать файл: {e}")
        return

    # 2. конвейер сборки (синхронный — уводим в поток, чтобы не блокировать бота)
    try:
        res = await asyncio.to_thread(
            build_all.build_site, local_path, SITE_DIR, HISTORY, MASK_PII
        )
    except SystemExit as e:
        await _set(status, report, f"❌ Не разобрал файл: {e}")
        return
    except Exception as e:
        log.exception("build_site failed")
        await _set(status, report, f"❌ Ошибка сборки: {e}")
        return

    date = res["date"]

    # 3. публикация на GitHub Pages
    await _set(status, report, f"✅ Собрал сводку за {date}. Публикую…")
    ok, pub = await asyncio.to_thread(git_publish, f"Сводка ЕДДС за {date}")

    # 4. ответ с динамикой
    dyn = dynamics_text(date)
    head = "🟢 Опубликовано" if ok else "⚠️ Собрано, но публикация не удалась"
    body = (
        f"{head} — сводка за <b>{date}</b>\n"
        f"📄 {url_svodka(date)}\n"
        f"📊 {url_analytics(date)}\n"
        f"🗂 {url_index()}\n"
        f"📚 Дней в истории: {res['days_in_history']}\n"
    )
    if dyn:
        body += f"\n<b>Динамика к вчера:</b>\n{dyn}"
    if not ok:
        body += f"\n\n<code>{pub}</code>"

    await _set(status, report, body)


def esc_chat(msg: Message) -> str:
    """Безопасное имя чата для подписи."""
    t = getattr(msg.chat, "title", None) or getattr(msg.chat, "full_name", None) or str(msg.chat.id)
    return (t or "").replace("<", "&lt;").replace(">", "&gt;")


async def _set(status, report, text: str):
    """Обновить статус (личка) или прислать новое сообщение (группа→личка)."""
    if status is not None:
        await status.edit_text(text, parse_mode="HTML", disable_web_page_preview=True)
    else:
        await report(text)


async def main():
    log.info("Бот запускается. Pages: %s | site: %s | history: %s",
             PAGES_URL or "(не задан)", SITE_DIR, HISTORY)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
