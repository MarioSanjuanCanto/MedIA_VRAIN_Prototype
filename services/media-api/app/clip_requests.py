"""Route explicit clip creation requests without a general agent exploration loop."""
import re
import unicodedata


def clip_request(message):
    normalized = ''.join(c for c in unicodedata.normalize('NFKD', message.lower()) if not unicodedata.combining(c))
    if re.search(r'\b(?:no|sin)\s+(?:quiero\s+)?(?:un\s+)?clip', normalized):
        return None
    action = re.search(r'\b(?:dame|hazme|haz|genera(?:me)?|crea(?:me)?|extrae|saca(?:me)?|corta|muestra(?:me)?|quiero|give|make|create|get|generate)\b', normalized)
    clip = re.search(r'\bclip\b', normalized)
    if not clip or not action:
        return None
    voice = bool(re.search(r'\b(?:audio|voz|locut\w*|voice)\b', normalized))
    # Preserve subject constraints after the requested artifact. Never substitute
    # a particular politician or topic when a general highlight was requested.
    tail = message[clip.end():].strip(' .,;:')
    tail = re.sub(r'(?i)\s*(?:y|and)\s+(?:(?:me\s+)?(?:lo\s+)?)?(?:locut\w*|(?:dame|genera|con|with)\s+.*(?:audio|voz|voice)).*$', '', tail)
    tail = re.sub(r'(?i)^(?:exacto|destacado|highlight|del vídeo|del video|from the video)\b\s*', '', tail)
    tail = re.sub(r'(?i)\b(?:de|del|en|from|of)\s+(?:el\s+)?(?:video\s+)?TV\d+\b', '', tail).strip()
    tail = re.sub(r'(?i)^(?:de|del|sobre|of|about)\s+', '', tail).strip(' .,;:')
    generic = ''.join(c for c in unicodedata.normalize('NFKD', tail.lower()) if not unicodedata.combining(c))
    if re.fullmatch(r'(?:(?:este|el|this|the)\s+)?(?:video|TV\d+)', generic, re.I):
        tail = ''
    if not tail:
        # "Qué dice X ... y dame un clip" puts the subject before the artifact.
        subject = re.search(r'(?i)(?:que|qué|what)\s+(?:dice|says)\s+(.+?)(?:\s+en su comparecencia)?\s+y\s+.*?clip', message)
        if subject:
            tail = subject.group(1).strip()
    return tail, voice
