"""CDN-корзины wbbasket.ru: card.json, sellers.json, расчёт хоста по vol."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

# Верхняя граница vol (включительно) -> номер корзины.
# Собрано из классических диапазонов WB и проверено по image-URL в ноябре 2026.
# Границы сдвигаются, поэтому при промахе включается перебор соседних корзин.
BASKET_MAX_VOL: list[tuple[int, int]] = [
    (143, 1), (287, 2), (431, 3), (575, 4), (719, 5), (1007, 6),
    (1061, 7), (1115, 8), (1313, 9), (1601, 10), (1655, 11),
    (1919, 12), (2045, 13), (2189, 14), (2405, 15), (2621, 16),
    (2837, 17), (3053, 18), (3269, 19), (3485, 20), (3701, 21),
    (3917, 22), (4133, 23), (4349, 24), (4565, 25), (4877, 26),
    (5189, 27), (5501, 28), (5813, 29), (6125, 30), (6437, 31),
    (6749, 32), (7061, 33), (7373, 34), (7685, 35), (7997, 36),
    (8309, 37), (8621, 38), (8933, 39), (9245, 40), (9557, 41),
    (10350, 41), (11150, 42), (11910, 43), (12680, 44), (13450, 45),
    (14213, 46), (14980, 47), (15700, 48), (16500, 49), (17500, 50),
    (18060, 51), (18800, 52), (19550, 53), (20320, 54), (21000, 55),
    (22000, 56), (23000, 57), (24000, 58), (25000, 59), (26000, 60),
]


def vol_of(nm: int) -> int:
    return nm // 100_000


def part_of(nm: int) -> int:
    return nm // 1_000


def guess_basket(vol: int) -> int:
    for max_vol, basket in BASKET_MAX_VOL:
        if vol <= max_vol:
            return basket
    # грубая оценка для очень новых артикулов
    return min(80, 60 + (vol - 26000) // 800)


def basket_url(nm: int, basket: int, path: str) -> str:
    return (
        f"https://basket-{basket:02d}.wbbasket.ru/"
        f"vol{vol_of(nm)}/part{part_of(nm)}/{nm}/{path}"
    )


def card_url(nm: int, basket: int) -> str:
    return basket_url(nm, basket, "info/ru/card.json")


def sellers_url(nm: int, basket: int) -> str:
    return basket_url(nm, basket, "info/sellers.json")


class BasketResolver:
    """Кэширует соответствие vol -> номер корзины, при необходимости зондирует CDN."""

    def __init__(self, cache_path: Optional[Path] = None) -> None:
        self.cache_path = Path(cache_path) if cache_path else Path.home() / ".wb_parser_baskets.json"
        self._vol_to_basket: dict[int, int] = {}
        self._load()

    def _load(self) -> None:
        if self.cache_path.is_file():
            try:
                data = json.loads(self.cache_path.read_text(encoding="utf-8"))
                self._vol_to_basket = {int(k): int(v) for k, v in data.items()}
            except Exception:
                self._vol_to_basket = {}

    def _save(self) -> None:
        try:
            self.cache_path.write_text(
                json.dumps(self._vol_to_basket, ensure_ascii=False, indent=0),
                encoding="utf-8",
            )
        except Exception:
            pass

    def remember(self, vol: int, basket: int) -> None:
        if self._vol_to_basket.get(vol) != basket:
            self._vol_to_basket[vol] = basket
            self._save()

    def candidates(self, vol: int) -> list[int]:
        known = self._vol_to_basket.get(vol)
        guessed = guess_basket(vol)
        ordered: list[int] = []
        for b in ([known, guessed] if known else [guessed]):
            if b and b not in ordered:
                ordered.append(b)
        for delta in range(1, 12):
            for b in (guessed + delta, guessed - delta):
                if 1 <= b <= 80 and b not in ordered:
                    ordered.append(b)
        for b in range(1, 81):
            if b not in ordered:
                ordered.append(b)
        return ordered

    def resolve(self, client: Any, nm: int) -> Optional[int]:
        vol = vol_of(nm)
        if vol in self._vol_to_basket:
            return self._vol_to_basket[vol]
        for basket in self.candidates(vol):
            url = card_url(nm, basket)
            try:
                resp = client.get(url, allow_status=(200, 404), json_accept=True)
            except Exception:
                continue
            if resp.status_code == 200:
                try:
                    data = resp.json()
                except ValueError:
                    continue
                if isinstance(data, dict) and data.get("nm_id"):
                    self.remember(vol, basket)
                    return basket
        return None

    def fetch_card(self, client: Any, nm: int) -> tuple[Optional[int], Optional[dict]]:
        basket = self.resolve(client, nm)
        if not basket:
            return None, None
        url = card_url(nm, basket)
        resp = client.get(url, allow_status=(200, 404))
        if resp.status_code != 200:
            return basket, None
        return basket, resp.json()

    def fetch_sellers(self, client: Any, nm: int, basket: Optional[int] = None) -> Optional[dict]:
        if basket is None:
            basket = self.resolve(client, nm)
        if not basket:
            return None
        url = sellers_url(nm, basket)
        resp = client.get(url, allow_status=(200, 404))
        if resp.status_code != 200:
            return None
        try:
            return resp.json()
        except ValueError:
            return None
