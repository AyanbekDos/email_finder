# email_scraper.py - ВЕРСИЯ "ТЕРМИНАТОР"

import re
import asyncio
import aiohttp
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import validators
from asyncio_throttle import Throttler
import logging
from typing import List, Set, Dict, Tuple

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

from config import config

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class EmailScraper:
    """
    Новая версия скрапера, основанная на стратегии "умного краулера".
    Эта версия не использует внешние поисковики или AI для поиска ссылок.
    """
    def __init__(self):
        self.email_pattern = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b')
        self.throttler = Throttler(rate_limit=config.MAX_CONCURRENT_SITES)

    async def scrape_emails_from_urls(self, urls: List[str], progress_callback=None) -> Dict[str, Dict]:
        """Основная функция для запуска сканирования по списку URL."""
        results = {}
        total = len(urls)
        
        async with async_playwright() as p:
            # Запускаем браузер один раз для всех задач
            browser = await p.chromium.launch()
            
            connector = aiohttp.TCPConnector(ssl=False, limit=config.MAX_CONCURRENT_SITES)
            timeout = aiohttp.ClientTimeout(total=config.SITE_TIMEOUT_MINUTES * 60, connect=30)
            
            async with aiohttp.ClientSession(
                connector=connector, timeout=timeout, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            ) as session:
                semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_SITES)
                tasks = [self._scrape_single_site(session, browser, semaphore, url) for url in urls]
                
                completed = 0
                for coro in asyncio.as_completed(tasks):
                    url, result_data = await coro
                    results[url] = result_data
                    if progress_callback:
                        completed += 1
                        progress = int((completed / total) * 100)
                        await progress_callback(progress, completed, total)
            
            await browser.close()
        return results

    async def _scrape_single_site(self, session: aiohttp.ClientSession, browser, semaphore: asyncio.Semaphore, url: str) -> Tuple[str, Dict]:
        """Сканирует один сайт, используя гибридный рендеринг (aiohttp + Playwright)."""
        async with semaphore:
            async with self.throttler:
                result_data = {"emails": set(), "contact_page": "", "status": "В процессе"}
                if not validators.url(url):
                    result_data["status"] = "Невалидный URL"
                    return url, result_data

                try:
                    logger.info(f"[{url}] Фаза 1А: Быстрая проверка через aiohttp...")
                    main_page_content, main_page_url = await self._get_page_content_simple(session, url)

                    if not main_page_content:
                        result_data["status"] = f"Сайт недоступен (URL: {main_page_url})"
                        return url, result_data
                    
                    internal_links = self._get_all_internal_links(main_page_content, main_page_url)

                    if len(internal_links) < 5:
                        logger.info(f"[{url}] Найдено мало ссылок ({len(internal_links)}). Фаза 1Б: Подключаю JS-рендер Playwright...")
                        js_content, js_main_url = await self._get_page_content_with_js(browser, url)
                        if js_content:
                            main_page_content = js_content
                            main_page_url = js_main_url
                            internal_links = self._get_all_internal_links(main_page_content, main_page_url)

                    result_data["emails"].update(self._get_emails_from_html(main_page_content))

                    priority1_keys = ['contact', 'kontakty', 'contatti', 'kontakt', 'contacts']
                    priority2_keys = ['about', 'team', 'staff', 'imprint', 'legal', 'feedback', 'company']

                    pages_to_scan = set()
                    p1_links = {link for link in internal_links if any(key in link.lower() for key in priority1_keys)}
                    pages_to_scan.update(p1_links)

                    if not pages_to_scan:
                        p2_links = {link for link in internal_links if any(key in link.lower() for key in priority2_keys)}
                        pages_to_scan.update(p2_links)

                    if pages_to_scan:
                        result_data["contact_page"] = list(pages_to_scan)[0]
                        result_data["status"] = "Поиск email на приоритетных страницах"
                    else:
                        result_data["status"] = "Приоритетные страницы не найдены"
                        
                    scan_tasks = [self._scan_contact_page_hybrid(session, browser, page_url) for page_url in list(pages_to_scan)[:config.MAX_PAGES_PER_DOMAIN]]
                    if scan_tasks:
                        for email_set in await asyncio.gather(*scan_tasks):
                            result_data["emails"].update(email_set)

                    result_data["emails"] = self._filter_valid_emails(list(result_data["emails"]))
                    
                    if len(result_data["emails"]) > config.MAX_EMAILS_PER_DOMAIN:
                        result_data["status"] = f"Успех (ограничено до {config.MAX_EMAILS_PER_DOMAIN})"
                        result_data["emails"] = list(result_data["emails"])[:config.MAX_EMAILS_PER_DOMAIN]
                    elif result_data["emails"]:
                        result_data["status"] = "Успех"
                    elif result_data["contact_page"]:
                        result_data["status"] = "Email не найден на странице контактов"
                    
                    logger.info(f"Найдено {len(result_data['emails'])} email на {url}. Статус: {result_data['status']}")
                    return url, result_data

                except Exception as e:
                    logger.error(f"Критическая ошибка при сканировании {url}: {e}")
                    result_data["status"] = f"Критическая ошибка: {e}"
                    return url, result_data

    async def _scan_contact_page_hybrid(self, session: aiohttp.ClientSession, browser, url: str) -> Set[str]:
        """Гибридный метод: сначала быстрый поиск, потом JS-рендер если нужно."""
        # 1. Быстрая попытка
        simple_emails = await self._get_emails_from_page(session, url)
        if simple_emails:
            return simple_emails
        
        # 2. Медленная, но мощная попытка с JS
        logger.info(f"На странице {url} не найдено email быстрым методом. Включаю JS-рендер...")
        js_emails = await self._get_emails_from_page_with_js(browser, url)
        return js_emails

    async def _get_page_content_with_js(self, browser, url: str) -> Tuple[str, str]:
        """Скачивание и рендеринг страницы с помощью Playwright. Умеет игнорировать ошибки SSL."""
        context = None
        page = None
        try:
            # Создаем контекст, который игнорирует ошибки SSL
            context = await browser.new_context(ignore_https_errors=True)
            page = await context.new_page()
            await page.goto(url, wait_until='domcontentloaded', timeout=40000)
            await page.wait_for_timeout(3000) 
            content = await page.content()
            final_url = page.url
            return content, final_url
        except Exception as e:
            logger.error(f"[Playwright] Ошибка при загрузке {url}: {e}")
            return None, url
        finally:
            if page: await page.close()
            if context: await context.close()

    async def _get_emails_from_page_with_js(self, browser, url: str) -> Set[str]:
        """Получает email со страницы, используя Playwright."""
        content, _ = await self._get_page_content_with_js(browser, url)
        return self._get_emails_from_html(content) if content else set()

    async def _get_page_content_simple(self, session: aiohttp.ClientSession, url: str) -> Tuple[str, str]:
        """Простое скачивание HTML через aiohttp."""
        try:
            async with session.get(url, timeout=20) as response:
                if 200 <= response.status < 300:
                    content = await response.text(errors='ignore')
                    return content, str(response.url)
        except Exception:
            return None, url
        return None, url

    def _get_all_internal_links(self, html_content: str, base_url: str) -> Set[str]:
        """Собирает все уникальные внутренние ссылки со страницы."""
        links = set()
        try:
            domain_name = urlparse(base_url).netloc
            soup = BeautifulSoup(html_content, 'html.parser')

            ignore_ext = ['.jpg', '.jpeg', '.png', '.gif', '.pdf', '.zip', '.rar', '.css', '.js', '.xml', '.svg', '.webp']
            ignore_keywords = ['login', 'signin', 'register', 'cart', 'checkout', 'my-account', 'tel:', 'mailto:', 'javascript:void(0)']

            for a_tag in soup.find_all('a', href=True):
                href = a_tag['href']
                if not href or any(key in href.lower() for key in ignore_keywords):
                    continue

                if any(href.lower().endswith(ext) for ext in ignore_ext):
                    continue

                full_url = urljoin(base_url, href)
                if domain_name in urlparse(full_url).netloc and validators.url(full_url):
                    links.add(full_url.split('#')[0]) # Убираем якоря
        except Exception as e:
            logger.error(f"Ошибка при парсинге ссылок: {e}")
        return links

    def _get_emails_from_html(self, html_content: str) -> Set[str]:
        """Извлекает email из готового HTML-контента."""
        return set(self.email_pattern.findall(html_content))

    async def _get_emails_from_page(self, session: aiohttp.ClientSession, url: str) -> Set[str]:
        """Загрузка страницы и извлечение email."""
        content, _ = await self._get_page_content_simple(session, url)
        return self._get_emails_from_html(content) if content else set()

    def _filter_valid_emails(self, emails: List[str]) -> List[str]:
        """Фильтрация валидных email адресов."""
        valid_emails = set()
        for email in emails:
            email_lower = email.lower().strip().strip('.')
            if any(ext in email_lower for ext in ['.jpg', '.png', '.gif', '.pdf', '.doc', '.zip']):
                continue
            if any(word in email_lower for word in ['example', 'test', 'sample', 'demo', 'sentry.io', 'wixpress.com']):
                continue
            if validators.email(email_lower):
                valid_emails.add(email_lower)
        return list(valid_emails) 