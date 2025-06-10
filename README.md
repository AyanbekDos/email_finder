# Telegram Email Scraper Bot

Бот для Telegram, который помогает находить email адреса на веб-сайтах. Бот использует гибридный подход к скрапингу, сочетая быстрые проверки с aiohttp и детальное сканирование с Playwright.

## Особенности

- 🔍 Поиск email адресов на веб-сайтах
- 🔄 Автоматическая верификация и повторное сканирование
- 📊 Генерация Excel отчетов
- 🛡️ Защита от блокировок
- 📱 Удобный Telegram интерфейс

## Установка

1. Клонируйте репозиторий:
```bash
git clone https://github.com/your-username/telegram-email-scraper.git
cd telegram-email-scraper
```

2. Создайте виртуальное окружение и установите зависимости:
```bash
python -m venv .venv
source .venv/bin/activate  # для Linux/Mac
.venv\Scripts\activate     # для Windows
pip install -r requirements.txt
```

3. Создайте файл `config.py` на основе `config.example.py` и заполните необходимые параметры.

## Использование

1. Запустите бота:
```bash
python bot.py
```

2. В Telegram отправьте боту список URL-адресов в текстовом файле.

3. Бот начнет сканирование и отправит результаты в Excel файле.

## Требования

- Python 3.8+
- Telegram Bot Token
- Groq API Key (опционально)

## Лицензия

MIT 