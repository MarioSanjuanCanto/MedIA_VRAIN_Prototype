"""FFmpeg operations used by Media Core tools."""

import subprocess
import sys
import math
import uuid

def seconds(value):
    parts = str(value).split(":")
    result = sum(float(v) * 60 ** i for i, v in enumerate(reversed(parts)))
    if not math.isfinite(result):
        raise ValueError("Tiempo inválido")
    return result
from pathlib import Path


def extract_audio(video_path: Path, output_audio: Path) -> bool:
    """Extract a 16 kHz mono MP3 track for WhisperX."""
    try:
        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-acodec",
            "libmp3lame",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-b:a",
            "64k",
            str(output_audio),
        ]
        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
        )
        return result.returncode == 0 and output_audio.exists()
    except Exception as error:
        print(f"[FFmpeg Audio Extraction Error] {error}", file=sys.stderr)
        return False


def cut_clip(
    input_video: Path, start_time: str, end_time: str, output_path: Path, *, precise: bool = False
) -> bool:
    """Cut a clip with stream copy first and a fast re-encode fallback."""
    try:
        start, end = seconds(start_time), seconds(end_time)
        if start < 0 or end <= start:
            return False
    except (TypeError, ValueError):
        return False
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(output_path.stem + "." + uuid.uuid4().hex + ".mp4")
    try:
        import os
        threads = str(min(os.cpu_count() or 4, 8))
        encode = ["-preset", "veryfast", "-threads", threads, "-c:v", "libx264", "-crf", "20", "-c:a", "aac"]
        codecs = [encode] if precise else [["-c", "copy"], encode]
        for codec in codecs:
            command = ["ffmpeg", "-y", "-ss", str(start), "-i", str(input_video),
                       "-t", str(end - start), *codec, "-avoid_negative_ts", "make_zero",
                       "-movflags", "+faststart", str(temporary)]
            try:
                result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=max(120, (end - start) * 4) if precise else 120)
                if result.returncode == 0 and temporary.exists() and temporary.stat().st_size > 1024:
                    temporary.replace(output_path)
                    return True
            except (OSError, subprocess.TimeoutExpired):
                pass
            temporary.unlink(missing_ok=True)
        return False
    finally:
        temporary.unlink(missing_ok=True)
