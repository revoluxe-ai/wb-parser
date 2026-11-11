#!/usr/bin/env python3
"""Мониторинг цен и остатков конкурентов на Wildberries."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from wb.client import WbClient
from wb.collect import WbCollector, parse_article_list, now_moscow
from wb.excel_out import write_frames
from wb.products import Product
from wb.telegram_notify import send_message

MOSCOW = ZoneInfo("Europe/Moscow")

EXAMPLES = """
Примеры:
  %(prog)s --ids 1464760255,550053815 -o report.xlsx --history history.json
  %(prog)s --file competitors.txt -o report.xlsx --history history.json --csv
  %(prog)s --ids 1464760255 --telegram
      (нужны переменные TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID)

Скрипт каждый запуск дописывает снимок в history.json, сравнивает с предыдущим
и пишет Excel с листами «Текущий снимок» и «Изменения». Строки с ростом цены
или падением остатка подсвечиваются красным, со снижением цены — зелёным.
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Мониторинг цен и остатков конкурентов Wildberries.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--ids", default="", help="Артикулы конкурентов через запятую")
    p.add_argument("--file", "-f", dest="ids_file", help="Файл с артикулами")
    p.add_argument("-o", "--output", default="wb_monitor.xlsx", help="Excel-отчёт (по умолчанию wb_monitor.xlsx)")
    p.add_argument("--history", default="wb_monitor_history.json", help="Файл истории снимков (JSON)")
    p.add_argument("--csv", action="store_true", help="Дополнительно сохранить CSV текущего снимка")
    p.add_argument("--dest", type=int, default=-1257786, help="Код региона WB (Москва = -1257786)")
    p.add_argument("--delay", type=float, default=0.45, help="Пауза между запросами, сек.")
    p.add_argument("--source", choices=("auto", "classic", "napi"), default="auto")
    p.add_argument("--telegram", action="store_true", help="Отправить краткий отчёт в Telegram (токен и chat id из env или флагов)")
    p.add_argument("--telegram-token", default=None, help="Токен бота (иначе TELEGRAM_BOT_TOKEN)")
    p.add_argument("--telegram-chat-id", default=None, help="Chat id (иначе TELEGRAM_CHAT_ID)")
    return p


def _read_ids(args: argparse.Namespace) -> list[int]:
    chunks: list[str] = []
    if args.ids:
        chunks.append(args.ids)
    if args.ids_file:
        path = Path(args.ids_file)
        if not path.is_file():
            raise SystemExit(f"Файл с артикулами не найден: {path}")
        chunks.append(path.read_text(encoding="utf-8"))
    ids = parse_article_list("\n".join(chunks))
    if not ids:
        raise SystemExit("Не указаны артикулы. Передайте --ids или --file.")
    return ids


def _snapshot(products: list[Product], ts: str) -> dict[str, Any]:
    items = {}
    for p in products:
        items[str(p.nm_id)] = {
            "nm_id": p.nm_id,
            "name": p.name,
            "brand": p.brand,
            "seller_name": p.seller_name,
            "price": p.price,
            "sale_price": p.sale_price,
            "discount_percent": p.discount_percent,
            "rating": p.rating,
            "review_count": p.review_count,
            "stock": p.stock,
            "availability": p.availability,
            "url": p.url,
        }
    return {"ts": ts, "items": items}


def _load_history(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"Не удалось прочитать историю {path}: {exc}")
    if isinstance(data, dict) and "runs" in data:
        return list(data["runs"])
    if isinstance(data, list):
        return data
    return []


def _save_history(path: Path, runs: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"runs": runs}, ensure_ascii=False, indent=2), encoding="utf-8")


def _delta(old: Optional[float], new: Optional[float]) -> Optional[float]:
    if old is None or new is None:
        return None
    return round(float(new) - float(old), 2)


def compare(prev: Optional[dict], current: dict) -> tuple[list[dict], list[dict]]:
    """Возвращает (строки изменений, флаги подсветки для этих строк)."""
    prev_items = (prev or {}).get("items") or {}
    cur_items = current.get("items") or {}
    rows: list[dict] = []
    flags: list[dict] = []
    all_ids = list(dict.fromkeys(list(cur_items.keys()) + list(prev_items.keys())))
    for key in all_ids:
        new = cur_items.get(key)
        old = prev_items.get(key)
        if new and not old:
            rows.append(_change_row(new, None, status="Новый товар"))
            flags.append({"kind": "new", "cols": ["Статус"]})
            continue
        if old and not new:
            rows.append(_change_row(old, old, status="Пропал из выдачи", use_old=True))
            flags.append({"kind": "gone", "cols": ["Статус"]})
            continue
        assert new is not None and old is not None
        d_price = _delta(old.get("sale_price"), new.get("sale_price"))
        d_base = _delta(old.get("price"), new.get("price"))
        d_stock = _delta(old.get("stock"), new.get("stock"))
        avail_changed = (old.get("availability") or "") != (new.get("availability") or "")
        changed = any(x is not None and x != 0 for x in (d_price, d_base, d_stock)) or avail_changed
        if not changed:
            rows.append(_change_row(new, old, status="Без изменений", d_price=d_price, d_stock=d_stock))
            flags.append({})
            continue
        status_parts = []
        kind = "change"
        highlight_cols = ["Статус"]
        if d_price is not None and d_price != 0:
            if d_price > 0:
                status_parts.append(f"цена ↑ {d_price:+.2f} ₽")
                kind = "price_up"
            else:
                status_parts.append(f"цена ↓ {d_price:+.2f} ₽")
                kind = "price_down"
            highlight_cols += ["Цена со скидкой, ₽", "Δ цена, ₽"]
        if d_stock is not None and d_stock != 0:
            if d_stock < 0:
                status_parts.append(f"остаток ↓ {d_stock:+.0f}")
                if kind not in ("price_up", "price_down"):
                    kind = "stock_down"
            else:
                status_parts.append(f"остаток ↑ {d_stock:+.0f}")
                if kind not in ("price_up", "price_down"):
                    kind = "stock_up"
            highlight_cols += ["Остаток", "Δ остаток"]
        if avail_changed:
            status_parts.append(f"наличие: {old.get('availability') or '—'} → {new.get('availability') or '—'}")
            highlight_cols.append("Наличие")
        rows.append(
            _change_row(
                new,
                old,
                status="; ".join(status_parts) or "Изменение",
                d_price=d_price,
                d_stock=d_stock,
            )
        )
        flags.append({"kind": kind, "cols": list(dict.fromkeys(highlight_cols))})
    return rows, flags


def _change_row(
    new: dict,
    old: Optional[dict],
    status: str,
    d_price: Optional[float] = None,
    d_stock: Optional[float] = None,
    use_old: bool = False,
) -> dict:
    src = old if use_old and old else new
    old = old or {}
    return {
        "Артикул": src.get("nm_id"),
        "Название": src.get("name"),
        "Бренд": src.get("brand"),
        "Продавец": src.get("seller_name"),
        "Цена было, ₽": old.get("price"),
        "Цена стало, ₽": None if use_old else new.get("price"),
        "Цена со скидкой было, ₽": old.get("sale_price"),
        "Цена со скидкой, ₽": None if use_old else new.get("sale_price"),
        "Δ цена, ₽": d_price,
        "Остаток было": old.get("stock"),
        "Остаток": None if use_old else new.get("stock"),
        "Δ остаток": d_stock,
        "Наличие": src.get("availability"),
        "Статус": status,
        "Ссылка": src.get("url"),
    }


def _current_rows(products: list[Product], ts: str) -> list[dict]:
    rows = []
    for p in products:
        p.finalize()
        rows.append(
            {
                "Артикул": p.nm_id,
                "Название": p.name,
                "Бренд": p.brand,
                "Продавец": p.seller_name,
                "Цена, ₽": p.price,
                "Цена со скидкой, ₽": p.sale_price,
                "Скидка, %": p.discount_percent,
                "Рейтинг": p.rating,
                "Отзывы": p.review_count,
                "Остаток": p.stock,
                "Наличие": p.availability,
                "Размеры": p.sizes,
                "Ссылка": p.url,
                "Дата сбора": ts,
            }
        )
    return rows


def _telegram_text(ts: str, products: list[Product], change_rows: list[dict], first_run: bool) -> str:
    lines = [f"Мониторинг WB, {ts}", f"Товаров в снимке: {len(products)}"]
    if first_run:
        lines.append("Это первый снимок — сравнивать пока не с чем. Следующий запуск покажет изменения.")
        for p in products[:15]:
            price = f"{p.sale_price:.0f} ₽" if p.sale_price is not None else "нет цены"
            stock = f", остаток {p.stock}" if p.stock is not None else ""
            lines.append(f"• {p.nm_id} {p.name[:40]} — {price}{stock}")
        return "\n".join(lines)
    changed = [r for r in change_rows if r.get("Статус") and r["Статус"] != "Без изменений"]
    lines.append(f"Изменений: {len(changed)}")
    if not changed:
        lines.append("Цен и остатков без изменений.")
    for r in changed[:20]:
        lines.append(f"• {r.get('Артикул')} {str(r.get('Название') or '')[:40]} — {r.get('Статус')}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ids = _read_ids(args)
    ts = now_moscow()
    collector = WbCollector(
        client=WbClient(delay=args.delay),
        dest=args.dest,
        enrich_seller=True,
        delay=args.delay,
        source=args.source,
    )
    products = collector.by_articles(ids)
    if not products:
        print("Не удалось собрать товары.", file=sys.stderr)
        return 1

    history_path = Path(args.history)
    runs = _load_history(history_path)
    prev = runs[-1] if runs else None
    current = _snapshot(products, ts)
    runs.append(current)
    _save_history(history_path, runs)

    change_rows, flags = compare(prev, current)
    current_df = pd.DataFrame(_current_rows(products, ts))
    changes_df = pd.DataFrame(change_rows)
    first_run = prev is None
    if first_run:
        # на первом запуске лист «Изменения» всё равно есть, но все статусы «Новый товар»
        pass

    sheets = {"Текущий снимок": current_df, "Изменения": changes_df}
    highlight = {"Изменения": {"row_flags": flags}}
    out = write_frames(args.output, sheets, highlight=highlight, also_csv=args.csv)
    changed_n = sum(1 for r in change_rows if r.get("Статус") != "Без изменений")
    print(f"Снимок {ts}: {len(products)} товаров. Изменений: {changed_n if not first_run else 'первый запуск'}.")
    print(f"Отчёт: {out}")
    print(f"История: {history_path} (снимков: {len(runs)})")

    if args.telegram:
        text = _telegram_text(ts, products, change_rows, first_run)
        try:
            send_message(text, token=args.telegram_token, chat_id=args.telegram_chat_id)
            print("Сообщение отправлено в Telegram.")
        except Exception as exc:
            print(f"Telegram не отправлен: {exc}", file=sys.stderr)
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
