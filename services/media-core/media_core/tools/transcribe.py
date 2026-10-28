"""MCP transcription with explicit source identity and optional background execution."""
import json
import re
import sys
import uuid
from pathlib import Path
from media_core.runtime import measured, stage
from media_core.config import CACHE_DIR, TRANSCRIPTS_DIR, get_replicate_token
from media_core.formatting import build_transcribe_agent_response
from media_core.indexing import index_test_vector
from media_core.services.ffmpeg import extract_audio
from media_core.services.whisperx import transcribe_with_whisperx
from media_core.storage import file_fingerprint, load_test_vector, resolve_video_file, source_matches_file


def _index_safely(transcription):
    try:
        index_test_vector(transcription)
        transcription['indexed_in_search'] = True
        transcription.pop('index_error', None)
    except Exception as error:
        transcription['indexed_in_search'] = False
        transcription['index_error'] = str(error)
        print(f'[Chroma Index Warning] {error}', file=sys.stderr)


def _save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _compatible(data, clean_id, video_file, language, diarization):
    if not isinstance(data, dict) or str(data.get('id', '')).upper() != clean_id:
        return False
    if data.get('status', 'success') != 'success' or not (data.get('segments') or data.get('full_text')):
        return False
    metadata = data.get('request_metadata') or {}
    # A local file must have a verified identity. Legacy JSON is only usable
    # for an explicit archive ID when the source media is unavailable.
    if video_file is not None and not source_matches_file(metadata.get('source'), video_file):
        return False
    if language != 'auto' and language not in {metadata.get('language'), data.get('language')}:
        return False
    if diarization and not metadata.get('diarization', data.get('diarization', False)):
        return False
    return True


@measured
def media_transcribe(source: str = '', language: str = 'es', diarization: bool = True,
                     force_whisperx: bool = False, full_segments: bool = False,
                     index_transcript: bool = True, background: bool = False) -> dict:
    """Transcribe e indexa el vídeo indicado; source es obligatorio, nunca se elige TV01.

    source: ID (TV02), nombre (TV02.mp4) o ruta completa del archivo.
    Para vídeos nuevos usa background=True: devuelve job_id inmediatamente.
    Consulta media_job_status cada 2 segundos hasta completed/failed; solo entonces
    resume el video_id devuelto. No resumas otro vídeo ni una búsqueda general.
    language: es, ca, en o auto para detectar el idioma de origen.
    diarization: separar hablantes, opcional y más lento.
    force_whisperx: ignora la caché y vuelve a transcribir.
    index_transcript: añade también las transcripciones reutilizadas al buscador si falta.
    full_segments: devuelve la transcripción completa; por defecto respuesta compacta.
    """
    if not source.strip():
        return {'status': 'error', 'error_code': 'source_required',
                'message': 'Indica source: TV02, TV02.mp4 o ruta completa. No se selecciona un vídeo por defecto.'}
    source = source.strip()
    video_file = resolve_video_file(source)
    stem = video_file.stem if video_file else Path(source).stem
    match = re.fullmatch(r'(TV\d+)(?:_demo)?', stem, re.I)
    clean_id = match.group(1).upper() if match else stem.upper()
    signature = file_fingerprint(video_file) if video_file else None
    print(f'[Transcribe] requested={source!r} resolved={video_file} video_id={clean_id} force={force_whisperx}', file=sys.stderr)
    if background:
        from media_core.tools.jobs import _submit
        result = _submit(media_transcribe, dict(source=str(video_file.resolve()) if video_file else source,
            language=language, diarization=diarization, force_whisperx=force_whisperx,
            full_segments=full_segments, index_transcript=index_transcript, background=False))
        return {**result, 'video_id': clean_id, 'source': str(video_file) if video_file else source}

    cache_file = CACHE_DIR / f'{video_file.stem}_whisperx.json' if video_file else None
    candidates = []
    if not force_whisperx:
        if cache_file and cache_file.exists():
            try:
                candidates.append(json.loads(cache_file.read_text(encoding='utf-8')))
            except (OSError, ValueError):
                pass
        with stage('transcript_lookup'):
            candidates.append(load_test_vector(clean_id))
        for data in candidates:
            if not _compatible(data, clean_id, video_file, language, diarization):
                continue
            data = dict(data)
            data['cache_hit'] = True
            data['cache_validation'] = 'fingerprint' if signature else 'archive_id_only'
            if video_file:
                # Stored transcripts are portable; responses should still expose
                # the path that is valid in the current runtime.
                data['source'] = str(video_file.resolve())
                data['video_file'] = str(video_file.resolve())
            if index_transcript and data.get('indexed_in_search') is not True:
                with stage('indexing'):
                    _index_safely(data)
                if cache_file:
                    _save(cache_file, data)
            return build_transcribe_agent_response(data, full_segments)

    if not video_file:
        return {'status': 'error', 'error_code': 'source_not_found', 'video_id': clean_id,
                'message': f'No se encuentra {source} ni una transcripción compatible. Indica la ruta del archivo.'}
    if not get_replicate_token():
        return {'status': 'error', 'error_code': 'missing_replicate_key', 'video_id': clean_id,
                'message': 'Falta REPLICATE_API_KEY para transcribir este archivo nuevo.'}
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    audio = CACHE_DIR / f'{video_file.stem}_{uuid.uuid4().hex}_temp.mp3'
    try:
        with stage('audio_extract'):
            if not extract_audio(video_file, audio):
                return {'status': 'error', 'error_code': 'audio_extraction_failed', 'video_id': clean_id,
                        'message': 'FFmpeg no pudo extraer el audio del vídeo.'}
        with stage('asr_remote'):
            data = transcribe_with_whisperx(audio, language=language, diarization=diarization)
    finally:
        audio.unlink(missing_ok=True)
    if isinstance(data, dict) and data.get('status') == 'error':
        return {**data, 'video_id': clean_id}
    if not data or data.get('status') != 'success' or not data.get('segments'):
        return {'status': 'error', 'error_code': 'asr_failed', 'video_id': clean_id,
                'message': 'WhisperX no devolvió una transcripción válida. Consulta el log de Replicate.'}
    current = file_fingerprint(video_file)
    if current != signature:
        return {'status': 'error', 'error_code': 'source_changed', 'video_id': clean_id,
                'message': 'El vídeo cambió durante la transcripción; vuelve a intentarlo.'}
    data.update(id=clean_id, source=str(video_file.resolve()), video_file=str(video_file.resolve()),
        request_metadata={'source': signature, 'language': language, 'diarization': data.get('diarization', False)},
        cache_hit=False, cache_validation='fingerprint', indexed_in_search=False,
        video_file_size_mb=round(video_file.stat().st_size / 1048576, 2), title=f'{clean_id} - {video_file.stem}')
    # Persist before indexing so an index failure never loses a paid ASR result.
    _save(cache_file, data)
    if re.fullmatch(r'TV\d+', clean_id):
        _save(TRANSCRIPTS_DIR / f'{clean_id}.json', data)
    if index_transcript:
        with stage('indexing'):
            _index_safely(data)
        _save(cache_file, data)
        if re.fullmatch(r'TV\d+', clean_id):
            _save(TRANSCRIPTS_DIR / f'{clean_id}.json', data)
    return build_transcribe_agent_response(data, full_segments)
