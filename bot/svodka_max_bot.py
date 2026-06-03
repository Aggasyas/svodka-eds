#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MAX-бот Сводки ЕДДС г.о. Солнечногорск (мессенджер MAX).

Работает параллельно с Telegram-ботом (svodka_bot.py) на том же движке.

Что делает:
  • Принимает суточную сводку .docx как файл-вложение.
  • Прогоняет конвейер build_all.build_site():
        парсинг → метрики → история → HTML (сводка дня, аналитика, индекс).
  • Публикует папку site/ (docs/) на GitHub Pages (git push).
  • Отвечает ссылками (кнопками) на свежую сводку + аналитику + динамикой.

Команды (текстом):
  /start, /help   — справка.
  /last           — ссылки на последнюю сводку + аналитику.

Доступ: только MAX user_id из ALLOWED_IDS.
В группе реагирует на файлы по NAME_FILTER (по умолчанию svodka*.docx),
только в чатах из ALLOWED_CHAT_IDS. Бот должен быть админом группы.

Запускается как systemd-сервис. Настройки — через переменные окружения
(см. .env.max.example и INSTRUKCIYA_MAX.md).
"""
import os
import re
import sys
import logging
import subprocess
import tempfile

PIPELINE_DIR = os.environ.get(
    "PIPELINE_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if PIPELINE_DIR not in sys.path:
    sys.path.insert(0, PIPELINE_DIR)

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
if BOT_DIR not in sys.path:
    sys.path.insert(0, BOT_DIR)

import build_all            # noqa: E402
import metrics as M         # noqa: E402
import max_core             # noqa: E402

# ------------------------- КОНФИГ -------------------------
BOT_TOKEN = os.environ["MAX_BOT_TOKEN"]                        # токен MAX-бота
SITE_DIR  = os.environ.get("SITE_DIR",  os.path.join(PIPELINE_DIR, "site"))
HISTORY   = os.environ.get("HISTORY",   os.path.join(PIPELINE_DIR, "history.jsonl"))
PAGES_URL = os.environ.get("PAGES_URL", "").rstrip("/")       # https://aggasyas.github.io/svodka-eds
GIT_REPO  = os.environ.get("GIT_REPO_DIR", PIPELINE_DIR)
MASK_PII  = os.environ.get("MASK_PII", "0") == "1"

ALLOWED_IDS = {
    int(x) for x in os.environ.get("ALLOWED_IDS", "").replace(" ", "").split(",") if x.lstrip("-").isdigit()
}
ALLOWED_CHAT_IDS = {
    int(x) for x in os.environ.get("ALLOWED_CHAT_IDS", "").replace(" ", "").split(",") if x.lstrip("-").isdigit()
}
_report = os.environ.get("REPORT_CHAT_ID", "").strip()
REPORT_CHAT_ID = int(_report) if _report.lstrip("-").isdigit() else None
# Доп. чат (обычно личка), куда ДУБЛИРУЕТСЯ финальное сообщение со ссылками.
_extra = os.environ.get("EXTRA_REPORT_CHAT_ID", "").strip()
EXTRA_REPORT_CHAT_ID = int(_extra) if _extra.lstrip("-").isdigit() else None

NAME_FILTER = re.compile(os.environ.get("NAME_FILTER", r"svodka.*\.docx$"), re.IGNORECASE)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("svodka_max_bot")

bot = max_core.MaxBot(BOT_TOKEN)


# ------------------------- ВСПОМОГАТЕЛЬНОЕ -------------------------
def url_svodka(date):
    return f"{PAGES_URL}/svodka-{date}.html" if PAGES_URL else f"svodka-{date}.html"


def url_analytics(date):
    return f"{PAGES_URL}/analytics-{date}.html" if PAGES_URL else f"analytics-{date}.html"


def url_index():
    return f"{PAGES_URL}/" if PAGES_URL else "index.html"


def last_date():
    hist = M.load_history(HISTORY)
    dates = sorted(r["date"] for r in hist if r.get("date"))
    return dates[-1] if dates else None


def dynamics_text(date):
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
        d1 = row.get("delta1")
        arrow = "•"
        if d1 is not None:
            arrow = "▲" if d1 > 0 else ("▼" if d1 < 0 else "▪")
        lines.append(f"{arrow} {row['label']}: {row['value']} ({txt})")
    return "\n".join(lines)


def user_allowed(uid):
    if not ALLOWED_IDS:
        return True
    return uid in ALLOWED_IDS


def chat_allowed(chat_id):
    return chat_id in ALLOWED_CHAT_IDS


def git_publish(commit_msg):
    try:
        site_rel = os.path.relpath(SITE_DIR, GIT_REPO)
        subprocess.run(["git", "-C", GIT_REPO, "add", site_rel, "history.jsonl"],
                       check=True, capture_output=True, text=True)
        diff = subprocess.run(["git", "-C", GIT_REPO, "diff", "--cached", "--quiet"])
        if diff.returncode == 0:
            return True, "изменений нет (уже опубликовано)"
        subprocess.run(["git", "-C", GIT_REPO, "commit", "-m", commit_msg],
                       check=True, capture_output=True, text=True)

        def _push():
            return subprocess.run(["git", "-C", GIT_REPO, "push", "origin", "HEAD"],
                                  capture_output=True, text=True)
        push = _push()
        if push.returncode != 0:
            subprocess.run(["git", "-C", GIT_REPO, "pull", "--no-rebase",
                            "--no-edit", "origin", "HEAD"],
                           check=True, capture_output=True, text=True)
            push = _push()
        if push.returncode != 0:
            return False, (push.stderr or push.stdout or "push failed")[-400:]
        return True, "опубликовано"
    except subprocess.CalledProcessError as e:
        return False, (e.stderr or e.stdout or str(e))[-400:]


def reply(ev, text, buttons=None):
    target = ev.get("chat_id")
    try:
        if target is not None:
            bot.send_message(text, chat_id=target, buttons=buttons)
        else:
            bot.send_message(text, user_id=ev.get("user_id"), buttons=buttons)
    except Exception:
        log.exception("Не удалось отправить ответ")


HELP_TEXT = (
    "🟢 **Бот Сводки ЕДДС** (MAX)\n\n"
    "Пришлите суточную сводку **.docx** — я разберу её, обновлю динамику, "
    "соберу аналитику и опубликую в интернете, а в ответ дам ссылку.\n\n"
    "**Команды:**\n"
    "/last — ссылки на последнюю сводку и аналитику\n"
)


# ------------------------- ОБРАБОТКА СОБЫТИЙ -------------------------
def handle_event(ev):
    chat_id = ev.get("chat_id")
    uid = ev.get("user_id")
    text = (ev.get("text") or "").strip()
    files = ev.get("files") or []
    is_group = ev.get("chat_type") not in (None, "dialog")

    if text.startswith("/"):
        cmd = text.split()[0].split("@")[0].lower()
        if cmd in ("/start", "/help"):
            if is_group and not chat_allowed(chat_id):
                return
            if not is_group and not user_allowed(uid):
                reply(ev, "⛔ Доступ запрещён. Обратитесь к администратору.")
                return
            reply(ev, HELP_TEXT)
            return
        if cmd == "/last":
            _cmd_last(ev)
            return
        return

    if not files:
        return

    if is_group:
        if not chat_allowed(chat_id):
            return
    else:
        if not user_allowed(uid):
            reply(ev, "⛔ Доступ запрещён.")
            return

    # ищем подходящий .docx
    docx = None
    for f in files:
        name = f.get("filename", "")
        if name.lower().endswith(".docx") and (not is_group or NAME_FILTER.search(name)):
            if f.get("url"):
                docx = f
                break
    if not docx:
        if not is_group:
            reply(ev, "Нужна суточная сводка **.docx** ЕДДС.")
        return

    report_target_ev = ev
    if is_group and REPORT_CHAT_ID:
        report_target_ev = {"chat_id": REPORT_CHAT_ID}

    bot.send_action("typing_on", chat_id=chat_id, user_id=uid)
    reply(report_target_ev, "⏳ Принял сводку ЕДДС, обрабатываю…")

    tmpdir = tempfile.mkdtemp(prefix="svodka_max_")
    local = os.path.join(tmpdir, docx["filename"])
    try:
        bot.download_to(docx["url"], local)
    except Exception as e:
        reply(report_target_ev, f"❌ Не смог скачать файл: {e}")
        return

    try:
        res = build_all.build_site(local, SITE_DIR, HISTORY, MASK_PII)
    except Exception as e:
        log.exception("build_site failed")
        reply(report_target_ev, f"❌ Ошибка сборки: {e}")
        return

    date = res["date"]

    reply(report_target_ev, f"✅ Собрал сводку за {date}. Публикую…")
    ok, pub = git_publish(f"Сводка ЕДДС за {date}")

    head = "🟢 Опубликовано" if ok else "⚠️ Собрано, но публикация не удалась"
    dyn = dynamics_text(date)
    body = (
        f"{head} — сводка ЕДДС за **{date}**\n"
        f"Дней в истории: {res.get('days_in_history', 0)}"
    )
    if dyn:
        body += f"\n\n**Динамика к вчера:**\n{dyn}"
    if not ok:
        body += f"\n\n`{pub}`"
    buttons = None
    if PAGES_URL:
        buttons = [
            [{"text": "📄 Сводка дня", "url": url_svodka(date)}],
            [{"text": "📊 Аналитика", "url": url_analytics(date)}],
            [{"text": "🗂 Все сводки", "url": url_index()}],
        ]
    else:
        body += f"\n\n📄 {url_svodka(date)}\n📊 {url_analytics(date)}\n🗂 {url_index()}"
    reply(report_target_ev, body, buttons=buttons)
    # Дублируем финальное сообщение со ссылками в доп. чат (личку),
    # если он задан и это не тот же самый чат, куда уже отправили.
    if EXTRA_REPORT_CHAT_ID and EXTRA_REPORT_CHAT_ID != report_target_ev.get("chat_id"):
        reply({"chat_id": EXTRA_REPORT_CHAT_ID}, body, buttons=buttons)


def _cmd_last(ev):
    chat_id = ev.get("chat_id")
    uid = ev.get("user_id")
    is_group = ev.get("chat_type") not in (None, "dialog")
    if is_group and not chat_allowed(chat_id):
        return
    if not is_group and not user_allowed(uid):
        return
    d = last_date()
    if not d:
        reply(ev, "Истории пока нет — пришлите суточную сводку .docx.")
        return
    dyn = dynamics_text(d)
    body = f"📄 Последняя сводка ЕДДС за **{d}**"
    if dyn:
        body += f"\n\n**Динамика к вчера:**\n{dyn}"
    buttons = None
    if PAGES_URL:
        buttons = [
            [{"text": "📄 Сводка дня", "url": url_svodka(d)}],
            [{"text": "📊 Аналитика", "url": url_analytics(d)}],
            [{"text": "🗂 Все сводки", "url": url_index()}],
        ]
    else:
        body += f"\n\n📄 {url_svodka(d)}\n📊 {url_analytics(d)}"
    reply(ev, body, buttons=buttons)


def main():
    me = bot.get_me()
    log.info("MAX-бот ЕДДС запущен: @%s (id %s) | Pages: %s | site: %s | mask=%s",
             me.get("username"), me.get("user_id"),
             PAGES_URL or "(не задан)", SITE_DIR, MASK_PII)
    max_core.run_long_poll(bot, handle_event, types=("message_created",))


if __name__ == "__main__":
    main()
