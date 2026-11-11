"""Проверка: видит ли WB витринную цену (с СПП) с вашего IP. Только stdlib.
Запуск: python spp_check.py  -> печатает сводку и пишет spp_check_result.json"""
import json, urllib.request, urllib.parse, statistics, sys

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
      "Accept": "*/*", "Origin": "https://www.wildberries.ru", "Referer": "https://www.wildberries.ru/"}
DEST = "-1257786"  # Москва
QUERIES = sys.argv[1:] or ["платье женское", "кроссовки мужские", "чехол для телефона"]
log = {"ip": None, "requests": [], "items": []}

def get(url):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read().decode("utf-8", "replace")
            log["requests"].append({"url": url[:150], "status": r.status})
            return json.loads(body)
    except Exception as e:
        log["requests"].append({"url": url[:150], "error": str(e)[:200]})
        print("  !", url[:90], "->", str(e)[:120])
        return None

def main():
    ip = get("https://api.ipify.org?format=json"); log["ip"] = ip and ip.get("ip")
    print("Ваш IP:", log["ip"])

    for q in QUERIES:
        print(f"\n=== {q}")
        s = get("https://search.wb.ru/exactmatch/ru/common/v13/search?" + urllib.parse.urlencode(
            {"appType": 1, "curr": "rub", "dest": DEST, "query": q, "resultset": "catalog", "page": 1, "spp": 30}))
        prods = (s or {}).get("products") or ((s or {}).get("data") or {}).get("products") or []
        nms = [p["id"] for p in prods[:20]]
        print("  поиск:", len(prods), "товаров")
        if not nms: continue
        c = get("https://card.wb.ru/cards/v4/detail?" + urllib.parse.urlencode(
            {"appType": 1, "curr": "rub", "dest": DEST, "nm": ";".join(map(str, nms))}))
        cards = (c or {}).get("products") or ((c or {}).get("data") or {}).get("products") or []
        disc = []
        for p in cards:
            pr = next((sz.get("price") for sz in p.get("sizes", []) if sz.get("price")), None)
            if not pr: continue
            basic, product = pr.get("basic", 0) / 100, pr.get("product", 0) / 100
            item = {"query": q, "nm": p["id"], "name": p.get("name"), "subject": p.get("subjectId"),
                    "basic": basic, "product": product, "raw_price": pr}
            log["items"].append(item)
            if basic: disc.append(1 - product / basic)
            print(f"  {p['id']:>12}  до скидок {basic:>8.0f}  витрина {product:>8.0f}  {p.get('name','')[:40]}")
        if disc:
            print(f"  общая скидка до витрины: медиана {statistics.median(disc)*100:.1f}%")

    json.dump(log, open("spp_check_result.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\nГотово. Пришлите файл spp_check_result.json")

if __name__ == "__main__":
    main()
