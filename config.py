import os
from dataclasses import dataclass
from dotenv import load_dotenv

# Загружаем переменные из .env файла
load_dotenv()

@dataclass
class Config:
    BOT_TOKEN: str = '7463480330:AAG5TDvWt3BDMnKYodXmx0l__kPh3ilTE40'
    GROQ_API_KEY: str = os.getenv('GROQ_API_KEY', 'ТВОЙ_КЛЮЧ_ОТ_GROQ')
    MAX_FILE_SIZE_MB: int = int(os.getenv('MAX_FILE_SIZE_MB', '10'))
    MAX_PAGES_PER_DOMAIN: int = int(os.getenv('MAX_PAGES_PER_DOMAIN', '5'))
    SITE_TIMEOUT_MINUTES: int = int(os.getenv('SITE_TIMEOUT_MINUTES', '2'))
    MAX_CONCURRENT_SITES: int = int(os.getenv('MAX_CONCURRENT_SITES', '3'))
    MAX_EMAILS_PER_DOMAIN: int = int(os.getenv('MAX_EMAILS_PER_DOMAIN', '5'))
    CLEANUP_HOURS: int = 24
    
    # Страницы для поиска email
    TARGET_PAGES = [
        'contact', 'contacts', 'about', 'team', 'staff', 
        'kontakt', 'kontakty', 'o-nas', 'komanda', 'imprint', 'legal', 'feedback', 'company'
    ]

config = Config() 