#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Извлечение числовых метрик из распарсенной сводки (вывод parse_svodka.py)
и работа с историей (history.jsonl). Метрики — основа аналитики «лучше/хуже».
"""
import re, json, os, statistics
from datetime import datetime, timedelta


def _num(v):
    if v is None:
        return 0
    s = str(v).strip()
    if s in ('', '-', '–', '—', 'нет', 'Нет', 'НЕТ'):
        return 0
    m = re.search(r'\d+', s)
    return int(m.group()) if m else 0


def _status(term):
    t = (term or '').lower()
    if not t.strip():
        return 'nostatus'
    if 'работе' in t or 'контрол' in t or 'не определ' in t or 'работы на' in t:
        return 'in_work'
    if re.search(r'\d{1,2}:\d{2}', t):
        return 'closed'
    return 'in_work'


# Ресурсы, по которым ведём отдельный учёт
RES_KEYS = {
    "ЦО": "heating", "ГВС": "hot_water", "ХВС": "cold_water",
    "Электроэнергия": "electricity", "Газ": "gas", "Подтопления": "flooding",
    "Экология": "ecology", "Канализация": "sewage",
    "Несанкционированные свалки": "garbage", "Другое": "tech_other",
}


def extract_metrics(data):
    """Возвращает плоский dict числовых метрик за сутки."""
    m = {"date": data.get("meta", {}).get("date", "")}

    # --- Происшествия: считаем активные эпизоды по типам ---
    inc_map = {}
    inc_total = 0
    seen = set()
    for inc in data.get("incidents", []):
        evs = tuple(inc.get("events", []))
        cnt = inc.get("count", "")
        n = len(inc.get("events", [])) or _num(cnt)
        if n == 0:
            continue
        # пропуск дублей merged-строк
        if evs and evs in seen:
            continue
        if evs:
            seen.add(evs)
        key = inc.get("type", "").strip()
        inc_map[key] = inc_map.get(key, 0) + n
        inc_total += n
    m["inc_total"] = inc_total
    # ключевые типы происшествий отдельными метриками
    def find_inc(*names):
        s = 0
        for k, v in inc_map.items():
            if any(nm.lower() in k.lower() for nm in names):
                s += v
        return s
    m["inc_dtp"] = find_inc("ДТП")
    m["inc_minors"] = find_inc("несовершеннолет")
    m["inc_fire"] = find_inc("пожар", "возгоран")
    m["inc_water"] = find_inc("на воде", "утонул")
    m["inc_uav"] = find_inc("БПЛА")
    m["inc_dead"] = find_inc("Погибло")
    m["inc_injured"] = find_inc("Пострадало", "Травмиров")
    m["inc_other"] = find_inc("Другое")

    # --- Технологические нарушения ---
    tech = data.get("tech_violations", [])
    m["tech_total"] = len(tech)
    m["tech_in_work"] = sum(1 for t in tech if _status(t.get("term", "")) == "in_work")
    m["tech_closed"] = sum(1 for t in tech if _status(t.get("term", "")) == "closed")
    m["tech_nostatus"] = m["tech_total"] - m["tech_in_work"] - m["tech_closed"]
    # по ресурсам
    res_count = {v: 0 for v in RES_KEYS.values()}
    for t in tech:
        rk = RES_KEYS.get(t.get("resource", ""), None)
        if rk:
            res_count[rk] += 1
    for rk, c in res_count.items():
        m[f"tech_{rk}"] = c

    # --- Обращения граждан ---
    # Формат таблицы обращений у ЕДДС меняется ото дня ко дню:
    #   • иногда есть явная строка "Всего обращений";
    #   • иногда её нет — тогда суммируем категории (ОС, ГВС, ХВС…), кроме
    #     "Горячая линия" (это отдельный показатель, не часть суммы).
    appeals = data.get("appeals", {}).get("summary", {})
    m["appeals_hotline"] = _num(appeals.get("Горячая линия"))
    explicit_total = appeals.get("Всего обращений")
    if explicit_total not in (None, "", "-"):
        m["appeals_total"] = _num(explicit_total)
    else:
        # сумма по категориям, исключая служебные/итоговые ключи
        skip = {"Горячая линия", "Всего обращений"}
        m["appeals_total"] = sum(
            _num(v) for k, v in appeals.items() if k not in skip
        )

    # --- Статистика вызовов ---
    edds = data.get("edds_stats", {})
    m["calls_112"] = _num(next((v for k, v in edds.items() if "112" in k), 0))
    m["calls_iskra"] = _num(next((v for k, v in edds.items() if "ИСКРА" in k), 0))

    # --- Выезды СОЛН СПАС ---
    spas = data.get("spas_stats", {})
    m["spas_total"] = _num(spas.get("Всего выездов"))

    # --- Горячая линия Главы (всего) ---
    hl = data.get("hotline_categories", [])
    m["mayor_hotline_total"] = sum(_num(c.get("count")) for c in hl)

    # --- Обращения по КПО "Нева" (запах) ---
    # Точное число: поимённые обращения раздела ЕДДС с упоминанием "Нева".
    neva = data.get("neva", {})
    m["neva_edds"] = _num(neva.get("edds_count", 0))
    # Справочно: "Экология" горячей линии Главы (агрегат, не приравнивается к Нева).
    if neva.get("hotline_eco_count") is not None:
        m["neva_hotline_eco"] = _num(neva.get("hotline_eco_count", 0))

    return m


# ---------- История ----------

def load_history(path):
    """Читает history.jsonl -> список dict, отсортированный по дате."""
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    rows.sort(key=lambda r: r.get("date", ""))
    return rows


def upsert_history(path, metrics):
    """Добавляет/обновляет запись за дату metrics['date'] (идемпотентно)."""
    rows = load_history(path)
    date = metrics.get("date")
    rows = [r for r in rows if r.get("date") != date]
    rows.append(metrics)
    rows.sort(key=lambda r: r.get("date", ""))
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return rows


# ---------- Сравнение / аналитика ----------

# Метрики, где рост = хуже (для интерпретации стрелок)
BAD_UP = {
    "inc_total", "inc_dtp", "inc_minors", "inc_fire", "inc_water", "inc_uav",
    "inc_dead", "inc_injured", "inc_other", "tech_total", "tech_in_work",
    "appeals_total", "appeals_hotline", "calls_112", "neva_edds",
}

METRIC_LABELS = {
    "inc_total": "Происшествий всего", "inc_dtp": "ДТП", "inc_minors": "С несовершеннолетними",
    "inc_fire": "Пожары/возгорания", "inc_water": "На воде", "inc_uav": "БПЛА",
    "inc_dead": "Погибло", "inc_injured": "Пострадало/травм.", "inc_other": "Прочие происшествия",
    "tech_total": "Технологич. нарушения", "tech_in_work": "Нарушений в работе",
    "tech_closed": "Нарушений устранено",
    "tech_hot_water": "ГВС", "tech_cold_water": "ХВС", "tech_heating": "Отопление",
    "tech_gas": "Газ", "tech_electricity": "Э/энергия", "tech_ecology": "Экология",
    "tech_sewage": "Канализация", "tech_flooding": "Подтопления", "tech_garbage": "Мусор",
    "appeals_total": "Обращений граждан", "appeals_hotline": "Горячая линия (звонки)",
    "calls_112": "Вызовов по 112", "calls_iskra": "Вызовов ИСКРА",
    "spas_total": "Выездов СОЛН СПАС", "mayor_hotline_total": "Горячая линия Главы",
    "neva_edds": "Запах с КПО «Нева» (ЕДДС)", "neva_hotline_eco": "Экология (горячая линия Главы)",
}


def _avg(vals):
    vals = [v for v in vals if v is not None]
    return round(statistics.mean(vals), 1) if vals else None


def compare(history, date, keys=None):
    """Сравнивает день `date` со вчера, позавчера и средними за 7/30 дней.
    Возвращает dict по метрикам с дельтами и оценкой."""
    by_date = {r["date"]: r for r in history if r.get("date")}
    cur = by_date.get(date)
    if not cur:
        return {}

    d = datetime.strptime(date, "%Y-%m-%d")
    prev1 = by_date.get((d - timedelta(days=1)).strftime("%Y-%m-%d"))
    prev2 = by_date.get((d - timedelta(days=2)).strftime("%Y-%m-%d"))

    # окна по фактически имеющимся датам (а не календарным)
    prior = [r for r in history if r.get("date", "") < date]
    win7 = prior[-7:]
    win30 = prior[-30:]

    if keys is None:
        keys = [k for k in cur.keys() if k != "date"]

    out = {}
    for k in keys:
        v = cur.get(k, 0) or 0
        y = (prev1 or {}).get(k)
        yy = (prev2 or {}).get(k)
        a7 = _avg([r.get(k, 0) for r in win7]) if win7 else None
        a30 = _avg([r.get(k, 0) for r in win30]) if win30 else None
        out[k] = {
            "label": METRIC_LABELS.get(k, k),
            "value": v,
            "prev1": y, "delta1": (v - y) if y is not None else None,
            "prev2": yy, "delta2": (v - yy) if yy is not None else None,
            "avg7": a7, "vs_avg7": (round(v - a7, 1) if a7 is not None else None),
            "avg30": a30, "vs_avg30": (round(v - a30, 1) if a30 is not None else None),
            "bad_up": k in BAD_UP,
        }
    return out


def verdict(cmp_row):
    """Текстовая оценка для метрики: лучше/хуже/как обычно (по сравнению с вчера)."""
    d1 = cmp_row.get("delta1")
    if d1 is None:
        return ("n/a", "нет данных за вчера")
    if d1 == 0:
        return ("same", "как вчера")
    worse = (d1 > 0) == cmp_row["bad_up"]
    word = "хуже" if worse else "лучше"
    sign = "+" if d1 > 0 else ""
    return ("worse" if worse else "better", f"{word} (вчера {sign}{d1})")


if __name__ == "__main__":
    import sys
    with open(sys.argv[1], encoding="utf-8") as f:
        data = json.load(f)
    print(json.dumps(extract_metrics(data), ensure_ascii=False, indent=2))
