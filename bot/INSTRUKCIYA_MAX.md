# Запуск MAX-бота Сводки ЕДДС — пошагово «за руку»

Бот работает в мессенджере **MAX** параллельно с Telegram-ботом, на том же
движке и сервере. Получает суточную сводку `.docx`, собирает сайт и публикует на
GitHub Pages, в ответ присылает кнопки со ссылками.

Telegram-бот при этом продолжает работать как раньше.

---

## Что понадобится

- Сервер, где уже крутится Telegram-бот ЕДДС (папка `/opt/svodka`, пользователь `svodka`).
- Токен MAX-бота `analiticbot` (business.max.ru → бот → **Интеграция** → **Получить токен**).
- 10 минут.

---

## Шаг 1. Обновить код на сервере

```bash
sudo -u svodka git -C /opt/svodka pull --no-rebase --no-edit origin main
```

Должны подтянуться: `bot/max_core.py`, `bot/svodka_max_bot.py`,
`bot/svodka-max-bot.service`, `bot/.env.max.example`.

---

## Шаг 2. Доустановить `requests`

```bash
sudo -u svodka /opt/svodka/venv/bin/pip install requests
```

(`python-docx` для разбора .docx уже стоит).

---

## Шаг 3. Создать `.env.max`

```bash
sudo -u svodka cp /opt/svodka/bot/.env.max.example /opt/svodka/bot/.env.max
sudo -u svodka nano /opt/svodka/bot/.env.max
```

Заполните:

```
MAX_BOT_TOKEN=сюда_токен_из_business.max.ru
PAGES_URL=https://aggasyas.github.io/svodka-eds
```

Сохранить: `Ctrl+O`, `Enter`, выйти: `Ctrl+X`. Закрыть права:

```bash
sudo chmod 600 /opt/svodka/bot/.env.max
sudo chown svodka:svodka /opt/svodka/bot/.env.max
```

---

## Шаг 4. Установить systemd-сервис

```bash
sudo cp /opt/svodka/bot/svodka-max-bot.service /etc/systemd/system/svodka-max-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now svodka-max-bot
sudo systemctl status svodka-max-bot --no-pager
journalctl -u svodka-max-bot -n 30 --no-pager
```

В логах: `MAX-бот ЕДДС запущен: @analiticbot (id …) …`.

---

## Шаг 5. Боевой прогон

1. Найдите в MAX бота **@analiticbot**, напишите `/start`.
2. Пришлите суточную сводку **.docx**.
3. Бот ответит «Опубликовано» с кнопками **Сводка дня / Аналитика / Все сводки**
   и короткой динамикой к вчера.
4. Pages обновляется 1–2 минуты.

---

## Группа (необязательно)

1. Добавьте бота в чат и **сделайте админом** (иначе MAX не отдаёт сообщения).
2. Узнайте `chat_id` из `journalctl -u svodka-max-bot -f`, впишите в `.env.max`:
   ```
   ALLOWED_CHAT_IDS=-123456789
   NAME_FILTER=svodka.*\.docx$
   ```
3. `sudo systemctl restart svodka-max-bot`.

---

## Заметки

- Оба бота (Telegram + MAX) собирают один и тот же сайт и пушат в один репозиторий.
- Маскировать ПДн на страницах: в `.env.max` поставьте `MASK_PII=1`.
- Логи: `journalctl -u svodka-max-bot -f`.
- После правок кода:
  `sudo -u svodka git -C /opt/svodka pull --no-rebase --no-edit origin main && sudo systemctl restart svodka-max-bot`.

> Напоминание: если старый токен Telegram-бота ЕДДС где-то «светился» — перевыпустите
> его у @BotFather. Токен MAX тоже держите в секрете (он только в `.env.max`).
