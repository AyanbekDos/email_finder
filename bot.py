import asyncio
import os
import logging
from datetime import datetime, timedelta
from typing import Dict
import aiofiles
import aiohttp
from telegram import Update, Document, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    filters, ContextTypes, CallbackQueryHandler
)

from config import config
from email_scraper import EmailScraper
from excel_handler import ExcelHandler

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class EmailScraperBot:
    def __init__(self):
        self.scraper = EmailScraper()
        self.excel_handler = ExcelHandler()
        self.active_tasks: Dict[int, asyncio.Task] = {}
        
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /start, показывает приветствие и главное меню."""
        user_name = update.effective_user.first_name
        welcome_text = (
            f"👋 Привет, {user_name}!\n\n"
            "Я бот для сбора email-адресов с сайтов. "
            "Используйте меню ниже, чтобы начать работу."
        )
        
        keyboard = [
            ["🚀 Начать скрапинг"],
            ["📈 Мой статус", "❓ Помощь"]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(welcome_text, reply_markup=reply_markup)

    async def show_scraping_instructions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показывает подробные инструкции по запуску скрапинга."""
        instructions_text = """
🚀 **Как начать скрапинг:**

**Способ 1: Отправьте файл**
Создайте `.txt` файл, где каждый URL находится на новой строке, и отправьте его мне.

**Способ 2: Отправьте сообщение**
Просто напишите или вставьте список сайтов в чат.

**Примеры форматов, которые я пойму:**
✅ `example.com`
✅ `www.example.com`
✅ `https://example.com`

---
**Что дальше?**
Я начну сканирование и буду показывать прогресс. В конце вы получите `Excel` файл с результатами.

**Ограничения:**
📁 Макс. размер файла: {max_size} МБ
⏱️ Таймаут на сайт: {timeout} мин.
🔄 Макс. страниц на домен: {max_pages}
        """.format(
            max_size=config.MAX_FILE_SIZE_MB,
            timeout=config.SITE_TIMEOUT_MINUTES,
            max_pages=config.MAX_PAGES_PER_DOMAIN
        )
        await update.message.reply_text(instructions_text, parse_mode='Markdown')

    async def handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка загруженного файла"""
        document: Document = update.message.document
        user_id = update.effective_user.id
        
        # Проверка размера файла
        if document.file_size > config.MAX_FILE_SIZE_MB * 1024 * 1024:
            await update.message.reply_text(
                f"❌ Файл слишком большой! Максимальный размер: {config.MAX_FILE_SIZE_MB} МБ"
            )
            return
        
        # Проверка расширения файла
        if not document.file_name.endswith('.txt'):
            await update.message.reply_text("❌ Поддерживаются только .txt файлы!")
            return
        
        # Проверка активных задач
        if user_id in self.active_tasks and not self.active_tasks[user_id].done():
            keyboard = [[InlineKeyboardButton("❌ Отменить текущую задачу", callback_data=f"cancel_{user_id}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                "⚠️ У вас уже есть активная задача. Отмените её или дождитесь завершения.",
                reply_markup=reply_markup
            )
            return
        
        try:
            # Скачивание файла
            status_message = await update.message.reply_text("📥 Загружаю файл...")
            
            file = await context.bot.get_file(document.file_id)
            file_path = f"temp_{user_id}_{document.file_name}"
            
            await file.download_to_drive(file_path)
            
            # Чтение URL из файла
            await status_message.edit_text("📖 Читаю URL из файла...")
            
            async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                content = await f.read()
                urls = [line.strip() for line in content.split('\n') if line.strip()]
            
            # Удаляем временный файл
            os.remove(file_path)
            
            if not urls:
                await status_message.edit_text("❌ Файл пустой или не содержит валидных URL!")
                return
            
            await status_message.edit_text(f"✅ Найдено {len(urls)} URL. Начинаю сканирование...")
            
            # Создаем задачу сканирования
            task = asyncio.create_task(
                self._process_urls(urls, user_id, update, context, status_message)
            )
            self.active_tasks[user_id] = task
            
        except Exception as e:
            logger.error(f"Ошибка при обработке файла: {e}")
            await update.message.reply_text("❌ Произошла ошибка при обработке файла!")

    async def _process_urls(self, urls, user_id, update, context, status_message):
        """Процесс сканирования URL с верификацией и повторным запуском."""
        excel_filename = ""
        try:
            # --- Фаза 1: Первичное сканирование ---
            await status_message.edit_text(f"✅ Найдено {len(urls)} URL. Фаза 1: Начинаю основное сканирование...")
            
            last_progress_text = "" # Будем хранить последний текст, чтобы не спамить API

            async def progress_callback(progress, completed, total):
                nonlocal last_progress_text
                try:
                    keyboard = [[InlineKeyboardButton("❌ Отменить", callback_data=f"cancel_{user_id}")]]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    progress_bar = "█" * (progress // 5) + "░" * (20 - progress // 5)
                    text = f"🔍 Фаза 1: {progress}%\n[{progress_bar}]\n\n📊 Обработано: {completed}/{total} сайтов"
                    
                    # --- УМНОЕ ОБНОВЛЕНИЕ ---
                    # Отправляем запрос только если текст действительно изменился
                    if text != last_progress_text:
                        await status_message.edit_text(text, reply_markup=reply_markup)
                        last_progress_text = text

                except BadRequest as e:
                    # Дополнительно ловим и игнорируем ошибку "Message is not modified", если она все же проскочит
                    if "Message is not modified" in str(e):
                        pass
                    else:
                        logger.warning(f"Ошибка BadRequest при обновлении прогресса: {e}")
                except Exception as e:
                    logger.warning(f"Другая ошибка при обновлении прогресса: {e}")
            
            results = await self.scraper.scrape_emails_from_urls(urls, progress_callback)
            
            if user_id in self.active_tasks and self.active_tasks[user_id].cancelled():
                return

            # --- Фаза 2: Проверка и повторный запуск ---
            await status_message.edit_text("🕵️ Фаза 2: Проверяю неудачные результаты...")
            
            retry_urls = []
            banned_links = {}

            urls_to_verify = {url: data['contact_page'] for url, data in results.items() if not data.get('emails') and data.get('contact_page')}
            
            if urls_to_verify:
                async with aiohttp.ClientSession() as session:
                    for url, contact_page in urls_to_verify.items():
                        try:
                            async with session.head(contact_page, timeout=10) as response:
                                if response.status == 404:
                                    logger.warning(f"Страница {contact_page} для {url} вернула 404. Добавляю в очередь на перепроверку.")
                                    retry_urls.append(url)
                                    banned_links.setdefault(url, set()).add(contact_page)
                        except Exception as e:
                            logger.warning(f"Ошибка при проверке {contact_page}: {e}")
            
            if retry_urls:
                await status_message.edit_text(f"🚀 Фаза 2: Найдено {len(retry_urls)} сайтов с битыми ссылками. Запускаю повторное сканирование...")
                retry_results = await self.scraper.scrape_emails_from_urls(retry_urls, banned_links=banned_links)
                results.update(retry_results)

            # --- Финальный этап: Создание отчета ---
            await status_message.edit_text("📊 Создаю итоговый Excel файл...")
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            excel_filename = f"email_results_{user_id}_{timestamp}.xlsx"
            
            try:
                self.excel_handler.create_excel_file(results, excel_filename)
                
                total_emails = sum(len(data.get('emails', [])) for data in results.values())
                sites_with_emails = sum(1 for data in results.values() if data.get('emails'))
                
                stats_text = f"""
✅ **Сканирование завершено!**

📊 **Итоговая статистика:**
• Всего сайтов: {len(urls)}
• Сайтов с email: {sites_with_emails}
• Всего найдено email: {total_emails}
• Сайтов перепроверено: {len(retry_urls)}

📁 Результаты в Excel файле:
                """
                
                await status_message.edit_text(stats_text, parse_mode='Markdown')
                
                with open(excel_filename, 'rb') as file:
                    await context.bot.send_document(
                        chat_id=update.effective_chat.id,
                        document=file,
                        filename=f"email_results_{timestamp}.xlsx",
                        caption="📊 Итоговые результаты сканирования email адресов"
                    )
            finally:
                if excel_filename and os.path.exists(excel_filename):
                    os.remove(excel_filename)
                    logger.info(f"Файл отчета {excel_filename} был успешно удален.")

        except asyncio.CancelledError:
            await status_message.edit_text("❌ Задача была отменена")
            if excel_filename and os.path.exists(excel_filename):
                os.remove(excel_filename)
                logger.info(f"Файл отчета {excel_filename} удален после отмены.")
        except Exception as e:
            logger.error(f"Ошибка при сканировании: {e}")
            await status_message.edit_text("❌ Произошла ошибка при сканировании!")
        finally:
            if user_id in self.active_tasks:
                del self.active_tasks[user_id]

    async def cancel_task(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Отмена активной задачи"""
        query = update.callback_query
        await query.answer()
        
        user_id = int(query.data.split('_')[1])
        
        if user_id in self.active_tasks and not self.active_tasks[user_id].done():
            self.active_tasks[user_id].cancel()
            await query.edit_message_text("❌ Задача отменена")
        else:
            await query.edit_message_text("ℹ️ Нет активных задач для отмены")

    async def _cleanup_file(self, filename: str):
        """Удаление файла через указанное время"""
        await asyncio.sleep(config.CLEANUP_HOURS * 3600)
        try:
            if os.path.exists(filename):
                os.remove(filename)
                logger.info(f"Файл {filename} удален")
        except Exception as e:
            logger.error(f"Ошибка при удалении файла {filename}: {e}")

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /help"""
        help_text = """
🔍 **Email Scraper Bot - Помощь**

**Как использовать:**
1. Создайте .txt файл со списком URL (один на строку)
2. Отправьте файл боту
3. Дождитесь завершения сканирования
4. Получите Excel файл с результатами

**Поддерживаемые форматы URL:**
• https://example.com
• http://example.com
• example.com (автоматически добавится http://)

**Где ищем email:**
• Главная страница
• Страницы контактов
• Страницы "О нас"
• Страницы команды

**Команды:**
/start - Начать работу
/help - Показать эту справку
/status - Показать статус активных задач

**Ограничения:**
• Максимальный размер файла: {max_size} МБ
• Таймаут на сайт: {timeout} минут
• Максимум страниц на домен: {max_pages}
        """.format(
            max_size=config.MAX_FILE_SIZE_MB,
            timeout=config.SITE_TIMEOUT_MINUTES,
            max_pages=config.MAX_PAGES_PER_DOMAIN
        )
        
        await update.message.reply_text(help_text, parse_mode='Markdown')

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /status"""
        user_id = update.effective_user.id
        if user_id in self.active_tasks and not self.active_tasks[user_id].done():
            await update.message.reply_text("🔄 У вас есть активная задача сканирования.")
        else:
            await update.message.reply_text("✅ Нет активных задач сканирования.")

    async def handle_text_urls(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка текстовых сообщений с URL"""
        user_id = update.effective_user.id
        text = update.message.text.strip()
        
        # Проверяем, содержит ли сообщение URL-подобные строки
        potential_urls = []
        lines = text.split('\n')
        
        for line in lines:
            line = line.strip()
            if line and not line.startswith('#') and not line.startswith('//'):
                # Простая проверка на URL-подобную строку
                if ('.' in line and ' ' not in line and len(line) > 3 and 
                    not line.startswith('/') and not line.startswith('@')):
                    potential_urls.append(line)
        
        if not potential_urls:
            # Если не похоже на URL, показываем подсказку
            await update.message.reply_text(
                "💡 **Как отправить URL для сканирования:**\n\n"
                "**Вариант 1:** Отправьте .txt файл со списком URL\n"
                "**Вариант 2:** Отправьте URL прямо в сообщении:\n\n"
                "```\n"
                "biohaus.it\n"
                "fassabortolo.com\n"
                "casalgrandepadana.it\n"
                "```\n\n"
                "**Поддерживаемые форматы:**\n"
                "• `example.com`\n"
                "• `www.example.com`\n"
                "• `https://example.com`\n"
                "• `http://example.com`",
                parse_mode='Markdown'
            )
            return
        
        # Проверка активных задач
        if user_id in self.active_tasks and not self.active_tasks[user_id].done():
            keyboard = [[InlineKeyboardButton("❌ Отменить текущую задачу", callback_data=f"cancel_{user_id}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                "⚠️ У вас уже есть активная задача. Отмените её или дождитесь завершения.",
                reply_markup=reply_markup
            )
            return
        
        try:
            status_message = await update.message.reply_text(
                f"📝 Получено {len(potential_urls)} URL из сообщения:\n\n" +
                "\n".join(f"• `{url}`" for url in potential_urls[:5]) +
                (f"\n• ... и еще {len(potential_urls) - 5}" if len(potential_urls) > 5 else "") +
                "\n\n🔍 Начинаю сканирование...",
                parse_mode='Markdown'
            )
            
            # Создаем задачу сканирования
            task = asyncio.create_task(
                self._process_urls(potential_urls, user_id, update, context, status_message)
            )
            self.active_tasks[user_id] = task
            
        except Exception as e:
            logger.error(f"Ошибка при обработке текстовых URL: {e}")
            await update.message.reply_text("❌ Произошла ошибка при обработке URL!")

def main():
    """Запуск бота"""
    if not config.BOT_TOKEN or config.BOT_TOKEN == 'YOUR_BOT_TOKEN_HERE':
        print("❌ Установите BOT_TOKEN в переменных окружения или в config.py")
        return
    
    # Создаем экземпляр бота
    bot = EmailScraperBot()
    
    # Создаем приложение
    application = Application.builder().token(config.BOT_TOKEN).build()
    
    # --- Основные команды ---
    application.add_handler(CommandHandler("start", bot.start))
    application.add_handler(CommandHandler("help", bot.help_command))
    application.add_handler(CommandHandler("status", bot.status_command))

    # --- Обработчики кнопок меню ---
    application.add_handler(MessageHandler(filters.Regex('^🚀 Начать скрапинг$'), bot.show_scraping_instructions))
    application.add_handler(MessageHandler(filters.Regex('^📈 Мой статус$'), bot.status_command))
    application.add_handler(MessageHandler(filters.Regex('^❓ Помощь$'), bot.help_command))

    # --- Обработчики контента ---
    application.add_handler(MessageHandler(filters.Document.ALL, bot.handle_document))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^🚀 Начать скрапинг$') & ~filters.Regex('^📈 Мой статус$') & ~filters.Regex('^❓ Помощь$'), bot.handle_text_urls))
    
    # --- Обработчик колбэков ---
    application.add_handler(CallbackQueryHandler(bot.cancel_task, pattern=r"cancel_\d+"))
    
    # Запускаем бота
    print("🚀 Бот запущен с главным меню!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main() 