#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Полный конвейер сборки сайта сводок ЕДДС из одного docx.

  python3 build_all.py <svodka.docx> [--site site] [--history history.jsonl] [--mask]

Шаги:
  1. parse_svodka.parse(docx)            -> данные за сутки
  2. metrics.extract_metrics(data)       -> числовые метрики
  3. metrics.upsert_history(history)     -> добавляем/обновляем день (идемпотентно)
  4. render_html.build(...)              -> site/svodka-<date>.html (с баннером динамики)
  5. render_analytics.build(...)         -> site/analytics-<date>.html
  6. render_index.build(...)             -> site/index.html (архив)

Идемпотентно: повторный прогон того же дня просто перезапишет его страницы и запись.
"""
import os, sys, argparse, json

import parse_svodka
import metrics
import render_html
import render_analytics
import render_index


def build_site(docx_path, site_dir="site", history_path="history.jsonl", mask=False):
    os.makedirs(site_dir, exist_ok=True)

    # 1. парсинг
    data = parse_svodka.parse(docx_path)
    date = data.get("meta", {}).get("date", "")
    if not date:
        raise SystemExit("Не удалось определить дату сводки из файла: " + docx_path)

    # 2-3. метрики + история
    m = metrics.extract_metrics(data)
    history = metrics.upsert_history(history_path, m)

    # 4. сводка за день (с баннером динамики)
    svodka_html = render_html.build(data, mask=mask, history=history)
    svodka_file = os.path.join(site_dir, f"svodka-{date}.html")
    with open(svodka_file, "w", encoding="utf-8") as f:
        f.write(svodka_html)

    # 5. аналитика за этот день
    an_html = render_analytics.build(history, target_date=date)
    an_file = os.path.join(site_dir, f"analytics-{date}.html")
    with open(an_file, "w", encoding="utf-8") as f:
        f.write(an_html)

    # 6. индекс-архив
    idx_html = render_index.build(history)
    idx_file = os.path.join(site_dir, "index.html")
    with open(idx_file, "w", encoding="utf-8") as f:
        f.write(idx_html)

    return {
        "date": date,
        "svodka": svodka_file,
        "analytics": an_file,
        "index": idx_file,
        "days_in_history": len(history),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("docx")
    ap.add_argument("--site", default="site")
    ap.add_argument("--history", default="history.jsonl")
    ap.add_argument("--mask", action="store_true", help="маскировать персональные данные (152-ФЗ)")
    a = ap.parse_args()
    res = build_site(a.docx, a.site, a.history, a.mask)
    print(json.dumps(res, ensure_ascii=False, indent=2))
