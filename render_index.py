#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Генератор страницы-индекса (архив всех сводок ЕДДС) из history.jsonl.
Самодостаточный HTML. Каждый день — карточка со ссылками на сводку и аналитику,
краткой оценкой дня и ключевыми числами.
"""
import sys, json, html, argparse
from collections import OrderedDict
from datetime import datetime
from metrics import load_history, compare, verdict

MONTHS = ["", "января", "февраля", "марта", "апреля", "мая", "июня",
          "июля", "августа", "сентября", "октября", "ноября", "декабря"]
MONTHS_NOM = ["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
              "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
WDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

BANNER_KPI = ["inc_total", "tech_total", "appeals_total", "calls_112"]


def esc(s):
    return html.escape(str(s)) if s is not None else ""


def long_date(d):
    try:
        dt = datetime.strptime(d, "%Y-%m-%d")
        return f"{dt.day} {MONTHS[dt.month]} {dt.year}"
    except Exception:
        return d


def wday(d):
    try:
        return WDAYS[datetime.strptime(d, "%Y-%m-%d").weekday()]
    except Exception:
        return ""


def day_verdict(history, date):
    """Оценка дня по 4 KPI: better/worse/neutral + есть ли история."""
    cmp_all = compare(history, date)
    if not cmp_all:
        return "neutral", False
    worse = better = 0
    has = False
    for k in BANNER_KPI:
        c = cmp_all.get(k, {})
        if c.get("delta1") is not None:
            has = True
        st, _ = verdict(c)
        if st == "worse":
            worse += 1
        elif st == "better":
            better += 1
    if not has:
        return "neutral", False
    if worse > better:
        return "bad", True
    if better > worse:
        return "good", True
    return "neutral", True


def build(history):
    history = [r for r in history if r.get("date")]
    # новые сверху
    rows = sorted(history, key=lambda r: r["date"], reverse=True)
    latest = rows[0]["date"] if rows else ""

    P = []
    P.append(f"""<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Сводки ЕДДС · архив</title><link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%2301696F'/%3E%3Cpath d='M32 8 L52 16 V32 C52 45 43 53 32 57 C21 53 12 45 12 32 V16 Z' fill='%23ffffff'/%3E%3Cpath d='M30 18 h4 v10 h10 v4 h-10 v10 h-4 v-10 h-10 v-4 h10 z' fill='%2301696F'/%3E%3C/svg%3E">
<style>
:root{{--bg:#f4f6f9;--card:#fff;--ink:#1a2230;--muted:#6b7686;--line:#e3e8ef;
--brand:#1b4f8a;--brand-soft:#eaf1fa;--red:#c0392b;--green:#1f7a4d;}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;font-size:15px;line-height:1.55}}
.wrap{{max-width:1000px;margin:0 auto;padding:20px 16px 64px}}
header.doc{{background:var(--brand);color:#fff;border-radius:14px;padding:22px 26px}}
header.doc h1{{margin:0 0 4px;font-size:23px}}
header.doc .sub{{opacity:.9;font-size:14px}}
.list{{margin-top:18px;display:flex;flex-direction:column;gap:12px}}
.row{{background:var(--card);border:1px solid var(--line);border-radius:14px;
padding:16px 18px;display:flex;align-items:center;gap:16px;flex-wrap:wrap}}
.row.latest{{border-color:#cfe0f3;box-shadow:0 0 0 2px var(--brand-soft)}}
.dt{{min-width:190px}}
.dt .d{{font-size:16px;font-weight:700}}
.dt .w{{font-size:12.5px;color:var(--muted);text-transform:capitalize}}
.vd{{font-size:12px;font-weight:700;padding:3px 11px;border-radius:20px;white-space:nowrap}}
.vd.bad{{background:#fdeeec;color:var(--red)}}
.vd.good{{background:#e9f7ef;color:var(--green)}}
.vd.neutral{{background:var(--brand-soft);color:var(--brand)}}
.nums{{display:flex;gap:18px;flex:1;flex-wrap:wrap}}
.nums .n{{font-size:13px;color:var(--muted)}}
.nums .n b{{color:var(--ink);font-size:15px}}
.links{{display:flex;gap:8px;margin-left:auto}}
.links a{{font-size:13px;font-weight:600;text-decoration:none;padding:7px 13px;border-radius:9px;border:1px solid var(--line);color:var(--brand);background:var(--card);white-space:nowrap}}
.links a.primary{{background:var(--brand);color:#fff;border-color:var(--brand)}}
.links a:hover{{background:#dbe7f7}}
.links a.primary:hover{{background:var(--brand)}}
.tag-demo{{font-size:10.5px;color:var(--muted);background:#eef2f7;padding:1px 7px;border-radius:6px;margin-left:6px}}
.foot{{text-align:center;color:#9aa4b2;font-size:12px;margin-top:24px}}
@media(max-width:680px){{.links{{margin-left:0;width:100%}}.dt{{min-width:auto}}}}

/* Навигация год → месяц → дни */
.tree{{margin-top:18px;display:flex;flex-direction:column;gap:12px}}
details.yr{{background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden}}
details.yr>summary{{list-style:none;cursor:pointer;padding:14px 18px;
  font-size:18px;font-weight:800;color:var(--brand);display:flex;align-items:center;gap:10px;
  background:var(--brand-soft)}}
details.yr>summary::-webkit-details-marker{{display:none}}
.caret{{display:inline-block;width:9px;height:9px;border-right:2px solid currentColor;
  border-bottom:2px solid currentColor;transform:rotate(-45deg);transition:transform .15s ease;flex:0 0 auto;margin-top:-2px}}
details[open]>summary .caret{{transform:rotate(45deg);margin-top:-4px}}
.yr .cnt{{font-size:13px;font-weight:600;color:var(--muted);margin-left:auto}}
.months{{padding:8px 12px 14px;display:flex;flex-direction:column;gap:8px}}
details.mo{{border:1px solid var(--line);border-radius:11px;overflow:hidden}}
details.mo>summary{{list-style:none;cursor:pointer;padding:11px 15px;font-size:15px;font-weight:700;
  color:var(--ink);display:flex;align-items:center;gap:9px;background:#fafbfd}}
details.mo>summary::-webkit-details-marker{{display:none}}
.mo .cnt{{font-size:12.5px;font-weight:600;color:var(--muted);margin-left:auto}}
.mo .list{{margin:0;padding:10px 12px}}
</style></head><body><div class="wrap">""")

    P.append(f"""<header class="doc">
<h1>Сводки ЕДДС · архив за все дни</h1>
<div class="sub">МКУ «СолнСпас» г.о. Солнечногорск · всего дней в архиве: {len(rows)}</div>
</header>""")

    def day_card(r):
        d = r["date"]
        vd_cls, has = day_verdict(history, d)
        vd_txt = {"bad": "напряжённее", "good": "спокойнее", "neutral": "в норме"}[vd_cls]
        if not has:
            vd_txt = "первый день"
        demo = '<span class="tag-demo">демо</span>' if r.get("_demo") else ""
        latest_cls = " latest" if d == latest else ""
        return f"""<div class="row{latest_cls}">
<div class="dt"><div class="d">{esc(long_date(d))}{demo}</div><div class="w">{esc(wday(d))}</div></div>
<div class="vd {vd_cls}">{esc(vd_txt)}</div>
<div class="nums">
<span class="n">Происшествий <b>{esc(r.get('inc_total',0))}</b></span>
<span class="n">Тех. нарушений <b>{esc(r.get('tech_total',0))}</b></span>
<span class="n">Обращений <b>{esc(r.get('appeals_total',0))}</b></span>
<span class="n">Вызовов 112 <b>{esc(r.get('calls_112',0))}</b></span>
</div>
<div class="links">
<a class="primary" href="svodka-{esc(d)}.html">Сводка</a>
<a href="analytics-{esc(d)}.html">Аналитика</a>
</div>
</div>"""

    # Группировка: год → месяц → дни (всё новое сверху)
    tree = OrderedDict()
    for r in rows:
        try:
            dt = datetime.strptime(r["date"], "%Y-%m-%d")
        except Exception:
            continue
        tree.setdefault(dt.year, OrderedDict()).setdefault(dt.month, []).append(r)

    latest_year = max(tree) if tree else None
    latest_month = max(tree[latest_year]) if latest_year else None

    P.append('<div class="tree">')
    for y in sorted(tree, reverse=True):
        y_days = sum(len(v) for v in tree[y].values())
        y_open = " open" if y == latest_year else ""
        P.append(f'<details class="yr"{y_open}><summary>'
                 f'<span class="caret"></span>{y} год'
                 f'<span class="cnt">{y_days} дн.</span></summary>')
        P.append('<div class="months">')
        for m in sorted(tree[y], reverse=True):
            recs = sorted(tree[y][m], key=lambda r: r["date"], reverse=True)
            m_open = " open" if (y == latest_year and m == latest_month) else ""
            P.append(f'<details class="mo"{m_open}><summary>'
                     f'<span class="caret"></span>{MONTHS_NOM[m]}'
                     f'<span class="cnt">{len(recs)} дн.</span></summary>')
            P.append('<div class="list">')
            for r in recs:
                P.append(day_card(r))
            P.append('</div></details>')
        P.append('</div></details>')
    P.append('</div>')

    P.append('<p class="foot">Архив формируется автоматически из ежедневных сводок ЕДДС МКУ «СолнСпас».</p>')
    P.append("</div></body></html>")
    return "\n".join(P)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("history", nargs="?", default="history.jsonl")
    ap.add_argument("-o", "--out", default="index.html")
    a = ap.parse_args()
    hist = load_history(a.history)
    out = build(hist)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"OK -> {a.out} ({len(out)} bytes, {len(hist)} дней)")
