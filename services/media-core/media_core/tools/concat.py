"""MCP tool to concatenate multiple video clips into a single continuous MP4."""

import re
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

from media_core.config import OUTPUT_CLIPS_DIR
from media_core.runtime import measured


@measured
def media_concat_clips(
    clip_paths: list[str] | str,
    output_filename: str = "",
) -> dict[str, Any]:
    """Une múltiples archivos de vídeo (clips MP4) en un único archivo continuo.

    Útil cuando se tienen varios cortes independientes y se desea fusionar
    en una sola pieza unificada sin pérdidas de calidad.

    Args:
        clip_paths: Lista de rutas absolutas o relativas a los clips de vídeo MP4 a unir.
        output_filename: Nombre opcional para el archivo de salida (por defecto 'concat_<uuid>.mp4').
    """
    if isinstance(clip_paths, str):
        paths = [p.strip().strip("'\"") for p in re.split(r'[\n,]+', clip_paths) if p.strip()]
    else:
        paths = [str(p).strip().strip("'\"") for p in clip_paths if p]

    if len(paths) < 2:
        return {
            "status": "error",
            "message": "Se requieren al menos 2 archivos de vídeo para concatenar.",
        }

    resolved_paths: list[Path] = []
    for p in paths:
        path_obj = Path(p).resolve()
        if not path_obj.exists() or path_obj.stat().st_size < 1024:
            return {
                "status": "error",
                "message": f"El archivo de clip no existe o está vacío: {p}",
            }
        resolved_paths.append(path_obj)

    OUTPUT_CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    if not output_filename:
        out_name = f"concat_{uuid.uuid4().hex[:8]}.mp4"
    else:
        out_name = output_filename if output_filename.endswith(".mp4") else f"{output_filename}.mp4"

    out_file = OUTPUT_CLIPS_DIR / out_name

    # Create temporary concat demuxer manifest
    manifest_path = None
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as manifest:
        manifest_path = Path(manifest.name)
        for p in resolved_paths:
            # Escape single quotes for FFmpeg concat demuxer
            safe_p = str(p).replace("'", "'\\''")
            manifest.write(f"file '{safe_p}'\n")

    try:
        # First attempt: stream copy (lossless and instant)
        cmd_copy = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(manifest_path),
            "-c", "copy",
            str(out_file),
        ]
        res = subprocess.run(cmd_copy, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)

        # Fallback if stream copy fails (e.g. codecs or resolutions differ)
        if res.returncode != 0 or not out_file.exists() or out_file.stat().st_size < 1024:
            cmd_reencode = [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", str(manifest_path),
                "-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
                "-c:a", "aac",
                str(out_file),
            ]
            res_re = subprocess.run(cmd_reencode, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=240)
            if res_re.returncode != 0 or not out_file.exists() or out_file.stat().st_size < 1024:
                return {
                    "status": "error",
                    "message": "Error al unir los clips con FFmpeg.",
                }

        size_mb = round(out_file.stat().st_size / (1024 * 1024), 2)
        return {
            "status": "success",
            "message": f"Se han unido con éxito {len(resolved_paths)} clips en un único archivo.",
            "concat_file": str(out_file),
            "clips_count": len(resolved_paths),
            "size_mb": size_mb,
            "clips_sources": [str(p) for p in resolved_paths],
        }
    finally:
        if manifest_path and manifest_path.exists():
            try:
                manifest_path.unlink(missing_ok=True)
            except OSError:
                pass
