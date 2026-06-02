#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Генератор HTML-сводки ЕДДС из структурированного JSON (вывод parse_svodka.py).
Один самодостаточный HTML-файл (без внешних зависимостей).
"""
import sys, json, re, html, argparse
try:
    from metrics import extract_metrics, load_history, compare, verdict, METRIC_LABELS
except Exception:
    extract_metrics = load_history = compare = verdict = None
    METRIC_LABELS = {}


def esc(s):
    return html.escape(str(s)) if s is not None else ""


# Шаблонные фразы, которые сокращаем для читаемости (суть не теряется)
BOILERPLATE = [
    (r'оператору Системы[- ]?112 поступило сообщение от заявителя о том,?\s*что\s*', ''),
    (r'поступило сообщение от заявителя о том,?\s*что\s*', ''),
    (r'\s*Информация передана в оперативные службы\.?', ''),
    (r'\s*Информация передана в оперативные службы,?\s*', ' → '),
    (r'по адресу:?\s*', ''),
    (r'\s*Ремонтные работы проводит\s*', ' Исполнитель: '),
    (r'\s*Работы проводит\s*', ' Исполнитель: '),
    (r'\s+', ' '),
]


def shorten(text):
    """Убирает канцелярит и шаблонные обороты, сохраняя факты."""
    if not text:
        return text
    t = text
    for pat, rep in BOILERPLATE:
        t = re.sub(pat, rep, t)
    t = re.sub(r'\s*→\s*→', ' →', t)
    t = re.sub(r'\s+([.,;])', r'\1', t)
    t = re.sub(r'[.,;]\s*,', ',', t)      # «....,» -> «,»
    t = re.sub(r',\s*→', ' →', t)
    t = re.sub(r'\.\s*→', ' →', t)
    t = re.sub(r'\s{2,}', ' ', t)
    return t.strip(' →\u2014-\t,;')


def num(v):
    """Парсит число из ячейки; 'нет','-' -> 0."""
    if v is None:
        return 0
    s = str(v).strip()
    if s in ('', '-', '–', '—', 'нет', 'Нет'):
        return 0
    m = re.search(r'\d+', s)
    return int(m.group()) if m else 0


def status_of(term):
    """Классифицирует срок устранения: closed / in_work / unknown."""
    t = (term or '').lower()
    if not t.strip():
        return 'unknown'
    if 'работе' in t or 'контрол' in t or 'не определ' in t or 'работы на' in t:
        return 'in_work'
    # время вида 21:21 или дата = закрыто
    if re.search(r'\d{1,2}:\d{2}', t):
        return 'closed'
    return 'in_work'


def mask_pii(text, enabled):
    """Маскировка персональных данных для публикации (152-ФЗ).

    Скрываем то, что позволяет идентифицировать человека:
      • телефоны;
      • ФИО (Фамилия И.О. и «Имя Отчество Фамилия»);
      • номер дома и квартиры в адресе (оставляем населённый пункт/улицу —
        этого достаточно для понимания географии, но не для поиска жильца).
    Числа и аналитика не затрагиваются.
    """
    if not enabled or not text:
        return text

    # 1) Телефоны: +7..., 8-XXX..., 8(XXX)...
    text = re.sub(
        r'(?:\+7|8)[\s\-(]?\d{3}[\s\-)]?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}',
        '8-XXX-XXX-XX-XX', text)

    # 2) ФИО в формате «Фамилия И.О.» / «Фамилия И. О.» где угодно в тексте
    text = re.sub(r'\b([А-ЯЁ][а-яё]{2,})\s+[А-ЯЁ]\.\s?[А-ЯЁ]\.', r'\1 ***', text)

    # 3) ФИО после ключевых слов (Заявитель/Гражданин/Обратился и т.п.):
    #    оставляем фамилию, скрываем имя и отчество
    text = re.sub(
        r'(Заявител[ьяюе]|Граждан[аеин]+|Обратил[сансь]+|Пострадавш[ийая]+|Погибш[ийая]+)'
        r'(\s*:?\s*)([А-ЯЁ][а-яё]+)\s+[А-ЯЁ][а-яё.]+(?:\s+[А-ЯЁ][а-яё.]+)?',
        r'\1\2\3 ***', text)

    # 4) Номер дома и квартиры в адресе — скрываем точную привязку к жилью.
    #    Оставляем населённый пункт/улицу. Примеры:
    #      "д.5А, кв. 442" -> "д.**, кв.**"
    #      "д. 13/1"       -> "д.**"
    text = re.sub(r'(кв\.?\s*)\d+[А-Яа-я]?', r'\1**', text)
    text = re.sub(r'(\bд\.?\s*)\d+[А-Яа-я]?(?:/\d+)?', r'\1**', text)
    text = re.sub(r'(\bдом\s*)\d+[А-Яа-я]?(?:/\d+)?', r'\1**', text, flags=re.IGNORECASE)

    return text


RESOURCE_ORDER = ["ЦО", "ГВС", "ХВС", "Электроэнергия", "Газ", "Подтопления",
                  "Экология", "Несанкционированные свалки", "Канализация", "Другое"]


# Ключевые KPI для баннера динамики в шапке сводки
BANNER_KPI = ["inc_total", "tech_total", "appeals_total", "calls_112"]


def _delta_chip(c):
    """HTML-чип: метрика, значение, стрелка к вчера, отклонение от ср.7дн."""
    d1 = c.get("delta1")
    bad_up = c.get("bad_up")
    if d1 is None:
        arr = '<span class="d-flat">нет данных за вчера</span>'
    elif d1 == 0:
        arr = '<span class="d-flat">как вчера</span>'
    else:
        worse = (d1 > 0) == bad_up
        cls = "d-bad" if worse else "d-good"
        symb = "\u25b2" if d1 > 0 else "\u25bc"
        sign = "+" if d1 > 0 else ""
        arr = '<span class="%s">%s %s%s к вчера</span>' % (cls, symb, sign, d1)
    vs7 = c.get("vs_avg7")
    a7 = c.get("avg7")
    vs7txt = ""
    if vs7 is not None and a7 is not None:
        if vs7 == 0:
            vs7txt = ' \u00b7 как обычно'
        else:
            worse7 = (vs7 > 0) == bad_up
            wc = "d-bad" if worse7 else "d-good"
            vs7txt = ' \u00b7 <span class="%s">%s%s к ср.7дн</span>' % (wc, "+" if vs7 > 0 else "", vs7)
    return ('<div class="d-chip"><div class="d-l">%s</div>'
            '<div class="d-v">%s</div><div class="d-cmp">%s%s</div></div>'
            % (esc(c.get("label", "")), esc(c.get("value", 0)), arr, vs7txt))


def build_banner(history, date):
    """Баннер динамики над сводкой: общий вывод дня + чипы по 4 KPI."""
    if not (history and compare and date):
        return ""
    cmp_all = compare(history, date)
    if not cmp_all:
        return ""
    worse = better = 0
    for k in BANNER_KPI:
        st, _ = verdict(cmp_all.get(k, {}))
        if st == "worse":
            worse += 1
        elif st == "better":
            better += 1
    if worse > better:
        head, cls = "За сутки обстановка напряжённее предыдущего дня", "bad"
    elif better > worse:
        head, cls = "За сутки обстановка спокойнее предыдущего дня", "good"
    else:
        head, cls = "За сутки обстановка в пределах обычного", "neutral"
    chips = "".join(_delta_chip(cmp_all[k]) for k in BANNER_KPI if k in cmp_all)
    has_hist = any(cmp_all[k].get("delta1") is not None for k in BANNER_KPI if k in cmp_all)
    if not has_hist:
        head = "Динамика появится со второго дня наблюдений"
        cls = "neutral"
    return ('<div class="dyn dyn-%s">'
            '<div class="dyn-head">%s</div>'
            '<div class="dyn-chips">%s</div>'
            '<a class="dyn-link" href="analytics-%s.html">Подробная аналитика и графики \u2192</a>'
            '</div>' % (cls, esc(head), chips, esc(date)))


# SVG-иконка "запах/экология" — нос с волнами запаха (без внешних зависимостей)
NEVA_ICON = (
    '<svg class="neva-ic" viewBox="0 0 24 24" fill="none" '
    'xmlns="http://www.w3.org/2000/svg" aria-hidden="true">'
    '<path d="M5 9c0-3 2.2-5 5-5 2.4 0 3.6 1.3 4 2.6" stroke="#d9742b" '
    'stroke-width="1.8" stroke-linecap="round"/>'
    '<path d="M14 7c1.2 0 2 .9 2 2 0 1.6-1.4 2.4-1.4 4 0 1.3 1 2.4 2.4 2.4" '
    'stroke="#d9742b" stroke-width="1.8" stroke-linecap="round"/>'
    '<path d="M5.5 13.5c1.5 0 1.5 1.6 3 1.6s1.5-1.6 3-1.6" stroke="#e8a06a" '
    'stroke-width="1.6" stroke-linecap="round"/>'
    '<path d="M5.5 17c1.5 0 1.5 1.6 3 1.6s1.5-1.6 3-1.6" stroke="#e8a06a" '
    'stroke-width="1.6" stroke-linecap="round"/>'
    '</svg>'
)


def _neva_delta_text(history, date):
    """Динамика neva_edds к вчера и к ср.7дн — рост = хуже (красный)."""
    if not (history and compare and date):
        return ""
    cmp_all = compare(history, date, keys=["neva_edds"])
    c = (cmp_all or {}).get("neva_edds")
    if not c:
        return ""
    d1 = c.get("delta1")
    if d1 is None:
        arr = '<span class="d-flat">нет данных за вчера</span>'
    elif d1 == 0:
        arr = '<span class="d-flat">как вчера</span>'
    else:
        worse = d1 > 0  # больше жалоб = хуже
        cls = "d-bad" if worse else "d-good"
        symb = "\u25b2" if d1 > 0 else "\u25bc"
        sign = "+" if d1 > 0 else ""
        arr = '<span class="%s">%s %s%s к вчера</span>' % (cls, symb, sign, d1)
    vs7 = c.get("vs_avg7"); a7 = c.get("avg7")
    vs7txt = ""
    if vs7 is not None and a7 is not None:
        if vs7 == 0:
            vs7txt = ' \u00b7 как обычно'
        else:
            wc = "d-bad" if vs7 > 0 else "d-good"
            vs7txt = ' \u00b7 <span class="%s">%s%s к ср.7дн</span>' % (wc, "+" if vs7 > 0 else "", vs7)
    return '<div class="neva-dyn">%s%s</div>' % (arr, vs7txt)


def build_neva_card(data, mask, history, date):
    """Плашка «Обращения по КПО Нева» вверху сводки."""
    neva = data.get("neva", {})
    edds_n = int(neva.get("edds_count", 0) or 0)
    eco_n = neva.get("hotline_eco_count")
    items = neva.get("edds_items", [])
    dyn = _neva_delta_text(history, date)

    # список обращений (с маскированием ПДн)
    if items:
        lis = "".join(
            '<li><span class="nt">%s</span> — %s</li>' % (
                esc(it.get("datetime", "").strip()),
                esc(mask_pii(it.get("desc", ""), mask)),
            )
            for it in items
        )
        listing = '<ul class="neva-list">%s</ul>' % lis
    elif edds_n == 0:
        listing = '<div class="neva-zero">За сутки обращений по запаху с КПО «Нева» на ЕДДС не поступало.</div>'
    else:
        listing = ""

    eco_pill = ""
    if eco_n is not None:
        eco_pill = (
            '<div class="neva-pill ctx"><div class="pv">%s</div>'
            '<div class="pl">«Экология» на горячей линии Главы<br><i>справочно, без детализации</i></div></div>'
            % esc(eco_n)
        )

    return (
        '<div class="neva-card">'
        '<div class="neva-top">%s<div class="neva-h">Обращения по запаху с КПО «Нева»</div></div>'
        '<div class="neva-nums">'
        '<div class="neva-pill"><div class="pv">%s</div><div class="pl">На ЕДДС (поимённые обращения)</div></div>'
        '%s'
        '</div>'
        '%s'
        '%s'
        '<div class="neva-sub">Считаются только детальные обращения раздела ЕДДС (подраздел «Экология») с упоминанием «Нева». Показатель горячей линии дан для контекста и не приравнивается к «Нева».</div>'
        '</div>'
    ) % (NEVA_ICON, esc(edds_n), eco_pill, dyn, listing)


def build(data, mask=False, history=None):
    h = data.get("header", {})
    meta = data.get("meta", {})
    date = meta.get("date", "")
    period = h.get("period", "")
    as_of = h.get("as_of", "")

    # --- метрики для плашек ---
    appeals = data.get("appeals", {}).get("summary", {})
    # "Всего обращений" есть не во всех форматах — если нет, считаем сумму
    # по категориям (кроме «Горячая линия» — это отдельный показатель).
    _explicit = appeals.get("Всего обращений")
    if _explicit not in (None, "", "-", "—"):
        total_appeals = _explicit
    else:
        _skip = {"Горячая линия", "Всего обращений"}
        _s = sum(num(v) for k, v in appeals.items() if k not in _skip)
        total_appeals = str(_s) if _s else "—"
    edds = data.get("edds_stats", {})
    sys112 = next((v for k, v in edds.items() if "112" in k), "—")
    iskra = next((v for k, v in edds.items() if "ИСКРА" in k), "—")
    spas = data.get("spas_stats", {})
    spas_total = spas.get("Всего выездов", "—")

    # происшествия: убрать дубликаты-артефакты (строки с одинаковым events, но "нет")
    incidents = []
    seen_events = set()
    for inc in data.get("incidents", []):
        evs = tuple(inc.get("events", []))
        cnt = inc.get("count", "")
        # пропускаем строки "нет" без событий
        if cnt in ("нет", "-", "") and not inc.get("events"):
            incidents.append({**inc, "_active": False})
            continue
        # пропускаем дубли: те же события, но это не первая строка (артефакт merge)
        if evs and evs in seen_events:
            continue
        if evs:
            seen_events.add(evs)
        incidents.append({**inc, "_active": bool(inc.get("events")) or (cnt not in ("нет", "-", ""))})

    active_incidents = [i for i in incidents if i.get("_active")]

    tech = data.get("tech_violations", [])
    # сгруппировать тех.нарушения по ресурсу
    tech_by_res = {}
    for t in tech:
        tech_by_res.setdefault(t.get("resource", "Другое"), []).append(t)

    hotline = [c for c in data.get("hotline_categories", []) if c.get("count") not in ("-", "", None)]
    water = data.get("water_levels", [])
    weather = data.get("weather", {})
    forecast = weather.get("forecast", {})
    warnings = data.get("warnings", [])

    def m(t):
        return esc(mask_pii(t, mask))

    # --- HTML ---
    parts = []
    parts.append(f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Сводка ЕДДС · {esc(period)}</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%2301696F'/%3E%3Cpath d='M32 8 L52 16 V32 C52 45 43 53 32 57 C21 53 12 45 12 32 V16 Z' fill='%23ffffff'/%3E%3Cpath d='M30 18 h4 v10 h10 v4 h-10 v10 h-4 v-10 h-10 v-4 h10 z' fill='%2301696F'/%3E%3C/svg%3E">
<style>
:root {{
  --bg:#f4f6f9; --card:#ffffff; --ink:#1a2230; --muted:#6b7686;
  --line:#e3e8ef; --brand:#1b4f8a; --brand-soft:#eaf1fa;
  --red:#c0392b; --amber:#c77700; --green:#1f7a4d; --blue:#1b4f8a;
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
  font-family:-apple-system,Segoe UI,Roboto,'Helvetica Neue',Arial,sans-serif;
  font-size:15px; line-height:1.55; }}
.wrap {{ max-width:980px; margin:0 auto; padding:20px 16px 64px; }}
header.doc {{ background:var(--brand); color:#fff; border-radius:14px;
  padding:22px 26px; box-shadow:0 6px 20px rgba(27,79,138,.18); }}
header.doc .org {{ font-size:13px; opacity:.92; letter-spacing:.02em; }}
header.doc h1 {{ margin:8px 0 4px; font-size:24px; letter-spacing:.01em; }}
header.doc .period {{ font-size:15px; opacity:.95; }}
header.doc .asof {{ font-size:13px; opacity:.85; margin-top:4px; }}
.metrics {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin:18px 0 6px; }}
.metric {{ background:var(--card); border:1px solid var(--line); border-radius:12px;
  padding:14px 16px; }}
.metric .v {{ font-size:28px; font-weight:700; line-height:1; }}
.metric .l {{ font-size:12.5px; color:var(--muted); margin-top:6px; }}
.metric.red .v {{ color:var(--red); }} .metric.blue .v {{ color:var(--blue); }}
.metric.green .v {{ color:var(--green); }} .metric.amber .v {{ color:var(--amber); }}
nav.page-tabs {{ display:flex; flex-wrap:wrap; gap:8px; margin:14px 0 2px; }}
nav.page-tabs a {{ font-size:13.5px; font-weight:600; text-decoration:none;
  padding:8px 16px; border-radius:10px; border:1px solid var(--line);
  background:var(--card); color:var(--brand); }}
nav.page-tabs a.active {{ background:var(--brand); color:#fff; border-color:var(--brand); }}
nav.page-tabs a:hover {{ background:#dbe7f7; }}
nav.page-tabs a.active:hover {{ background:var(--brand); }}
.dyn {{ margin-top:16px; border-radius:14px; padding:16px 20px;
  border:1px solid var(--line); background:var(--card); }}
.dyn-bad {{ background:#fdeeec; border-color:#f3cdc7; }}
.dyn-good {{ background:#e9f7ef; border-color:#c7ecd6; }}
.dyn-neutral {{ background:var(--brand-soft); border-color:#cfe0f3; }}
.dyn-head {{ font-size:16px; font-weight:700; margin-bottom:12px; }}
.dyn-bad .dyn-head {{ color:var(--red); }}
.dyn-good .dyn-head {{ color:var(--green); }}
.dyn-neutral .dyn-head {{ color:var(--brand); }}
.dyn-chips {{ display:grid; grid-template-columns:repeat(4,1fr); gap:10px; }}
.d-chip {{ background:rgba(255,255,255,.7); border:1px solid var(--line);
  border-radius:10px; padding:10px 12px; }}
.d-l {{ font-size:12px; color:var(--muted); }}
.d-v {{ font-size:24px; font-weight:700; line-height:1.1; margin:2px 0 3px; color:var(--ink); }}
.d-cmp {{ font-size:11.5px; color:var(--muted); }}
.d-good {{ color:var(--green); font-weight:700; }}
.d-bad {{ color:var(--red); font-weight:700; }}
.d-flat {{ color:var(--muted); font-weight:600; }}
.dyn-link {{ display:inline-block; margin-top:12px; font-size:13px; font-weight:600;
  color:var(--brand); text-decoration:none; }}
.dyn-link:hover {{ text-decoration:underline; }}
@media(max-width:680px){{ .dyn-chips {{ grid-template-columns:repeat(2,1fr); }} }}
nav.toc {{ display:flex; flex-wrap:wrap; gap:8px; margin:16px 0 6px; }}
nav.toc a {{ font-size:13px; color:var(--brand); text-decoration:none;
  background:var(--brand-soft); padding:5px 11px; border-radius:20px; }}
nav.toc a:hover {{ background:#dbe7f7; }}
section.card {{ background:var(--card); border:1px solid var(--line);
  border-radius:14px; padding:20px 22px; margin-top:16px; }}
section.card > h2 {{ margin:0 0 14px; font-size:18px; color:var(--brand);
  display:flex; align-items:center; gap:9px; padding-bottom:10px;
  border-bottom:2px solid var(--brand-soft); }}
.badge {{ font-size:12px; font-weight:600; padding:2px 9px; border-radius:20px;
  background:var(--brand-soft); color:var(--brand); }}
.note-ok {{ background:#e9f7ef; border:1px solid #c7ecd6; color:var(--green);
  padding:11px 14px; border-radius:10px; font-size:14px; }}
.info-note {{ display:flex; gap:11px; align-items:flex-start; margin-top:14px;
  background:#eef4fb; border:1px solid #d3e2f3; border-left:4px solid #5b8fd6;
  padding:12px 15px; border-radius:10px; }}
.info-note .info-ic {{ flex:0 0 auto; width:22px; height:22px; border-radius:50%;
  background:#5b8fd6; color:#fff; font-weight:700; font-size:14px; line-height:22px;
  text-align:center; }}
.info-note .info-h {{ font-weight:700; font-size:14px; color:#2f5a93; margin-bottom:4px; }}
.info-note .info-ul {{ margin:0; padding-left:18px; font-size:14px; color:var(--ink); }}
.info-note .info-ul li {{ margin:2px 0; }}
.info-note .info-sub {{ font-size:12px; color:#6b7a8d; margin-top:6px; font-style:italic; }}
.neva-card {{ margin:16px 0 6px; background:#fdf3ec; border:1px solid #f0cdb4;
  border-left:5px solid #d9742b; border-radius:12px; padding:15px 18px; }}
.neva-card .neva-top {{ display:flex; align-items:center; gap:10px; margin-bottom:10px; }}
.neva-card .neva-ic {{ flex:0 0 auto; width:30px; height:30px; }}
.neva-card .neva-h {{ font-weight:800; font-size:15.5px; color:#9c4a12; }}
.neva-card .neva-nums {{ display:flex; flex-wrap:wrap; gap:12px; margin-bottom:6px; }}
.neva-card .neva-pill {{ background:#fff; border:1px solid #f0cdb4; border-radius:10px;
  padding:9px 14px; min-width:120px; }}
.neva-card .neva-pill .pv {{ font-size:24px; font-weight:800; color:#c4571a; line-height:1; }}
.neva-card .neva-pill .pl {{ font-size:12px; color:#7a6a5d; margin-top:4px; }}
.neva-card .neva-pill.ctx .pv {{ color:#9a8576; }}
.neva-card .neva-list {{ margin:8px 0 0; padding-left:18px; font-size:13.5px; color:var(--ink); }}
.neva-card .neva-list li {{ margin:3px 0; }}
.neva-card .neva-list .nt {{ color:#9c4a12; font-weight:600; }}
.neva-card .neva-sub {{ font-size:12px; color:#8a7a6d; margin-top:8px; font-style:italic; }}
.neva-card .neva-zero {{ font-size:14px; color:#5a7a5a; }}
.neva-card .neva-dyn {{ font-size:13.5px; margin-top:2px; }}
table {{ width:100%; border-collapse:collapse; font-size:14px; }}
th, td {{ text-align:left; padding:9px 11px; border-bottom:1px solid var(--line);
  vertical-align:top; }}
th {{ background:#f7f9fc; color:var(--muted); font-weight:600; font-size:12.5px;
  text-transform:uppercase; letter-spacing:.03em; }}
tr:last-child td {{ border-bottom:none; }}
.cnt {{ display:inline-block; min-width:24px; text-align:center; font-weight:700;
  color:#fff; background:var(--red); border-radius:7px; padding:1px 7px; font-size:13px; }}
.inc-block {{ margin-bottom:14px; }}
.inc-block h3 {{ margin:0 0 6px; font-size:15px; display:flex; gap:8px; align-items:center; }}
.inc-block ul {{ margin:0; padding-left:18px; }}
.inc-block li {{ margin:5px 0; color:#33404f; }}
.res-tag {{ display:inline-block; font-size:11.5px; font-weight:700; padding:2px 8px;
  border-radius:6px; margin-right:6px; background:#eef2f7; color:#445; }}
.res-ГВС,.res-ХВС {{ background:#e7f0fb; color:#1b4f8a; }}
.res-Газ {{ background:#fdeee0; color:#b56400; }}
.res-Экология {{ background:#e9f7ef; color:#1f7a4d; }}
.res-Канализация {{ background:#f1eafc; color:#6b3fa0; }}
.term {{ font-size:12.5px; color:var(--muted); white-space:nowrap; }}
.term.work {{ color:var(--amber); font-weight:600; }}
.grid2 {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; }}
.water-card {{ border:1px solid var(--line); border-radius:10px; padding:12px 14px; }}
.water-card .post {{ font-weight:600; font-size:13.5px; margin-bottom:6px; }}
.water-card .lvl {{ display:flex; justify-content:space-between; font-size:13px; }}
.weather-grid {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; }}
.weather-grid .wc {{ border:1px solid var(--line); border-radius:10px; padding:12px; }}
.weather-grid .wc .l {{ font-size:11.5px; color:var(--muted); text-transform:uppercase; }}
.weather-grid .wc .v {{ font-size:18px; font-weight:700; margin-top:4px; }}
.warn {{ border-left:4px solid var(--amber); background:#fdf6ec; padding:10px 14px;
  border-radius:0 8px 8px 0; margin-bottom:10px; font-size:13.5px; }}
.warn b {{ color:var(--amber); }}
.chips {{ display:flex; flex-wrap:wrap; gap:8px; }}
.chip {{ background:#f7f9fc; border:1px solid var(--line); border-radius:8px;
  padding:7px 11px; font-size:13px; }}
.chip b {{ color:var(--brand); }}
footer.sign {{ margin-top:22px; text-align:right; color:var(--muted); font-size:14px; }}
footer.sign .role {{ font-size:12.5px; }}
.appeal-row {{ display:flex; justify-content:space-between; padding:7px 0;
  border-bottom:1px solid var(--line); font-size:14px; }}
.appeal-row:last-child {{ border:none; }}
/* Сводные плашки раздела (от общего к частному) */
.summary-row {{ display:flex; gap:12px; flex-wrap:wrap; }}
.sb {{ flex:1; min-width:110px; border:1px solid var(--line); border-radius:11px;
  padding:12px 16px; background:#fafbfd; }}
.sb .v {{ font-size:26px; font-weight:700; line-height:1; }}
.sb .l {{ font-size:12px; color:var(--muted); margin-top:5px; }}
.sb.total {{ background:var(--brand-soft); border-color:#cfe0f3; }}
.sb.total .v {{ color:var(--brand); }}
.sb.work {{ background:#fdf6ec; border-color:#f0dcbd; }}
.sb.work .v {{ color:var(--amber); }}
.sb.closed {{ background:#e9f7ef; border-color:#c7ecd6; }}
.sb.closed .v {{ color:var(--green); }}
.sb.blue .v {{ color:var(--blue); }} .sb.gray .v {{ color:var(--muted); }}
.res-row {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:12px; }}
.res-pill {{ display:inline-flex; align-items:center; gap:7px; background:#f4f7fb;
  border:1px solid var(--line); border-radius:9px; padding:7px 12px; font-size:13.5px; color:#33404f; }}
.res-pill .rn {{ font-weight:700; color:var(--brand); font-size:15px; }}
.res-pill.alert {{ background:#fdeeec; border-color:#f3cdc7; }}
.res-pill.alert .rn {{ color:var(--red); }}
details.det {{ margin-top:16px; }}
details.det > summary {{ cursor:pointer; font-size:13.5px; color:var(--brand);
  font-weight:600; padding:8px 0; list-style:none; display:flex; align-items:center; gap:8px; }}
details.det > summary::-webkit-details-marker {{ display:none; }}
details.det > summary::before {{ content:''; flex:0 0 auto; width:0; height:0;
  border-style:solid; border-width:5px 0 5px 8px;
  border-color:transparent transparent transparent var(--brand);
  transition:transform .15s ease; }}
details.det[open] > summary::before {{ transform:rotate(90deg); }}
.term.done {{ color:var(--green); font-weight:600; }}
.tech-tbl {{ table-layout:fixed; }}
.tech-tbl td {{ word-break:break-word; overflow-wrap:anywhere; }}
.tech-tbl td:nth-child(2) {{ font-size:13.5px; line-height:1.5; }}
.tech-tbl td:first-child {{ font-size:12.5px; }}
.tech-tbl th:nth-child(3), .tech-tbl td:nth-child(3) {{ text-align:center; }}
@media (max-width:680px) {{
  .metrics,.weather-grid {{ grid-template-columns:repeat(2,1fr); }}
  .grid2 {{ grid-template-columns:1fr; }}
  .summary-row {{ flex-direction:row; }}
}}
</style>
</head>
<body>
<div class="wrap">
<header class="doc">
  <div class="org">{esc(h.get('org',''))}</div>
  <h1>Сводка ЕДДС МКУ «СолнСпас»</h1>
  <div class="period">по чрезвычайным (аварийным) ситуациям и происшествиям за сутки {esc(period)} г.</div>
  <div class="asof">по состоянию на {esc(as_of)}</div>
</header>
<nav class="page-tabs">
  <a href="index.html">🗂 Все сводки</a>
  <a class="active" href="svodka-{esc(date)}.html">📄 Сводка за день</a>
  <a href="analytics-{esc(date)}.html">📊 Аналитика и динамика</a>
</nav>
""")

    # метрики
    parts.append(f"""
<div class="metrics">
  <div class="metric blue"><div class="v">{esc(sys112)}</div><div class="l">Вызовов по Системе-112</div></div>
  <div class="metric blue"><div class="v">{esc(total_appeals)}</div><div class="l">Обращений граждан</div></div>
  <div class="metric amber"><div class="v">{esc(len(active_incidents))}</div><div class="l">Происшествий с событиями</div></div>
  <div class="metric green"><div class="v">{esc(spas_total)}</div><div class="l">Выездов СОЛН СПАС</div></div>
</div>
""")

    # плашка «Нева» (всегда показываем, даже при нуле)
    parts.append(build_neva_card(data, mask, history, date))

    # баннер динамики (если есть история)
    banner = build_banner(history, date) if history is not None else ""
    if banner:
        parts.append(banner)

    # навигация
    parts.append("""<nav class="toc">
  <a href="#cs">ЧС</a><a href="#inc">Происшествия</a><a href="#fire">Пожары</a>
  <a href="#tech">Технологич. нарушения</a><a href="#appeals">Обращения</a>
  <a href="#hotline">Горячая линия Главы</a><a href="#stats">Статистика</a>
  <a href="#water">Уровни воды</a><a href="#weather">Погода</a>
</nav>""")

    # ЧС статус
    if h.get("cs_status"):
        parts.append(f"""<section class="card" id="cs">
  <h2>Чрезвычайные ситуации</h2>
  <div class="note-ok">✓ {esc(h['cs_status'])}</div>
</section>""")

    # Происшествия — саммари + сокращённые эпизоды
    parts.append('<section class="card" id="inc"><h2>Происшествия (преступления)</h2>')
    if active_incidents:
        total_ev = sum(len(i.get("events", [])) or num(i.get("count")) for i in active_incidents)
        parts.append('<div class="res-row" style="margin-bottom:14px">')
        for inc in active_incidents:
            c = inc.get("count", "")
            cn = c if c not in ("нет", "-", "") else str(len(inc.get("events", [])))
            parts.append(f'<div class="res-pill alert"><span class="rn">{esc(cn)}</span> {esc(inc["type"])}</div>')
        parts.append('</div>')
        for inc in active_incidents:
            cnt = inc.get("count", "")
            cnt_html = f'<span class="cnt">{esc(cnt)}</span>' if cnt not in ("нет", "-", "") else ""
            parts.append(f'<div class="inc-block"><h3>{esc(inc["type"])} {cnt_html}</h3>')
            if inc.get("events"):
                parts.append("<ul>")
                for ev in inc["events"]:
                    parts.append(f"<li>{m(shorten(ev))}</li>")
                parts.append("</ul>")
            parts.append("</div>")
    else:
        parts.append('<div class="note-ok">Происшествий не зафиксировано.</div>')
    parts.append("</section>")

    # Пожары
    fire = data.get("fire", {})
    parts.append('<section class="card" id="fire"><h2>Пожарная обстановка</h2>')
    if fire.get("empty", True):
        parts.append('<div class="note-ok">✓ Пожаров и возгораний за прошедшие сутки не зарегистрировано.</div>')
    else:
        parts.append(f'<p>{esc(", ".join(fire.get("raw", [])))}</p>')
    parts.append("</section>")

    # Технологические нарушения — аналитика «от общего к частному»
    parts.append('<section class="card" id="tech"><h2>Технологические нарушения</h2>')
    if tech:
        # 1) сводка по статусам (итог всегда бьётся: work + closed + nostatus = total)
        n_total = len(tech)
        n_work = sum(1 for t in tech if status_of(t.get("term","")) == "in_work")
        n_closed = sum(1 for t in tech if status_of(t.get("term","")) == "closed")
        n_nostatus = n_total - n_work - n_closed
        # 2) разбивка по ресурсам
        res_count = {}
        for t in tech:
            res_count[t.get("resource","Другое")] = res_count.get(t.get("resource","Другое"), 0) + 1
        # человекочитаемые ярлыки ресурсов
        RES_LABEL = {"ЦО":"Отопление","ГВС":"ГВС","ХВС":"ХВС","Электроэнергия":"Э/энергия",
                     "Газ":"Газ","Подтопления":"Подтопления","Экология":"Экология",
                     "Канализация":"Канализация","Несанкционированные свалки":"Мусор","Другое":"Другое"}
        sb_html = ('<div class="summary-row">'
                   f'<div class="sb total"><div class="v">{n_total}</div><div class="l">всего</div></div>'
                   f'<div class="sb work"><div class="v">{n_work}</div><div class="l">в работе</div></div>'
                   f'<div class="sb closed"><div class="v">{n_closed}</div><div class="l">устранено</div></div>')
        if n_nostatus > 0:
            sb_html += f'<div class="sb gray"><div class="v">{n_nostatus}</div><div class="l">без срока / инфо</div></div>'
        sb_html += '</div>'
        parts.append(sb_html)
        # плашки по ресурсам
        parts.append('<div class="res-row">')
        order = [r for r in RESOURCE_ORDER if r in res_count] + [r for r in res_count if r not in RESOURCE_ORDER]
        for r in order:
            label = RES_LABEL.get(r, r)
            parts.append(f'<div class="res-pill res-{esc(r)}"><span class="rn">{res_count[r]}</span> {esc(label)}</div>')
        parts.append('</div>')
        # 3) детали — компактная таблица с сокращёнными описаниями
        parts.append('<details class="det" open><summary>Детализация ({} шт.)</summary>'.format(n_total))
        parts.append('<table class="tech-tbl"><colgroup><col style="width:120px"><col><col style="width:96px"></colgroup>'
                     '<thead><tr><th>Ресурс / время</th><th>Что и где</th><th>Статус</th></tr></thead><tbody>')
        order2 = [r for r in RESOURCE_ORDER if r in tech_by_res] + [r for r in tech_by_res if r not in RESOURCE_ORDER]
        for res in order2:
            for t in tech_by_res[res]:
                term = t.get("term", "")
                st = status_of(term)
                term_cls = {"in_work":"term work","closed":"term done","unknown":"term"}[st]
                # короткий бейдж + полный текст в title (чтобы не обрезало узкую колонку)
                if st == "closed":
                    badge = term if re.fullmatch(r'\s*\d{1,2}:\d{2}\s*', term or '') else "устранено"
                elif st == "in_work":
                    badge = "в работе"
                else:
                    badge = "—"
                title_attr = f' title="{esc(term)}"' if term and term != badge else ""
                term_txt = badge
                dt = t.get("datetime", "").replace("\n", " ")
                label = RES_LABEL.get(res, res)
                parts.append(f'<tr><td style="white-space:nowrap"><span class="res-tag res-{esc(res)}">{esc(label)}</span><br><span class="term">{esc(dt)}</span></td>'
                             f'<td>{m(shorten(t.get("desc","")))}</td>'
                             f'<td class="{term_cls}"{title_attr}>{esc(term_txt)}</td></tr>')
        parts.append("</tbody></table></details>")
    else:
        parts.append('<div class="note-ok">Технологических нарушений не зафиксировано.</div>')

    # Справочно-консультативные сообщения — НЕ нарушения, показываем отдельно
    info_msgs = data.get("info_messages", [])
    if info_msgs:
        items = "".join(
            f'<li>{m(esc(im.get("desc", "")))}</li>' for im in info_msgs
        )
        parts.append(
            '<div class="info-note">'
            '<span class="info-ic">&#8505;</span>'
            f'<div><div class="info-h">Справочно-консультативные обращения</div>'
            f'<ul class="info-ul">{items}</ul>'
            '<div class="info-sub">Информация справочного характера, не является технологическим нарушением.</div></div>'
            '</div>'
        )
    parts.append("</section>")

    # Обращения граждан — «от общего к частному»
    parts.append('<section class="card" id="appeals"><h2>Обращения граждан за сутки</h2>')
    total = total_appeals  # уже посчитано выше (с учётом обоих форматов)
    hot = appeals.get("Горячая линия", "")
    # 1) общий итог + каналы
    edds_msg = next((v for k, v in edds.items() if "сообщений" in k.lower()), "")
    parts.append('<div class="summary-row">'
                 f'<div class="sb total"><div class="v">{esc(total or "—")}</div><div class="l">всего обращений</div></div>'
                 f'<div class="sb blue"><div class="v">{esc(hot or "—")}</div><div class="l">на горячую линию Главы</div></div>'
                 f'<div class="sb gray"><div class="v">{esc(sys112)}</div><div class="l">вызовов НС-112</div></div>'
                 '</div>')
    # 2) разбивка по тематике (исключая итоги и нули)
    SKIP = {"Всего обращений", "Горячая линия"}
    cats = [(k, v) for k, v in appeals.items() if k not in SKIP and v not in ("-", "", None, "0")]
    if cats:
        parts.append('<div class="l" style="font-size:12.5px;color:var(--muted);margin:14px 0 8px">По тематике:</div><div class="res-row">')
        for k, v in sorted(cats, key=lambda x: -num(x[1])):
            parts.append(f'<div class="res-pill"><span class="rn">{esc(v)}</span> {esc(k)}</div>')
        parts.append('</div>')
    parts.append("</section>")

    # Горячая линия Главы — от общего к частному
    if hotline:
        ht_total = sum(num(c["count"]) for c in hotline)
        parts.append('<section class="card" id="hotline"><h2>Горячая линия Главы г.о. Солнечногорск</h2>')
        parts.append('<div class="summary-row">'
                     f'<div class="sb total"><div class="v">{esc(hot or ht_total)}</div><div class="l">всего обращений</div></div>'
                     f'<div class="sb blue"><div class="v">{len(hotline)}</div><div class="l">активных категорий</div></div>'
                     '</div>')
        parts.append('<div class="res-row" style="margin-top:12px">')
        for c in sorted(hotline, key=lambda x: -num(x["count"])):
            parts.append(f'<div class="res-pill"><span class="rn">{esc(c["count"])}</span> {esc(c["category"])}</div>')
        parts.append("</div></section>")

    # Статистика
    parts.append('<section class="card" id="stats"><h2>Статистика дежурной смены</h2>')
    parts.append('<div class="chips">')
    for k, v in edds.items():
        parts.append(f'<div class="chip">{esc(k.replace(chr(10)," "))}: <b>{esc(v)}</b></div>')
    parts.append("</div>")
    if spas:
        parts.append('<h3 style="font-size:15px;margin:16px 0 8px;color:var(--ink)">Выезды МКУ «СОЛН СПАС»</h3><div class="chips">')
        for k, v in spas.items():
            if v not in ("-", "", None):
                parts.append(f'<div class="chip">{esc(k.replace(chr(10)," "))}: <b>{esc(v)}</b></div>')
        parts.append("</div>")
    parts.append("</section>")

    # Уровни воды
    if water:
        parts.append('<section class="card" id="water"><h2>Автоматизированные посты уровня воды</h2><div class="grid2">')
        for w in water:
            zero = re.sub(r'.*?-\s*', '', w.get("zero", ""))
            actual = re.sub(r'.*?[–-]\s*', '', w.get("actual", ""))
            parts.append(f'<div class="water-card"><div class="post">{esc(w["post"])}</div>'
                         f'<div class="lvl"><span style="color:var(--muted)">«0» поста</span><b>{esc(zero)}</b></div>'
                         f'<div class="lvl"><span style="color:var(--muted)">Актуальный</span><b>{esc(actual)}</b></div></div>')
        parts.append("</div></section>")

    # Погода
    parts.append('<section class="card" id="weather"><h2>Погода на предстоящие сутки</h2>')
    if forecast:
        parts.append('<div class="weather-grid">')
        for k, v in forecast.items():
            kk = k.replace("\n", " ")
            if "Осадки" in kk or len(v) > 40:
                continue
            parts.append(f'<div class="wc"><div class="l">{esc(kk)}</div><div class="v">{esc(v)}</div></div>')
        parts.append("</div>")
        for k, v in forecast.items():
            if "Осадки" in k.replace("\n", " "):
                parts.append(f'<p style="margin-top:12px;font-size:13.5px">{esc(v.replace(chr(10)," "))}</p>')
    parts.append(f'<p style="font-size:13.5px;color:var(--muted)">{esc(weather.get("dangerous",""))} {esc(weather.get("unfavorable",""))}</p>')
    if data.get("fire_danger_class"):
        parts.append(f'<p style="font-size:13.5px">{esc(data["fire_danger_class"][0].upper()+data["fire_danger_class"][1:])}.</p>')
    if warnings:
        for w in warnings:
            parts.append(f'<div class="warn"><b>Предупреждение о НГЯ № {esc(w["num"])}</b><br>{esc(w["text"])}</div>')
    parts.append("</section>")

    # подпись
    if data.get("signed_by"):
        parts.append(f'<footer class="sign"><div class="role">Старший дежурный оперативный смены специалистов ЕДДС и Системы–112</div><div><b>{esc(data["signed_by"])}</b></div></footer>')

    parts.append("</div></body></html>")
    return "\n".join(parts)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("json_path")
    ap.add_argument("-o", "--out", default="svodka.html")
    ap.add_argument("--mask", action="store_true", help="маскировать персональные данные")
    ap.add_argument("--history", default=None, help="path к history.jsonl для баннера динамики")
    a = ap.parse_args()
    with open(a.json_path, encoding="utf-8") as f:
        data = json.load(f)
    hist = load_history(a.history) if (a.history and load_history) else None
    htmlout = build(data, mask=a.mask, history=hist)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(htmlout)
    print(f"OK -> {a.out} ({len(htmlout)} bytes)")
