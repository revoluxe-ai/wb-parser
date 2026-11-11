#!/usr/bin/env python3
"""Сбор данных Wildberries в Excel: поиск, список артикулов или категория."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# запуск и как скрипт, и как модуль
sys.path.insert(0, str(Path(__file__).resolve().parent))

from wb.client import WbClient
from wb.collect import WbCollector, parse_article_list
from wb.excel_out import write_products


EXAMPLES = """
Примеры:
  %(prog)s search "платье женское" --pages 2 -o platya.xlsx
  %(prog)s search "платье женское" --pages 2 -o platya.xlsx --csv
  %(prog)s articles 1464760255,550053815 -o cards.xlsx
  %(prog)s articles --file artikuly.txt -o cards.xlsx --csv
  %(prog)s category "https://www.wildberries.ru/catalog/zhenshchinam/odezhda" --pages 2 -o cat.xlsx

Артикулы в файле можно писать через запятую, пробел или с новой строки.
Регион по умолчанию — Москва (dest=-1257786). Источник данных выбирается автоматически:
если search.wb.ru / card.wb.ru недоступны, используется napi.wildberries.ru и CDN wbbasket.ru.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Парсер Wildberries: выгрузка товаров в Excel (и CSV).",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-o", "--output", default="wb_export.xlsx", help="Путь к Excel-файлу (по умолчанию wb_export.xlsx)")
    common.add_argument("--csv", action="store_true", help="Дополнительно сохранить CSV рядом с xlsx")
    common.add_argument("--pages", type=int, default=1, help="Сколько страниц поиска/каталога собрать (по умолчанию 1, до 100 товаров на странице)")
    common.add_argument("--dest", type=int, default=-1257786, help="Код региона WB (по умолчанию -1257786, Москва)")
    common.add_argument("--delay", type=float, default=0.45, help="Пауза между запросами в секундах (по умолчанию 0.45)")
    common.add_argument("--no-seller", action="store_true", help="Не запрашивать имя продавца с CDN (быстрее)")
    common.add_argument(
        "--source",
        choices=("auto", "classic", "napi"),
        default="auto",
        help="Источник: auto (по умолчанию), classic (search.wb.ru/card.wb.ru) или napi",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_search = sub.add_parser("search", parents=[common], help="Поиск по запросу")
    p_search.add_argument("query", help="Поисковый запрос, например: платье женское")

    p_art = sub.add_parser("articles", parents=[common], help="Список артикулов (nm)")
    p_art.add_argument("ids", nargs="?", default="", help="Артикулы через запятую или пробел")
    p_art.add_argument("--file", "-f", dest="ids_file", help="Файл с артикулами")

    p_cat = sub.add_parser("category", parents=[common], help="Категория по URL каталога")
    p_cat.add_argument("url", help="URL или путь категории, например https://www.wildberries.ru/catalog/zhenshchinam/odezhda")

    return parser


def _read_ids(args: argparse.Namespace) -> list[int]:
    chunks: list[str] = []
    if getattr(args, "ids", ""):
        chunks.append(args.ids)
    if getattr(args, "ids_file", None):
        path = Path(args.ids_file)
        if not path.is_file():
            raise SystemExit(f"Файл с артикулами не найден: {path}")
        chunks.append(path.read_text(encoding="utf-8"))
    ids = parse_article_list("\n".join(chunks))
    if not ids:
        raise SystemExit("Не указаны артикулы. Передайте их аргументом или через --file.")
    return ids


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    client = WbClient(delay=args.delay)
    collector = WbCollector(
        client=client,
        dest=args.dest,
        enrich_seller=not args.no_seller,
        delay=args.delay,
        source=args.source,
    )

    if args.command == "search":
        products = collector.search(args.query, pages=args.pages)
    elif args.command == "articles":
        products = collector.by_articles(_read_ids(args))
    elif args.command == "category":
        products = collector.category(args.url, pages=args.pages)
    else:
        parser.print_help()
        return 2

    if not products:
        print("Ничего не собрано. Проверьте запрос и доступ к API WB.", file=sys.stderr)
        return 1

    out = write_products(products, args.output, also_csv=args.csv)
    print(f"Сохранено {len(products)} товаров → {out}")
    if args.csv:
        print(f"CSV: {Path(args.output).with_suffix('.csv')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
