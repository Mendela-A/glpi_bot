import html
import re


def strip_html(text: str) -> str:
    """Конвертує HTML-контент GLPI у plain text для Telegram."""
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</?p>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = html.unescape(text)
    # Прибираємо зайві порожні рядки
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()
