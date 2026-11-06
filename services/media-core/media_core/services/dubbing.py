"""Translate timed dialogue and build a duration-locked dubbed video."""

from __future__ import annotations

import json
import array
import math
import re
import subprocess
import tempfile
import uuid
import wave
from pathlib import Path

from media_core.config import DEFAULT_LLM_MODEL, OUTPUT_CLIPS_DIR, get_openrouter_key
from media_core.services.openrouter import call_openrouter
from media_core.services.tts import get_audio_duration, synthesize_speech
from media_core.services.scene_detection import probe_video
from media_core.runtime import check_cancelled, progress


LANGUAGE_NAMES = {
    "es": "Spanish",
    "ca": "Catalan",
    "en": "English",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
}


def _atempo_chain(factor: float) -> str:
    """Return an FFmpeg atempo chain for any positive factor."""
    values: list[float] = []
    while factor > 2.0:
        values.append(2.0)
        factor /= 2.0
    while factor < 0.5:
        values.append(0.5)
        factor /= 0.5
    values.append(factor)
    return ",".join(f"atempo={value:.8f}" for value in values)


def translate_segments(segments: list[dict], target_language: str) -> list[dict]:
    """Translate a bounded list while retaining its ids and timing contract."""
    language = LANGUAGE_NAMES.get(target_language)
    if not language:
        raise ValueError("target_language debe ser es, ca, en, fr, de, it o pt")
    if not get_openrouter_key():
        raise ValueError("Falta OPENROUTER_API_KEY para traducir el diálogo.")
    source = [{
        "id": index,
        "speaker": str(item.get("speaker", "")),
        "duration_seconds": round(float(item["end_seconds"]) - float(item["start_seconds"]), 3),
        "text": str(item["text"]).strip(),
    } for index, item in enumerate(segments)]
    batches: list[list[dict]] = []
    current: list[dict] = []
    current_chars = 0
    for item in source:
        size = len(item["text"])
        if current and (len(current) >= 30 or current_chars + size > 9000):
            batches.append(current)
            current, current_chars = [], 0
        current.append(item)
        current_chars += size
    if current:
        batches.append(current)

    by_id: dict[int, str] = {}
    for batch_index, batch in enumerate(batches, 1):
        check_cancelled()
        progress("translation", batch=batch_index, total_batches=len(batches))
        response = call_openrouter(
            [
                {"role": "system", "content": (
                    f"Translate broadcast dialogue naturally into {language}. Preserve meaning, tone, names and "
                    "speaker intent. Adapt wording to fit the supplied duration at a natural speaking pace. "
                    "Return only JSON: {\"segments\":[{\"id\":0,\"text\":\"...\"}]}. "
                    "Return every supplied id exactly once; never merge or split segments."
                )},
                {"role": "user", "content": json.dumps(batch, ensure_ascii=False)},
            ],
            model=DEFAULT_LLM_MODEL,
            json_mode=True,
            temperature=0.1,
            max_tokens=min(6000, 180 + sum(max(24, len(item["text"]) * 2) for item in batch)),
            raise_errors=True,
        )
        try:
            payload = json.loads(response or "")
            translated = payload["segments"]
            translated_batch = {item["id"]: str(item["text"]).strip() for item in translated}
        except (TypeError, ValueError, KeyError) as exc:
            raise ValueError("El traductor no devolvió segmentos JSON válidos.") from exc
        expected = {item["id"] for item in batch}
        if (set(translated_batch) != expected or len(translated) != len(expected)
                or any(not translated_batch[index] for index in expected)):
            raise ValueError("La traducción no conservó todos los segmentos y sus identificadores.")
        by_id.update(translated_batch)
    return [{**item, "translated_text": by_id[index]} for index, item in enumerate(segments)]


def render_dubbed_video(
    source: Path,
    segments: list[dict],
    target_language: str,
    *,
    voice_provider: str = "piper",
    output_name: str = "",
) -> dict:
    """Synthesize, duration-fit and mix translated speech over a ducked source track."""
    if not segments:
        raise ValueError("No hay diálogo dentro del clip para doblar.")
    clip_duration = float(probe_video(source)['duration'])
    if not math.isfinite(clip_duration) or clip_duration <= 0:
        raise ValueError('El clip de origen tiene duración inválida.')
    OUTPUT_CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", output_name or source.stem).strip("_")[:70]
    signature = uuid.uuid5(uuid.NAMESPACE_URL, json.dumps([
        'verified-two-pass-audio-v3',
        str(source.resolve()), source.stat().st_size, source.stat().st_mtime_ns,
        target_language, voice_provider,
        [(item["start_seconds"], item["end_seconds"], item["translated_text"]) for item in segments],
    ], ensure_ascii=False)).hex[:16]
    output = OUTPUT_CLIPS_DIR / f"{safe}_dub_{target_language}_{signature}.mp4"
    if output.is_file() and output.stat().st_size > 1024:
        try:
            verification = verify_audio(output, clip_duration, segments)
            return {"video_file": str(output), "cache_hit": True, 'audio_verification': verification}
        except ValueError:
            pass  # Never reuse a truncated/silent cached result.

    temporary_output = output.with_name(f".{output.stem}_{uuid.uuid4().hex}.mp4")
    with tempfile.TemporaryDirectory(prefix="media-dub-") as _directory:
        filters: list[str] = []
        inputs = []
        for index, segment in enumerate(segments):
            check_cancelled()
            progress("dub_synthesis", segment=index + 1, total_segments=len(segments))
            target_duration = float(segment["end_seconds"]) - float(segment["start_seconds"])
            result = synthesize_speech(
                text=segment["translated_text"],
                language=target_language,
                provider=voice_provider,
                output_filename=f"dub_{signature}_{index}",
                format_mp3=False,
            )
            if result.get("status") != "success":
                raise ValueError(f"No se pudo sintetizar el segmento {index + 1}: {result.get('message', 'error desconocido')}")
            audio = Path(result["audio_file"])
            duration = float(result.get("duration_seconds") or get_audio_duration(audio))
            if not audio.is_file() or duration <= 0:
                raise ValueError(f"La locución del segmento {index + 1} no es válida.")
            tempo = duration / target_duration
            delay = max(0, round(float(segment["start_seconds"]) * 1000))
            fitted = Path(_directory) / f'voice_{index}.wav'
            # Materialize atempo before amix: the combined graph can finish at
            # the first adelay and discard every voiced sample on some FFmpeg builds.
            _run_audio_command(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(audio),
                '-af', f'aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,'
                       f'{_atempo_chain(tempo)},apad=whole_dur={target_duration:.6f},'
                       f'atrim=duration={target_duration:.6f},asetpts=PTS-STARTPTS',
                '-t', f'{target_duration:.6f}', '-c:a', 'pcm_s16le', str(fitted)], fitted)
            verify_audio(fitted, target_duration)
            inputs.extend(['-i', str(fitted)])
            filters.append(f'[{index}:a]adelay={delay}|{delay}[voice{index}]')
        voice_inputs = "".join(f"[voice{index}]" for index in range(len(segments)))
        filters.append(f"{voice_inputs}amix=inputs={len(segments)}:normalize=0,"
                       f"apad=whole_dur={clip_duration:.6f},atrim=duration={clip_duration:.6f}[voices]")
        voice_track = Path(_directory) / 'translated.wav'
        voice_command = ['ffmpeg', '-nostdin', '-v', 'error', '-y', *inputs,
                         '-filter_complex', ';'.join(filters), '-map', '[voices]',
                         '-t', f'{clip_duration:.6f}', '-c:a', 'pcm_s16le', str(voice_track)]
        progress('dub_voice_track', total_segments=len(segments))
        _run_audio_command(voice_command, voice_track)
        verify_audio(voice_track, clip_duration, segments)
        check_cancelled()
        # Side-chain ducking retains music/effects and lowers the original dialogue
        # only while translated speech is active. True stem replacement requires a
        # configured source-separation model and is reported honestly in metadata.
        mix = (
            "[1:a]asetpts=PTS-STARTPTS,asplit=2[sidechain][translated];"
            "[0:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=PTS-STARTPTS[original];"
            "[original][sidechain]sidechaincompress=threshold=0.01:ratio=12:attack=8:release=180:makeup=1[ducked];"
            f"[ducked][translated]amix=inputs=2:duration=longest:weights='0.45 1':normalize=0,"
            f"alimiter=limit=0.95,apad=whole_dur={clip_duration:.6f},atrim=duration={clip_duration:.6f}[mixed]"
        )
        command = [
            "ffmpeg", '-nostdin', '-v', 'error', "-y", '-i', str(source), '-i', str(voice_track),
            "-filter_complex", mix,
            "-map", "0:v:0", "-map", "[mixed]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-t", f'{clip_duration:.6f}', "-movflags", "+faststart", str(temporary_output),
        ]
        try:
            check_cancelled()
            progress("dub_mix", total_segments=len(segments))
            _run_audio_command(command, temporary_output)
            verification = verify_audio(temporary_output, clip_duration, segments)
            check_cancelled()
            temporary_output.replace(output)
        finally:
            temporary_output.unlink(missing_ok=True)
    return {"video_file": str(output), "cache_hit": False, 'audio_verification': verification}


def _run_audio_command(command, output):
    check_cancelled()
    completed = subprocess.run(command, capture_output=True, text=True, timeout=1800)
    if completed.returncode or not output.is_file() or output.stat().st_size <= 1024:
        detail = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else 'salida vacía'
        raise ValueError(f'FFmpeg no pudo mezclar el doblaje: {detail[:300]}')


def verify_audio(path: Path, expected_duration: float, windows: list[dict] | None = None) -> dict:
    """Measure decoded PCM, not container duration or ASR success."""
    with tempfile.TemporaryDirectory(prefix='media-audio-check-') as directory:
        pcm = Path(directory) / 'decoded.wav'
        _run_audio_command(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(path),
            '-map', '0:a:0', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(pcm)], pcm)
        with wave.open(str(pcm), 'rb') as audio:
            duration = audio.getnframes() / audio.getframerate()
            loudest = 0
            while block := audio.readframes(16000):
                samples = array.array('h', block)
                loudest = max(loudest, math.sqrt(sum(int(value)**2 for value in samples) / len(samples)))
            for number, window in enumerate(windows or [], 1):
                start = round(float(window['start_seconds'])*16000)
                end = min(audio.getnframes(), round(float(window['end_seconds'])*16000))
                if end <= start:
                    raise ValueError(f'El segmento {number} queda fuera de la pista de audio.')
                audio.setpos(start)
                samples = array.array('h', audio.readframes(end-start))
                rms = math.sqrt(sum(int(value)**2 for value in samples)/max(1,len(samples)))
                if rms < 32768*10**(-55/20):
                    raise ValueError(f'El segmento {number} no contiene voz audible; no se publica el doblaje.')
        if abs(duration - expected_duration) > .25:
            raise ValueError(f'Audio incompleto: {duration:.2f} s decodificados para {expected_duration:.2f} s de vídeo.')
        rms_dbfs = 20 * math.log10(max(loudest, 1e-9) / 32768)
        if rms_dbfs < -55:
            raise ValueError('La pista de doblaje está vacía o en silencio; no se publica como correcta.')
        return {'decoded_duration_seconds': round(duration, 3), 'loudest_second_dbfs': round(rms_dbfs, 1),
                'verified_segments': len(windows or [])}


def validate_timed_segments(segments: list[dict], clip_duration: float) -> list[dict]:
    """Normalize relative dialogue segments and reject overlaps or invalid ranges."""
    normalized = []
    previous_end = 0.0
    for item in segments:
        try:
            start = float(item["start_seconds"])
            end = float(item["end_seconds"])
            text = str(item["text"]).strip()
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Cada segmento necesita start_seconds, end_seconds y text.") from exc
        if not all(map(math.isfinite, (start, end))) or start < previous_end - 0.02 or end <= start or end > clip_duration + 0.1 or not text:
            raise ValueError("Los segmentos de diálogo contienen tiempos inválidos o solapados.")
        normalized.append({**item, "start_seconds": max(0.0, start), "end_seconds": min(clip_duration, end), "text": text})
        previous_end = end
    return normalized
