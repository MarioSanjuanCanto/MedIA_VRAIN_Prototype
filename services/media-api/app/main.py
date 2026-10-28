"""Local MedIA web API. Only media-core owns inference and the vector store."""
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import timedelta
import hashlib
import json
from functools import lru_cache
import mimetypes
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time
from typing import Literal
import unicodedata
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response, UploadFile
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from pydantic import BaseModel, Field
from .bridge import HermesBridge
from .cost_report import build_cost_report
from .deliverables import assemble
from .provider_costs import provider_costs
from .turn_clips import parse_turn_clip_request

load_dotenv(Path(__file__).resolve().parents[1] / '.env')
ROOT = Path(os.getenv('MEDIA_REPO_ROOT', str(Path(__file__).resolve().parents[3]))).resolve()


def configured_directory(name: str, default: Path) -> Path:
    configured = os.getenv(name, '').strip()
    path = Path(configured).expanduser() if configured else default
    return (ROOT / path).resolve() if not path.is_absolute() else path.resolve()


SHARED = configured_directory('MEDIA_SHARED_DIR', ROOT / 'shared')
DATA = configured_directory('MEDIA_DATA_DIR', ROOT / 'data')
STATE = configured_directory('MEDIA_WEB_DATA', DATA / 'state')
TRANSCRIPTS = SHARED / 'test-vectors' / 'transcripts'
VIDEOS = DATA / 'test-vectors' / 'videos'
LEGACY_VIDEOS = (ROOT / 'experiments' / 'test_vectors' / 'videos').resolve()
CACHE = DATA / 'cache' / 'transcriptions'
VISUAL_CACHE = DATA / 'cache' / 'visual'
OCR_CACHE = DATA / 'cache' / 'ocr'
KEYFRAMES = DATA / 'cache' / 'keyframes'
ROOTS = {
    'video': VIDEOS,
    'clip': DATA / 'outputs' / 'clips',
    'audio': DATA / 'outputs' / 'audio',
}
MCP_URL = os.getenv('MEDIA_MCP_URL', 'http://127.0.0.1:8001/mcp')
HERMES_WEBUI_URL = (os.getenv('HERMES_WEBUI_URL', '').strip() or 'http://127.0.0.1:8787').rstrip('/')
EXTENSIONS = {'.mp4', '.mov', '.mkv', '.webm', '.m4v'}
bridge = HermesBridge(ROOT)
tasks = set()
chat_tasks = {}
chat_live_jobs = {}
indexing_jobs = {}
slots = asyncio.Semaphore(2)
thumb_slots = asyncio.Semaphore(2)
cleanup_executor = None


@lru_cache(maxsize=128)
def cached_sha256(path: str, size: int, modified_ns: int) -> str:
    del size, modified_ns
    digest = hashlib.sha256()
    with Path(path).open('rb') as media:
        for chunk in iter(lambda: media.read(4 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def file_fingerprint(path: Path) -> dict:
    resolved = path.resolve()
    before = resolved.stat()
    sha256 = cached_sha256(str(resolved), before.st_size, before.st_mtime_ns)
    after = resolved.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise OSError(f'El archivo cambió mientras se calculaba su huella: {resolved}')
    return {
        'algorithm': 'sha256',
        'sha256': sha256,
        'size': before.st_size,
    }


def source_matches_file(source_identity, path: Path) -> bool:
    if isinstance(source_identity, dict):
        try:
            if source_identity.get('algorithm') != 'sha256':
                return False
            stat = path.stat()
            if 'size' in source_identity and source_identity['size'] != stat.st_size:
                return False
            return source_identity == file_fingerprint(path)
        except OSError:
            return False
    if isinstance(source_identity, list) and len(source_identity) == 3:
        stat = path.stat()
        if source_identity == [str(path.resolve()), stat.st_size, stat.st_mtime_ns]:
            return True
        try:
            orig_name = Path(source_identity[0]).name.casefold()
            if orig_name == path.name.casefold() and source_identity[1] == stat.st_size:
                return True
        except Exception:
            pass
        return False
    return False


def db():
    STATE.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(STATE / 'history.sqlite')
    con.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
    con.execute('''CREATE TABLE IF NOT EXISTS deleted_conversations (
        conversation_id TEXT PRIMARY KEY, deleted_at REAL NOT NULL
    )''')
    con.execute('''CREATE TABLE IF NOT EXISTS hermes_cleanup (
        conversation_id TEXT PRIMARY KEY,
        session_id TEXT,
        effective_session_id TEXT,
        mode TEXT,
        attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt_at REAL NOT NULL,
        last_error TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )''')
    cleanup_columns = {row[1] for row in con.execute('PRAGMA table_info(hermes_cleanup)')}
    if 'effective_session_id' not in cleanup_columns:
        con.execute('ALTER TABLE hermes_cleanup ADD COLUMN effective_session_id TEXT')
    return con


def save(job):
    # DELETE marks the in-memory object before cancelling its coroutine.  A
    # cancelled asyncio.to_thread call cannot stop the worker immediately, so
    # its finally block may still arrive after the database row was removed.
    # Never let that stale completion recreate a deleted conversation.
    if job.get('_discarded'):
        return
    job['updated_at'] = time.time()
    with db() as con:
        con.execute('INSERT OR REPLACE INTO jobs VALUES (?,?)', (job['id'], json.dumps(job, ensure_ascii=False)))


def get_job(job_id):
    with db() as con:
        row = con.execute('SELECT body FROM jobs WHERE id=?', (job_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Trabajo no encontrado')
    return json.loads(row[0])


def recent():
    with db() as con:
        return sorted((json.loads(x[0]) for x in con.execute('SELECT body FROM jobs')), key=lambda x: x['created_at'], reverse=True)


def media_jobs():
    return [job for job in recent() if job.get('kind', 'media') == 'media']


def chat_jobs(conversation_id=None):
    jobs = [job for job in recent() if job.get('kind') == 'hermes_chat']
    if conversation_id is not None:
        jobs = [job for job in jobs if job['conversation_id'] == conversation_id]
    for job in jobs:
        if not job.get('artifacts') and job.get('answer'):
            scan_artifacts_from_text(job['answer'], job)
    return jobs


def conversation_is_deleted(conversation_id: str) -> bool:
    with db() as con:
        return con.execute(
            'SELECT 1 FROM deleted_conversations WHERE conversation_id=?',
            (conversation_id,),
        ).fetchone() is not None


def delete_conversation_state(
    conversation_id: str, session_id=None, effective_session_id=None, mode=None,
) -> int:
    """Atomically tombstone, enqueue Hermes cleanup, and delete chat jobs."""
    now = time.time()
    with db() as con:
        con.execute(
            'INSERT OR IGNORE INTO deleted_conversations VALUES (?,?)',
            (conversation_id, now),
        )
        con.execute('''
            INSERT INTO hermes_cleanup
                (conversation_id, session_id, effective_session_id, mode,
                 attempts, next_attempt_at, last_error, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(conversation_id) DO UPDATE SET
                session_id=COALESCE(excluded.session_id, hermes_cleanup.session_id),
                effective_session_id=COALESCE(
                    excluded.effective_session_id, hermes_cleanup.effective_session_id
                ),
                mode=COALESCE(excluded.mode, hermes_cleanup.mode),
                next_attempt_at=excluded.next_attempt_at,
                last_error=NULL,
                updated_at=excluded.updated_at
        ''', (
            conversation_id, session_id, effective_session_id, mode,
            0, now, None, now, now,
        ))
        rows = con.execute('SELECT id, body FROM jobs').fetchall()
        job_ids = []
        for job_id, body in rows:
            try:
                job = json.loads(body)
            except (TypeError, ValueError):
                continue
            if job.get('kind') == 'hermes_chat' and job.get('conversation_id') == conversation_id:
                job_ids.append(job_id)
        con.executemany('DELETE FROM jobs WHERE id=?', ((job_id,) for job_id in job_ids))
    return len(job_ids)


def due_hermes_cleanup():
    with db() as con:
        row = con.execute('''
            SELECT conversation_id, session_id, effective_session_id, mode, attempts
            FROM hermes_cleanup
            WHERE next_attempt_at <= ?
            ORDER BY created_at
            LIMIT 1
        ''', (time.time(),)).fetchone()
    return row


async def cleanup_hermes_sessions_once() -> bool:
    pending = due_hermes_cleanup()
    if not pending:
        return False
    conversation_id, session_id, effective_session_id, mode, attempts = pending
    identity = bridge.session_identity(conversation_id)
    session_id = session_id or identity.get('stored_session_id')
    effective_session_id = effective_session_id or identity.get('session_id')
    mode = mode or identity.get('mode')
    with db() as con:
        con.execute('''UPDATE hermes_cleanup
            SET session_id=?, effective_session_id=?, mode=?, updated_at=?
            WHERE conversation_id=?
        ''', (session_id, effective_session_id, mode, time.time(), conversation_id))
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            cleanup_executor,
            bridge.delete_session,
            conversation_id,
            session_id,
            mode,
            effective_session_id,
        )
    except Exception as exc:
        # Keep the durable target on every operational/programming failure. A
        # later deployment or recovered Hermes instance can retry it safely.
        retry_at = time.time() + min(60, 2 ** min(attempts, 5))
        with db() as con:
            latest = bridge.session_identity(conversation_id)
            con.execute('''
                UPDATE hermes_cleanup
                SET session_id=COALESCE(?, session_id),
                    effective_session_id=COALESCE(?, effective_session_id),
                    mode=COALESCE(?, mode), attempts=?, next_attempt_at=?,
                    last_error=?, updated_at=?
                WHERE conversation_id=?
            ''', (
                latest.get('stored_session_id'), latest.get('session_id'), latest.get('mode'),
                attempts + 1, retry_at, str(exc)[:1000], time.time(), conversation_id,
            ))
    else:
        with db() as con:
            con.execute(
                'DELETE FROM hermes_cleanup WHERE conversation_id=?',
                (conversation_id,),
            )
    return True


async def hermes_cleanup_worker():
    while True:
        try:
            processed = await cleanup_hermes_sessions_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            # SQLite/IO failures are transient here; the durable row remains
            # available and the worker must stay alive for a later retry.
            processed = False
        await asyncio.sleep(0.05 if processed else 0.1)


@asynccontextmanager
async def lifespan(app):
    global cleanup_executor
    for job in recent():
        if job['status'] in {'queued', 'running'}:
            job.update(status='failed', error='La API se reinició. Los resultados anteriores se conservan; vuelve a iniciar la petición.')
            save(job)
    cost_warmup = asyncio.create_task(provider_costs(ROOT, STATE))
    tasks.add(cost_warmup)
    cost_warmup.add_done_callback(tasks.discard)
    cleanup_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='hermes-cleanup')
    cleanup_worker = asyncio.create_task(hermes_cleanup_worker())
    tasks.add(cleanup_worker)
    cleanup_worker.add_done_callback(tasks.discard)
    yield
    active_tasks = list(tasks)
    for task in active_tasks:
        task.cancel()
    await asyncio.gather(*active_tasks, return_exceptions=True)
    executor = cleanup_executor
    cleanup_executor = None
    if executor is not None:
        await asyncio.to_thread(executor.shutdown, wait=True, cancel_futures=False)
    await asyncio.to_thread(bridge.close)


app = FastAPI(title='MedIA API', lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=['http://localhost:3000', 'http://127.0.0.1:3000'], allow_methods=['GET', 'POST', 'DELETE'], allow_headers=['Content-Type'])
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])


@app.middleware('http')
async def local_origin(request: Request, call_next):
    # Dev UI is same-origin through the Next rewrite; do not expose mutations to other sites.
    origin = request.headers.get('origin')
    allowed = {'http://localhost:3000', 'http://127.0.0.1:3000', 'http://localhost:8010', 'http://127.0.0.1:8010'}
    if origin and origin not in allowed:
        return JSONResponse({'detail': 'Origen no permitido'}, status_code=403)
    return await call_next(request)


def safe_file(kind, name):
    root = ROOTS.get(kind)
    if not root or Path(name).name != name or ':' in name or '\\' in name:
        raise HTTPException(404, 'Archivo no encontrado')
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        if kind == 'video' and LEGACY_VIDEOS.is_dir():
            legacy_path = (LEGACY_VIDEOS / name).resolve()
            if legacy_path.is_relative_to(LEGACY_VIDEOS) and legacy_path.is_file():
                path = legacy_path
            else:
                raise HTTPException(404, 'Archivo no encontrado')
        else:
            raise HTTPException(404, 'Archivo no encontrado')
    allowed = EXTENSIONS if kind != 'audio' else {'.wav', '.mp3', '.ogg', '.m4a'}
    if path.suffix.lower() not in allowed:
        raise HTTPException(404, 'Tipo de archivo no permitido')
    return path


def artifact(path):
    try:
        candidate = Path(path)
        if not candidate.is_absolute() and candidate.parts[:1] == ('keyframes',):
            p = (KEYFRAMES.parent / candidate).resolve()
        else:
            p = candidate.resolve()
    except (ValueError, TypeError, OSError):
        return None
    for kind, root in ROOTS.items():
        if kind != 'video' and p.parent == root.resolve() and p.is_file() and p.suffix.lower() in {'.mp4', '.webm', '.wav', '.mp3', '.ogg'}:
            from urllib.parse import quote
            return {'kind': kind, 'name': p.name, 'url': f'/api/files/{kind}/{quote(p.name)}'}
    if p.is_file() and p.suffix.lower() in {'.jpg', '.jpeg', '.png'} and p.is_relative_to(KEYFRAMES.resolve()):
        video_id = p.parent.name
        from urllib.parse import quote
        return {'kind': 'image', 'name': p.name, 'url': f'/api/keyframes/{video_id}/{quote(p.name)}'}
    return None


def collect(value, job):
    artifacts = job.setdefault('artifacts', [])
    if isinstance(value, str):
        item = artifact(value)
        if item and item['url'] not in {a['url'] for a in artifacts}:
            artifacts.append(item)
        elif value.startswith(('{', '[')):
            try:
                collect(json.loads(value), job)
            except ValueError:
                pass
    elif isinstance(value, list):
        for v in value:
            collect(v, job)
    elif isinstance(value, dict):
        if value.get('status') in {'error', 'failed', 'running', 'queued'}:
            return
        if value.get('executive_summary'):
            job['summary'] = value['executive_summary']
        for key, v in value.items():
            if key not in {'events', 'completed_chunks'}:
                collect(v, job)


async def publish_deliverables(job, groups, answer=''):
    """Prefer files explicitly selected in the final answer; otherwise last successful result."""
    selected = []
    for kind in ('clip', 'audio'):
        candidates = [a for group in groups for a in group if a['kind'] == kind]
        # Only references to files actually returned by this request can be final.
        referenced = {a['url']: a for a in candidates if a['name'] in answer or a['url'] in answer}
        chosen = sorted(referenced.values(), key=lambda a: answer.find(a['name'])) if referenced else next(
            ([a for a in group if a['kind'] == kind] for group in reversed(groups) if any(a['kind'] == kind for a in group)), [])
        chosen = list({a['url']: a for a in chosen}.values())
        if chosen:
            if kind == 'clip':
                # Several requested turns are separate downloadable clips.
                selected.extend(chosen)
            else:
                paths = [safe_file(kind, a['name']) for a in chosen]
                path = await asyncio.to_thread(assemble, paths, kind, ROOTS[kind])
                selected.append(artifact(str(path)))
    image_groups = [[a for a in group if a['kind'] == 'image'] for group in groups]
    referenced_images = [a for group in image_groups for a in group if a['name'] in answer or a['url'] in answer]
    images = referenced_images or next((group for group in reversed(image_groups) if group), [])
    selected.extend({a['url']: a for a in images}.values())
    job['artifacts'] = selected


def scan_artifacts_from_text(text: str, job: dict):
    if not text:
        return
    existing_urls = {a['url'] for a in job.get('artifacts', [])}
    for kind, root in (('clip', ROOTS['clip']), ('audio', ROOTS['audio'])):
        if not root.is_dir():
            continue
        try:
            for file in root.iterdir():
                if file.is_file() and file.name in text:
                    item = artifact(str(file))
                    if item and item['url'] not in existing_urls:
                        job.setdefault('artifacts', []).append(item)
                        existing_urls.add(item['url'])
        except OSError:
            pass
    # Scan for keyframes in text (e.g. MEDIA:...keyframes/TV04/frame_0514.jpg)
    keyframe_matches = re.finditer(r'keyframes[\\/]([a-zA-Z0-9_-]+)[\\/]([a-zA-Z0-9_\-.]+\.(?:jpg|jpeg|png))', text, re.IGNORECASE)
    for m in keyframe_matches:
        vid, fname = m.group(1).upper(), m.group(2)
        kf_path = KEYFRAMES / vid / fname
        if kf_path.is_file():
            url = f'/api/keyframes/{vid}/{fname}'
            if url not in existing_urls:
                job.setdefault('artifacts', []).append({'kind': 'image', 'name': fname, 'url': url})
                existing_urls.add(url)


@lru_cache(maxsize=256)
def probe_video_meta(path_str: str, size: int, mtime_ns: int) -> dict:
    del size, mtime_ns
    try:
        out = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration:stream=width,height,codec_type', '-of', 'json', path_str],
            check=True, capture_output=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )
        data = json.loads(out.stdout)
        dur = float(data.get('format', {}).get('duration') or 0.0)
        width, height = 0, 0
        has_video = False
        for s in data.get('streams', []):
            if s.get('codec_type') == 'video':
                has_video = True
                width = int(s.get('width') or 0)
                height = int(s.get('height') or 0)
                break
        return {'valid': has_video, 'duration': dur, 'width': width, 'height': height}
    except Exception:
        return {'valid': False, 'duration': 0.0, 'width': 0, 'height': 0}


def catalog():
    rows = []
    seen = set()
    paths = []
    if VIDEOS.is_dir():
        paths.extend(sorted(VIDEOS.glob('*'), key=lambda x: x.name.casefold()))
    if LEGACY_VIDEOS.is_dir() and LEGACY_VIDEOS != VIDEOS.resolve():
        for p in sorted(LEGACY_VIDEOS.glob('*'), key=lambda x: x.name.casefold()):
            if p.name.casefold() not in {x.name.casefold() for x in paths}:
                paths.append(p)

    for p in paths:
        if p.suffix.lower() not in EXTENSIONS or not p.is_file():
            continue
        vid = re.sub(r'_demo$', '', p.stem, flags=re.I).upper()
        if vid in seen:
            continue
        seen.add(vid)
        data = {}
        for cache in (CACHE / f'{vid}_whisperx.json', TRANSCRIPTS / f'{vid}.json'):
            try:
                candidate = json.loads(cache.read_text(encoding='utf-8'))
                if str(candidate.get('id', '')).upper() == vid:
                    data = candidate
                    break
            except (OSError, ValueError):
                continue
        st = p.stat()
        verified = source_matches_file(data.get('request_metadata', {}).get('source'), p)
        ready = verified and bool(data.get('segments') or data.get('full_text')) and data.get('status', 'success') == 'success'

        is_indexing = (p.name in indexing_jobs and indexing_jobs[p.name].get('status') == 'indexing')
        indexing_stage = indexing_jobs.get(p.name, {}).get('stage', '') if is_indexing else ''

        if ready and data.get('duration_seconds'):
            duration = float(data.get('duration_seconds', 0))
        else:
            meta = probe_video_meta(str(p.resolve()), st.st_size, st.st_mtime_ns)
            duration = meta.get('duration', 0.0)

        status = 'indexing' if is_indexing else ('ready' if ready else ('unverified' if data else 'new'))
        rows.append({
            'id': vid,
            'filename': p.name,
            'title': p.stem.replace('_', ' '),
            'duration_seconds': round(duration, 2),
            'language': data.get('language', '') if ready else '',
            'size_mb': round(st.st_size / 1048576, 1),
            'status': status,
            'indexing_stage': indexing_stage,
            'indexed': ready and data.get('indexed_in_search') is True,
            'visual_indexed': (VISUAL_CACHE / f'{vid}_visual.json').is_file(),
            'url': '/api/files/video/' + p.name,
            'thumbnail': '/api/thumbnail/' + p.name,
        })
    return rows


async def mcp_call(name, arguments):
    async with streamablehttp_client(MCP_URL, timeout=timedelta(seconds=15), sse_read_timeout=timedelta(seconds=1800)) as (read, write, _):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=1800)) as session:
            await session.initialize()
            result = await session.call_tool(name, arguments)
            if result.isError:
                raise RuntimeError('media-core: ' + ' '.join(getattr(c, 'text', '') for c in result.content))
            structured = result.structuredContent
            if structured is not None:
                return structured.get('result', structured)
            texts = [c.text for c in result.content if getattr(c, 'type', None) == 'text']
            parsed = [json.loads(t) for t in texts]
            return parsed[0] if len(parsed) == 1 else parsed


def checked(result):
    values = result if isinstance(result, list) else [result]
    for value in values:
        if isinstance(value, dict) and value.get('status') in {'error', 'failed'}:
            raise RuntimeError(value.get('message') or str(value.get('result', 'La operación de media-core falló.')))
    return result


async def cancel_media_core_job(job, timeout_seconds: float = 3.0):
    """Best-effort cancellation for the one Media Core job owned by this turn."""
    remote_id = job.get('media_core_job_id')
    owner_id = job.get('media_core_owner_id')
    arguments = {}
    if remote_id:
        arguments['job_id'] = remote_id
    if owner_id:
        arguments['owner_id'] = owner_id
    if not arguments:
        return None
    try:
        return await asyncio.wait_for(
            mcp_call('media_job_cancel', arguments),
            timeout=timeout_seconds,
        )
    except Exception as exc:
        # Chat deletion/cancellation must remain available even if Media Core is
        # restarting. Keep the diagnostic on the in-memory job without failing
        # the user's lifecycle operation.
        job.setdefault('events', []).append({
            'stage': 'No se pudo confirmar la cancelación en Media Core',
            'at': round(time.time() - job.get('created_at', time.time()), 1),
            'warning': str(exc)[:300],
        })
        return None


async def tool(job, name, args, stage):
    job['stage'] = stage
    job['events'].append({'stage': stage, 'at': round(time.time() - job['created_at'], 1)})
    save(job)
    result = checked(await mcp_call(name, args))
    sequence = 0
    if isinstance(result, dict) and result.get('job_id'):
        remote_id = result['job_id']
        deadline = time.monotonic() + 1800
        while True:
            if time.monotonic() > deadline:
                raise RuntimeError('media-core sigue trabajando. Consulta su estado antes de repetir la petición.')
            await asyncio.sleep(2)
            state = checked(await mcp_call('media_job_status', {'job_id': remote_id, 'after_sequence': sequence}))
            for event in state.get('events', []):
                sequence = max(sequence, event.get('sequence', 0))
            save(job)
            if state.get('status') == 'completed':
                result = checked(state.get('result'))
                break
    collect(result, job)
    save(job)
    return result


async def auto_index_video(filename: str, path: Path):
    vid = re.sub(r'_demo$', '', path.stem, flags=re.I).upper()
    indexing_jobs[filename] = {
        'status': 'indexing',
        'stage': 'Iniciando transcripción e indexación...',
        'started_at': time.time(),
        'error': None,
    }
    try:
        # 1. Transcribe audio and sync semantic chunks into Chroma search collection
        indexing_jobs[filename]['stage'] = 'Transcribiendo audio e indexando en búsqueda...'
        try:
            await mcp_call('media_transcribe', {
                'source': filename,
                'language': 'auto',
                'diarization': False,
                'index_transcript': True,
                'background': False,
            })
        except Exception as te:
            print(f"[Auto-Index] Warning transcribing {filename}: {te}", flush=True)

        # 2. Extract scene keyframes and index visual embeddings (CLIP) into Chroma
        indexing_jobs[filename]['stage'] = 'Extrayendo fotogramas y generando índice visual...'
        try:
            await mcp_call('media_index_visual', {
                'video_id': vid,
                'force': False,
            })
        except Exception as ve:
            print(f"[Auto-Index] Warning visual indexing {filename}: {ve}", flush=True)

        indexing_jobs[filename] = {
            'status': 'ready',
            'stage': 'Indexación completa',
            'completed_at': time.time(),
            'error': None,
        }
    except Exception as exc:
        indexing_jobs[filename] = {
            'status': 'failed',
            'stage': f'Error en indexación: {exc}',
            'error': str(exc),
            'failed_at': time.time(),
        }


class NewJob(BaseModel):
    video: str
    action: Literal['summary', 'clips', 'audio', 'transcribe', 'chat'] = 'summary'
    message: str = Field(default='', max_length=8000)
    language: Literal['es', 'ca', 'en'] = 'es'
    source_language: Literal['es', 'ca', 'en', 'auto'] = 'auto'
    conversation_id: str = Field(default='', max_length=64, pattern=r'^[a-zA-Z0-9-]*$')


class ChatRequest(BaseModel):
    message: str = Field(max_length=12000)
    conversation_id: str = Field(default='', max_length=64, pattern=r'^[a-zA-Z0-9-]*$')
    reasoning_effort: Literal['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'] | None = None
    voice_language: Literal['es', 'ca', 'en'] | None = None


class ReasoningRequest(BaseModel):
    effort: Literal['minimal', 'low', 'medium', 'high', 'xhigh', 'max']


class VoiceReplyRequest(BaseModel):
    job_id: str = Field(pattern=r'^[a-zA-Z0-9-]{1,64}$')
    language: Literal['es', 'ca', 'en'] = 'es'


class VoiceGreetingRequest(BaseModel):
    language: Literal['es', 'ca', 'en'] = 'es'


class VoiceFillerRequest(VoiceGreetingRequest):
    index: int = Field(default=0, ge=0, le=3)


@app.post('/api/voice/filler')
async def voice_filler(req: VoiceFillerRequest):
    phrases = {
        'es': ['Estoy en ello.', 'Dame un momento, sigo con tu petición.',
               'Esto está tardando un poco; sigo pendiente.', 'Sigo por aquí, todavía no ha terminado.'],
        'ca': ['M’hi poso.', 'Un moment, continuo amb la teva petició.',
               'Això està trigant una mica; continuo pendent.', 'Encara soc aquí, això encara no ha acabat.'],
        'en': ['I’m on it.', 'Give me a moment, I’m still working on your request.',
               'This is taking a little longer; I’m keeping an eye on it.', 'I’m still here; it hasn’t finished yet.'],
    }
    text = phrases[req.language][req.index]
    try:
        result = checked(await mcp_call('media_voice_reply', {'answer': text, 'language': req.language, 'spoken_text': text}))
        name = Path(result['audio_file']).name
        safe_file('audio', name)
        return {'spoken_text': result['spoken_text'], 'url': '/api/files/audio/' + name}
    except Exception as exc:
        raise HTTPException(503, 'No se pudo preparar la frase de acompañamiento.') from exc


def split_voice_answer(answer: str) -> tuple[str, str]:
    match = re.search(r'\s*<media_voice>([\s\S]*?)</media_voice>\s*$', answer)
    if not match:
        return answer, ''
    spoken = re.sub(r'\s+', ' ', match[1]).strip()
    return answer[:match.start()].rstrip(), spoken


@app.post('/api/voice/greeting')
async def voice_greeting(req: VoiceGreetingRequest):
    greetings = {
        'es': '¡Hola, soy MedIA! Pulsa Hablar y cuéntame qué quieres hacer con tus vídeos; enviaré tu petición cuando hagas una pausa.',
        'ca': 'Hola, soc MedIA! Prem Parlar i explica’m què vols fer amb els teus vídeos; enviaré la petició quan facis una pausa.',
        'en': 'Hi, I’m MedIA! Press Talk and tell me what you want to do with your videos; I’ll send your request when you pause.',
    }
    try:
        result = checked(await mcp_call('media_voice_reply', {'answer': greetings[req.language], 'language': req.language,
                                                            'spoken_text': greetings[req.language]}))
        name = Path(result['audio_file']).name
        safe_file('audio', name)
        return {'spoken_text': result['spoken_text'], 'url': '/api/files/audio/' + name}
    except Exception as exc:
        raise HTTPException(503, f'No se pudo preparar el saludo: {exc}') from exc


voice_slots = asyncio.Semaphore(2)


@app.get('/api/voice/status')
async def voice_status():
    try:
        return checked(await mcp_call('media_voice_status', {}))
    except Exception as exc:
        raise HTTPException(503, 'El servicio de voz no está disponible. Reinicia Media Core con la versión actual.') from exc


@app.post('/api/voice/transcribe')
async def transcribe_voice(file: UploadFile, language: Literal['es', 'ca', 'en', 'auto'] = 'es'):
    try:
        recording = await file.read(8 * 1024 * 1024 + 1)
    finally:
        await file.close()
    if not recording:
        raise HTTPException(400, 'La grabación está vacía.')
    if len(recording) > 8 * 1024 * 1024:
        raise HTTPException(413, 'La grabación supera 8 MB. Graba como máximo 60 segundos.')
    if voice_slots.locked():
        raise HTTPException(429, 'El servicio de voz está ocupado. Inténtalo en unos segundos.')
    async with voice_slots:
        try:
            return checked(await mcp_call('media_transcribe_microphone', {
                'audio_base64': base64.b64encode(recording).decode('ascii'), 'language': language,
            }))
        except Exception as exc:
            raise HTTPException(422, f'No se pudo transcribir: {exc}') from exc


@app.post('/api/voice/reply')
async def voice_reply(req: VoiceReplyRequest):
    job = get_job(req.job_id)
    if job.get('kind') != 'hermes_chat' or job.get('status') != 'completed':
        raise HTTPException(409, 'Espera a que termine la respuesta del chat.')
    if voice_slots.locked():
        raise HTTPException(429, 'El servicio de voz está ocupado. Inténtalo en unos segundos.')
    async with voice_slots:
        try:
            arguments = {'answer': job.get('answer', ''), 'language': req.language}
            if job.get('spoken_answer'):
                arguments['spoken_text'] = job['spoken_answer']
            result = checked(await mcp_call('media_voice_reply', arguments))
            name = Path(result['audio_file']).name
            safe_file('audio', name)
            # Don't return a stale answer after the conversation has been deleted.
            get_job(req.job_id)
            return {'spoken_text': result['spoken_text'], 'duration_seconds': result['duration_seconds'],
                    'url': '/api/files/audio/' + name, 'provider': 'piper'}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(503, f'No se pudo generar la voz: {exc}') from exc


async def webui_reasoning(effort: str | None = None):
    """Read or update the global reasoning preference shown by Hermes WebUI."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            if effort is None:
                response = await client.get(f'{HERMES_WEBUI_URL}/api/reasoning')
            else:
                response = await client.post(
                    f'{HERMES_WEBUI_URL}/api/reasoning',
                    json={'effort': effort},
                )
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail='No se pudo sincronizar el razonamiento con Hermes WebUI.',
        ) from exc


async def execute_turn_clips_chat(job, plan):
    """Cut numbered speaking turns in transcript order without an LLM selector."""
    video_id, person = plan['video_id'], plan['person']
    job['stage'] = 'Localizando intervenciones en la transcripción'
    save(job)
    speaker_result = checked(await mcp_call('media_speakers', {
        'video_id': video_id, 'person': person,
    }))
    turns = speaker_result.get('turns', []) if isinstance(speaker_result, dict) else []
    if not turns:
        raise RuntimeError(f'No se encontraron intervenciones de {person} en {video_id}.')

    if plan['mode'] == 'index':
        index = plan['index']
        if index > len(turns):
            raise RuntimeError(f'{person} tiene {len(turns)} intervenciones; no existe la número {index}.')
        indexes = [index]
    elif plan['mode'] == 'first':
        indexes = list(range(1, min(plan['count'], len(turns)) + 1))
    elif plan['mode'] == 'last':
        indexes = list(range(max(1, len(turns) - plan['count'] + 1), len(turns) + 1))
    else:
        indexes = list(range(1, len(turns) + 1))

    selected = []
    for offset in range(0, len(indexes), 10):
        batch = indexes[offset:offset + 10]
        job['stage'] = f'Cortando intervenciones {batch[0]}–{batch[-1]} de {len(turns)}'
        job['events'].append({'stage': job['stage'], 'at': round(time.time() - job['created_at'], 1)})
        save(job)
        result = checked(await mcp_call('media_clips', {
            'video_id': video_id, 'person': person, 'turn_index': batch[0],
            'max_clips': len(batch), 'cut_video_files': True,
        }))
        if not isinstance(result, list) or len(result) != len(batch):
            raise RuntimeError('Media Core no devolvió todos los turnos solicitados.')
        for index, clip in zip(batch, result):
            turn = turns[index - 1]
            if (clip.get('turn_index') != index
                    or clip.get('cut_status') != 'archivo_mp4_cortado_exitosamente'
                    or not clip.get('cut_video_file')
                    or abs(float(clip['start_seconds']) - float(turn['start_seconds'])) > 0.01
                    or abs(float(clip['end_seconds']) - float(turn['end_seconds'])) > 0.01):
                raise RuntimeError(f'El clip de la intervención {index} no coincide con el turno diarizado.')
            selected.append(clip)

    artifacts = [artifact(clip['cut_video_file']) for clip in selected]
    if any(item is None or item['kind'] != 'clip' for item in artifacts):
        raise RuntimeError('Algún MP4 no está disponible para descarga desde Media API.')
    job['artifacts'] = artifacts
    noun = 'clip' if len(selected) == 1 else 'clips'
    order = 'correspondiente al turno solicitado' if len(selected) == 1 else 'ordenados por aparición'
    lines = [f"He preparado {len(selected)} {noun} de las intervenciones de {speaker_result.get('query_person', person)} en {video_id}, {order}:"]
    for clip, item in zip(selected, artifacts):
        lines.append(f"{clip['turn_index']}. {clip['start_time']}–{clip['end_time']} · {item['url']}")
    job['answer'] = '\n'.join(lines)
    job.update(status='completed', stage='Listo')


async def execute_chat(job, req):
    async with slots:
        if job['status'] == 'cancelled':
            return
        job['status'] = 'running'
        job['stage'] = 'Hermes está trabajando'
        save(job)
        try:
            history = []
            for old in reversed(chat_jobs(job['conversation_id'])):
                if old['id'] == job['id'] or old['status'] != 'completed' or not old.get('answer'):
                    continue
                history.extend([
                    {'role': 'user', 'content': old['message']},
                    {'role': 'assistant', 'content': old['answer']},
                ])
            turn_plan = parse_turn_clip_request(req.message, history)
            if turn_plan:
                await execute_turn_clips_chat(job, turn_plan)
                return
            bridge_message = req.message
            if re.search(r'\b(?:clip|clips|corte|cortes|intervenci[oó]n|intervenciones|turnos?)\b', req.message, re.IGNORECASE):
                bridge_message = (
                    'Para clips de intervenciones numeradas o de una persona, consulta media_speakers '
                    'y usa media_clips con person y turn_index/max_clips o turn_indices. '
                    'Si el usuario dice "el número 6", usa el sexto turno de la lista anterior; '
                    'si pide "los 5 primeros", corta cinco turnos completos por separado. '
                    'No vuelvas a localizar esos turnos por coincidencia textual. '
                    'Incluye en la respuesta la ruta de cada MP4 solicitado. '
                    f'Petición: {req.message}'
                )
            elif (re.search(r'\bv[ií]deos?\b', req.message, re.IGNORECASE)
                  and re.search(r'\b(?:disponibles|cat[aá]logo|transcripci[oó]n|transcritos|indexados)\b', req.message, re.IGNORECASE)):
                bridge_message = (
                    'Consulta ahora get_server_info de media-core antes de responder. '
                    'Usa local_video_files para los vídeos presentes e indexed_test_vectors para las transcripciones indexadas. '
                    'Distingue los vídeos disponibles de las transcripciones que puedan existir sin archivo de vídeo. '
                    'No inventes títulos ni reutilices una lista de otra conversación. '
                    f'Petición: {req.message}'
                )
            elif re.search(r'\b(?:imagen|foto|fotograma|captura|recort|identific|qui[eé]n es|buscar por internet)\b', req.message, re.IGNORECASE):
                previous_questions = [item['content'] for item in history if item['role'] == 'user']
                video_ids = re.findall(r'\bTV\d{2,}\b', req.message, re.IGNORECASE)
                if not video_ids and previous_questions:
                    video_ids = re.findall(r'\bTV\d{2,}\b', previous_questions[-1], re.IGNORECASE)
                if video_ids:
                    bridge_message = (
                        f'La petición se refiere al vídeo {video_ids[-1].upper()}. '
                        'Para mostrar un fotograma usa media_describe_scene de media-core. '
                        'Para recortar o aislar a un sujeto/objeto en un fotograma usa media_crop_subject. '
                        'Para identificar a una persona desconocida buscando por internet usa media_identify_person_web '
                        '(que recorta al sujeto, busca en la web/Wikipedia y genera automáticamente una comparativa con la foto oficial). '
                        'Incluye siempre las imágenes en formato Markdown (![...](/api/keyframes/...)) o MEDIA:<ruta> devueltas por la herramienta. '
                        'No uses search_files ni busques rutas en el disco; el MCP ya te da los enlaces y rutas listos. '
                        'Si es una continuación, conserva el momento descrito antes. '
                        f'Petición: {req.message}'
                    )
            loop = asyncio.get_running_loop()
            groups = []

            def update(event, payload):
                if event not in {'tool.start', 'tool.complete'}:
                    return

                def apply():
                    if job['status'] == 'cancelled':
                        return
                    details = payload if isinstance(payload, dict) else {}
                    name = re.sub(r'[^a-zA-Z0-9_.:-]', '', str(details.get('name') or 'procesando'))[:120] or 'procesando'
                    label = ''
                    if name in {'skill_view', 'skills_list'}:
                        candidate = details.get('label')
                        arguments = details.get('arguments') or details.get('args') or {}
                        if not candidate and isinstance(arguments, dict):
                            candidate = arguments.get('name') or arguments.get('category')
                        if candidate and re.fullmatch(r'[a-zA-Z0-9_.:/ -]{1,100}', str(candidate)):
                            label = str(candidate)
                    display_name = label if name == 'skill_view' and label else name
                    activity = {
                        'kind': 'skill' if name == 'skill_view' else 'tool',
                        'name': name,
                        'label': label,
                        'stage': f'Consultando skill {display_name}' if name == 'skill_view' else f'Ejecutando {display_name}',
                        'status': 'running' if event == 'tool.start' else 'completed',
                        'at': round(time.time() - job['created_at'], 1),
                    }
                    if event == 'tool.start':
                        job['stage'] = activity['stage']
                        job['events'].append(activity)
                    if event == 'tool.complete':
                        candidate = {'artifacts': []}
                        collect(details.get('result'), candidate)
                        if candidate['artifacts']:
                            groups.append(candidate['artifacts'])
                        running = next((item for item in reversed(job['events'])
                                        if item.get('name') == name and item.get('status') == 'running'), None)
                        if running:
                            running.update(status='completed', completed_at=activity['at'])
                        else:
                            job['events'].append(activity)
                        job['stage'] = 'MedIA está preparando el siguiente paso'
                    save(job)

                loop.call_soon_threadsafe(apply)

            bridge_args = (
                job['conversation_id'],
                bridge_message,
                history[-40:],
                update,
            )
            if req.voice_language:
                voice_language = {'es': 'castellano', 'ca': 'catalán', 'en': 'inglés'}[req.voice_language]
                bridge_args = (bridge_args[0], bridge_args[1] + (
                    f'\n\nModo de conversación por voz: responde en {voice_language}. '
                    'Escribe la respuesta completa para el chat como siempre. Al final añade '
                    '<media_voice>una intervención oral breve</media_voice>. '
                    'Esa intervención será tu voz: máximo dos frases y 35 palabras, con tono cercano y natural. '
                    'Responde al propósito del usuario, confirma solo lo que hayas logrado y, si aporta valor, '
                    'propón un siguiente paso concreto o pregunta algo pertinente. No leas tablas, rutas ni listas. '
                    'No digas simplemente que la respuesta está en el chat. Conserva cualquier error o limitación importante. '
                    'Habla como en una conversación: puedes usar de vez en cuando «vale» o «claro» si encaja, '
                    'sin repetir fórmulas en todos los turnos ni decir que sigues trabajando cuando ya has terminado. '
                    'No llames a herramientas TTS para esta intervención: la aplicación la reproduce con Piper.'
                ), *bridge_args[2:])
            if req.reasoning_effort:
                bridge_args += (req.reasoning_effort,)
            job['answer'] = await asyncio.to_thread(bridge.run, *bridge_args)
            if req.voice_language:
                job['answer'], job['spoken_answer'] = split_voice_answer(job['answer'])
            hermes_session_id = bridge.stored_session_id(job['conversation_id'])
            if hermes_session_id:
                job['hermes_session_id'] = hermes_session_id
            identity = bridge.session_identity(job['conversation_id'])
            if identity.get('session_id'):
                job['hermes_effective_session_id'] = identity['session_id']
            hermes_mode = bridge.session_mode(job['conversation_id'])
            if hermes_mode:
                job['hermes_mode'] = hermes_mode
            await asyncio.sleep(0)
            if job['status'] == 'cancelled':
                return
            if groups:
                await publish_deliverables(job, groups, job['answer'])
            scan_artifacts_from_text(job['answer'], job)
            job.update(status='completed', stage='Listo')
        except asyncio.CancelledError:
            if job['status'] != 'cancelled':
                job.update(status='failed', error='La API se ha detenido. Vuelve a enviar el mensaje.', stage='Necesita atención')
            raise
        except Exception as exc:
            if job['status'] != 'cancelled':
                job.update(status='failed', error=str(exc)[:1600], stage='Necesita atención')
        finally:
            job['elapsed_seconds'] = round(time.time() - job['created_at'], 1)
            save(job)


def parse_visual_sweep_command(message: str):
    match = re.fullmatch(r'/clips-todos\s+(TV\d{2,})\s+(.+)', message.strip(), re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return match.group(1).upper(), match.group(2).strip()


async def execute_visual_sweep_chat(job, video_id: str, query: str):
    """Run Media Core's exhaustive visual job while mirroring its progress in chat."""
    async with slots:
        owner_id = job.setdefault('media_core_owner_id', f"chat:{job['id']}")
        job.update(status='running', stage='Preparando análisis visual completo', progress=1)
        save(job)
        try:
            queued = checked(await mcp_call('media_find_all_visual', {
                'video_id': video_id,
                'query': query,
                'extract_clips': True,
                'owner_id': owner_id,
            }))
            if isinstance(queued, dict) and queued.get('status') == 'cancelled':
                job.update(status='cancelled', stage='Detenido')
                return
            if not isinstance(queued, dict) or not queued.get('job_id'):
                raise RuntimeError('Media Core no devolvió un identificador de trabajo.')
            remote_id = queued['job_id']
            job['media_core_job_id'] = remote_id
            save(job)
            sequence = 0
            deadline = time.monotonic() + 7200
            while True:
                if job['status'] == 'cancelled':
                    return
                if time.monotonic() > deadline:
                    raise RuntimeError('El análisis visual superó el tiempo máximo de dos horas.')
                await asyncio.sleep(1)
                state = await mcp_call('media_job_status', {'job_id': remote_id, 'after_sequence': sequence})
                for event in state.get('events', []):
                    sequence = max(sequence, int(event.get('sequence', 0)))
                    job['stage'] = str(event.get('stage') or job['stage'])
                    if isinstance(event.get('percent'), (int, float)):
                        job['progress'] = max(job.get('progress', 0), min(100, round(event['percent'])))
                    job['events'].append({
                        'stage': job['stage'], 'progress': job.get('progress', 0),
                        'at': round(time.time() - job['created_at'], 1),
                    })
                save(job)
                if state.get('status') == 'failed':
                    result = state.get('result') or {}
                    raise RuntimeError(result.get('message') or 'El análisis visual falló.')
                if state.get('status') in {'cancelling', 'cancelled'}:
                    job.update(status='cancelled', stage='Detenido')
                    return
                if state.get('status') == 'completed':
                    result = checked(state.get('result'))
                    break
            collect(result, job)
            count = int(result.get('occurrence_count', 0))
            coverage = result.get('coverage') or {}
            sampled = int(coverage.get('sampled_frames', 0))
            interval = coverage.get('sample_interval_seconds', 0)
            if count:
                rows = []
                for index, item in enumerate(result.get('clips', []), 1):
                    rows.append(
                        f"- Clip {index}: {item.get('start_seconds', 0):.1f}–{item.get('end_seconds', 0):.1f} s"
                        + (f" — {item.get('description')}" if item.get('description') else '')
                    )
                job['answer'] = (
                    f"He encontrado {count} apariciones visuales relacionadas con «{query}» y he extraído sus clips.\n\n"
                    + '\n'.join(rows)
                    + f"\n\nCobertura: vídeo completo, {sampled} fotogramas muestreados cada {interval:g} s. "
                      "El recuento representa apariciones visuales; no personas únicas, salvo que se añada seguimiento de identidad."
                )
            else:
                job['answer'] = (
                    f"No he encontrado apariciones visuales verificables de «{query}».\n\n"
                    f"Cobertura: vídeo completo, {sampled} fotogramas muestreados cada {interval:g} s. "
                    "Esto no demuestra ausencia entre dos muestras; reduce el intervalo para una inspección más densa."
                )
            handoff = {
                'video_id': result.get('video_id', video_id),
                'query': query,
                'coverage': coverage,
                'occurrence_count': count,
                'clips': [
                    {key: item.get(key) for key in (
                        'start_seconds', 'end_seconds', 'description', 'confidence', 'clip_file'
                    )}
                    for item in result.get('clips', [])
                ],
            }
            job.update(stage='Hermes está preparando la respuesta', progress=99)
            save(job)
            hermes_prompt = (
                "Media Core ha terminado un análisis visual exhaustivo y ha generado los clips. "
                "El siguiente JSON es el resultado autoritativo. No uses herramientas, no repitas el análisis, "
                "no cambies intervalos ni inventes rutas. Responde en español con un resumen breve y enumera "
                "todos los clips, sus tiempos, descripciones y rutas. Si no hay clips, indícalo claramente.\n\n"
                + json.dumps(handoff, ensure_ascii=False)
            )
            try:
                hermes_answer = await asyncio.to_thread(
                    bridge.run, job['conversation_id'], hermes_prompt, [], lambda event, payload: None
                )
                if isinstance(hermes_answer, str) and hermes_answer.strip():
                    job['answer'] = hermes_answer.strip()
            except Exception as exc:
                # The deterministic result and generated artifacts remain usable.
                job['events'].append({
                    'stage': 'Hermes no pudo redactar; se usa el resultado de Media Core',
                    'at': round(time.time() - job['created_at'], 1),
                    'warning': str(exc)[:300],
                })
            hermes_session_id = bridge.stored_session_id(job['conversation_id'])
            if hermes_session_id:
                job['hermes_session_id'] = hermes_session_id
            identity = bridge.session_identity(job['conversation_id'])
            if identity.get('session_id'):
                job['hermes_effective_session_id'] = identity['session_id']
            hermes_mode = bridge.session_mode(job['conversation_id'])
            if hermes_mode:
                job['hermes_mode'] = hermes_mode
            job.update(status='completed', stage='Listo', progress=100)
        except asyncio.CancelledError:
            if job['status'] != 'cancelled':
                job.update(status='failed', error='La API se detuvo durante el análisis.', stage='Necesita atención')
            raise
        except Exception as exc:
            if job['status'] != 'cancelled':
                job.update(status='failed', error=str(exc)[:1600], stage='Necesita atención')
        finally:
            job['elapsed_seconds'] = round(time.time() - job['created_at'], 1)
            save(job)


async def execute(job, req):
    async with slots:
        job['status'] = 'running'
        save(job)
        try:
            video = next(v for v in catalog() if v['filename'] == req.video)
            vid = video['id']
            if req.action == 'chat':
                # Hermes discovers MCP tools when its process starts. Starting it
                # before media-core is ready leaves a session without these tools.
                await asyncio.wait_for(mcp_ping(), timeout=10)
                # A combined "find a clip and voice it" request has a deterministic
                # media-core path. Keep Hermes for open-ended questions, but do not
                # let it explore the same video repeatedly for this common action.
                normalized_request = ''.join(
                    character for character in unicodedata.normalize('NFKD', req.message.casefold())
                    if not unicodedata.combining(character)
                )
                wants_clip = 'clip' in normalized_request or 'corte' in normalized_request
                wants_voice = any(word in normalized_request for word in ('locut', 'audio', 'voz', 'locución'))
                if wants_clip and wants_voice:
                    target_lang = req.language
                    if re.search(r'\b(?:ingles|english)\b', normalized_request):
                        target_lang = 'en'
                    elif re.search(r'\b(?:catala|catalan)\b', normalized_request):
                        target_lang = 'ca'
                    elif re.search(r'\b(?:espanol|spanish|castellano)\b', normalized_request):
                        target_lang = 'es'

                    if re.search(r'\bvox\b', normalized_request):
                        clip_query = 'VOX'
                    elif re.search(r'\balian[cs]a\b', normalized_request):
                        clip_query = 'Aliança Catalana'
                    else:
                        name = re.search(r'\b(?:ignacio|silvia)\s+[a-záéíóúñ]+', normalized_request)
                        if name:
                            clip_query = name.group(0)
                        elif topic_match := re.search(r'\b(?:sobre|de|hable\s+de|habla\s+de|donde\s+se\s+hable\s+de|donde\s+habla)\s+(.+?)(?:\s+y\s+me\s+lo|\s+y\s+locut|\s+en\s+(?:ingles|espanol|catalan)|$)', normalized_request):
                            clip_query = topic_match.group(1).strip()
                        else:
                            clip_query = req.message

                    selected = await tool(job, 'media_clips', {'video_id': vid, 'query': clip_query,
                        'max_clips': 1, 'selection_mode': 'auto', 'cut_video_files': True}, 'Seleccionando el clip')
                    job['clips'] = [{key: clip.get(key) for key in ('title', 'start_time', 'end_time', 'duration', 'speaker', 'selection_mode', 'match_evidence', 'match_warning', 'justification', 'cache_hit')} for clip in selected]
                    summary = await tool(job, 'media_summarize', {'video_id': vid, 'style': 'breve', 'language': target_lang, 'query': clip_query}, 'Preparando el resumen')
                    job['answer'] = summary.get('executive_summary', '')
                    if not job['answer']:
                        raise RuntimeError('El resumen ha llegado vacío. No se ha generado una locución.')
                    await tool(job, 'media_tts', {'text': job['answer'], 'language': target_lang, 'provider': 'piper'}, 'Generando la voz')
                    clip_interval = f" ({job['clips'][0].get('start_time')}–{job['clips'][0].get('end_time')})" if job.get('clips') and job['clips'][0].get('start_time') else ""
                    job['answer'] = f"He localizado el fragmento solicitado{clip_interval} y he preparado una locución del resumen en {target_lang}."
                    job.update(status='completed', stage='Listo')
                    return
                job['stage'] = 'Hermes está trabajando'
                save(job)
                history = []
                for old in reversed(recent()):
                    if old['conversation_id'] == job['conversation_id'] and old['id'] != job['id'] and old.get('answer'):
                        history.extend([{'role': 'user', 'content': old['message']}, {'role': 'assistant', 'content': old['answer']}])
                prompt = ('Usa la skill media-video-archive y exclusivamente las herramientas media-core para esta petición. '
                    'No uses text_to_speech, terminal, pip ni ffmpeg manual. No instales paquetes. '
                    'Verifica siempre video_id, espera media_job_status y conserva los errores explícitos. '
                    'Si generas voz usa media_tts o media_speak provider=piper. Incluye las rutas absolutas de los resultados. '
                    'Entrega la cantidad de clips que el usuario solicite, hasta 10 por llamada; si no indica cantidad, entrega uno. '
                    'Para una intervención numerada o varios turnos de una persona, consulta media_speakers y '
                    'usa media_clips con person, turn_index/max_clips o turn_indices, sin volver a buscarlos por texto. '
                    'Para audio usa preferentemente media_tts con todo el texto. '
                    'Para saber quién habla en un minuto concreto usa media_speakers (pasando timestamp); '
                    'para aspectos visuales, personas, qué está haciendo alguien o qué pasa en un minuto concreto, usa media_describe_scene (pasando timestamp, person y action_query); '
                    'para recortar o aislar un sujeto/objeto en un fotograma según lo solicitado usa media_crop_subject (pasando timestamp y target_description); '
                    'para identificar a personas no etiquetadas (en segundo plano, bancadas o desconocidos) buscando en internet y contrastando con fotos oficiales usa media_identify_person_web (pasando timestamp y person_description); '
                    'para buscar conceptos visuales generales o fotos usa media_search_visual; '
                    'nunca concluyas que alguien no existe solo porque media_search textual no lo encuentre. Si piden foto o recorte, incluye /api/keyframes/{video_id}/{filename} o el markdown_image devuelto. '
                    'Una selección speaker_turn contiene el turno identificado: si responde a la petición, úsala y detén la búsqueda de clips. '
                    'No vuelvas a generar una pieza que ya responde a la petición. Como máximo dos búsquedas enfocadas; '
                    'después genera el resultado o explica qué información falta. No resumas repetidamente. '
                    'En tu respuesta final incluye solo las rutas de las piezas elegidas, nunca borradores ni frases de audio separadas. '
                    'Explica brevemente el contenido y el intervalo. Evita enumerar herramientas, skills, parámetros y reintentos internos. '
                    f'Vídeo seleccionado: {vid}. Archivo: {bridge.runtime_path(VIDEOS / req.video)}. Idioma de respuesta/voz: {req.language}. '
                    f'Petición del usuario: {req.message}')
                loop = asyncio.get_running_loop()
                groups = []
                def update(event, payload):
                    if event in {'tool.start', 'tool.complete'}:
                        def apply():
                            job['stage'] = 'Hermes: ' + str(payload.get('name', 'procesando'))
                            if event == 'tool.complete':
                                candidate = {'artifacts': []}
                                collect(payload.get('result'), candidate)
                                if candidate['artifacts']:
                                    groups.append(candidate['artifacts'])
                                job['events'].append({'stage': job['stage'], 'at': round(time.time() - job['created_at'], 1)})
                            save(job)
                        loop.call_soon_threadsafe(apply)
                answer = await asyncio.to_thread(bridge.run, job['conversation_id'], prompt, history[-30:], update)
                # Drain tool callbacks scheduled before message.complete.
                await asyncio.sleep(0)
                job['answer'] = answer
                job['stage'] = 'Preparando el resultado final'
                save(job)
                await publish_deliverables(job, groups, answer)
                scan_artifacts_from_text(answer, job)
            else:
                if video['status'] != 'ready' or req.action == 'transcribe':
                    await tool(job, 'media_transcribe', {'source': str(VIDEOS / req.video), 'language': req.source_language,
                        'diarization': False, 'background': True, 'index_transcript': True}, 'Transcribiendo e indexando')
                if req.action in {'summary', 'audio'}:
                    summary = await tool(job, 'media_summarize', {'video_id': vid, 'style': 'breve', 'language': req.language, 'query': req.message}, 'Preparando el resumen')
                    job['answer'] = summary.get('executive_summary', '')
                    if not job['answer']:
                        raise RuntimeError('El resumen ha llegado vacío. No se ha generado una locución.')
                    if req.action == 'audio':
                        await tool(job, 'media_tts', {'text': job['answer'], 'language': req.language, 'provider': 'piper'}, 'Generando la voz')
                elif req.action == 'clips':
                    selected_clips = await tool(job, 'media_clips', {'video_id': vid, 'query': req.message, 'max_clips': 1,
                        # UI requests are natural-language/subjective by default;
                        # editorial can resolve meaning while media-core still
                        # requires literal evidence before cutting the file.
                        'selection_mode': 'auto', 'cut_video_files': True}, 'Seleccionando y extrayendo el clip')
                    job['clips'] = [{key: clip.get(key) for key in ('title', 'start_time', 'end_time', 'duration', 'speaker', 'selection_mode', 'match_evidence', 'match_warning', 'justification', 'cache_hit')} for clip in selected_clips]
                    if not any(a['kind'] == 'clip' for a in job['artifacts']):
                        raise RuntimeError('La selección no produjo un archivo de clip. Prueba indicando un tema concreto.')
                else:
                    job['answer'] = 'Transcripción e indexación terminadas.'
            job.update(status='completed', stage='Listo')
        except asyncio.CancelledError:
            job.update(status='failed', error='La API se ha detenido. Comprueba media-core antes de repetir.')
            raise
        except Exception as exc:
            message = str(exc)
            if isinstance(exc, BaseExceptionGroup):
                message = 'No se pudo completar la llamada a media-core. Comprueba el servidor en el puerto 8001 y sus registros.'
            job.update(status='failed', error=message[:1600], stage='Necesita atención')
        finally:
            job['elapsed_seconds'] = round(time.time() - job['created_at'], 1)
            save(job)


async def mcp_ping():
    # Discovery checks the transport without loading Chroma or inference models.
    async with streamablehttp_client(MCP_URL, timeout=timedelta(seconds=5)) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            if 'media_tts' not in {tool.name for tool in tools.tools}:
                raise RuntimeError('El endpoint no es media-core.')

@app.get('/api/health')
async def health():
    try:
        await asyncio.wait_for(mcp_ping(), timeout=8)
        connected = True
    except Exception:
        connected = False
    hermes = await asyncio.to_thread(bridge.status)
    return {
        'api': True,
        'media_core': connected,
        'hermes_installed': hermes['available'],
        'hermes_running': hermes['running'],
        'hermes_configured_mode': hermes['configured_mode'],
        'hermes_mode': hermes['active_mode'],
    }


@app.get('/api/reasoning')
async def get_reasoning():
    return await webui_reasoning()


@app.post('/api/reasoning')
async def set_reasoning(req: ReasoningRequest):
    return await webui_reasoning(req.effort)


@app.get('/api/videos')
async def videos():
    return await asyncio.to_thread(catalog)


@app.delete('/api/videos/{name}')
async def delete_video(name: str):
    path = safe_file('video', name)
    video_id = re.sub(r'_demo$', '', path.stem, flags=re.I).upper()
    if indexing_jobs.get(name, {}).get('status') == 'indexing' or any(
        job.get('video') == name and job.get('status') in {'queued', 'running'}
        for job in media_jobs()
    ):
        raise HTTPException(409, 'Espera a que termine el procesamiento antes de eliminar este vídeo.')

    try:
        checked(await mcp_call('media_forget_video', {'video_id': video_id, 'source_stem': path.stem}))
    except Exception as exc:
        raise HTTPException(503, f'No se pudo limpiar el índice del vídeo: {exc}') from exc

    stat = path.stat()
    thumb_key = hashlib.sha256(f'{path}:{stat.st_mtime_ns}:{stat.st_size}'.encode()).hexdigest()
    try:
        await asyncio.to_thread(path.unlink)
    except OSError as exc:
        raise HTTPException(500, f'No se pudo eliminar el archivo de vídeo: {exc}') from exc
    (STATE / 'thumbs' / f'{thumb_key}.jpg').unlink(missing_ok=True)
    indexing_jobs.pop(name, None)
    return {'filename': name, 'deleted': True}


@app.get('/api/files/{kind}/{name}')
async def file(kind: str, name: str, download: bool = False):
    path = safe_file(kind, name)
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream',
        filename=path.name if download else None, content_disposition_type='attachment' if download else 'inline')


@app.get('/api/thumbnail/{name}')
async def thumbnail(name: str):
    path = safe_file('video', name)
    key = hashlib.sha256(f'{path}:{path.stat().st_mtime_ns}:{path.stat().st_size}'.encode()).hexdigest()
    dest = STATE / 'thumbs' / f'{key}.jpg'
    async with thumb_slots:
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            def render():
                subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-ss', '2', '-i', str(path), '-frames:v', '1', '-vf', 'scale=640:-2', '-y', str(dest)], timeout=25, check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            try:
                await asyncio.to_thread(render)
            except (OSError, subprocess.SubprocessError):
                raise HTTPException(503, 'No se pudo crear el fotograma. Comprueba FFmpeg.')
    return FileResponse(dest, media_type='image/jpeg', headers={'Cache-Control': 'private, max-age=3600'})


@app.post('/api/videos', status_code=201)
async def upload(file: UploadFile):
    suffix = Path(file.filename or '').suffix.lower()
    if suffix not in EXTENSIONS:
        raise HTTPException(400, 'Selecciona un vídeo MP4, MOV, MKV o WebM.')

    raw_stem = Path(file.filename or 'video').stem
    clean_stem = re.sub(r'[\/\\:\*\?"<>\|\s]+', '_', raw_stem).strip('_') or 'video'
    VIDEOS.mkdir(parents=True, exist_ok=True)
    name = f'{clean_stem}{suffix}'
    path = VIDEOS / name

    tmp = STATE / f'{uuid.uuid4().hex}.upload'
    STATE.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with tmp.open('xb') as target:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > int(os.getenv('MEDIA_UPLOAD_MAX_MB', '4096')) * 1048576:
                    raise HTTPException(413, 'El vídeo supera el límite de subida configurado.')
                target.write(chunk)
        if not total:
            raise HTTPException(400, 'El archivo está vacío.')

        def verify():
            out = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=codec_type', '-of', 'json', str(tmp)], check=True, capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            return bool(json.loads(out.stdout).get('streams'))
        try:
            valid = await asyncio.to_thread(verify)
        except (OSError, ValueError, subprocess.SubprocessError):
            raise HTTPException(400, 'No se puede leer el vídeo. Comprueba el archivo y FFprobe.')
        if not valid:
            raise HTTPException(400, 'El archivo no contiene una pista de vídeo.')

        if path.exists() and path.stat().st_size != total:
            counter = 1
            while path.exists() and path.stat().st_size != total:
                name = f'{clean_stem}_{counter}{suffix}'
                path = VIDEOS / name
                counter += 1

        import shutil
        await asyncio.to_thread(shutil.move, str(tmp), str(path))

        # Auto-index video in background
        task = asyncio.create_task(auto_index_video(name, path))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

        return {
            'filename': name,
            'status': 'indexing',
            'stage': 'Indexando automáticamente...',
        }
    finally:
        tmp.unlink(missing_ok=True)
        await file.close()


@app.post('/api/videos/{name}/index', status_code=202)
async def index_video_route(name: str):
    path = safe_file('video', name)
    if name in indexing_jobs and indexing_jobs[name].get('status') == 'indexing':
        return indexing_jobs[name]
    task = asyncio.create_task(auto_index_video(name, path))
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return {
        'filename': name,
        'status': 'indexing',
        'stage': 'Indexación iniciada'
    }


@app.get('/api/videos/{name}/status')
async def video_status_route(name: str):
    safe_file('video', name)
    if name in indexing_jobs:
        return indexing_jobs[name]
    item = next((v for v in catalog() if v['filename'] == name), None)
    if not item:
        raise HTTPException(404, 'Vídeo no encontrado')
    return {
        'status': item['status'],
        'stage': item.get('indexing_stage', ''),
        'indexed': item.get('indexed', False),
    }


@app.get('/api/transcript/{name}')
async def transcript(name: str):
    safe_file('video', name)
    item = next(v for v in catalog() if v['filename'] == name)
    if item['status'] != 'ready':
        raise HTTPException(409, 'Este vídeo necesita una transcripción verificada.')
    source = safe_file('video', name)
    for path in (CACHE / f"{item['id']}_whisperx.json", TRANSCRIPTS / f"{item['id']}.json"):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        source_identity = data.get('request_metadata', {}).get('source')
        if str(data.get('id', '')).upper() == item['id'] and source_matches_file(source_identity, source):
            return {'text': data.get('full_text', ''), 'segments': data.get('segments', [])}
    raise HTTPException(409, 'La fuente cambió. Vuelve a transcribir este vídeo.')


@app.get('/api/visual/search')
async def visual_search(q: str, video_id: str = '', top_k: int = 5):
    try:
        return await mcp_call('media_search_visual', {'query': q, 'video_id': video_id, 'top_k': top_k})
    except Exception as exc:
        raise HTTPException(500, f'Error en búsqueda visual: {exc}')


@app.post('/api/visual/describe')
async def visual_describe(payload: dict):
    video_id = payload.get('video_id', 'TV01')
    timestamp = float(payload.get('timestamp', 0.0))
    scene_id = payload.get('scene_id')
    prompt = payload.get('prompt', '')
    person = payload.get('person', '')
    action_query = payload.get('action_query', '')
    multi_frame = payload.get('multi_frame', True)
    try:
        return await mcp_call('media_describe_scene', {
            'video_id': video_id,
            'timestamp': timestamp,
            'scene_id': scene_id,
            'prompt': prompt,
            'person': person,
            'action_query': action_query,
            'multi_frame': multi_frame,
        })
    except Exception as exc:
        raise HTTPException(500, f'Error en VLM: {exc}')


@app.get('/api/speakers/{name}')
async def speakers_info(name: str, timestamp: float | None = None, person: str = ''):
    item = next((v for v in catalog() if v['filename'] == name or v['id'] == name.upper()), None)
    clean_id = item['id'] if item else re.sub(r'_demo$', '', Path(name).stem, flags=re.I).upper()
    try:
        payload = {'video_id': clean_id}
        if timestamp is not None:
            payload['timestamp'] = timestamp
        if person:
            payload['person'] = person
        return await mcp_call('media_speakers', payload)
    except Exception as exc:
        raise HTTPException(500, f'Error al resolver oradores: {exc}')


@app.get('/api/visual/{name}')
async def visual_manifest(name: str):
    item = next((v for v in catalog() if v['filename'] == name or v['id'] == name.upper()), None)
    clean_id = item['id'] if item else re.sub(r'_demo$', '', Path(name).stem, flags=re.I).upper()
    path = VISUAL_CACHE / f'{clean_id}_visual.json'
    if not path.is_file():
        raise HTTPException(404, 'No existe análisis visual para este vídeo.')
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise HTTPException(500, 'Error al leer el manifiesto visual.')


@app.get('/api/keyframes/{video_id}/{filename}')
async def keyframe_image(video_id: str, filename: str):
    clean_id = re.sub(r'[^a-zA-Z0-9_-]', '', video_id).upper()
    safe_name = Path(filename).name
    if safe_name != filename or not safe_name.endswith('.jpg'):
        raise HTTPException(404, 'Fotograma no encontrado.')
    target = (KEYFRAMES / clean_id / safe_name).resolve()
    if not target.is_file() or not target.is_relative_to(KEYFRAMES.resolve()):
        raise HTTPException(404, 'Fotograma no encontrado.')
    return FileResponse(target, media_type='image/jpeg', headers={'Cache-Control': 'private, max-age=86400'})


@app.get('/api/ocr/{name}')
async def ocr_manifest(name: str):
    item = next((v for v in catalog() if v['filename'] == name or v['id'] == name.upper()), None)
    clean_id = item['id'] if item else re.sub(r'_demo$', '', Path(name).stem, flags=re.I).upper()
    path = OCR_CACHE / f'{clean_id}_ocr.json'
    if not path.is_file():
        raise HTTPException(404, 'No existe análisis OCR para este vídeo.')
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise HTTPException(500, 'Error al leer el manifiesto OCR.')


@app.get('/api/jobs')
async def jobs():
    return media_jobs()[:100]


@app.get('/api/costs')
async def costs(response: Response):
    response.headers['Cache-Control'] = 'no-store'
    return await provider_costs(ROOT, STATE)


@app.get('/api/costs/report.pdf')
async def costs_report():
    current = await provider_costs(ROOT, STATE)
    filename = f"uso-apis-{current.get('period', 'actual')}.pdf"
    return Response(
        content=build_cost_report(current),
        media_type='application/pdf',
        headers={
            'Content-Disposition': f'attachment; filename="{filename}"',
            'Cache-Control': 'no-store',
        },
    )


@app.get('/api/jobs/{job_id}')
async def job_status(job_id: str):
    return get_job(job_id)


@app.post('/api/jobs', status_code=202)
async def create_job(req: NewJob):
    safe_file('video', req.video)
    current = recent()
    active = [j for j in current if j['status'] in {'queued', 'running'}]
    if len(active) >= 4:
        raise HTTPException(429, 'Hay cuatro peticiones en curso. Espera a que termine una.')
    if req.conversation_id and any(j['conversation_id'] == req.conversation_id for j in active):
        raise HTTPException(409, 'Espera a que termine la petición de esta conversación.')
    if req.action == 'chat' and not req.message.strip():
        raise HTTPException(400, 'Escribe una petición para Hermes.')
    job = {'id': uuid.uuid4().hex, 'kind': 'media', 'conversation_id': req.conversation_id or uuid.uuid4().hex,
        'video': req.video, 'action': req.action, 'language': req.language, 'message': req.message,
        'status': 'queued', 'stage': 'En cola', 'created_at': time.time(), 'artifacts': [], 'events': [], 'summary': '', 'answer': ''}
    save(job)
    task = asyncio.create_task(execute(job, req))
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return job


@app.get('/api/chat/{conversation_id}')
async def chat_history(conversation_id: str):
    if not re.fullmatch(r'[a-zA-Z0-9-]{1,64}', conversation_id):
        raise HTTPException(404, 'Conversación no encontrada')
    return list(reversed(chat_jobs(conversation_id)))


@app.delete('/api/chat/{conversation_id}')
async def delete_chat(conversation_id: str):
    if not re.fullmatch(r'[a-zA-Z0-9-]{1,64}', conversation_id):
        raise HTTPException(404, 'Conversación no encontrada')
    conversation_jobs = chat_jobs(conversation_id)
    persisted_session_id = next(
        (
            job.get('hermes_session_id')
            for job in conversation_jobs
            if job.get('hermes_session_id')
        ),
        None,
    )
    # An active local turn may already have created its durable Hermes session
    # while the job has not yet persisted that id. Capture it before interrupt,
    # because terminating the local gateway clears the bridge session maps.
    identity = bridge.session_identity(conversation_id)
    persisted_session_id = persisted_session_id or identity.get('stored_session_id')
    effective_session_id = next(
        (
            job.get('hermes_effective_session_id')
            for job in conversation_jobs
            if job.get('hermes_effective_session_id')
        ),
        None,
    ) or identity.get('session_id')
    hermes_mode = next(
        (job.get('hermes_mode') for job in conversation_jobs if job.get('hermes_mode')),
        None,
    ) or identity.get('mode')
    deleted_jobs = delete_conversation_state(
        conversation_id, persisted_session_id, effective_session_id, hermes_mode,
    )

    # Invalidate before cancellation: task.cancel() does not terminate the
    # thread spawned by asyncio.to_thread, and that thread may complete later.
    live_jobs = [
        job for job in chat_live_jobs.values()
        if job.get('conversation_id') == conversation_id
    ]
    for job in live_jobs:
        job.update(status='cancelled', stage='Detenido', _discarded=True)

    # Ask Media Core to stop only the expensive work owned by these turns, but
    # never make DELETE wait on another service.
    for job in live_jobs:
        remote_cancel = asyncio.create_task(cancel_media_core_job(job))
        tasks.add(remote_cancel)
        remote_cancel.add_done_callback(tasks.discard)

    # Cancel queued/async work immediately. The durable cleanup worker performs
    # transport interruption and session deletion without delaying this HTTP
    # response (the local protocol may otherwise block for up to 30 seconds).
    for job in live_jobs:
        task = chat_tasks.get(job['id'])
        if task and not task.done():
            task.cancel()
    # Start the cancellation coroutines before returning; completed in-memory
    # calls finish here, while real network I/O continues in the background.
    await asyncio.sleep(0)

    return {
        'conversation_id': conversation_id,
        'deleted_jobs': deleted_jobs,
        'hermes': {
            'queued': True,
            'session_ids': list(filter(None, (effective_session_id, persisted_session_id))),
            'mode': hermes_mode,
        },
    }


@app.get('/api/chat/jobs/{job_id}')
async def chat_job_status(job_id: str):
    job = get_job(job_id)
    if job.get('kind') != 'hermes_chat':
        raise HTTPException(404, 'Mensaje no encontrado')
    return job


@app.post('/api/chat/jobs/{job_id}/cancel')
async def cancel_chat_job(job_id: str):
    job = chat_live_jobs.get(job_id) or get_job(job_id)
    if job.get('kind') != 'hermes_chat':
        raise HTTPException(404, 'Mensaje no encontrado')
    if job['status'] not in {'queued', 'running'}:
        return job
    job.update(status='cancelled', stage='Detenido', elapsed_seconds=round(time.time() - job['created_at'], 1))
    save(job)
    await cancel_media_core_job(job)
    task = chat_tasks.get(job_id)
    if task:
        task.cancel()
    bridge.interrupt(job['conversation_id'])
    return job


@app.post('/api/chat/jobs', status_code=202)
async def create_chat_job(req: ChatRequest):
    if not req.message.strip():
        raise HTTPException(400, 'Escribe un mensaje para Hermes.')
    if req.conversation_id and conversation_is_deleted(req.conversation_id):
        raise HTTPException(410, 'La conversación fue eliminada.')
    if req.message.strip().lower().startswith('/clips-todos') and not parse_visual_sweep_command(req.message):
        raise HTTPException(400, 'Usa /clips-todos <vídeo> <qué buscar>, por ejemplo: /clips-todos TV05 personas mayores.')
    active = [job for job in recent() if job['status'] in {'queued', 'running'}]
    if len(active) >= 4:
        raise HTTPException(429, 'Hay cuatro peticiones en curso. Espera a que termine una.')
    if req.conversation_id and any(
        job.get('kind') == 'hermes_chat'
        and job['conversation_id'] == req.conversation_id
        for job in active
    ):
        raise HTTPException(409, 'Espera a que Hermes termine el mensaje anterior.')
    visual_sweep = parse_visual_sweep_command(req.message)
    job = {
        'id': uuid.uuid4().hex,
        'kind': 'hermes_chat',
        'conversation_id': req.conversation_id or uuid.uuid4().hex,
        'message': req.message.strip(),
        'reasoning_effort': req.reasoning_effort,
        'status': 'queued',
        'stage': 'En cola',
        'created_at': time.time(),
        'events': [],
        'answer': '',
        'progress': 0,
        'artifacts': [],
    }
    if visual_sweep:
        # Assigned before scheduling the coroutine so DELETE can cancel by
        # owner even while the remote create request is still in flight.
        job['media_core_owner_id'] = f"chat:{job['id']}"
    save(job)
    task = asyncio.create_task(
        execute_visual_sweep_chat(job, *visual_sweep) if visual_sweep else execute_chat(job, req)
    )
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    chat_tasks[job['id']] = task
    chat_live_jobs[job['id']] = job

    def forget_chat_task(done, job_id=job['id']):
        chat_tasks.pop(job_id, None)
        chat_live_jobs.pop(job_id, None)

    task.add_done_callback(forget_chat_task)
    return job
