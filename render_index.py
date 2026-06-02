#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Генератор страницы-индекса (архив всех сводок ЕДДС) из history.jsonl.
Самодостаточный HTML. Каждый день — карточка со ссылками на сводку и аналитику,
краткой оценкой дня и ключевыми числами.
"""
import sys, json, html, argparse
from datetime import datetime
from metrics import load_history, compare, verdict

MONTHS = ["", "января", "февраля", "марта", "апреля", "мая", "июня",
          "июля", "августа", "сентября", "октября", "ноября", "декабря"]
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
<title>Сводки ЕДДС · архив</title>
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
</style></head><body><div class="wrap">""")

    P.append(f"""<header class="doc">
<h1>Сводки ЕДДС · архив за все дни</h1>
<div class="sub">МКУ «СолнСпас» г.о. Солнечногорск · всего дней в архиве: {len(rows)}</div>
</header>""")

    P.append('<div class="list">')
    for r in rows:
        d = r["date"]
        vd_cls, has = day_verdict(history, d)
        vd_txt = {"bad": "напряжённее", "good": "спокойнее", "neutral": "в норме"}[vd_cls]
        if not has:
            vd_txt = "первый день"
        demo = '<span class="tag-demo">демо</span>' if r.get("_demo") else ""
        latest_cls = " latest" if d == latest else ""
        P.append(f"""<div class="row{latest_cls}">
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
</div>""")
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
