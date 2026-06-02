#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Генератор страницы аналитики ЕДДС из history.jsonl.
Самодостаточный HTML: инлайновые SVG-спарклайны/столбцы, без внешних библиотек.
Даёт ответ «лучше/хуже чем вчера/позавчера/чем обычно».
"""
import sys, json, html, argparse
from datetime import datetime
from metrics import load_history, compare, verdict, METRIC_LABELS, BAD_UP


def esc(s):
    return html.escape(str(s)) if s is not None else ""


def fmt_date(d):
    try:
        return datetime.strptime(d, "%Y-%m-%d").strftime("%d.%m")
    except Exception:
        return d


# Наборы метрик для дашборда (ключевые KPI сверху)
KPI = ["inc_total", "tech_total", "appeals_total", "calls_112"]
# Группы для детальных графиков.
# main — главный график группы (виден всегда), extra — детальные (внутри спойлера)
GROUPS = [
    ("Происшествия", "inc_total", ["inc_dtp", "inc_minors", "inc_fire", "inc_uav", "inc_other"]),
    ("Технологические нарушения", "tech_total", ["tech_in_work", "tech_closed", "tech_gas", "tech_cold_water", "tech_hot_water", "tech_ecology", "tech_sewage", "tech_heating", "tech_electricity"]),
    ("Обращения граждан", "appeals_total", ["appeals_hotline", "mayor_hotline_total", "spas_total"]),
    ("Вызовы по 112", "calls_112", ["calls_iskra"]),
]


def sparkline(vals, w=240, h=44, color="#1b4f8a"):
    vals = [v if v is not None else 0 for v in vals]
    if not vals:
        return ""
    mn, mx = min(vals), max(vals)
    rng = (mx - mn) or 1
    n = len(vals)
    step = w / max(1, n - 1)
    pts = []
    for i, v in enumerate(vals):
        x = i * step
        y = h - 4 - (v - mn) / rng * (h - 10)
        pts.append(f"{x:.1f},{y:.1f}")
    last_x, last_y = pts[-1].split(",")
    poly = " ".join(pts)
    area = f"0,{h} " + poly + f" {w},{h}"
    return (f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" class="spark">'
            f'<polygon points="{area}" fill="{color}" opacity="0.08"/>'
            f'<polyline points="{poly}" fill="none" stroke="{color}" stroke-width="2" '
            f'stroke-linejoin="round" stroke-linecap="round"/>'
            f'<circle cx="{last_x}" cy="{last_y}" r="3" fill="{color}"/></svg>')


def bars(labels, vals, w=560, h=180, color="#1b4f8a", today_idx=None):
    vals = [v if v is not None else 0 for v in vals]
    mx = max(vals) or 1
    n = len(vals)
    bw = w / n * 0.62
    gap = w / n
    out = [f'<svg viewBox="0 0 {w} {h+26}" class="bars">']
    for i, v in enumerate(vals):
        bh = (v / mx) * h
        x = i * gap + (gap - bw) / 2
        y = h - bh
        c = "#c0392b" if (today_idx is not None and i == today_idx) else color
        op = "1" if (today_idx is not None and i == today_idx) else "0.55"
        out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="2" fill="{c}" opacity="{op}"><title>{esc(labels[i])}: {v}</title></rect>')
        if n <= 16 or i % 3 == 0:
            out.append(f'<text x="{x+bw/2:.1f}" y="{h+16}" font-size="9" fill="#9aa4b2" text-anchor="middle">{esc(labels[i])}</text>')
    out.append("</svg>")
    return "".join(out)


def trend_arrow(delta, bad_up):
    if delta is None:
        return '<span class="ar flat">—</span>'
    if delta == 0:
        return '<span class="ar flat">→ 0</span>'
    worse = (delta > 0) == bad_up
    cls = "down-bad" if worse else "up-good"
    arr = "▲" if delta > 0 else "▼"
    sign = "+" if delta > 0 else ""
    return f'<span class="ar {cls}">{arr} {sign}{delta}</span>'


def build(history, target_date=None):
    history = [r for r in history if r.get("date")]
    if not history:
        return "<html><body>Нет данных</body></html>"
    if target_date is None:
        target_date = history[-1]["date"]

    dates = [r["date"] for r in history]
    cmp_all = compare(history, target_date)

    # генеральный вывод дня
    worse_cnt = better_cnt = 0
    for k in KPI:
        st, _ = verdict(cmp_all.get(k, {}))
        if st == "worse":
            worse_cnt += 1
        elif st == "better":
            better_cnt += 1
    if worse_cnt > better_cnt:
        overall = ("За сутки обстановка в целом напряжённее обычного", "bad")
    elif better_cnt > worse_cnt:
        overall = ("За сутки обстановка в целом спокойнее обычного", "good")
    else:
        overall = ("За сутки обстановка в пределах обычного", "neutral")

    P = []
    P.append(f"""<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Аналитика ЕДДС · динамика</title><link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%2301696F'/%3E%3Cpath d='M32 8 L52 16 V32 C52 45 43 53 32 57 C21 53 12 45 12 32 V16 Z' fill='%23ffffff'/%3E%3Cpath d='M30 18 h4 v10 h10 v4 h-10 v10 h-4 v-10 h-10 v-4 h10 z' fill='%2301696F'/%3E%3C/svg%3E">
<style>
:root{{--bg:#f4f6f9;--card:#fff;--ink:#1a2230;--muted:#6b7686;--line:#e3e8ef;
--brand:#1b4f8a;--brand-soft:#eaf1fa;--red:#c0392b;--green:#1f7a4d;--amber:#c77700;}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;font-size:15px;line-height:1.55}}
.wrap{{max-width:1000px;margin:0 auto;padding:20px 16px 64px}}
header.doc{{background:var(--brand);color:#fff;border-radius:14px;padding:22px 26px}}
header.doc h1{{margin:0 0 4px;font-size:23px}}
header.doc .sub{{opacity:.9;font-size:14px}}
.nav{{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0 2px}}
.nav a{{font-size:13.5px;font-weight:600;color:var(--brand);text-decoration:none;background:var(--card);border:1px solid var(--line);padding:8px 16px;border-radius:10px}}
.nav a.active{{background:var(--brand);color:#fff;border-color:var(--brand)}}
.nav a:hover{{background:#dbe7f7}}
.nav a.active:hover{{background:var(--brand)}}
.verdict{{margin-top:16px;border-radius:14px;padding:16px 20px;font-size:16px;font-weight:600}}
.verdict.bad{{background:#fdeeec;color:var(--red);border:1px solid #f3cdc7}}
.verdict.good{{background:#e9f7ef;color:var(--green);border:1px solid #c7ecd6}}
.verdict.neutral{{background:var(--brand-soft);color:var(--brand);border:1px solid #cfe0f3}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-top:16px}}
.kpi{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px}}
.kpi .l{{font-size:12.5px;color:var(--muted)}}
.kpi .v{{font-size:30px;font-weight:700;margin:4px 0 2px}}
.kpi .spark{{width:100%;height:44px;display:block;margin-top:6px}}
.ar{{font-size:13px;font-weight:700;display:inline-block}}
.ar.up-good,.ar.down-good{{color:var(--green)}}
.ar.down-bad,.ar.up-bad{{color:var(--red)}}
.ar.flat{{color:var(--muted)}}
.cmp-line{{font-size:12px;color:var(--muted);margin-top:4px}}
section.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px 22px;margin-top:16px}}
section.card>h2{{margin:0 0 14px;font-size:18px;color:var(--brand);padding-bottom:10px;border-bottom:2px solid var(--brand-soft)}}
table{{width:100%;border-collapse:collapse;font-size:14px}}
th,td{{text-align:left;padding:9px 11px;border-bottom:1px solid var(--line)}}
th{{background:#f7f9fc;color:var(--muted);font-size:12px;text-transform:uppercase}}
td.num,th.num{{text-align:right}}
tr:last-child td{{border-bottom:none}}
.bars{{width:100%;height:auto;display:block}}
.metric-row td:first-child{{font-weight:600}}
.tag{{font-size:11px;padding:2px 8px;border-radius:20px;font-weight:600}}
.tag.worse{{background:#fdeeec;color:var(--red)}} .tag.better{{background:#e9f7ef;color:var(--green)}}
.tag.same{{background:#eef2f7;color:var(--muted)}}
.chart-block{{margin-bottom:8px}}
.chart-block h3{{font-size:14px;margin:0 0 6px;color:var(--ink)}}
.group{{padding:14px 0;border-bottom:1px solid var(--line)}}
.group:last-child{{border-bottom:none}}
.grp-title{{font-size:15px;margin:0 0 8px;color:var(--brand);font-weight:700}}
.spoiler{{margin-top:8px}}
.spoiler>summary{{cursor:pointer;font-size:13px;color:var(--brand);background:var(--brand-soft);padding:7px 13px;border-radius:8px;list-style:none;user-select:none;display:inline-flex;align-items:center;gap:8px}}
.spoiler>summary::-webkit-details-marker{{display:none}}
.spoiler>summary::before{{content:'';flex:0 0 auto;width:0;height:0;border-style:solid;border-width:5px 0 5px 8px;border-color:transparent transparent transparent var(--brand);transition:transform .15s ease}}
.spoiler[open]>summary::before{{transform:rotate(90deg)}}
.spoiler[open]>summary{{margin-bottom:12px}}
.spoiler-body{{display:grid;grid-template-columns:repeat(2,1fr);gap:14px 20px}}
@media(max-width:680px){{.spoiler-body{{grid-template-columns:1fr}}}}
@media(max-width:680px){{.kpis{{grid-template-columns:repeat(2,1fr)}}}}
</style></head><body><div class="wrap">""")

    P.append(f"""<header class="doc">
<h1>Аналитика ЕДДС · динамика по дням</h1>
<div class="sub">Данные на {esc(fmt_date(target_date))}.{esc(datetime.strptime(target_date,'%Y-%m-%d').strftime('%Y'))} · история за {len(history)} сут.</div>
</header>
<div class="nav"><a href="index.html">🗂 Все сводки</a><a href="svodka-{esc(target_date)}.html">📄 Сводка за день</a><a class="active" href="analytics-{esc(target_date)}.html">📊 Аналитика и динамика</a></div>
<div class="verdict {overall[1]}">{esc(overall[0])}.</div>""")

    # KPI плашки со спарклайнами
    P.append('<div class="kpis">')
    for k in KPI:
        c = cmp_all.get(k, {})
        series = [r.get(k, 0) for r in history][-30:]
        col = "#c0392b" if c.get("bad_up") else "#1f7a4d"
        st, txt = verdict(c)
        a7 = c.get("avg7")
        vs7 = c.get("vs_avg7")
        vs7txt = ""
        if vs7 is not None:
            worse7 = (vs7 > 0) == c.get("bad_up")
            vs7txt = f' · чем обычно: <b style="color:{"#c0392b" if worse7 and vs7!=0 else ("#1f7a4d" if vs7!=0 else "#6b7686")}">{"+" if vs7>0 else ""}{vs7}</b>'
        P.append(f"""<div class="kpi">
<div class="l">{esc(METRIC_LABELS.get(k,k))}</div>
<div class="v">{esc(c.get('value',0))} {trend_arrow(c.get('delta1'), c.get('bad_up'))}</div>
<div class="cmp-line">ср.7дн {esc(a7)}{vs7txt}</div>
{sparkline(series, color=col)}
</div>""")
    P.append('</div>')

    # Таблица «лучше/хуже» по всем ключевым метрикам
    P.append('<section class="card"><h2>Сравнение с предыдущими сутками</h2>')
    P.append('<table><thead><tr><th>Показатель</th><th class="num">Сегодня</th><th class="num">Вчера</th><th class="num">Ср. 7 дн</th><th class="num">Ср. 30 дн</th><th>Оценка</th></tr></thead><tbody>')
    table_keys = ["inc_total","inc_dtp","inc_minors","inc_uav","tech_total","tech_in_work","tech_gas","tech_cold_water","tech_hot_water","tech_ecology","appeals_total","appeals_hotline","calls_112","spas_total"]
    for k in table_keys:
        c = cmp_all.get(k)
        if not c:
            continue
        st, txt = verdict(c)
        tag = {"worse":"worse","better":"better","same":"same","n/a":"same"}[st]
        tagtxt = {"worse":"хуже","better":"лучше","same":"= вчера","n/a":"—"}[st]
        P.append(f'<tr class="metric-row"><td>{esc(c["label"])}</td>'
                 f'<td class="num"><b>{esc(c["value"])}</b></td>'
                 f'<td class="num">{esc(c["prev1"]) if c["prev1"] is not None else "—"}</td>'
                 f'<td class="num">{esc(c["avg7"]) if c["avg7"] is not None else "—"}</td>'
                 f'<td class="num">{esc(c["avg30"]) if c["avg30"] is not None else "—"}</td>'
                 f'<td><span class="tag {tag}">{tagtxt}</span></td></tr>')
    P.append('</tbody></table></section>')

    # Графики по группам: главный виден всегда, детальные — в спойлере
    last30 = history[-30:]
    labels30 = [fmt_date(r["date"]) for r in last30]
    today_idx = len(last30) - 1 if last30 and last30[-1]["date"] == target_date else None

    def chart_block(k):
        if k not in cmp_all:
            return None
        vals = [r.get(k, 0) for r in last30]
        if max(vals) == 0:
            return None
        c = cmp_all[k]
        b = [f'<div class="chart-block"><h3>{esc(METRIC_LABELS.get(k,k))} '
             f'<span style="font-weight:400;color:#6b7686">— сегодня {c["value"]}, '
             f'ср.30дн {c["avg30"]}</span></h3>']
        b.append(bars(labels30, vals, today_idx=today_idx))
        b.append('</div>')
        return "".join(b)

    P.append('<section class="card"><h2>Динамика по дням (за 30 сут.)</h2>')
    for title, main_key, extra_keys in GROUPS:
        main_html = chart_block(main_key)
        if main_html is None:
            continue
        P.append(f'<div class="group"><h4 class="grp-title">{esc(title)}</h4>')
        P.append(main_html)
        # детальные графики в спойлере
        extra_html = [h for h in (chart_block(k) for k in extra_keys) if h]
        if extra_html:
            P.append(f'<details class="spoiler"><summary>Подробнее — детализация ({len(extra_html)})</summary>'
                     '<div class="spoiler-body">' + "".join(extra_html) + '</div></details>')
        P.append('</div>')
    P.append('</section>')

    P.append('<p style="text-align:center;color:#9aa4b2;font-size:12px;margin-top:24px">'
             'Аналитика формируется автоматически из ежедневных сводок ЕДДС МКУ «СолнСпас».</p>')
    P.append("</div></body></html>")
    return "\n".join(P)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("history", nargs="?", default="history.jsonl")
    ap.add_argument("-o", "--out", default="analytics.html")
    ap.add_argument("-d", "--date", default=None)
    a = ap.parse_args()
    hist = load_history(a.history)
    out = build(hist, a.date)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"OK -> {a.out} ({len(out)} bytes, {len(hist)} дней)")
