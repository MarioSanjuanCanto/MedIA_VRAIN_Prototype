from media_core.services.clip_selection import compact_query, select_window


def segment(start, text, speaker):
    return dict(start_seconds=start, end_seconds=start+9, text=text, speaker=speaker)


def parliament():
    return [segment(0, 'Este es el momento donde se habla de inmigración.', 'A'),
            segment(10, 'Otra mención al momento donde habla una persona.', 'A'),
            segment(20, 'Tiene la palabra la diputada señora Silvia Oriol Serra.', 'CHAIR'),
            segment(30, 'Doscientos mil.', 'B'),
            segment(40, 'Personas que solicitan la regularización.', 'B'),
            segment(50, 'Para responder, tiene la palabra el presidente.', 'CHAIR'),
            segment(60, 'La respuesta del presidente.', 'C')]


def test_person_request_uses_introduction_then_full_turn():
    selection, person = select_window('El momento donde habla Silvia Orriols', parliament())
    assert person
    assert (selection['start'], selection['end']) == (3, 4)
    assert selection['method'] == 'speaker_turn'
    assert selection['matched_terms'] == {'silvia': 'silvia', 'orriols': 'oriol'}
    assert selection['match_warning']


def test_query_wording_and_accents_do_not_change_the_turn():
    for query in ['Donde habla Silvia Orriols?', 'intervención de Sílvia Orriols', 'Sílvia Orriols']:
        selection, _ = select_window(query, parliament())
        assert (selection['start'], selection['end']) == (3, 4)


def test_nonexistent_speaker_does_not_match_generic_words():
    selection, person = select_window('El momento donde habla Maria Perez', parliament())
    assert person and selection is None


def test_name_mentioned_before_introduction_is_not_the_speaker():
    source = [segment(0, 'Silvia Orriols ha preguntado. Tiene la palabra el presidente.', 'CHAIR'),
              segment(10, 'Esta es la respuesta.', 'PRESIDENT')]
    assert select_window('Donde habla Silvia Orriols', source) == (None, True)


def test_speaker_without_diarization_is_not_guessed():
    source = parliament()
    for s in source: s['speaker'] = 'Hablante'
    assert select_window('Donde habla Silvia Orriols', source) == (None, True)


def test_topic_requires_content_words_not_query_instructions():
    source = parliament()
    selection, person = select_window('El momento donde se habla de inmigración', source)
    assert not person and selection['start'] == 0
    assert select_window('El momento donde se habla de astronomía', source) == (None, False)


def test_compact_query_removes_clip_instructions_and_video_id():
    query = 'Hazme por favor un clip de TV05 sobre el accidente de un motorista en Cabranes'

    assert compact_query(query) == 'accidente motorista cabranes'


def test_long_generic_block_cannot_outscore_exact_topic():
    source = [segment(i*10, 'Este momento donde se habla en la sesión.', 'A') for i in range(20)]
    source.append(segment(200, 'El precio del combustible vuelve a subir.', 'B'))
    selection, _ = select_window('Quiero buscar el momento sobre precio combustible', source)
    assert selection['start'] == selection['end'] == 20


def test_main_intervention_wins_over_short_opening_question():
    source = [segment(0, 'En nombre del Grupo Parlamentario de Vox tiene la palabra Ignacio Garriga.', 'CHAIR'),
        segment(10, 'Una pregunta breve.', 'GARRIGA'),
        segment(20, 'Para responder tiene la palabra el presidente.', 'CHAIR'),
        segment(30, 'Respuesta.', 'PRESIDENT'),
        segment(40, 'Tiene la palabra Ignacio Garriga.', 'CHAIR'),
        segment(50, 'La argumentación sobre presupuestos.', 'GARRIGA'),
        segment(60, 'Los datos de vivienda.', 'GARRIGA'),
        segment(70, 'Conclusión de la intervención.', 'GARRIGA'),
        segment(80, 'La siguiente pregunta: tiene la palabra otra persona.', 'CHAIR'),
        segment(90, 'Otro tema.', 'GARRIGA')]
    for query in ['Ignacio Garriga', 'VOX']:
        selection, _ = select_window(query, source)
        assert (selection['start'], selection['end']) == (5, 7)
        assert selection['turns_found'] == 2
        assert 'intervención más larga' in selection['match_warning']
        if query == 'VOX':
            assert 'Vox' in selection['evidence']


def test_conversational_topic_query_matches_content():
    source = [
        segment(0, 'Inicio del telediario y sumario.', 'Hablante'),
        segment(10, 'Muchos visitantes se han querido acercar hasta los lagos de Covadonga en coche.', 'Hablante'),
        segment(20, 'Fin de la noticia sobre Covadonga.', 'Hablante'),
    ]
    query = 'Se dice algo de los lagos de covadonga en el video TV05? Dame un clip'
    selection, person = select_window(query, source)
    assert not person
    assert selection is not None
    assert selection['evidence'] == source[1]['text']
    assert selection['start'] <= 1 <= selection['end']
    assert 'covadonga' in selection['matched_terms']
    assert 'lagos' in selection['matched_terms']


def test_party_alias_resolution_for_alianca_catalana():
    source = parliament()
    for query in [
        'donde habla Aliança catalana',
        'Dame un clip donde habla Aliança Catalana y me lo locutas',
        'Aliança Catalana',
    ]:
        selection, person = select_window(query, source)
        assert person, f"Should identify person/speaker intent for '{query}'"
        assert selection is not None, f"Should find speaker turn for '{query}'"
        assert (selection['start'], selection['end']) == (3, 4)


