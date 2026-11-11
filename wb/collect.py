"""Сбор товаров: поиск, артикулы, категория. Классические API и запасной путь через napi."""

from __future__ import annotations

import re
import sys
from datetime import datetime
from typing import Iterable, Optional
from urllib.parse import urlparse, unquote
from zoneinfo import ZoneInfo

from .basket import BasketResolver, vol_of
from .client import MOBILE_UA, WbClient
from .products import (
    Product,
    apply_card_json,
    apply_sellers_json,
    from_classic,
    from_detail_html,
    from_napi,
    merge,
    product_url,
)

MOSCOW = ZoneInfo("Europe/Moscow")
DEFAULT_DEST = -1257786  # Москва
MENU_URL = "https://static-basket-01.wbbasket.ru/vol0/data/main-menu-ru-ru-v3.json"

CLASSIC_SEARCH_VERSIONS = ("v18", "v14", "v13", "v5", "v4")
NAPI_SEARCH = "https://napi.wildberries.ru/api/search/results"
NAPI_CATALOG_PREFIX = "https://napi.wildberries.ru/api"
NAPI_DETAIL = "https://napi.wildberries.ru/catalog/{nm}/detail.aspx"
CARD_DETAIL = "https://card.wb.ru/cards/v2/detail"
CLASSIC_CATALOG = "https://catalog.wb.ru/catalog/{shard}/v2/catalog"


def now_moscow() -> str:
    return datetime.now(MOSCOW).strftime("%Y-%m-%d %H:%M:%S %Z")


class WbCollector:
    def __init__(
        self,
        client: Optional[WbClient] = None,
        dest: int = DEFAULT_DEST,
        enrich_seller: bool = True,
        delay: float = 0.45,
        source: str = "auto",
    ) -> None:
        self.client = client or WbClient(delay=delay)
        self.dest = dest
        self.enrich_seller = enrich_seller
        self.source_pref = source
        self.basket = BasketResolver()
        self._classic_ok: Optional[bool] = None
        self._napi_ok: Optional[bool] = None
        self._menu: Optional[list] = None
        self.notes: list[str] = []

    def log(self, message: str) -> None:
        print(message, file=sys.stderr, flush=True)
        self.notes.append(message)

    # ----- capability probes -----

    def classic_available(self) -> bool:
        if self.source_pref == "napi":
            return False
        if self.source_pref == "classic":
            self._classic_ok = True
            return True
        if self._classic_ok is not None:
            return self._classic_ok
        url = f"https://search.wb.ru/exactmatch/ru/common/v18/search"
        params = {
            "appType": 1,
            "curr": "rub",
            "dest": self.dest,
            "lang": "ru",
            "page": 1,
            "query": "платье",
            "resultset": "catalog",
            "sort": "popular",
            "spp": 30,
        }
        try:
            resp = self.client.get(url, params=params, impersonate=True, allow_status=(200, 403, 429, 498))
            if resp.status_code == 200:
                data = resp.json()
                products = _classic_products(data)
                self._classic_ok = bool(products)
            else:
                self._classic_ok = False
        except Exception:
            self._classic_ok = False
        if not self._classic_ok:
            self.log(
                "Классические API search.wb.ru / card.wb.ru недоступны с этого IP "
                "(WBAAS 403/498). Переключаюсь на napi.wildberries.ru и CDN wbbasket.ru."
            )
        else:
            self.log("Классические API search.wb.ru / card.wb.ru отвечают, использую их.")
        return self._classic_ok

    # ----- search -----

    def search(self, query: str, pages: int = 1) -> list[Product]:
        pages = max(1, int(pages))
        collected_at = now_moscow()
        if self.classic_available():
            items = self._search_classic(query, pages)
        else:
            items = self._search_napi(query, pages)
        for item in items:
            item.collected_at = collected_at
        if self.enrich_seller:
            self._enrich_sellers(items)
        return items

    def _search_classic(self, query: str, pages: int) -> list[Product]:
        items: list[Product] = []
        first_id = None
        version_used = None
        for page in range(1, pages + 1):
            data = None
            versions = CLASSIC_SEARCH_VERSIONS if version_used is None else (version_used,)
            last_err = None
            for ver in versions:
                url = f"https://search.wb.ru/exactmatch/ru/common/{ver}/search"
                params = {
                    "appType": 1,
                    "curr": "rub",
                    "dest": self.dest,
                    "lang": "ru",
                    "page": page,
                    "query": query,
                    "resultset": "catalog",
                    "sort": "popular",
                    "spp": 30,
                }
                try:
                    resp = self.client.get(url, params=params, impersonate=True, allow_status=(200, 403, 404, 429))
                    if resp.status_code != 200:
                        last_err = f"HTTP {resp.status_code}"
                        continue
                    payload = resp.json()
                    products = _classic_products(payload)
                    if products is None:
                        last_err = "нет products"
                        continue
                    version_used = ver
                    data = products
                    break
                except Exception as exc:
                    last_err = str(exc)
                    continue
            if data is None:
                self.log(f"Поиск classic, страница {page}: {last_err or 'ошибка'}")
                break
            if not data:
                break
            page_first = data[0].get("id")
            if page > 1 and first_id is not None and page_first == first_id:
                self.log(f"Страница {page} повторяет первую (особенность пагинации WB) — останавливаюсь.")
                break
            if page == 1:
                first_id = page_first
            for i, raw in enumerate(data):
                pos = (page - 1) * 100 + i + 1
                items.append(from_classic(raw, position=pos, source=f"search.wb.ru/{version_used}"))
            self.log(f"Поиск classic «{query}»: страница {page}, товаров {len(data)}")
        return items

    def _search_napi(self, query: str, pages: int) -> list[Product]:
        items: list[Product] = []
        total_pages = pages
        for page in range(1, pages + 1):
            if page > total_pages:
                break
            params = {"search": query, "page": page, "pagesize": 100, "sort": "popular"}
            try:
                payload = self.client.get_json(
                    NAPI_SEARCH,
                    params=params,
                    referer="https://www.wildberries.ru/catalog/0/search.aspx",
                )
            except Exception as exc:
                self.log(f"Поиск napi, страница {page}: {exc}")
                break
            data = payload.get("data") or {}
            if payload.get("state") == -1:
                self.log(f"Поиск napi: {data.get('errorMsg') or 'пустой ответ'}")
                break
            pager = data.get("pager") or {}
            if pager.get("totalPages"):
                total_pages = min(pages, int(pager["totalPages"]))
            products = data.get("products") or []
            if not products:
                break
            if products and not isinstance(products[0], dict):
                # на всякий случай, если пришли только id
                products = self._hydrate_ids(products, page)
                items.extend(products)
                continue
            page_size = int(pager.get("pageSize") or 100)
            for i, raw in enumerate(products):
                pos = (page - 1) * page_size + i + 1
                _remember_basket_from_napi(self.basket, raw)
                items.append(from_napi(raw, position=pos, source="napi.search"))
            self.log(
                f"Поиск napi «{query}»: страница {page}/{pager.get('totalPages') or '?'}, "
                f"товаров {len(products)}"
            )
        if total_pages < pages:
            self.log(
                f"napi.wildberries.ru отдаёт максимум {total_pages} страниц по этому запросу "
                f"(обычно до 5, по 100 товаров)."
            )
        return items

    # ----- articles -----

    def by_articles(self, nms: Iterable[int]) -> list[Product]:
        ids = [int(x) for x in nms]
        collected_at = now_moscow()
        if self.classic_available():
            items = self._articles_classic(ids)
        else:
            items = [self._article_fallback(nm) for nm in ids]
        for item in items:
            item.collected_at = collected_at
        return items

    def _articles_classic(self, ids: list[int]) -> list[Product]:
        items: list[Product] = []
        # card.wb.ru принимает пачку nm через ;
        chunk_size = 50
        raw_by_id: dict[int, dict] = {}
        for i in range(0, len(ids), chunk_size):
            chunk = ids[i : i + chunk_size]
            params = {
                "appType": 1,
                "curr": "rub",
                "dest": self.dest,
                "spp": 30,
                "nm": ";".join(str(x) for x in chunk),
            }
            try:
                resp = self.client.get(CARD_DETAIL, params=params, impersonate=True, allow_status=(200, 403, 404))
                if resp.status_code != 200:
                    self.log(f"card.wb.ru HTTP {resp.status_code} для {len(chunk)} артикулов")
                    continue
                products = _classic_products(resp.json()) or []
                for raw in products:
                    rid = int(raw.get("id") or 0)
                    if rid:
                        raw_by_id[rid] = raw
            except Exception as exc:
                self.log(f"card.wb.ru: {exc}")
        for nm in ids:
            if nm in raw_by_id:
                items.append(from_classic(raw_by_id[nm], source="card.wb.ru"))
            else:
                self.log(f"Артикул {nm} не найден в card.wb.ru, пробую запасной путь")
                items.append(self._article_fallback(nm))
        if self.enrich_seller:
            self._enrich_sellers(items)
        return items

    def _article_fallback(self, nm: int) -> Product:
        product = Product(nm_id=nm, url=product_url(nm), source="napi.detail+wbbasket")
        # 1) HTML/JSON-LD карточка — цена, скидка, рейтинг, наличие
        try:
            resp = self.client.get(
                NAPI_DETAIL.format(nm=nm),
                json_accept=False,
                referer=product_url(nm),
                allow_status=(200, 404),
            )
            if resp.status_code == 200 and ("ld+json" in resp.text or "price-block" in resp.text or "<title>" in resp.text):
                product = merge(product, from_detail_html(nm, resp.text))
            elif resp.status_code != 200:
                product.error = f"карточка HTTP {resp.status_code}"
        except Exception as exc:
            product.error = f"карточка: {exc}"

        # 2) card.json + sellers.json с CDN
        try:
            basket, card = self.basket.fetch_card(self.client, nm)
            if card:
                apply_card_json(product, card)
                sellers = self.basket.fetch_sellers(self.client, nm, basket)
                if sellers:
                    apply_sellers_json(product, sellers)
            elif not product.name:
                product.error = product.error or "не найден на CDN wbbasket.ru"
        except Exception as exc:
            if not product.name:
                product.error = product.error or f"CDN: {exc}"

        product.source = "napi.detail+wbbasket"
        return product.finalize()

    # ----- category -----

    def category(self, url_or_path: str, pages: int = 1) -> list[Product]:
        pages = max(1, int(pages))
        collected_at = now_moscow()
        info = self.resolve_category(url_or_path)
        if not info:
            self.log(f"Не удалось разобрать категорию: {url_or_path}")
            return []
        self.log(f"Категория: {info.get('name') or info.get('path')} (id={info.get('id')})")

        items: list[Product] = []
        if self.classic_available() and info.get("shard") and info.get("query"):
            items = self._category_classic(info, pages)
        if not items:
            # searchQuery из меню даёт полноценные карточки через napi search
            if info.get("searchQuery"):
                items = self._search_napi(info["searchQuery"], pages)
                for it in items:
                    if it.source == "napi.search":
                        it.source = "napi.category-search"
            if not items and info.get("path"):
                items = self._category_napi(info, pages)
        for item in items:
            item.collected_at = collected_at
        if self.enrich_seller:
            self._enrich_sellers(items)
        return items

    def _category_classic(self, info: dict, pages: int) -> list[Product]:
        items: list[Product] = []
        shard = info["shard"]
        query = info["query"]
        first_id = None
        for page in range(1, pages + 1):
            url = CLASSIC_CATALOG.format(shard=shard)
            # query уже вида cat=8137 или subject=...
            extra = f"appType=1&curr=rub&dest={self.dest}&lang=ru&page={page}&sort=popular&spp=30"
            full = f"{url}?{query}&{extra}"
            try:
                resp = self.client.get(full, impersonate=True, allow_status=(200, 403, 404))
                if resp.status_code != 200:
                    self.log(f"catalog.wb.ru HTTP {resp.status_code}, страница {page}")
                    break
                products = _classic_products(resp.json()) or []
            except Exception as exc:
                self.log(f"catalog.wb.ru: {exc}")
                break
            if not products:
                break
            page_first = products[0].get("id")
            if page > 1 and first_id is not None and page_first == first_id:
                break
            if page == 1:
                first_id = page_first
            for i, raw in enumerate(products):
                pos = (page - 1) * 100 + i + 1
                items.append(from_classic(raw, position=pos, source="catalog.wb.ru"))
            self.log(f"Каталог classic: страница {page}, товаров {len(products)}")
        return items

    def _category_napi(self, info: dict, pages: int) -> list[Product]:
        items: list[Product] = []
        path = info["path"]
        url = NAPI_CATALOG_PREFIX + path
        params: dict = {"page": 1, "pagesize": 100, "sort": "popular"}
        if info.get("query"):
            for part in info["query"].split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[k] = v
        total_pages = pages
        for page in range(1, pages + 1):
            if page > total_pages:
                break
            params["page"] = page
            try:
                payload = self.client.get_json(url, params=params)
            except Exception as exc:
                self.log(f"napi catalog, страница {page}: {exc}")
                break
            data = payload.get("data") or {}
            pager = data.get("pager") or {}
            if pager.get("totalPages"):
                total_pages = min(pages, int(pager["totalPages"]))
            products = data.get("products") or []
            if not products:
                self.log(f"napi catalog «{path}»: пустая страница {page}")
                break
            if isinstance(products[0], int):
                page_items = []
                page_size = int(pager.get("pageSize") or 100)
                for i, nm in enumerate(products):
                    p = self._article_fallback(int(nm))
                    p.position = (page - 1) * page_size + i + 1
                    page_items.append(p)
                items.extend(page_items)
                self.log(f"napi catalog «{path}»: страница {page}, id={len(products)}, карточки обогащены")
            else:
                page_size = int(pager.get("pageSize") or 100)
                for i, raw in enumerate(products):
                    pos = (page - 1) * page_size + i + 1
                    items.append(from_napi(raw, position=pos, source="napi.catalog"))
                self.log(f"napi catalog «{path}»: страница {page}, товаров {len(products)}")
        return items

    def _hydrate_ids(self, ids: list, page: int) -> list[Product]:
        out = []
        for i, nm in enumerate(ids):
            p = self._article_fallback(int(nm))
            p.position = (page - 1) * 100 + i + 1
            out.append(p)
        return out

    # ----- menu / category URL -----

    def load_menu(self) -> list:
        if self._menu is None:
            self._menu = self.client.get_json(MENU_URL)
        return self._menu

    def resolve_category(self, url_or_path: str) -> Optional[dict]:
        path = _normalize_catalog_path(url_or_path)
        if not path:
            return None
        try:
            menu = self.load_menu()
        except Exception as exc:
            self.log(f"Не удалось загрузить меню WB: {exc}")
            menu = []
        match = _find_menu_path(menu, path)
        if match:
            return {
                "id": match.get("id"),
                "name": match.get("name") or match.get("seo"),
                "path": match.get("url") or path,
                "shard": match.get("shard") or "",
                "query": match.get("query") or "",
                "searchQuery": match.get("searchQuery") or "",
            }
        return {"id": None, "name": path, "path": path, "shard": "", "query": "", "searchQuery": ""}

    # ----- enrich -----

    def _enrich_sellers(self, items: list[Product]) -> None:
        missing = [p for p in items if p.nm_id and (not p.seller_name or p.seller_id is None)]
        if not missing:
            return
        self.log(f"Дополняю продавца из wbbasket sellers.json для {len(missing)} товаров...")
        for p in missing:
            try:
                sellers = self.basket.fetch_sellers(self.client, p.nm_id)
                if sellers:
                    apply_sellers_json(p, sellers)
            except Exception:
                continue


def _classic_products(payload: Any) -> Optional[list]:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    products = data.get("products")
    if products is None and isinstance(payload.get("products"), list):
        products = payload["products"]
    if products is None:
        return None
    return products


def _normalize_catalog_path(url_or_path: str) -> str:
    raw = (url_or_path or "").strip()
    if not raw:
        return ""
    if raw.startswith("http://") or raw.startswith("https://"):
        parsed = urlparse(raw)
        path = unquote(parsed.path or "")
    else:
        path = raw if raw.startswith("/") else "/catalog/" + raw.lstrip("/")
    path = path.rstrip("/")
    if not path.startswith("/catalog/"):
        if path.startswith("catalog/"):
            path = "/" + path
        else:
            path = "/catalog/" + path.lstrip("/")
    return path


def _find_menu_path(nodes: list, path: str) -> Optional[dict]:
    target = path.rstrip("/")
    for node in nodes or []:
        url = (node.get("url") or "").rstrip("/")
        if url == target:
            return node
        childs = node.get("childs") or node.get("children") or []
        if childs:
            found = _find_menu_path(childs, path)
            if found:
                return found
    return None



def _remember_basket_from_napi(resolver: BasketResolver, raw: dict) -> None:
    url = raw.get("hdImageUrl") or raw.get("imageUrl") or ""
    m = re.search(r"basket-(\d+)\.wbbasket\.ru/vol(\d+)/", url)
    if m:
        resolver.remember(int(m.group(2)), int(m.group(1)))

def parse_article_list(text: str) -> list[int]:
    """Разбирает артикулы из строки, файла или CSV: 1,2 3;4."""
    ids: list[int] = []
    seen = set()
    for token in re.split(r"[\s,;]+", text.strip()):
        token = token.strip()
        if not token:
            continue
        m = re.search(r"(\d{5,15})", token)
        if not m:
            continue
        nm = int(m.group(1))
        if nm not in seen:
            seen.add(nm)
            ids.append(nm)
    return ids
