#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Минимальный клиент MAX Bot API (мессенджер MAX, platform-api.max.ru) на чистом
requests — без внешних SDK. Переиспользуется ботами ЦУР и ЕДДС.

Что умеет:
  • long polling   GET  /updates
  • профиль бота    GET  /me
  • отправка текста POST /messages (в чат или пользователю, с inline-кнопками)
  • скачивание входящего файла-вложения (payload.url из message_created)

Авторизация: заголовок  Authorization: <token>   (без "Bearer").
Документация: https://dev.max.ru/docs-api
"""
import os
import time
import logging
import requests

API_BASE = os.environ.get("MAX_API_BASE", "https://platform-api.max.ru").rstrip("/")

log = logging.getLogger("max_core")


class MaxBot:
    def __init__(self, token: str, api_base: str = API_BASE, timeout: int = 60):
        self.token = token
        self.base = api_base.rstrip("/")
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers.update({"Authorization": token})

    # ---------------- низкоуровневые запросы ----------------
    def _get(self, path, params=None, timeout=None):
        r = self.s.get(f"{self.base}{path}", params=params,
                       timeout=timeout or self.timeout)
        r.raise_for_status()
        return r.json()

    def _post(self, path, params=None, json=None, timeout=None):
        r = self.s.post(f"{self.base}{path}", params=params, json=json,
                        timeout=timeout or self.timeout)
        # MAX отдаёт текст ошибки в теле — приложим его в исключение
        if r.status_code >= 400:
            raise requests.HTTPError(f"{r.status_code}: {r.text[:500]}", response=r)
        return r.json() if r.text else {}

    # ---------------- API ----------------
    def get_me(self) -> dict:
        return self._get("/me")

    def get_updates(self, marker=None, timeout=30, limit=100, types=None) -> dict:
        params = {"timeout": timeout, "limit": limit}
        if marker is not None:
            params["marker"] = marker
        if types:
            params["types"] = ",".join(types)
        # запас по сети сверх long-poll-таймаута
        return self._get("/updates", params=params, timeout=timeout + 25)

    def send_message(self, text, chat_id=None, user_id=None,
                     buttons=None, fmt="markdown") -> dict:
        """Отправить текст. buttons — список рядов inline-кнопок-ссылок:
        [[{"text": "...", "url": "..."}], ...]"""
        params = {}
        if chat_id is not None:
            params["chat_id"] = chat_id
        elif user_id is not None:
            params["user_id"] = user_id
        body = {"text": text[:3900]}
        if fmt:
            body["format"] = fmt  # "markdown" | "html"
        if buttons:
            rows = [[{"type": "link", "text": b["text"], "url": b["url"]}
                     for b in row] for row in buttons]
            body["attachments"] = [
                {"type": "inline_keyboard", "payload": {"buttons": rows}}
            ]
        return self._post("/messages", params=params, json=body)

    def send_action(self, action, chat_id=None, user_id=None):
        """typing_on / sending_file и т.п. Не критично — ошибки глушим."""
        try:
            params = {}
            if chat_id is not None:
                params["chat_id"] = chat_id
            elif user_id is not None:
                params["user_id"] = user_id
            self._post("/actions", params=params, json={"action": action})
        except Exception:
            pass

    def download_to(self, url: str, dest_path: str):
        """Скачать входящий файл по payload.url во вложении message_created."""
        with self.s.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(dest_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 16):
                    if chunk:
                        f.write(chunk)
        return dest_path


# ---------------- разбор обновлений ----------------
def iter_message_created(updates: dict):
    """Из ответа /updates вернуть события message_created в удобном виде."""
    for u in updates.get("updates", []):
        if u.get("update_type") != "message_created":
            continue
        msg = u.get("message", {}) or {}
        rec = msg.get("recipient", {}) or {}
        sender = msg.get("sender", {}) or {}
        body = msg.get("body", {}) or {}
        files = []
        for att in body.get("attachments", []) or []:
            if att.get("type") == "file":
                payload = att.get("payload", {}) or {}
                files.append({
                    "filename": att.get("filename") or "file.xlsx",
                    "size": att.get("size"),
                    "url": payload.get("url"),
                    "token": payload.get("token"),
                })
        yield {
            "chat_id": rec.get("chat_id"),
            "chat_type": rec.get("chat_type"),
            "user_id": sender.get("user_id") or rec.get("user_id"),
            "sender_name": sender.get("name", ""),
            "text": body.get("text", "") or "",
            "files": files,
        }


def run_long_poll(bot: MaxBot, handle_event, types=("message_created",),
                  poll_timeout=30, on_error_sleep=3):
    """Бесконечный цикл long polling. handle_event(event_dict) вызывается
    на каждое message_created. Маркер двигаем сами."""
    marker = None
    log.info("MAX long polling запущен (types=%s)", ",".join(types))
    while True:
        try:
            res = bot.get_updates(marker=marker, timeout=poll_timeout,
                                  types=list(types))
            for ev in iter_message_created(res):
                try:
                    handle_event(ev)
                except Exception:
                    log.exception("Ошибка обработки события")
            new_marker = res.get("marker")
            if new_marker is not None:
                marker = new_marker
        except requests.HTTPError as e:
            log.error("HTTP ошибка long poll: %s", e)
            time.sleep(on_error_sleep)
        except requests.RequestException as e:
            log.warning("Сеть long poll: %s", e)
            time.sleep(on_error_sleep)
        except Exception:
            log.exception("Неожиданная ошибка long poll")
            time.sleep(on_error_sleep)
