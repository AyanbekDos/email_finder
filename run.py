#!/usr/bin/env python3
"""
Telegram Email Scraper Bot
Запуск: python run.py
"""

import os
import sys
from bot import main

if __name__ == '__main__':
    # Проверяем наличие токена
    if not os.getenv('BOT_TOKEN'):
        print("❌ Не установлен BOT_TOKEN!")
        print("Установите переменную окружения:")
        print("export BOT_TOKEN='your_bot_token_here'")
        print("или отредактируйте config.py")
        sys.exit(1)
    
    try:
        main()
    except KeyboardInterrupt:
        print("\n👋 Бот остановлен")
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        sys.exit(1) 