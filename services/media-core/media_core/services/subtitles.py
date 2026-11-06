"""Create valid SRT captions and burn them into a video with FFmpeg."""

from __future__ import annotations

import hashlib
from functools import lru_cache
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from media_core.config import OUTPUT_CLIPS_DIR


ENCODERS = {
    'libx264': ('-preset', 'veryfast', '-crf', '20', '-threads', str(min(os.cpu_count() or 4, 8))),
    'h264_qsv': ('-preset', 'veryfast', '-global_quality', '20'),
    'h264_nvenc': ('-preset', 'p4', '-cq', '20', '-b:v', '0'),
    'h264_amf': ('-quality', 'speed', '-rc', 'cqp', '-qp_i', '20', '-qp_p', '20'),
    'h264_videotoolbox': ('-b:v', '8M', '-allow_sw', '0'),
}


@lru_cache(maxsize=8)
def _select_encoder(preference: str) -> str:
    """Probe actual hardware once; an advertised FFmpeg encoder may have no GPU."""
    if preference not in {'auto', *ENCODERS}:
        raise ValueError('MEDIA_SUBTITLE_ENCODER no es un codificador admitido.')
    candidates = (['h264_videotoolbox'] if sys.platform == 'darwin' else
                  ['h264_qsv', 'h264_nvenc', 'h264_amf']) if preference == 'auto' else [preference]
    for name in candidates:
        if name == 'libx264': return name
        try:
            probe = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                'color=c=black:s=128x128:r=25:d=0.08', '-frames:v', '1', '-c:v', name,
                *ENCODERS[name], '-f', 'null', '-'], timeout=6, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            if probe.returncode == 0: return name
        except (OSError, subprocess.TimeoutExpired):
            pass
    return 'libx264'


def _export(command: list[str], encoder: str):
    result = subprocess.run(command, capture_output=True, text=True, timeout=1800)
    if result.returncode and encoder != 'libx264':
        # A successful tiny probe cannot guarantee a full-size export (VRAM/driver).
        position = command.index('-c:v')
        command = (command[:position+1] + ['libx264', *ENCODERS['libx264']]
                   + command[position+2+len(ENCODERS[encoder]):])
        result = subprocess.run(command, capture_output=True, text=True, timeout=1800)
    return result


def srt_timestamp(seconds: float) -> str:
    total_ms = max(0, round(float(seconds) * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_srt(segments: list[dict], destination: Path) -> None:
    if not segments:
        raise ValueError("No hay segmentos para generar subtítulos.")
    blocks = []
    for index, item in enumerate(segments, 1):
        text = str(item.get("subtitle_text", item.get("text", ""))).strip()
        if not text:
            continue
        # Keep captions readable without altering their timing or meaning.
        wrapped = "\n".join(textwrap.wrap(text, width=44, break_long_words=False, break_on_hyphens=False))
        blocks.append(
            f"{index}\n{srt_timestamp(item['start_seconds'])} --> "
            f"{srt_timestamp(item['end_seconds'])}\n{wrapped}\n"
        )
    if not blocks:
        raise ValueError("La transcripción no contiene texto utilizable para subtítulos.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(blocks), encoding="utf-8")


def split_caption_segments(segments: list[dict], line_width: int = 44) -> list[dict]:
    """Split long speech segments into readable two-line cues proportionally."""
    result = []
    for item in segments:
        text = str(item.get("subtitle_text", item.get("text", ""))).strip()
        lines = textwrap.wrap(text, width=line_width, break_long_words=False, break_on_hyphens=False)
        chunks = ["\n".join(lines[index:index + 2]) for index in range(0, len(lines), 2)] or [text]
        start, end = float(item["start_seconds"]), float(item["end_seconds"])
        weights = [max(1, len(chunk.replace("\n", " "))) for chunk in chunks]
        total = sum(weights)
        cursor = start
        for index, (chunk, weight) in enumerate(zip(chunks, weights, strict=True)):
            chunk_end = end if index == len(chunks) - 1 else cursor + (end - start) * weight / total
            result.append({**item, "start_seconds": cursor, "end_seconds": chunk_end, "subtitle_text": chunk})
            cursor = chunk_end
    return result


def _video_geometry(source: Path) -> tuple[int, int, float]:
    result = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height:format=duration", "-of", "json", str(source),
    ], capture_output=True, text=True, timeout=30)
    try:
        data = json.loads(result.stdout)
        stream = data["streams"][0]
        return int(stream["width"]), int(stream["height"]), float(data["format"]["duration"])
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ValueError("FFprobe no pudo obtener el tamaño y duración del vídeo.") from exc


def _font(size: int):
    for candidate in (
        str(Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / 'arialbd.ttf'),
        str(Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / 'segoeuib.ttf'),
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ):
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    for name in ('DejaVuSans-Bold.ttf', 'Arial.ttf'):
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    raise ValueError('No hay una fuente Unicode escalable para los subtítulos. Instala Arial o DejaVu Sans.')


def _caption_png(width: int, height: int, text: str, destination: Path, *, crop_top: int = 0) -> None:
    canvas = Image.new("RGBA", (width, height - crop_top), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    font_size = max(18, round(height * 0.05))
    font = _font(font_size)
    max_chars = max(22, round(width / (font_size * 0.58)))
    lines = text.splitlines() or textwrap.wrap(
        text, width=max_chars, break_long_words=False, break_on_hyphens=False,
    ) or [text]
    lines = lines[:2]
    # Use real pixel widths, including accents, to keep captions inside safe margins.
    while font_size > 18 and max(draw.textlength(line, font=font) for line in lines) > width * .90:
        font_size -= 1
        font = _font(font_size)
    spacing = max(4, round(font_size * 0.22))
    boxes = [draw.textbbox((0, 0), line, font=font, stroke_width=2, anchor='lt') for line in lines]
    text_width = max(box[2] - box[0] for box in boxes)
    line_height = max(box[3] - box[1] for box in boxes)
    block_height = len(lines) * line_height + (len(lines) - 1) * spacing
    x = (width - text_width) // 2
    y = height - block_height - max(24, round(height * 0.07)) - crop_top
    padding_x, padding_y = max(12, font_size // 2), max(7, font_size // 4)
    draw.rounded_rectangle(
        (x - padding_x, y - padding_y, x + text_width + padding_x, y + block_height + padding_y),
        radius=max(8, font_size // 4), fill=(0, 0, 0, 172),
    )
    cursor = y
    for line, box in zip(lines, boxes, strict=True):
        line_width = box[2] - box[0]
        draw.text(
            ((width - line_width) // 2, cursor), line, font=font, fill="white",
            stroke_width=2, stroke_fill="black", anchor='lt',
        )
        cursor += line_height + spacing
    canvas.save(destination)


def _ffconcat_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace("'", "'\\''")


def render_subtitled_video(
    source: Path,
    segments: list[dict],
    language: str,
    *,
    output_name: str = "",
) -> dict:
    """Burn captions into a new MP4 while preserving the original audio."""
    segments = split_caption_segments(segments)
    encoder = _select_encoder(os.getenv('MEDIA_SUBTITLE_ENCODER', 'auto').strip() or 'auto')
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", output_name or source.stem).strip("_")[:70]
    signature = hashlib.sha256(json.dumps([
        "subtitle-render-v4-yuva-strip",
        encoder,
        str(source.resolve()), source.stat().st_size, source.stat().st_mtime_ns, language,
        [(item["start_seconds"], item["end_seconds"], item.get("subtitle_text", item.get("text")))
         for item in segments],
    ], ensure_ascii=False).encode()).hexdigest()[:16]
    OUTPUT_CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    subtitle = OUTPUT_CLIPS_DIR / f"{safe}_sub_{language}_{signature}.srt"
    output = OUTPUT_CLIPS_DIR / f"{safe}_sub_{language}_{signature}.mp4"
    if output.is_file() and output.stat().st_size > 1024 and subtitle.is_file():
        return {"video_file": str(output), "subtitle_file": str(subtitle), "cache_hit": True}
    write_srt(segments, subtitle)
    temporary = output.with_name(f".{output.stem}_{uuid.uuid4().hex}.mp4")
    try:
        width, height, video_duration = _video_geometry(source)
        with tempfile.TemporaryDirectory(prefix="media-subtitles-") as directory:
            workspace = Path(directory)
            strip_height = min(height, max(150, round(height * .25)))
            transparent = workspace / "transparent.png"
            Image.new("RGBA", (width, strip_height), (0, 0, 0, 0)).save(transparent)
            timeline: list[tuple[Path, float]] = []
            cursor = 0.0
            for index, item in enumerate(segments):
                start, end = float(item["start_seconds"]), float(item["end_seconds"])
                if start > cursor:
                    timeline.append((transparent, start - cursor))
                caption = workspace / f"caption_{index:05d}.png"
                _caption_png(width, height, str(item.get("subtitle_text", item.get("text", ""))), caption,
                             crop_top=height-strip_height)
                timeline.append((caption, max(0.04, end - start)))
                cursor = end
            if video_duration > cursor:
                timeline.append((transparent, video_duration - cursor))
            manifest = workspace / "captions.ffconcat"
            rows = ["ffconcat version 1.0"]
            for image, duration in timeline:
                rows.extend((f"file '{_ffconcat_path(image)}'", f"duration {duration:.6f}"))
            # The concat demuxer needs the last image repeated to honor its duration.
            rows.append(f"file '{_ffconcat_path(timeline[-1][0])}'")
            manifest.write_text("\n".join(rows) + "\n", encoding="utf-8")
            command = [
                "ffmpeg", "-y", "-i", str(source), "-f", "concat", "-safe", "0", "-i", str(manifest),
                "-filter_complex", "[1:v]format=yuva420p[subs];[0:v][subs]overlay=0:main_h-overlay_h:format=yuv420[out]",
                "-map", "[out]", "-map", "0:a?", "-c:v", encoder, *ENCODERS[encoder],
                "-pix_fmt", "yuv420p", "-profile:v", "high", "-c:a", "copy",
                "-t", f"{video_duration:.6f}", "-movflags", "+faststart", str(temporary),
            ]
            completed = _export(command, encoder)
            if completed.returncode != 0 or not temporary.is_file() or temporary.stat().st_size <= 1024:
                detail = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else "salida vacía"
                raise ValueError(f"FFmpeg no pudo incrustar los subtítulos: {detail[:300]}")
            temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return {"video_file": str(output), "subtitle_file": str(subtitle), "cache_hit": False}
