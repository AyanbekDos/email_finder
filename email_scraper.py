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

    def _normalize_url(self, url: str) -> str:
        """
        Умная нормализация URL с поддержкой различных форматов
        
        Примеры:
        biohaus.it -> https://biohaus.it
        www.example.com -> https://www.example.com  
        http://test.com -> http://test.com (остается как есть)
        """
        if not url or not isinstance(url, str):
            return None
        
        url = url.strip()
        
        # Если URL уже содержит протокол, проверяем его валидность
        if url.startswith(('http://', 'https://')):
            if validators.url(url):
                return url
            else:
                return None
        
        # Если URL начинается с www., добавляем https://
        if url.startswith('www.'):
            candidate = f"https://{url}"
            if validators.url(candidate):
                return candidate
        
        # Проверяем, является ли это доменом (содержит точку и не содержит пробелов)
        if '.' in url and ' ' not in url and len(url.split('.')) >= 2:
            # Пробуем разные варианты
            candidates = [
                f"https://{url}",
                f"https://www.{url}",
                f"http://{url}",
                f"http://www.{url}"
            ]
            
            for candidate in candidates:
                if validators.url(candidate):
                    logger.info(f"URL нормализован: {url} -> {candidate}")
                    return candidate
        
        logger.warning(f"Не удалось нормализовать URL: {url}")
        return None

    def _sort_contact_pages_by_priority(self, contact_links: set, base_url: str) -> list:
        """
        Сортирует страницы контактов по приоритету:
        1. Точные совпадения (/contatti, /contact)
        2. Короткие пути (меньше подкаталогов)
        3. Подстраницы контактов (/contatti/something)
        """
        base_domain = urlparse(base_url).netloc
        
        # Группируем ссылки по типам
        exact_matches = []      # /contatti, /contact
        short_paths = []        # /contact-us, /kontakt
        sub_pages = []          # /contatti/creative-center
        
        for link in contact_links:
            try:
                parsed = urlparse(link)
                path = parsed.path.lower().strip('/')
                
                # Проверяем, что это тот же домен
                if parsed.netloc != base_domain:
                    continue
                
                # Точные совпадения (приоритет 1)
                if path in ['contatti', 'contact', 'contacts', 'kontakt', 'kontakty']:
                    exact_matches.append(link)
                
                # Короткие пути без подкаталогов (приоритет 2)
                elif '/' not in path and any(key in path for key in ['contact', 'kontakt']):
                    short_paths.append(link)
                
                # Подстраницы контактов (приоритет 3)
                else:
                    sub_pages.append(link)
                    
            except Exception as e:
                logger.warning(f"Ошибка при парсинге ссылки {link}: {e}")
                continue
        
        # Сортируем внутри каждой группы по длине пути (короче = лучше)
        exact_matches.sort(key=lambda x: len(urlparse(x).path))
        short_paths.sort(key=lambda x: len(urlparse(x).path))
        sub_pages.sort(key=lambda x: len(urlparse(x).path))
        
        # Объединяем в правильном порядке приоритетов
        result = exact_matches + short_paths + sub_pages
        
        logger.info(f"Сортировка страниц контактов:")
        for i, link in enumerate(result[:5], 1):
            logger.info(f"  {i}. {link}")
        
        return result

    def _prioritize_emails_by_relevance(self, emails_with_context: List[Dict], site_url: str) -> List[Dict]:
        """
        Сортирует email адреса по приоритету: домен, контекст, корпоративные префиксы.
        Возвращает список словарей с полной информацией для сортировки.
        """
        if not emails_with_context:
            return []

        site_domain = urlparse(site_url).netloc.replace('www.', '').lower()
        
        corporate_prefixes = [
            'info', 'contact', 'sales', 'support', 'admin', 'hello', 'mail', 
            'marketing', 'press', 'jobs', 'office', 'reception', 'billing'
        ]

        for email_data in emails_with_context:
            email_lower = email_data['address'].lower()
            email_prefix, email_domain = email_lower.split('@') if '@' in email_lower else ('', '')

            # 1. Приоритет за доменом сайта
            is_domain_match = site_domain in email_domain
            email_data['is_domain_match'] = is_domain_match
            
            # 2. Бонус за корпоративный префикс
            prefix_score = 0
            for i, prefix in enumerate(corporate_prefixes):
                if prefix in email_prefix:
                    prefix_score = len(corporate_prefixes) - i # Чем раньше в списке, тем выше балл
                    break
            
            # 3. Финальный счет
            total_score = (
                (100 if is_domain_match else 0) + 
                email_data.get('score', 0) +      # Баллы за контекст (mailto, footer)
                prefix_score                      # Баллы за префикс (info, sales)
            )
            email_data['total_score'] = total_score
            
        # Сортируем по убыванию финального счета
        sorted_emails = sorted(emails_with_context, key=lambda x: x['total_score'], reverse=True)
        
        logger.info(f"Приоритизация email для {site_domain}:")
        for email in sorted_emails[:3]:
            logger.info(f"  - Email: {email['address']}, Score: {email['total_score']}, Match: {email['is_domain_match']}")
            
        return sorted_emails

    def _get_emails_with_context(self, html_content: str, base_url: str) -> List[Dict]:
        """Извлекает email и анализирует их контекст для определения приоритета."""
        emails_with_context = []
        if not html_content:
            return []
            
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Ищем email по всему тексту
        text_emails = self.email_pattern.findall(soup.get_text(separator=" "))
        
        # Ищем email в mailto ссылках (высший приоритет)
        mailto_links = {a['href'].replace('mailto:', '').split('?')[0] for a in soup.find_all('a', href=re.compile(r'^mailto:'))}
        
        all_found_emails = set(text_emails) | mailto_links
        
        for email in all_found_emails:
            score = 0
            context_info = []

            # Приоритет за mailto
            if email in mailto_links:
                score += 50
                context_info.append("mailto")
                
            # Ищем email в тегах footer, header, address (высокий приоритет)
            element = soup.find(string=re.compile(re.escape(email), re.IGNORECASE))
            if element:
                for parent in element.find_parents(['footer', 'header', 'address']):
                    score += 30
                    context_info.append(parent.name)
                    break
            
            emails_with_context.append({
                "address": email,
                "score": score, # Базовый счет за контекст
                "context": ", ".join(context_info)
            })
            
        return emails_with_context

    def _get_emails_with_context_from_set(self, email_set: set, base_url: str) -> List[Dict]:
        """Преобразует set email в список с контекстом для дальнейшей приоритизации"""
        return [{"address": email, "score": 0, "context": "unknown"} for email in email_set]

    async def _scan_single_page_for_emails(self, browser, url: str) -> List[Dict]:
        """Сканирует одну страницу и возвращает email с контекстом."""
        content, _ = await self._get_page_content_with_js(browser, url)
        return self._get_emails_with_context(content, url)

    def _filter_and_limit_emails(self, prioritized_emails: List[Dict]) -> List[str]:
        """Фильтрует и обрезает финальный список email"""
        # Сначала фильтруем по стандартным правилам
        valid_emails = []
        seen = set()
        for email_data in prioritized_emails:
            email_lower = email_data['address'].lower().strip().strip('.')
            if email_lower in seen:
                continue
            
            if any(ext in email_lower for ext in ['.jpg', '.png', '.gif', '.pdf', '.doc', '.zip']):
                continue
            if any(word in email_lower for word in ['example', 'test', 'sample', 'demo', 'sentry.io', 'wixpress.com']):
                continue
            if validators.email(email_lower):
                valid_emails.append(email_data['address'])
                seen.add(email_lower)
                
        # Ограничиваем количество
        return valid_emails[:config.MAX_EMAILS_PER_DOMAIN]

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
        """Сканирует один сайт по новой трехуровневой стратегии."""
        async with semaphore:
            async with self.throttler:
                result_data = {"emails": set(), "contact_page": "", "status": "В процессе"}
                
                normalized_url = self._normalize_url(url)
                if not normalized_url:
                    result_data["status"] = "Невалидный URL"
                    return url, result_data

                try:
                    # --- Уровень 1: "Снайперский выстрел" (Главная страница) ---
                    logger.info(f"[{url}] Уровень 1: Анализ главной страницы...")
                    main_page_content, main_page_url = await self._get_page_content_with_js(browser, normalized_url)
                    
                    if not main_page_content:
                        result_data["status"] = f"Сайт недоступен (URL: {main_page_url})"
                        return url, result_data

                    # Ищем email с контекстным приоритетом (подвал, шапка)
                    main_page_emails = self._get_emails_with_context(main_page_content, main_page_url)
                    result_data["emails"].update(email['address'] for email in main_page_emails)

                    # Проверяем, нашли ли мы "золотой" email
                    prioritized_emails = self._prioritize_emails_by_relevance(main_page_emails, main_page_url)
                    if prioritized_emails and prioritized_emails[0]['score'] >= 100:
                        result_data["emails"] = self._filter_and_limit_emails(prioritized_emails)
                        result_data["status"] = "Успех (найдено на главной странице)"
                        result_data["contact_page"] = "Главная страница"
                        logger.info(f"[{url}] Найден высокоприоритетный email на главной. Поиск завершен.")
                        return url, result_data
                    
                    # --- Уровень 2: "Тактический штурм" (Страница контактов) ---
                    logger.info(f"[{url}] Уровень 2: Поиск приоритетной страницы контактов...")
                    internal_links = self._get_all_internal_links(main_page_content, main_page_url)
                    
                    priority1_keys = ['contact', 'kontakty', 'contatti', 'kontakt', 'contacts']
                    priority2_keys = ['about', 'team', 'staff', 'imprint', 'legal', 'feedback', 'company']
                    
                    p1_links = {link for link in internal_links if any(key in urlparse(link).path.lower() for key in priority1_keys)}
                    
                    best_contact_page = None
                    if p1_links:
                        sorted_contact_links = self._sort_contact_pages_by_priority(p1_links, base_url=main_page_url)
                        best_contact_page = sorted_contact_links[0] if sorted_contact_links else None

                    if best_contact_page:
                        result_data["contact_page"] = best_contact_page
                        logger.info(f"[{url}] Сканирую страницу контактов: {best_contact_page}")
                        contact_emails = await self._scan_single_page_for_emails(browser, best_contact_page)
                        result_data["emails"].update(email['address'] for email in contact_emails)
                    else:
                        logger.info(f"[{url}] Основная страница контактов не найдена.")

                    # --- Финальная обработка и Уровень 3 (если нужно) ---
                    # Если после уровней 1 и 2 нет email с доменом сайта, делаем последний рывок
                    final_emails = self._prioritize_emails_by_relevance(
                        self._get_emails_with_context_from_set(result_data["emails"], main_page_url), 
                        main_page_url
                    )
                    
                    if not any(email['is_domain_match'] for email in final_emails):
                        logger.info(f"[{url}] Уровень 3: Расширенный поиск по другим страницам...")
                        other_links = {link for link in internal_links if any(key in urlparse(link).path.lower() for key in priority2_keys)}
                        pages_to_scan = list(other_links)[:2]
                        
                        for page_url in pages_to_scan:
                            logger.info(f"[{url}] Сканирую дополнительную страницу: {page_url}")
                            other_emails = await self._scan_single_page_for_emails(browser, page_url)
                            result_data["emails"].update(email['address'] for email in other_emails)

                    # --- Итог ---
                    final_emails_with_context = self._get_emails_with_context_from_set(result_data["emails"], main_page_url)
                    final_prioritized = self._prioritize_emails_by_relevance(final_emails_with_context, main_page_url)
                    
                    result_data["emails"] = self._filter_and_limit_emails(final_prioritized)
                    
                    if result_data["emails"]:
                        result_data["status"] = "Успех"
                    elif result_data["contact_page"]:
                        result_data["status"] = "Email не найден на приоритетных страницах"
                    else:
                        result_data["status"] = "Email не найден"

                    logger.info(f"Найдено {len(result_data['emails'])} email на {url}. Статус: {result_data['status']}")
                    return url, result_data

                except Exception as e:
                    logger.error(f"Критическая ошибка при сканировании {url}: {e}", exc_info=True)
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

    def _filter_valid_emails(self, emails: List[str], site_url: str = None) -> List[str]:
        """Фильтрация и приоритизация валидных email адресов."""
        valid_emails = set()
        
        for email in emails:
            email_lower = email.lower().strip().strip('.')
            
            # Фильтруем нежелательные email
            if any(ext in email_lower for ext in ['.jpg', '.png', '.gif', '.pdf', '.doc', '.zip']):
                continue
            if any(word in email_lower for word in ['example', 'test', 'sample', 'demo', 'sentry.io', 'wixpress.com']):
                continue
            if validators.email(email_lower):
                valid_emails.add(email_lower)
        
        # Приоритизируем email если есть URL сайта
        if site_url and valid_emails:
            site_domain = urlparse(site_url).netloc
            return self._prioritize_emails_by_relevance(list(valid_emails), site_domain)
        
        return list(valid_emails) 