# Парсер Wildberries → Excel

Сбор данных с публичных API Wildberries в таблицу Excel (и при желании CSV).
Подходит для кворков «парсер WB в Excel» и «мониторинг цен и остатков конкурентов».

Режимы:

- поиск по запросу (первые N страниц, с позицией в выдаче);
- список артикулов (nm);
- категория по URL каталога WB (если категория находится в меню сайта).

По каждому товару: артикул, название, бренд, продавец (имя и id), цена, цена со скидкой, скидка %, рейтинг, число отзывов, остаток (если API его отдаёт), наличие, размеры, ссылка, позиция в поиске.

## Установка

Нужен Python 3.10 или новее.

```bash
cd wb_parser
python3 -m venv .venv
# Windows:
# .venv\Scripts\activate
# Linux / macOS:
source .venv/bin/activate
pip install -r requirements.txt
```

Пакет `curl_cffi` желателен: с ним запросы к `search.wb.ru` и `card.wb.ru` чаще проходят антибот WBAAS. Без него парсер тоже работает, но с части датацентровых IP эти хосты отвечают 403.

## Как это устроено

Парсер сам проверяет, какие эндпоинты доступны с вашего IP.

1. **Классические JSON API** (предпочтительно, если отвечают):
   - поиск: `https://search.wb.ru/exactmatch/ru/common/v18/search`
   - карточки: `https://card.wb.ru/cards/v2/detail`
   - каталог: `https://catalog.wb.ru/catalog/{shard}/v2/catalog`
   - цены в **копейках** (`priceU`, `salePriceU`), делятся на 100;
   - остатки суммируются по размерам и складам (`sizes[].stocks[].qty`).

2. **Запасной путь** (проверен, когда классические API закрыты WBAAS):
   - поиск и категории: `https://napi.wildberries.ru/api/search/results` и `https://napi.wildberries.ru/api/catalog/...`;
   - карточка по артикулу: HTML/JSON-LD `https://napi.wildberries.ru/catalog/{nm}/detail.aspx`;
   - описание, бренд, продавец: CDN `https://basket-XX.wbbasket.ru/vol{vol}/part{part}/{nm}/info/ru/card.json` и `info/sellers.json`;
   - меню категорий: `https://static-basket-01.wbbasket.ru/vol0/data/main-menu-ru-ru-v3.json`.

С части IP (в том числе многих серверных) `search.wb.ru`, `card.wb.ru` и сам `www.wildberries.ru` отвечают 403/498. Тогда автоматически включается запасной путь. Точный складской остаток в этом режиме часто **недоступен** — в таблице будут цена, наличие (в наличии / нет в наличии), рейтинг и продавец. С домашнего российского IP классические API обычно отдают и остатки.

Поиск через napi отдаёт не больше **5 страниц** по 100 товаров. Классический поиск может отдать больше, но у WB пагинация иногда «зацикливается» — парсер это детектирует и останавливается.

Между запросами стоит пауза (по умолчанию 0.45 с), при ошибках 429/5xx — повтор с увеличением интервала, используется обычный браузерный User-Agent.

## Использование

```bash
# Справка
python parse.py --help
python parse.py search --help
python monitor.py --help

# Поиск: 2 страницы, Excel + CSV
python parse.py search "платье женское" --pages 2 -o platya.xlsx --csv

# Список артикулов
python parse.py articles 1464760255,550053815 -o cards.xlsx

# Артикулы из файла (через запятую, пробел или с новой строки)
python parse.py articles --file artikuly.txt -o cards.xlsx --csv

# Категория по URL
python parse.py category "https://www.wildberries.ru/catalog/zhenshchinam/odezhda" --pages 2 -o cat.xlsx

# Другой регион и более редкие запросы
python parse.py search "кроссовки" --dest=-1257786 --delay 0.8 -o out.xlsx
```

Регион `dest=-1257786` — Москва, влияет на цену и наличие в классическом API.

## Мониторинг конкурентов

```bash
python monitor.py --ids 1464760255,550053815 -o report.xlsx --history history.json
python monitor.py --file competitors.txt -o report.xlsx --history history.json
```

Каждый запуск:

1. Собирает актуальные цены и остатки по списку артикулов.
2. Дописывает снимок в `--history` (JSON).
3. Сравнивает с предыдущим запуском.
4. Пишет Excel с листами **«Текущий снимок»** и **«Изменения»**. Рост цены или падение остатка подсвечивается красным, снижение цены — зелёным.

Первый запуск только создаёт точку отсчёта, сравнивать ещё не с чем.

### Telegram-уведомления

Парсер **ничего не отправляет сам**, пока вы явно не укажете `--telegram` и не зададите токен с chat id.

```bash
export TELEGRAM_BOT_TOKEN="123456:ABC..."
export TELEGRAM_CHAT_ID="123456789"
python monitor.py --ids 1464760255,550053815 --telegram -o report.xlsx
```

Либо флаги `--telegram-token` и `--telegram-chat-id`. Сообщение — короткий текст: кто изменил цену или остаток.

Как получить chat id: напишите боту, затем откройте `https://api.telegram.org/bot<ТОКЕН>/getUpdates`.

## Расписание

### Linux / macOS: cron

Каждый день в 9:00 (системный часовой пояс сервера; если нужна Москва, поставьте `TZ=Europe/Moscow`):

```cron
0 9 * * * TZ=Europe/Moscow cd /home/you/wb_parser && /home/you/wb_parser/.venv/bin/python monitor.py --file /home/you/wb_parser/competitors.txt --history /home/you/wb_parser/history.json -o /home/you/wb_parser/report.xlsx --telegram >> /home/you/wb_parser/monitor.log 2>&1
```

Редактирование: `crontab -e`.

### Windows: Планировщик заданий

1. Откройте «Планировщик заданий» → «Создать задачу».
2. Триггер: ежедневно в нужное время.
3. Действие: «Запуск программы».
   - Программа: `C:\path\to\wb_parser\.venv\Scripts\python.exe`
   - Аргументы: `monitor.py --file competitors.txt --history history.json -o report.xlsx`
   - Рабочая папка: `C:\path\to\wb_parser`
4. При необходимости добавьте переменные `TELEGRAM_BOT_TOKEN` и `TELEGRAM_CHAT_ID` в «Параметры среды» задачи или в аргументы `--telegram --telegram-token ...`.

## Примеры файлов

После проверки в каталоге `examples/` лежат реальные выгрузки:

- `examples/search_platye_zhenskoe.xlsx` — поиск «платье женское», 2 страницы;
- `examples/articles_sample.xlsx` — несколько артикулов;
- `examples/monitor_report.xlsx` — отчёт мониторинга с листом изменений.

## Ограничения

- Это неофициальные публичные эндпоинты витрины, не Seller API. WB может сменить формат или включить защиту.
- С IP, попавших под WBAAS, классические `search.wb.ru` / `card.wb.ru` недоступны; запасной путь работает, но без точного остатка по складам.
- Не запрашивайте слишком часто: делайте паузы, не ставьте cron чаще, чем нужно (раз в несколько часов обычно достаточно).
- Цены зависят от региона (`--dest`) и персональных скидок; с публичного API вы видите «витринную» цену, не кабинет селлера.

## Лицензия

Код для самостоятельной работы и услуг на фрилансе. Соблюдайте правила Wildberries и не перегружайте их серверы.
