"""Модель товара и разбор ответов WB (классический JSON, napi, HTML/JSON-LD)."""

from __future__ import annotations

import html as htmlmod
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

PRODUCT_URL = "https://www.wildberries.ru/catalog/{nm}/detail.aspx"


def kopecks_to_rub(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return round(int(value) / 100.0, 2)
    except (TypeError, ValueError):
        try:
            return round(float(value) / 100.0, 2)
        except (TypeError, ValueError):
            return None


def as_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = htmlmod.unescape(value)
        value = (
            value.replace("\xa0", " ")
            .replace(" ", "")
            .replace("₽", "")
            .replace("%", "")
            .replace(",", ".")
        )
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        f = as_float(value)
        return int(f) if f is not None else None


def product_url(nm: int) -> str:
    return PRODUCT_URL.format(nm=nm)


def discount_of(price: Optional[float], sale: Optional[float]) -> Optional[float]:
    if price is None or sale is None or price <= 0:
        return None
    if sale >= price:
        return 0.0
    return round((1 - sale / price) * 100, 1)


@dataclass
class Product:
    nm_id: int
    name: str = ""
    brand: str = ""
    seller_name: str = ""
    seller_id: Optional[int] = None
    price: Optional[float] = None
    sale_price: Optional[float] = None
    discount_percent: Optional[float] = None
    rating: Optional[float] = None
    review_count: Optional[int] = None
    stock: Optional[int] = None
    availability: str = ""
    sizes: str = ""
    url: str = ""
    position: Optional[int] = None
    error: str = ""
    source: str = ""
    collected_at: str = ""

    def finalize(self) -> "Product":
        if not self.url and self.nm_id:
            self.url = product_url(self.nm_id)
        if self.discount_percent is None:
            self.discount_percent = discount_of(self.price, self.sale_price)
        if self.sale_price is None and self.price is not None:
            self.sale_price = self.price
        if not self.availability:
            if self.stock is not None:
                self.availability = "В наличии" if self.stock > 0 else "Нет в наличии"
        return self

    def to_row(self) -> dict[str, Any]:
        self.finalize()
        return asdict(self)


# ----- классический ответ card.wb.ru / search.wb.ru -----

def _sizes_and_stock_classic(product: dict) -> tuple[str, Optional[int]]:
    sizes = product.get("sizes") or []
    names: list[str] = []
    total = 0
    has_qty = False
    for size in sizes:
        if not isinstance(size, dict):
            continue
        name = size.get("origName") or size.get("name") or size.get("techSize") or ""
        qty = 0
        for st in size.get("stocks") or []:
            if isinstance(st, dict) and st.get("qty") is not None:
                qty += int(st["qty"])
                has_qty = True
        if size.get("qty") is not None:
            try:
                qty = max(qty, int(size["qty"]))
                has_qty = True
            except (TypeError, ValueError):
                pass
        total += qty
        label = str(name) if name else ""
        if has_qty and label:
            label = f"{label}({qty})"
        if label:
            names.append(label)
    return ", ".join(names), (total if has_qty else None)


def from_classic(product: dict, position: Optional[int] = None, source: str = "card.wb.ru") -> Product:
    nm = int(product.get("id") or product.get("nm_id") or product.get("nm") or 0)
    price = kopecks_to_rub(product.get("priceU") if product.get("priceU") is not None else product.get("price"))
    sale = kopecks_to_rub(
        product.get("salePriceU") if product.get("salePriceU") is not None else product.get("salePrice")
    )
    sale_pct = product.get("sale")
    sizes, stock = _sizes_and_stock_classic(product)
    rating = product.get("reviewRating")
    if rating is None:
        rating = product.get("rating")
    # WB иногда отдаёт rating в шкале 0–5 или 0–10; reviewRating обычно 0–5
    rating_f = as_float(rating)
    if rating_f is not None and rating_f > 5.5:
        rating_f = round(rating_f / 2.0, 2) if rating_f <= 10 else rating_f
    seller = product.get("supplier") or product.get("supplierName") or ""
    seller_id = as_int(product.get("supplierId") or product.get("supplier_id"))
    total_qty = product.get("totalQuantity") or product.get("volume")
    if stock is None and total_qty is not None:
        stock = as_int(total_qty)
    return Product(
        nm_id=nm,
        name=str(product.get("name") or product.get("imt_name") or ""),
        brand=str(product.get("brand") or product.get("brandName") or ""),
        seller_name=str(seller),
        seller_id=seller_id,
        price=price,
        sale_price=sale,
        discount_percent=as_float(sale_pct) if sale_pct is not None else None,
        rating=rating_f,
        review_count=as_int(product.get("feedbacks") or product.get("feedbackCount")),
        stock=stock,
        sizes=sizes,
        url=product_url(nm) if nm else "",
        position=position,
        source=source,
    ).finalize()


# ----- napi.wildberries.ru/api/search/results -----

def _sizes_napi(sizes: Any) -> str:
    if not sizes:
        return ""
    if isinstance(sizes, dict):
        # {"chrt_id": "XS", ...} — порядок как в выдаче
        return ", ".join(str(v) for v in sizes.values() if v)
    if isinstance(sizes, list):
        parts = []
        for item in sizes:
            if isinstance(item, dict):
                parts.append(str(item.get("name") or item.get("techSize") or item.get("origName") or ""))
            else:
                parts.append(str(item))
        return ", ".join(p for p in parts if p)
    return str(sizes)


def from_napi(product: dict, position: Optional[int] = None, source: str = "napi.search") -> Product:
    nm = int(product.get("cod1S") or product.get("nm_id") or product.get("id") or 0)
    price = as_float(product.get("rawPrice"))
    sale = as_float(product.get("rawSalePrice"))
    return Product(
        nm_id=nm,
        name=str(product.get("name") or ""),
        brand=str(product.get("brandName") or product.get("brand") or ""),
        price=price,
        sale_price=sale,
        discount_percent=as_float(product.get("sale")),
        rating=as_float(product.get("mark") or product.get("rating")),
        review_count=as_int(product.get("feedbackCount") or product.get("feedbacks")),
        sizes=_sizes_napi(product.get("sizes")),
        url=product_url(nm) if nm else "",
        position=position,
        source=source,
    ).finalize()


# ----- HTML карточки napi /catalog/{nm}/detail.aspx -----

_RE_LD = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)
_RE_FINAL = re.compile(
    r'class="[^"]*price-block__final-price[^"]*"[^>]*>\s*([^<]+)',
    re.I,
)
_RE_OLD = re.compile(
    r'class="[^"]*price-block__old-price[^"]*"[^>]*>\s*([^<]+)',
    re.I,
)
_RE_SALE = re.compile(r"Скидка\s+(\d+)\s*%", re.I)
_RE_SELLER = re.compile(
    r'aside-info__item--seller[^>]*>\s*<span>\s*([^<]+)\s*</span>',
    re.I | re.S,
)
_RE_SIZE = re.compile(
    r'class="sizes-list__size"[^>]*>\s*([^<]+)\s*</span>(?:\s*<span class="sizes-list__size-ru"[^>]*>\s*([^<]+)\s*</span>)?',
    re.I,
)
_RE_TITLE_PRICE = re.compile(r"купить за\s+([\d\s\xa0]+)\s*₽", re.I)


def _parse_rub_text(text: str) -> Optional[float]:
    text = htmlmod.unescape(text or "")
    digits = re.sub(r"[^\d.,]", "", text.replace("\xa0", "").replace(" ", ""))
    if not digits:
        return None
    return as_float(digits)


def from_detail_html(nm: int, html: str, source: str = "napi.detail") -> Product:
    p = Product(nm_id=nm, url=product_url(nm), source=source)
    ld_product: dict[str, Any] = {}
    for raw in _RE_LD.findall(html or ""):
        try:
            import json

            data = json.loads(htmlmod.unescape(raw.strip()))
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and item.get("@type") == "Product":
                ld_product = item
                break
        if ld_product:
            break

    if ld_product:
        p.name = str(ld_product.get("name") or "")
        brand = ld_product.get("brand") or {}
        if isinstance(brand, dict):
            p.brand = str(brand.get("name") or "")
        elif brand:
            p.brand = str(brand)
        offers = ld_product.get("offers") or {}
        if isinstance(offers, dict):
            p.sale_price = as_float(offers.get("price"))
            avail = str(offers.get("availability") or "")
            if "OutOfStock" in avail or "SoldOut" in avail:
                p.availability = "Нет в наличии"
                if p.stock is None:
                    p.stock = 0
            elif "InStock" in avail:
                p.availability = "В наличии"
        rating = ld_product.get("aggregateRating") or {}
        if isinstance(rating, dict):
            p.rating = as_float(rating.get("ratingValue"))
            p.review_count = as_int(rating.get("reviewCount") or rating.get("ratingCount"))

    final = _RE_FINAL.search(html or "")
    old = _RE_OLD.search(html or "")
    if final:
        p.sale_price = _parse_rub_text(final.group(1)) or p.sale_price
    if old:
        p.price = _parse_rub_text(old.group(1)) or p.price
    if p.price is None and p.sale_price is not None:
        # нет зачёркнутой — цена без скидки совпадает с текущей
        p.price = p.sale_price
    sale_m = _RE_SALE.search(html or "")
    if sale_m:
        p.discount_percent = as_float(sale_m.group(1))
    seller_m = _RE_SELLER.search(html or "")
    if seller_m:
        p.seller_name = htmlmod.unescape(seller_m.group(1)).strip()
    sizes = []
    for m in _RE_SIZE.finditer(html or ""):
        tech = htmlmod.unescape(m.group(1)).strip()
        ru = htmlmod.unescape(m.group(2)).strip() if m.group(2) else ""
        sizes.append(f"{tech}/{ru}" if ru and ru != tech else tech)
    if sizes:
        p.sizes = ", ".join(sizes)
    if not p.sale_price:
        title_m = _RE_TITLE_PRICE.search(html or "")
        if title_m:
            p.sale_price = _parse_rub_text(title_m.group(1))
    if not p.name:
        tm = re.search(r"<title>(.*?)</title>", html or "", re.I | re.S)
        if tm:
            title = htmlmod.unescape(re.sub(r"\s+", " ", tm.group(1))).strip()
            title = re.sub(r"\s+\d+\s+купить за.*$", "", title)
            p.name = title
    return p.finalize()


def merge(base: Product, extra: Product) -> Product:
    """Дополняет base полями из extra, если они пустые, либо extra точнее (рейтинг)."""
    for field_name in (
        "name",
        "brand",
        "seller_name",
        "sizes",
        "availability",
        "url",
        "source",
    ):
        if not getattr(base, field_name) and getattr(extra, field_name):
            setattr(base, field_name, getattr(extra, field_name))
    for field_name in (
        "seller_id",
        "price",
        "sale_price",
        "discount_percent",
        "rating",
        "review_count",
        "stock",
    ):
        if getattr(base, field_name) is None and getattr(extra, field_name) is not None:
            setattr(base, field_name, getattr(extra, field_name))
    # JSON-LD рейтинг обычно точнее, чем целая mark из поиска
    if extra.rating is not None and (base.rating is None or (base.rating == int(base.rating) and extra.rating != base.rating)):
        if extra.source.endswith("detail") or extra.source.endswith("ld"):
            base.rating = extra.rating
            if extra.review_count is not None:
                base.review_count = extra.review_count
    if extra.seller_id is not None:
        base.seller_id = extra.seller_id
    if extra.seller_name and not base.seller_name:
        base.seller_name = extra.seller_name
    return base.finalize()


def apply_card_json(product: Product, card: dict) -> Product:
    if not card:
        return product
    product.name = product.name or str(card.get("imt_name") or "")
    selling = card.get("selling") or {}
    if isinstance(selling, dict):
        product.brand = product.brand or str(selling.get("brand_name") or "")
        if product.seller_id is None:
            product.seller_id = as_int(selling.get("supplier_id"))
    sizes_table = card.get("sizes_table") or {}
    values = sizes_table.get("values") if isinstance(sizes_table, dict) else None
    if values and not product.sizes:
        product.sizes = ", ".join(
            str(v.get("tech_size") or "") for v in values if isinstance(v, dict) and v.get("tech_size")
        )
    return product.finalize()


def apply_sellers_json(product: Product, sellers: dict) -> Product:
    if not sellers:
        return product
    product.seller_id = product.seller_id or as_int(sellers.get("supplierId"))
    product.seller_name = product.seller_name or str(
        sellers.get("supplierName") or sellers.get("trademark") or sellers.get("supplierFullName") or ""
    )
    return product.finalize()
