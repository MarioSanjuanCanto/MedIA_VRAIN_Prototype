"""Assemble selected results, never exploratory tool outputs, into one file per kind."""
import hashlib
import os
from pathlib import Path
import subprocess
import uuid


def assemble(paths: list[Path], kind: str, destination: Path) -> Path:
    if len(paths) == 1:
        return paths[0]
    if not paths:
        raise ValueError('No hay archivos para preparar el resultado.')
    signature = [(str(p.resolve()), p.stat().st_size, p.stat().st_mtime_ns) for p in paths]
    digest = hashlib.sha256(repr(signature).encode()).hexdigest()[:24]
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / f'resultado_{digest}{".wav" if kind == "audio" else ".mp4"}'
    if output.is_file() and output.stat().st_size > 44:
        return output
    token = uuid.uuid4().hex
    temporary = destination / f'.{token}{output.suffix}'
    manifest = destination / f'.{token}.ffconcat'
    try:
        if kind == 'audio':
            # Normalize before joining: chunks may use different WAV/MP3 formats.
            inputs = [arg for path in paths for arg in ('-i', str(path))]
            filters = [f'[{i}:a]aresample=24000,aformat=sample_fmts=s16:channel_layouts=mono,asetpts=PTS-STARTPTS[a{i}]' for i in range(len(paths))]
            filters.append(''.join(f'[a{i}]' for i in range(len(paths))) + f'concat=n={len(paths)}:v=0:a=1[out]')
            command = ['ffmpeg', '-v', 'error', *inputs, '-filter_complex', ';'.join(filters), '-map', '[out]', '-c:a', 'pcm_s16le', str(temporary)]
        else:
            # Selected cuts come from the same source video and retain its streams.
            manifest.write_text(''.join("file '" + str(p.resolve()).replace('\\', '/').replace("'", "'\\''") + "'\n" for p in paths), encoding='utf-8')
            command = ['ffmpeg', '-v', 'error', '-f', 'concat', '-safe', '0', '-i', str(manifest),
                '-c:v', 'libx264', '-preset', 'fast', '-threads', '2', '-c:a', 'aac', '-movflags', '+faststart', str(temporary)]
        subprocess.run(command, check=True, capture_output=True, timeout=1800,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if not temporary.is_file() or temporary.stat().st_size <= 44:
            raise RuntimeError('No se pudo preparar el archivo completo.')
        temporary.replace(output)
        return output
    finally:
        temporary.unlink(missing_ok=True)
        manifest.unlink(missing_ok=True)
