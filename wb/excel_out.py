"""Запись Excel (openpyxl) с оформлением и опционального CSV."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.formatting.rule import CellIsRule, FormulaRule

from .products import Product

COLUMNS = [
    ("nm_id", "Артикул"),
    ("name", "Название"),
    ("brand", "Бренд"),
    ("seller_name", "Продавец"),
    ("seller_id", "ID продавца"),
    ("price", "Цена, ₽"),
    ("sale_price", "Цена со скидкой, ₽"),
    ("discount_percent", "Скидка, %"),
    ("rating", "Рейтинг"),
    ("review_count", "Отзывы"),
    ("stock", "Остаток"),
    ("availability", "Наличие"),
    ("sizes", "Размеры"),
    ("url", "Ссылка"),
    ("position", "Позиция в поиске"),
    ("collected_at", "Дата сбора"),
    ("source", "Источник"),
    ("error", "Ошибка"),
]

HEADER_FILL = PatternFill("solid", fgColor="7B1FA2")
HEADER_FONT = Font(bold=True, color="FFFFFF", name="Calibri", size=11)
CELL_FONT = Font(name="Calibri", size=10)
ALT_FILL = PatternFill("solid", fgColor="F6EFFA")
THIN = Border(
    left=Side(style="thin", color="D0D0D0"),
    right=Side(style="thin", color="D0D0D0"),
    top=Side(style="thin", color="D0D0D0"),
    bottom=Side(style="thin", color="D0D0D0"),
)
WIDTHS = {
    "Артикул": 14,
    "Название": 42,
    "Бренд": 18,
    "Продавец": 24,
    "ID продавца": 14,
    "Цена, ₽": 12,
    "Цена со скидкой, ₽": 18,
    "Скидка, %": 11,
    "Рейтинг": 10,
    "Отзывы": 10,
    "Остаток": 11,
    "Наличие": 16,
    "Размеры": 28,
    "Ссылка": 48,
    "Позиция в поиске": 16,
    "Дата сбора": 22,
    "Источник": 22,
    "Ошибка": 28,
}

NUMBER_FORMATS = {
    "Цена, ₽": "#,##0.00",
    "Цена со скидкой, ₽": "#,##0.00",
    "Скидка, %": "0.0",
    "Рейтинг": "0.0",
    "Отзывы": "#,##0",
    "Остаток": "#,##0",
    "ID продавца": "0",
    "Артикул": "0",
    "Позиция в поиске": "0",
}




INT_COLUMNS = {"Артикул", "ID продавца", "Отзывы", "Остаток", "Позиция в поиске",
               "Остаток было", "Δ остаток"}


def _as_int_cols(df: pd.DataFrame) -> pd.DataFrame:
    for col in df.columns:
        if col in INT_COLUMNS:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    return df


def products_to_frame(products: list[Product]) -> pd.DataFrame:
    rows = []
    for p in products:
        data = p.to_row()
        rows.append({title: data.get(key) for key, title in COLUMNS})
    if not rows:
        return pd.DataFrame(columns=[title for _, title in COLUMNS])
    return _as_int_cols(pd.DataFrame(rows, columns=[title for _, title in COLUMNS]))


def write_products(
    products: list[Product],
    path: str | Path,
    also_csv: bool = False,
    sheet_name: str = "Товары",
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df = products_to_frame(products)
    # скрываем пустую колонку «Ошибка», если ошибок нет
    for col in ("Ошибка", "Позиция в поиске"):
        if col in df.columns and (df[col].isna().all() or df[col].fillna("").astype(str).isin(["", "None", "<NA>"]).all()):
            df = df.drop(columns=[col])
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
        _style_sheet(writer.book[sheet_name], df)
    if also_csv:
        csv_path = path.with_suffix(".csv")
        df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    return path


def write_frames(
    path: str | Path,
    sheets: dict[str, pd.DataFrame],
    highlight: Optional[dict] = None,
    also_csv: bool = False,
) -> Path:
    """highlight = {sheet_name: {"rows": [1-based data rows], "cols": [col names], "up_cols": ..., "down_cols": ...}}"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for name, df in sheets.items():
            safe = name[:31]
            df = _as_int_cols(df.copy())
            sheets[name] = df
            df.to_excel(writer, index=False, sheet_name=safe)
            _style_sheet(writer.book[safe], df)
            if highlight and name in highlight:
                _highlight_changes(writer.book[safe], df, highlight[name])
    if also_csv:
        first = next(iter(sheets.values()))
        first.to_csv(path.with_suffix(".csv"), index=False, encoding="utf-8-sig")
    return path


def _style_sheet(ws, df: pd.DataFrame) -> None:
    ws.auto_filter.ref = ws.dimensions
    ws.freeze_panes = "A2"
    ws.row_dimensions[1].height = 22
    for col_idx, col_name in enumerate(df.columns, start=1):
        cell = ws.cell(1, col_idx)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        letter = get_column_letter(col_idx)
        ws.column_dimensions[letter].width = WIDTHS.get(str(col_name), 16)
        fmt = NUMBER_FORMATS.get(str(col_name))
        for row_idx in range(2, ws.max_row + 1):
            c = ws.cell(row_idx, col_idx)
            c.font = CELL_FONT
            c.border = THIN
            c.alignment = Alignment(vertical="center", wrap_text=False)
            if row_idx % 2 == 0:
                if c.fill.fgColor is None or c.fill.fgColor.rgb in (None, "00000000", "00FFFFFF"):
                    c.fill = ALT_FILL
            if fmt:
                c.number_format = fmt
            if str(col_name) == "Ссылка" and c.value:
                c.hyperlink = str(c.value)
                c.font = Font(name="Calibri", size=10, color="1565C0", underline="single")
    ws.auto_filter.ref = ws.dimensions
    ws.sheet_view.showGridLines = False


UP_FILL = PatternFill("solid", fgColor="FFCDD2")  # цена выросла — красный
DOWN_FILL = PatternFill("solid", fgColor="C8E6C9")  # цена упала — зелёный
CHANGE_FILL = PatternFill("solid", fgColor="FFF9C4")  # прочее изменение


def _highlight_changes(ws, df: pd.DataFrame, spec: dict) -> None:
    """Подсвечивает строки/ячейки с изменениями. spec['row_flags'] = list of dicts per data row."""
    flags = spec.get("row_flags") or []
    for i, flag in enumerate(flags):
        excel_row = i + 2
        if not flag:
            continue
        kind = flag.get("kind")  # price_up, price_down, stock_up, stock_down, change, new, gone
        fill = CHANGE_FILL
        if kind in ("price_up", "stock_down", "gone"):
            fill = UP_FILL
        elif kind in ("price_down", "stock_up"):
            fill = DOWN_FILL
        cols = flag.get("cols") or list(df.columns)
        for col_name in cols:
            if col_name not in df.columns:
                continue
            col_idx = list(df.columns).index(col_name) + 1
            ws.cell(excel_row, col_idx).fill = fill
