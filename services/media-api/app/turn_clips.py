"""Parse requests for complete, diarized speaker turns without semantic search."""

import re
import unicodedata


_NUMBERS = {
    'un': 1, 'uno': 1, 'una': 1, 'dos': 2, 'tres': 3, 'cuatro': 4,
    'cinco': 5, 'seis': 6, 'siete': 7, 'ocho': 8, 'nueve': 9, 'diez': 10,
}
_ORDINALS = {
    'primera': 1, 'primer': 1, 'segunda': 2, 'tercera': 3, 'cuarta': 4,
    'quinta': 5, 'sexta': 6, 'septima': 7, 'octava': 8, 'novena': 9,
    'decima': 10,
}


def _plain(value: str) -> str:
    return ''.join(char for char in unicodedata.normalize('NFKD', value.casefold())
                   if not unicodedata.combining(char))


def _person(text: str) -> str:
    match = re.search(
        r'\b(?:intervenciones?|turnos?)\s+(?:de|del)\s+(.+?)'
        r'(?=\s+(?:en|del)\s+(?:(?:el|la)\s+)?(?:video\s+)?TV\d+|[?.,!]|$)',
        text, re.IGNORECASE,
    )
    return match.group(1).strip() if match else ''


def parse_turn_clip_request(message: str, history: list[dict]) -> dict | None:
    """Return an unambiguous person/video/turn selection, or defer to Hermes."""
    plain = _plain(message)
    if not re.search(r'\b(?:clips?|cortes?|corta|cortar|recorta|recortar)\b', plain):
        return None
    if not re.search(r'\b(?:intervencion(?:es)?|turnos?|numero)\b', plain):
        return None

    previous = [item.get('content', '') for item in reversed(history)
                if item.get('role') == 'user']
    current_video = re.findall(r'\bTV\d{2,}\b', message, re.IGNORECASE)
    video_id = current_video[-1].upper() if current_video else ''
    if not video_id:
        for text in previous:
            matches = re.findall(r'\bTV\d{2,}\b', text, re.IGNORECASE)
            if matches:
                video_id = matches[-1].upper()
                break
    person = _person(message)
    if not person:
        for text in previous:
            # A name inherited from another video is not a safe turn reference.
            prior_videos = re.findall(r'\bTV\d{2,}\b', text, re.IGNORECASE)
            if prior_videos and prior_videos[-1].upper() != video_id:
                continue
            person = _person(text)
            if person:
                break
    if not video_id or not person:
        return None

    number_word = '|'.join(_NUMBERS)
    count_match = re.search(rf'\b(\d+|{number_word})\s+(primer[oa]s?|ultim[oa]s?)\b', plain)
    reverse_count = None if count_match else re.search(
        rf'\b(primer[oa]s?|ultim[oa]s?)\s+(\d+|{number_word})\b', plain)
    if count_match or reverse_count:
        raw = count_match.group(1) if count_match else reverse_count.group(2)
        position = count_match.group(2) if count_match else reverse_count.group(1)
        count = int(raw) if raw.isdigit() else _NUMBERS[raw]
        return {'video_id': video_id, 'person': person,
                'mode': 'first' if position.startswith('primer') else 'last',
                'count': count} if count > 0 else None

    numbered = re.search(r'\bnumero\s*(\d+)\b', plain)
    if not numbered:
        numbered = re.search(r'\bintervencion\s*(\d+)\b', plain)
    if numbered:
        index = int(numbered.group(1))
        return {'video_id': video_id, 'person': person, 'mode': 'index', 'index': index} if index > 0 else None
    for word, index in _ORDINALS.items():
        if re.search(rf'\b{word}\s+intervencion\b', plain):
            return {'video_id': video_id, 'person': person, 'mode': 'index', 'index': index}

    if re.search(r'\b(?:todas?|cada)\b', plain) or re.search(r'\b(?:intervenciones|turnos)\b', plain):
        return {'video_id': video_id, 'person': person, 'mode': 'all'}
    return None
