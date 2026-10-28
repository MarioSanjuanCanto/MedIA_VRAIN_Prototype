"""Ground clip requests in transcript evidence, including named speaking turns."""

import re
import unicodedata
from difflib import SequenceMatcher

# Query instructions must never count as evidence in the source.
STOP_WORDS = set(
    'el la los las un una unos unas de del al en por para con sobre que se es y o a donde cuando como quien '
    'quiero buscar busca encontrar encuentra momento momentos parte partes fragmento fragmentos clip clips '
    'video videos habla hablar hable hablando dice decir intervencion interviene turno discurso corta cortar '
    'recorta recortar dame puedes favor the and of in on at where when who speaks speaking find show me please '
    'segment els les amb parla parlar vull cercar cerca troba tros '
    'algo algun alguno alguna algunos algunas hay existe existen cuenta cuentan dicen menciona mencionan '
    'mencion aparece aparecen muestra muestrame saca sacame pon ponme haz hazme dime saber tema temas '
    'corte cortes audio audios noticia noticias '
    'locuta locutas locutame locutar locucion resume resumelo resumeme resumen explica explicame explicacion cuentame '
    'ingles english espanol spanish catalan catala castellano idioma idiomas language languages'.split()
)
TURN_CUE = re.compile(r'\b(?:tiene la palabra|te la paraula|you have the floor)\b')
PERSON_CUE = re.compile(r'\b(?:habla|hablar|hable|interviene|parla|parlar|speaks|speaking|intervencion de|intervencio de|turno de|discurso de)\s+(.+)', re.I)
QUERY_INSTRUCTION_WORDS = set(
    'haz hazme haga hacer genera generame generar crea creame crear '
    'saca sacame sacar prepara preparame preparar necesito quisiera '
    'archivo salida mp4 directament create make generate '
    'locuta locutas locutame locutar locucion audio audios resume resumelo resumeme resumen explica explicame '
    'ingles english espanol spanish catalan catala castellano idioma idiomas language languages'.split()
)
ENTITY_SPEAKER_ALIASES = {
    ('alianca', 'catalana'): [('silvia', 'oriol'), ('silvia', 'orriols'), ('oriol',), ('orriols',), ('grup', 'mix'), ('grupo', 'mix')],
    ('alianza', 'catalana'): [('silvia', 'oriol'), ('silvia', 'orriols'), ('oriol',), ('orriols',), ('grup', 'mix'), ('grupo', 'mix')],
    ('alianca',): [('silvia', 'oriol'), ('silvia', 'orriols'), ('oriol',), ('orriols',), ('grup', 'mix'), ('grupo', 'mix')],
    ('alianza',): [('silvia', 'oriol'), ('silvia', 'orriols'), ('oriol',), ('orriols',), ('grup', 'mix'), ('grupo', 'mix')],
    ('grup', 'mixt'): [('silvia', 'oriol'), ('silvia', 'orriols')],
    ('grupo', 'mixto'): [('silvia', 'oriol'), ('silvia', 'orriols')],
    ('vox',): [('ignacio', 'garriga'), ('garriga',)],
    ('pp',): [('alejandro', 'fernandez')],
    ('partido', 'popular'): [('alejandro', 'fernandez')],
    ('erc',): [('josep', 'maria', 'jove'), ('jove',)],
    ('esquerra',): [('josep', 'maria', 'jove'), ('jove',)],
    ('esquerra', 'republicana'): [('josep', 'maria', 'jove'), ('jove',)],
    ('junts',): [('monica', 'sales'), ('sales',)],
    ('comuns',): [('jessica', 'albiach'), ('albiach',)],
    ('cup',): [('pilar', 'castillejo'), ('castillejo',)],
    ('salvador', 'illa'): [('presidente', 'de', 'la', 'generalitat'), ('president', 'de', 'la', 'generalitat'), ('president',), ('presidente',)],
    ('illa',): [('presidente', 'de', 'la', 'generalitat'), ('president', 'de', 'la', 'generalitat'), ('president',), ('presidente',)],
    ('president',): [('presidente', 'de', 'la', 'generalitat'), ('president', 'de', 'la', 'generalitat'), ('salvador', 'illa')],
    ('presidente',): [('presidente', 'de', 'la', 'generalitat'), ('president', 'de', 'la', 'generalitat'), ('salvador', 'illa')],
    ('president', 'de', 'la', 'generalitat'): [('salvador', 'illa'), ('illa',)],
    ('presidente', 'de', 'la', 'generalitat'): [('salvador', 'illa'), ('illa',)],
    ('govern',): [('presidente', 'de', 'la', 'generalitat'), ('salvador', 'illa')],
    ('gobierno',): [('presidente', 'de', 'la', 'generalitat'), ('salvador', 'illa')],
}


def normalize(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', text.lower()) if not unicodedata.combining(c))


def terms(text):
    return list(
        dict.fromkeys(
            w for w in re.findall(r'\w+', normalize(text))
            if len(w) > 2 and w not in STOP_WORDS and not re.match(r'^tv\d+$', w)
        )
    )


def compact_query(text: str, max_terms: int = 8) -> str:
    """Reduce a natural-language clip request to a short editorial topic."""
    content_terms = [
        term
        for term in terms(text)
        if term not in QUERY_INSTRUCTION_WORDS and not re.fullmatch(r'tv\d+', term)
    ]
    return ' '.join(content_terms[:max_terms]) or text.strip()


def matched_terms(query_terms, text, fuzzy=False):
    words = set(re.findall(r'\w+', normalize(text)))
    matches = {}
    for term in query_terms:
        if term in words:
            matches[term] = term
        elif fuzzy and len(term) >= 5:
            candidates = [w for w in words if len(w) >= 4 and w[0] == term[0]]
            best = max(candidates, key=lambda w: SequenceMatcher(None, term, w).ratio(), default='')
            if best and SequenceMatcher(None, term, best).ratio() >= .8:
                matches[term] = best
            elif term not in matches:
                norm_term = term.replace('mm', 'nm')
                for w in words:
                    norm_w = w.replace('mm', 'nm')
                    min_stem = 6 if len(norm_term) >= 6 else len(norm_term)
                    if len(norm_w) >= min_stem and norm_term[:min_stem] == norm_w[:min_stem]:
                        matches[term] = w
                        break
    return matches


def select_window(query, segments):
    """Return (selection, person_intent). No match must not become a generic clip."""
    query_terms = terms(query)
    if not query_terms:
        return None, False
    cue = PERSON_CUE.search(normalize(query))
    # "se habla de inmigración" is a topic request, not a named speaker.
    person_intent = bool(cue and not re.match(r'(?:de|sobre|del|about)\b', cue[1]))
    name_terms = terms(cue[1]) if person_intent else query_terms

    # Check if query or name mentions an entity/party alias that maps to a known speaker
    alias_targets = []
    for entity_keys, speaker_options in ENTITY_SPEAKER_ALIASES.items():
        if all(k in name_terms for k in entity_keys) or all(k in query_terms for k in entity_keys):
            for opt in speaker_options:
                alias_targets.append(list(opt))

    turns = []
    group_speaker = None
    group_evidence = ''
    for i, segment in enumerate(segments):
        text = normalize(segment.get('text', ''))
        introduction = TURN_CUE.search(text)
        if not introduction:
            continue
        # Match the name AFTER the introduction, not other people mentioned before it.
        matches = matched_terms(name_terms, text[introduction.end():], fuzzy=len(name_terms) >= 2)
        group = re.search(r'\bgrupo parlamentario\b.*', text[:introduction.start()])
        group_matches = matched_terms(name_terms, group[0]) if group else {}

        # If name_terms didn't match, check if any alias matches the speaker or group
        if len(matches) != len(name_terms) and len(group_matches) != len(name_terms):
            for alias in alias_targets:
                m = matched_terms(alias, text[introduction.end():], fuzzy=len(alias) >= 2)
                if len(m) == len(alias):
                    matches = {term: term for term in name_terms}
                    break
                if group:
                    gm = matched_terms(alias, group[0], fuzzy=len(alias) >= 2)
                    if len(gm) == len(alias):
                        group_matches = {term: term for term in name_terms}
                        break

        if 'siguiente pregunta' in text:
            group_speaker = None
            group_evidence = ''
        following_speaker = segments[i+1].get('speaker') if i+1 < len(segments) else None
        group_return = bool(group_speaker and following_speaker == group_speaker)
        if len(matches) != len(name_terms):
            if len(group_matches) == len(name_terms):
                matches = group_matches
                group_speaker = following_speaker
                group_evidence = segment.get('text', '')
            elif group_return:
                matches = {term: term for term in name_terms}
            else:
                continue
        # Fuzzy spelling is only accepted when another name token matches exactly.
        if any(k != v for k, v in matches.items()) and not any(k == v for k, v in matches.items()):
            continue
        person_intent = True
        start = i + 1
        if start >= len(segments):
            continue
        speaker = segments[start].get('speaker')
        introducer = segment.get('speaker')
        if not speaker or speaker == introducer or speaker.lower() in {'hablante', 'unknown', 'speaker'}:
            continue
        end = start
        while end + 1 < len(segments):
            following = segments[end + 1]
            if following.get('speaker') != speaker or TURN_CUE.search(normalize(following.get('text', ''))):
                break
            end += 1
        turns.append({'start': start, 'end': end, 'method': 'speaker_turn',
                'evidence': (group_evidence + ' ' + segment.get('text', '')).strip() if group_return else segment.get('text', ''), 'matched_terms': matches,
                'match_warning': 'El nombre tiene una grafía aproximada en la transcripción.' if any(k != v for k, v in matches.items()) else ''})
    if turns:
        # A short opening question must not eclipse the substantive intervention.
        best = max(turns, key=lambda t: segments[t['end']].get('end_seconds', 0) - segments[t['start']].get('start_seconds', 0))
        best['turns_found'] = len(turns)
        best['all_turns'] = turns
        if len(turns) > 1:
            best['match_warning'] = (best['match_warning'] + f' Se han localizado {len(turns)} intervenciones. Se ha seleccionado la de mayor duración por defecto.').strip()
        return best, True
    if person_intent:
        return None, True
    candidates = []
    min_required = len(query_terms) if len(query_terms) <= 2 else max(2, int(len(query_terms) * 0.6))
    for i, segment in enumerate(segments):
        # Whole words with conservative typo tolerance, not arbitrary four-letter prefixes.
        matches = matched_terms(query_terms, segment.get('text', ''), fuzzy=True)
        if len(matches) < min_required:
            continue
        exact_count = sum(k == v for k, v in matches.items())
        candidates.append((len(matches), exact_count, i, matches))
    if not candidates:
        return None, False
    candidates.sort(key=lambda row: (row[0], row[1], -row[2]), reverse=True)
    _, exact_count, anchor, matches = candidates[0]
    speaker = segments[anchor].get('speaker')
    start = anchor - 1 if anchor > 0 and speaker and segments[anchor-1].get('speaker') == speaker else anchor
    end = anchor + 1 if anchor + 1 < len(segments) and speaker and segments[anchor+1].get('speaker') == speaker else anchor
    return {'start': start, 'end': end, 'method': 'lexical',
            'evidence': segments[anchor].get('text', ''), 'matched_terms': matches,
            'match_warning': ''}, False
