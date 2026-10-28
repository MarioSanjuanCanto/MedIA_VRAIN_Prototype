"""Hermes adapter supporting the local stdio bridge and the Docker HTTP gateway."""
import json
import os
from pathlib import Path, PurePosixPath
import queue
import subprocess
import threading
import time
import uuid
from urllib.parse import quote

import httpx


def _sse_events(response):
    """Yield (event, data) pairs from an SSE response without exposing raw payloads."""
    event = 'message'
    data = []
    for line in response.iter_lines():
        if not line:
            if data:
                yield event, '\n'.join(data)
            event, data = 'message', []
        elif line.startswith('event:'):
            event = line[6:].strip() or 'message'
        elif line.startswith('data:'):
            data.append(line[5:].lstrip())
    if data:
        yield event, '\n'.join(data)


class HermesBridge:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.configured_mode = os.getenv('HERMES_MODE', 'auto').strip().lower()
        if self.configured_mode not in {'auto', 'docker', 'local'}:
            raise ValueError('HERMES_MODE debe ser auto, docker o local.')
        self.api_url = (os.getenv('HERMES_API_URL', '').strip() or 'http://127.0.0.1:8642').rstrip('/')
        self.api_key = os.getenv('HERMES_API_KEY', '').strip()
        self.docker_workdir = PurePosixPath(os.getenv('HERMES_DOCKER_WORKDIR', '').strip() or '/workspace')
        if not self.docker_workdir.is_absolute():
            raise ValueError('HERMES_DOCKER_WORKDIR debe ser una ruta absoluta.')
        self.active_mode = None
        self._transport = None
        default = Path(os.getenv('LOCALAPPDATA', str(Path.home()))) / 'hermes/hermes-agent'
        self.home = Path(os.getenv('HERMES_AGENT_DIR', '').strip() or default)
        default_python = self.home / ('venv/Scripts/python.exe' if os.name == 'nt' else 'venv/bin/python')
        self.python = Path(os.getenv('HERMES_PYTHON', '').strip() or default_python)
        self.process = None
        self.frames = queue.Queue()
        self.lock = threading.Lock()
        self.sessions = {}
        self.stored_sessions = {}
        self.session_reasoning = {}
        self.session_modes = {}
        self.orphan_sessions = {}
        self.active_lock = threading.Lock()
        self.active_runs = {}

    def interrupt(self, conversation):
        """Stop an active turn without waiting for Hermes' tool call to finish."""
        with self.active_lock:
            active = self.active_runs.get(conversation)
            if not active:
                return False
            active['stop'].set()
            mode = active['mode']
            client = active.get('client')
            session_id = self.sessions.get(conversation)
            stored_session_id = self.stored_sessions.get(conversation)
            if session_id or stored_session_id:
                self.orphan_sessions[conversation] = {
                    'session_id': session_id,
                    'stored_session_id': stored_session_id,
                    'mode': self.session_modes.get(conversation) or mode,
                }
        if client is not None:
            try:
                client.close()
            except (OSError, RuntimeError, httpx.HTTPError):
                pass
        if mode == 'local' and active.get('owns_local', True):
            self._terminate_local()
        return True

    def stored_session_id(self, conversation):
        return self.session_identity(conversation).get('stored_session_id')

    def session_identity(self, conversation):
        with self.active_lock:
            active = self.active_runs.get(conversation)
            orphan = self.orphan_sessions.get(conversation) or {}
            return {
                'session_id': self.sessions.get(conversation) or orphan.get('session_id'),
                'stored_session_id': (
                    self.stored_sessions.get(conversation) or orphan.get('stored_session_id')
                ),
                'mode': (
                    self.session_modes.get(conversation)
                    or orphan.get('mode')
                    or (active.get('mode') if active else None)
                ),
            }

    def session_mode(self, conversation):
        return self.session_identity(conversation).get('mode')

    def delete_session(
        self, conversation, persisted_session_id=None, mode=None, effective_session_id=None,
    ):
        """Delete the Hermes session associated with a MedIA conversation."""
        # In auto mode availability can change between the turn and deferred
        # cleanup. Preserve the backend that actually owns this session.
        identity = self.session_identity(conversation)
        mode = mode or identity.get('mode')
        # Deletion is a forceful operation.  Closing the active transport makes
        # it safe to remove the durable session even when a turn was running.
        self.interrupt(conversation)

        mode = mode or self._select_mode()
        identity = self.session_identity(conversation)
        session_id = identity.get('session_id') or effective_session_id
        stored_session_id = identity.get('stored_session_id') or persisted_session_id
        if mode != 'docker':
            if not session_id and not stored_session_id:
                return {'deleted': False, 'session_ids': [], 'mode': mode}
            target = stored_session_id or session_id
            try:
                with self.lock:
                    self._start()
                    deadline = time.monotonic() + 30
                    if session_id:
                        close_rid = self._send('session.close', {'session_id': session_id})
                        # The live id belongs to the gateway process that may
                        # just have been terminated. Missing is already closed.
                        self._wait_for_rpc(close_rid, deadline, missing_ok=True)
                    delete_rid = self._send('session.delete', {'session_id': target})
                    deleted = self._wait_for_rpc(delete_rid, deadline, missing_ok=True)
            except Exception:
                # Preserve the durable id so a transient gateway failure can be retried.
                if stored_session_id:
                    self.stored_sessions[conversation] = stored_session_id
                raise
            self.sessions.pop(conversation, None)
            self.stored_sessions.pop(conversation, None)
            self.session_reasoning.pop(conversation, None)
            self.session_modes.pop(conversation, None)
            self.orphan_sessions.pop(conversation, None)
            return {
                'deleted': bool(deleted and deleted.get('deleted')),
                'session_ids': [target] if deleted and deleted.get('deleted') else [],
                'mode': mode,
            }

        if not self.api_key:
            raise RuntimeError('Configura HERMES_API_KEY en services/media-api/.env para usar Hermes Docker.')
        session_ids = list(dict.fromkeys(filter(None, (
            session_id, effective_session_id, conversation,
        ))))
        deleted = []
        try:
            with self._client(httpx.Timeout(10, connect=3)) as client:
                for current in session_ids:
                    response = client.delete(f'/api/sessions/{quote(current, safe="")}')
                    if response.status_code in {401, 403}:
                        raise RuntimeError('Hermes Docker rechazó la clave HERMES_API_KEY.')
                    if response.status_code == 404:
                        continue
                    response.raise_for_status()
                    deleted.append(current)
        except RuntimeError:
            raise
        except httpx.HTTPError as exc:
            raise RuntimeError(f'No se pudo borrar la sesión en Hermes Docker ({self.api_url}).') from exc
        self.sessions.pop(conversation, None)
        self.stored_sessions.pop(conversation, None)
        self.session_reasoning.pop(conversation, None)
        self.session_modes.pop(conversation, None)
        self.orphan_sessions.pop(conversation, None)
        return {'deleted': bool(deleted), 'session_ids': deleted, 'mode': mode}

    def _wait_for_rpc(self, rid, deadline, missing_ok=False):
        while True:
            frame = self._next(deadline)
            if frame.get('id') != rid:
                continue
            error = frame.get('error')
            if not error:
                return frame.get('result') or {}
            if missing_ok and isinstance(error, dict) and error.get('code') == 4007:
                return {}
            message = error.get('message') if isinstance(error, dict) else str(error)
            raise RuntimeError(message or 'Hermes local devolvió un error al borrar la sesión.')

    def _terminate_local(self):
        process = self.process
        if process and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except OSError:
                    pass
        self.frames.put({'bridge_closed': True})
        with self.active_lock:
            for conversation, session_id in self.sessions.items():
                self.orphan_sessions[conversation] = {
                    'session_id': session_id,
                    'stored_session_id': self.stored_sessions.get(conversation),
                    'mode': self.session_modes.get(conversation) or 'local',
                }
            for conversation, stored_session_id in self.stored_sessions.items():
                self.orphan_sessions.setdefault(conversation, {
                    'session_id': None,
                    'stored_session_id': stored_session_id,
                    'mode': self.session_modes.get(conversation) or 'local',
                })
            self.sessions.clear()
            self.stored_sessions.clear()
            self.session_reasoning.clear()

    def _local_available(self):
        return self.python.is_file() and (self.home / 'tui_gateway/entry.py').is_file()

    def _client(self, timeout=None):
        return httpx.Client(
            base_url=self.api_url,
            headers={'Authorization': f'Bearer {self.api_key}'},
            timeout=timeout or httpx.Timeout(2, connect=1),
            transport=self._transport,
        )

    def _docker_available(self):
        if not self.api_key:
            return False
        try:
            with self._client() as client:
                health = client.get('/health')
                models = client.get('/v1/models')
            return health.status_code == 200 and health.json().get('status') == 'ok' and models.status_code == 200
        except (httpx.HTTPError, ValueError):
            return False

    def _select_mode(self):
        if self.configured_mode == 'auto':
            selected = 'docker' if self._docker_available() else 'local'
        else:
            selected = self.configured_mode
        self.active_mode = selected
        return selected

    def available(self):
        mode = self._select_mode()
        return self._docker_available() if mode == 'docker' else self._local_available()

    def status(self):
        if self.configured_mode == 'auto':
            docker_ready = self._docker_available()
            mode = 'docker' if docker_ready else 'local'
            available = docker_ready or self._local_available()
        elif self.configured_mode == 'docker':
            mode = 'docker'
            available = self._docker_available()
        else:
            mode = 'local'
            available = self._local_available()
        self.active_mode = mode
        running = available if mode == 'docker' else bool(self.process and self.process.poll() is None)
        return {
            'configured_mode': self.configured_mode,
            'active_mode': mode,
            'available': available,
            'running': running,
        }

    def runtime_path(self, path):
        """Return a path visible from the transport selected for the next request."""
        resolved = Path(path).resolve()
        if self._select_mode() != 'docker':
            return str(resolved)
        try:
            relative = resolved.relative_to(self.root)
        except ValueError as exc:
            raise RuntimeError('Hermes Docker solo puede acceder a archivos dentro del proyecto montado.') from exc
        return self.docker_workdir.joinpath(*relative.parts).as_posix()

    def _to_docker_text(self, text):
        root_str = str(self.root)
        docker_workdir_str = str(self.docker_workdir).replace('\\', '/')
        if root_str in text:
            import re
            pattern = re.escape(root_str) + r'[^\s"\'`]*'
            def repl(m):
                path_part = m.group(0)
                rel = path_part[len(root_str):].replace('\\', '/')
                return docker_workdir_str + rel
            text = re.sub(pattern, repl, text)
        return text

    def _from_docker_text(self, text):
        docker_workdir_str = str(self.docker_workdir).replace('\\', '/')
        if docker_workdir_str in text:
            import re
            pattern = re.escape(docker_workdir_str) + r'[^\s"\'`]*'
            def repl(m):
                path_part = m.group(0)
                rel = path_part[len(docker_workdir_str):].lstrip('/')
                return str(self.root / Path(rel))
            text = re.sub(pattern, repl, text)
        return text

    def _start(self):
        if self.process and self.process.poll() is None:
            return
        if not self._local_available():
            raise RuntimeError('Configura HERMES_AGENT_DIR y HERMES_PYTHON en services/media-api/.env.')
        self.sessions.clear()
        self.stored_sessions.clear()
        self.frames = queue.Queue()
        self.process = subprocess.Popen(
            [str(self.python), '-u', '-m', 'tui_gateway.entry'], cwd=self.home,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', errors='replace',
            env={**os.environ, 'PYTHONUTF8': '1', 'HERMES_VOICE': '0', 'HERMES_VOICE_TTS': '0'},
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
        process, frames = self.process, self.frames
        def read():
            for line in process.stdout:
                try:
                    frames.put(json.loads(line))
                except ValueError:
                    continue
            frames.put({'bridge_closed': True})
        threading.Thread(target=read, daemon=True).start()

    def _send(self, method, params):
        rid = uuid.uuid4().hex
        self.process.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': rid, 'method': method, 'params': params}, ensure_ascii=False) + '\n')
        self.process.stdin.flush()
        return rid

    def _next(self, deadline):
        try:
            frame = self.frames.get(timeout=max(.01, deadline - time.monotonic()))
        except queue.Empty:
            raise RuntimeError('Hermes no respondió a tiempo. Revisa su configuración y vuelve a intentar.')
        if frame.get('bridge_closed'):
            raise RuntimeError('El proceso de Hermes se cerró. Comprueba su entorno Python.')
        return frame

    def probe(self):
        with self.lock:
            self._start()
            # Creating a lazy session verifies the transport, without an LLM turn.
            rid = self._send('session.create', {'cwd': str(self.root), 'title': 'MedIA conexión'})
            deadline = time.monotonic() + 60
            while True:
                frame = self._next(deadline)
                if frame.get('id') == rid:
                    if frame.get('error'):
                        raise RuntimeError(str(frame['error']))
                    return frame['result']

    def run(self, conversation, text, history, on_event, reasoning_effort=None):
        active = {'stop': threading.Event(), 'mode': None, 'client': None, 'owns_local': False}
        with self.active_lock:
            self.active_runs[conversation] = active
        try:
            mode = self._select_mode()
            with self.active_lock:
                active['mode'] = mode
                self.session_modes[conversation] = mode
            if active['stop'].is_set():
                raise RuntimeError('Hermes detenido por el usuario.')
            if mode == 'docker':
                return self._run_docker(conversation, text, history, on_event, active, reasoning_effort)
            return self._run_local(conversation, text, history, on_event, active, reasoning_effort)
        finally:
            with self.active_lock:
                if self.active_runs.get(conversation) is active:
                    self.active_runs.pop(conversation, None)

    def _run_docker(self, conversation, text, history, on_event, active=None, reasoning_effort=None):
        if not self.api_key:
            raise RuntimeError('Configura HERMES_API_KEY en services/media-api/.env para usar Hermes Docker.')
        messages = [
            {**message, 'content': self._to_docker_text(str(message.get('content', '')))}
            for message in history
        ]
        messages.append({'role': 'user', 'content': self._to_docker_text(text)})
        headers = {'X-Hermes-Session-Id': conversation} if conversation else {}
        answer_parts = []
        try:
            with self._client(httpx.Timeout(1800, connect=5)) as client:
                if active is not None:
                    with self.active_lock:
                        active['client'] = client
                    if active['stop'].is_set():
                        raise RuntimeError('Hermes detenido por el usuario.')
                try:
                    body = {
                        'model': 'hermes-agent',
                        'messages': messages,
                        'stream': True,
                    }
                    if reasoning_effort:
                        body['model_options'] = {'reasoning_effort': reasoning_effort}
                    with client.stream('POST', '/v1/chat/completions', headers=headers, json=body) as response:
                        effective_session = response.headers.get('X-Hermes-Session-Id')
                        if conversation and effective_session:
                            with self.active_lock:
                                if active is not None and active['stop'].is_set():
                                    self.orphan_sessions[conversation] = {
                                        'session_id': effective_session,
                                        'stored_session_id': None,
                                        'mode': 'docker',
                                    }
                                else:
                                    self.sessions[conversation] = effective_session
                        if response.status_code in {401, 403}:
                            raise RuntimeError('Hermes Docker rechazó la clave HERMES_API_KEY.')
                        response.raise_for_status()
                        if 'text/event-stream' not in response.headers.get('content-type', ''):
                            try:
                                payload = json.loads(response.read())
                                answer_parts.append(payload['choices'][0]['message']['content'])
                            except (ValueError, KeyError, IndexError, TypeError) as exc:
                                raise RuntimeError('Hermes Docker devolvió una respuesta no válida.') from exc
                        else:
                            for event, data in _sse_events(response):
                                if active is not None and active['stop'].is_set():
                                    raise RuntimeError('Hermes detenido por el usuario.')
                                if data == '[DONE]':
                                    break
                                try:
                                    payload = json.loads(data)
                                except ValueError:
                                    continue
                                if not isinstance(payload, dict):
                                    continue
                                if event == 'hermes.tool.progress':
                                    status = payload.get('status')
                                    if status in {'running', 'completed'}:
                                        on_event('tool.start' if status == 'running' else 'tool.complete', {
                                            'name': payload.get('tool', 'procesando'),
                                            'label': payload.get('label', ''),
                                            'tool_call_id': payload.get('toolCallId', ''),
                                            'result': {},
                                        })
                                    continue
                                choices = payload.get('choices') or []
                                if choices:
                                    content = (choices[0].get('delta') or {}).get('content')
                                    if isinstance(content, str):
                                        answer_parts.append(content)
                                    if choices[0].get('finish_reason') == 'error':
                                        raise RuntimeError((payload.get('error') or {}).get('message') or 'Hermes Docker no pudo completar la petición.')
                finally:
                    if active is not None:
                        with self.active_lock:
                            active['client'] = None
        except RuntimeError:
            raise
        except httpx.HTTPError as exc:
            if active is not None and active['stop'].is_set():
                raise RuntimeError('Hermes detenido por el usuario.') from exc
            raise RuntimeError(f'No se pudo conectar con Hermes Docker en {self.api_url}.') from exc
        if active is not None and active['stop'].is_set():
            raise RuntimeError('Hermes detenido por el usuario.')
        try:
            answer = ''.join(answer_parts)
        except (TypeError, ValueError) as exc:
            raise RuntimeError('Hermes Docker devolvió una respuesta no válida.') from exc
        answer = self._from_docker_text(answer or '')
        on_event('message.complete', {'text': answer})
        return answer

    def _run_local(self, conversation, text, history, on_event, active=None, reasoning_effort=None):
        text = self._from_docker_text(text)
        history = [
            {**message, 'content': self._from_docker_text(str(message.get('content', '')))}
            for message in history
        ]
        with self.lock:
            if active is not None:
                with self.active_lock:
                    active['owns_local'] = True
            if active is not None and active['stop'].is_set():
                raise RuntimeError('Hermes detenido por el usuario.')
            self._start()
            if active is not None and active['stop'].is_set():
                self._terminate_local()
                raise RuntimeError('Hermes detenido por el usuario.')
            deadline = time.monotonic() + 1800
            sid = self.sessions.get(conversation)
            if sid and self.session_reasoning.get(conversation) != reasoning_effort:
                self.sessions.pop(conversation, None)
                self.stored_sessions.pop(conversation, None)
                self.session_reasoning.pop(conversation, None)
                sid = None
            if not sid:
                create_params = {'cwd': str(self.root), 'title': 'MedIA archivo audiovisual', 'messages': history}
                if reasoning_effort:
                    create_params['reasoning_effort'] = reasoning_effort
                rid = self._send('session.create', create_params)
                while True:
                    frame = self._next(deadline)
                    if frame.get('id') == rid:
                        if frame.get('error'):
                            raise RuntimeError(str(frame['error']))
                        sid = frame['result']['session_id']
                        stored_sid = frame['result'].get('stored_session_id')
                        with self.active_lock:
                            if active is not None and active['stop'].is_set():
                                self.orphan_sessions[conversation] = {
                                    'session_id': sid,
                                    'stored_session_id': stored_sid,
                                    'mode': 'local',
                                }
                            else:
                                self.sessions[conversation] = sid
                                if stored_sid:
                                    self.stored_sessions[conversation] = stored_sid
                                self.session_reasoning[conversation] = reasoning_effort
                        break
                if active is not None and active['stop'].is_set():
                    raise RuntimeError('Hermes detenido por el usuario.')
            rid = self._send('prompt.submit', {'session_id': sid, 'text': text})
            try:
                while True:
                    if active is not None and active['stop'].is_set():
                        raise RuntimeError('Hermes detenido por el usuario.')
                    frame = self._next(deadline)
                    if frame.get('id') == rid and frame.get('error'):
                        raise RuntimeError(str(frame['error']))
                    params = frame.get('params') or {}
                    event = frame.get('method') or frame.get('event') or ''
                    # Hermes uses method=event and an event name in params.
                    event = params.get('type', params.get('event', event))
                    payload = params.get('payload', params.get('data', params))
                    frame_sid = params.get('session_id', frame.get('session_id'))
                    if frame_sid and frame_sid != sid:
                        continue
                    if event in {'approval.request', 'clarify.request', 'secret.request', 'sudo.request'}:
                        raise RuntimeError('Hermes necesita una confirmación interactiva. Continúa esta petición en Hermes Desktop.')
                    on_event(event, payload)
                    if event == 'message.complete':
                        if payload.get('status') in {'error', 'interrupted'}:
                            raise RuntimeError(payload.get('error') or payload.get('text') or 'Hermes interrumpido')
                        return payload.get('text', '')
            except Exception:
                if active is not None and active['stop'].is_set():
                    self._terminate_local()
                elif self.process and self.process.poll() is None:
                    self._send('session.interrupt', {'session_id': sid})
                self.sessions.pop(conversation, None)
                self.stored_sessions.pop(conversation, None)
                self.session_reasoning.pop(conversation, None)
                raise

    def close(self):
        if self.process and self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
