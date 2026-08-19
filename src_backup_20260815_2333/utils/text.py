import re


def clean_content(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'<system_reminder>.*?</system_reminder>', '', text, flags=re.DOTALL)
    text = re.sub(r'\[Image[^\]]*\]', '', text)
    text = re.sub(r'<image_caption>.*?</image_caption>', '', text, flags=re.DOTALL)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def truncate(text: str, max_len: int = 100, suffix: str = "...") -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len - len(suffix)] + suffix