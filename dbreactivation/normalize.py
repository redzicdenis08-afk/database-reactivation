import re

def clean_phone(phone: str) -> str:
    cleaned = re.sub(r'\D', '', phone)
    if len(cleaned) == 10:
        return "+1" + cleaned
    elif len(cleaned) == 11 and cleaned.startswith("1"):
        return "+" + cleaned
    return cleaned
