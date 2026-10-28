"""Server-side provider billing queries with a local usage fallback."""

from datetime import datetime, timezone
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values
import httpx


PROVIDER_NAMES = {
    'openrouter': 'OpenRouter',
    'fal': 'fal.ai',
    'replicate': 'Replicate',
}
_refresh_tasks: dict[str, asyncio.Task] = {}


def _settings(root: Path) -> dict[str, str]:
    """Load provider credentials without returning or logging them."""
    values: dict[str, str] = {}
    for path in (
        root / '.env',
        root / 'services/media-core/.env',
        root / 'services/media-api/.env',
    ):
        if path.is_file():
            values.update({key: str(value) for key, value in dotenv_values(path).items() if value})
    for key in (
        'OPENROUTER_API_KEY', 'FAL_ADMIN_KEY', 'FAL_KEY', 'FAL_API_KEY',
        'REPLICATE_API_KEY', 'REPLICATE_API_TOKEN',
        'REPLICATE_A100_80GB_USD_PER_SECOND', 'REPLICATE_QWEN_TTS_USD_PER_1000_CHARS',
    ):
        if os.getenv(key):
            values[key] = os.environ[key]
    return values


def _empty(provider: str, detail: str, configured: bool = False) -> dict:
    return {
        'id': provider,
        'name': PROVIDER_NAMES[provider],
        'cost_usd': None,
        'requests': None,
        'cost_type': 'unavailable',
        'configured': configured,
        'complete': False,
        'detail': detail,
    }


def local_usage(state: Path, month_start: datetime) -> dict[str, dict]:
    """Read local events once; never sum provider snapshots here."""
    totals = {
        provider: {'cost_usd': 0.0, 'requests': 0, 'measured_requests': 0, 'estimated_requests': 0}
        for provider in PROVIDER_NAMES
    }
    try:
        lines = (state / 'api-usage.jsonl').read_text(encoding='utf-8').splitlines()
    except OSError:
        return totals
    for line in lines:
        try:
            event = json.loads(line)
            created_at = datetime.fromisoformat(str(event['created_at']).replace('Z', '+00:00'))
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            provider = totals.get(str(event.get('provider', '')).lower())
            if created_at < month_start or provider is None:
                continue
            provider['requests'] += 1
            if event.get('cost_usd') is not None:
                provider['cost_usd'] += max(0.0, float(event['cost_usd']))
                provider['measured_requests'] += 1
                if event.get('cost_type') == 'estimated':
                    provider['estimated_requests'] += 1
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return totals


def _with_fallback(result: dict, fallback: dict) -> dict:
    if result['cost_usd'] is not None or not fallback['measured_requests']:
        return result
    return {
        **result,
        'cost_usd': round(fallback['cost_usd'], 6),
        'requests': fallback['requests'],
        'cost_type': 'estimated' if fallback['estimated_requests'] else 'local',
        'complete': fallback['requests'] == fallback['measured_requests'],
        'detail': 'Estimación local; el proveedor no respondió.' if fallback['estimated_requests'] else
            'Registro local; el proveedor no respondió.',
    }


async def openrouter_cost(client: httpx.AsyncClient, key: str) -> dict:
    if not key:
        return _empty('openrouter', 'API key no configurada.')
    headers = {'Authorization': f'Bearer {key}'}
    try:
        response = await client.get('https://openrouter.ai/api/v1/key', headers=headers)
        response.raise_for_status()
        data = response.json().get('data') or {}
        if data.get('is_management_key'):
            usage = 0.0
            offset = 0
            for _ in range(100):
                page = await client.get('https://openrouter.ai/api/v1/keys', headers=headers, params={'offset': offset})
                page.raise_for_status()
                keys = page.json().get('data') or []
                usage += sum(float(item.get('usage_monthly') or 0) for item in keys)
                if not keys:
                    break
                offset += len(keys)
            detail = 'Gasto mensual de las claves del workspace.'
        else:
            usage = float(data.get('usage_monthly') or 0)
            detail = 'Gasto mensual informado por OpenRouter.'
        return {'id': 'openrouter', 'name': 'OpenRouter', 'cost_usd': round(usage, 6),
            'requests': None, 'cost_type': 'actual', 'configured': True, 'complete': True, 'detail': detail}
    except (httpx.HTTPError, TypeError, ValueError, KeyError):
        return _empty('openrouter', 'No se pudo consultar la facturación.', True)


async def fal_cost(client: httpx.AsyncClient, key: str, start: datetime, end: datetime) -> dict:
    if not key:
        return _empty('fal', 'Clave Admin no configurada.')
    headers = {'Authorization': f'Key {key}'}
    params = {'start': start.isoformat(), 'end': end.isoformat(), 'limit': 1000}
    total_nano_usd = 0
    requests = 0
    try:
        while True:
            response = await client.get('https://api.fal.ai/v1/models/billing-events', headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
            events = data.get('billing_events') or []
            total_nano_usd += sum(int(event.get('cost_estimate_nano_usd') or 0) for event in events)
            requests += len(events)
            cursor = data.get('next_cursor')
            if not data.get('has_more') or not cursor:
                break
            params['cursor'] = cursor
        return {'id': 'fal', 'name': 'fal.ai', 'cost_usd': round(total_nano_usd / 1_000_000_000, 6),
            'requests': requests, 'cost_type': 'actual', 'configured': True, 'complete': True,
            'detail': 'Eventos de facturación del mes.'}
    except httpx.HTTPStatusError as exc:
        detail = 'La clave necesita alcance Admin.' if exc.response.status_code == 403 else 'No se pudo consultar la facturación.'
        return _empty('fal', detail, True)
    except (httpx.HTTPError, TypeError, ValueError, KeyError):
        return _empty('fal', 'No se pudo consultar la facturación.', True)


def estimate_replicate_prediction(prediction: dict, a100_rate: float, qwen_rate: float = 0.02) -> float | None:
    if prediction.get('status') in {'failed', 'aborted'}:
        return 0.0
    model = str(prediction.get('model') or '')
    inputs = prediction.get('input') or {}
    if model == 'qwen/qwen3-tts' and isinstance(inputs.get('text'), str):
        return len(inputs['text']) / 1000 * qwen_rate
    if model.startswith('victor-upmeet/whisperx'):
        predict_time = (prediction.get('metrics') or {}).get('predict_time')
        return float(predict_time) * a100_rate if predict_time is not None else None
    return None


async def replicate_cost(client: httpx.AsyncClient, key: str, start: datetime, end: datetime,
        a100_rate: float, qwen_rate: float) -> dict:
    if not key:
        return _empty('replicate', 'API key no configurada.')
    headers = {'Authorization': f'Bearer {key}'}
    url = 'https://api.replicate.com/v1/predictions'
    total = 0.0
    requests = 0
    measured = 0
    try:
        for _ in range(30):
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            stop = False
            for prediction in data.get('results') or []:
                created_at = datetime.fromisoformat(str(prediction['created_at']).replace('Z', '+00:00'))
                if created_at < start:
                    stop = True
                    break
                if created_at >= end:
                    continue
                requests += 1
                estimate = estimate_replicate_prediction(prediction, a100_rate, qwen_rate)
                if estimate is not None:
                    total += max(0.0, estimate)
                    measured += 1
            next_url = data.get('next')
            if stop or not next_url:
                break
            parsed = urlparse(next_url)
            if parsed.scheme != 'https' or parsed.netloc != 'api.replicate.com':
                break
            url = next_url
        detail = ('Estimación completa según entradas y tiempo de ejecución.' if requests == measured else
            f'Estimación parcial: {measured} de {requests} ejecuciones tienen tarifa calculable.')
        return {'id': 'replicate', 'name': 'Replicate', 'cost_usd': round(total, 6) if measured else None,
            'requests': requests, 'cost_type': 'estimated' if measured else 'unavailable', 'configured': True,
            'complete': requests == measured, 'detail': detail}
    except (httpx.HTTPError, TypeError, ValueError, KeyError):
        return _empty('replicate', 'No se pudieron consultar las predicciones.', True)


async def _fetch_provider_costs(root: Path, state: Path) -> dict:
    now = datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = now
    settings = _settings(root)
    local = local_usage(state, start)
    openrouter_key = settings.get('OPENROUTER_API_KEY', '').strip()
    fal_key = (settings.get('FAL_ADMIN_KEY') or settings.get('FAL_KEY') or settings.get('FAL_API_KEY') or '').strip()
    replicate_key = (settings.get('REPLICATE_API_KEY') or settings.get('REPLICATE_API_TOKEN') or '').strip()
    try:
        a100_rate = float(settings.get('REPLICATE_A100_80GB_USD_PER_SECOND', '0.0014'))
    except ValueError:
        a100_rate = 0.0014
    try:
        qwen_rate = float(settings.get('REPLICATE_QWEN_TTS_USD_PER_1000_CHARS', '0.02'))
    except ValueError:
        qwen_rate = 0.02
    timeout = httpx.Timeout(12, connect=5)
    async with httpx.AsyncClient(timeout=timeout) as client:
        import asyncio
        results = await asyncio.gather(
            openrouter_cost(client, openrouter_key),
            fal_cost(client, fal_key, start, end),
            replicate_cost(client, replicate_key, start, end, a100_rate, qwen_rate),
        )
    rows = [_with_fallback(result, local[result['id']]) for result in results]
    actual = sum(row['cost_usd'] or 0 for row in rows if row['cost_type'] in {'actual', 'local'})
    estimated = sum(row['cost_usd'] or 0 for row in rows if row['cost_type'] == 'estimated')
    return {
        'currency': 'USD',
        'period': start.strftime('%Y-%m'),
        'actual_cost_usd': round(actual, 6),
        'estimated_cost_usd': round(estimated, 6),
        'total_cost_usd': round(actual + estimated, 6),
        'complete': all(row['complete'] for row in rows if row['configured']),
        'providers': rows,
    }


def _read_snapshot(path: Path) -> dict | None:
    try:
        snapshot = json.loads(path.read_text(encoding='utf-8'))
        datetime.fromisoformat(str(snapshot['refreshed_at']).replace('Z', '+00:00'))
        return snapshot
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


async def _refresh_snapshot(root: Path, state: Path, path: Path) -> dict:
    result = await _fetch_provider_costs(root, state)
    result['refreshed_at'] = datetime.now(timezone.utc).isoformat()
    result['refreshing'] = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
        temporary.replace(path)
    except OSError:
        pass
    return result


async def provider_costs(root: Path, state: Path) -> dict:
    """Return a fresh snapshot quickly and refresh stale data in the background."""
    path = state / 'provider-costs.json'
    snapshot = _read_snapshot(path)
    current_period = datetime.now(timezone.utc).strftime('%Y-%m')
    snapshot_is_current = bool(snapshot and snapshot.get('period') == current_period)
    try:
        ttl = max(30, int(os.getenv('PROVIDER_COST_CACHE_SECONDS', '300')))
    except ValueError:
        ttl = 300
    if snapshot_is_current and snapshot:
        refreshed_at = datetime.fromisoformat(str(snapshot['refreshed_at']).replace('Z', '+00:00'))
        if refreshed_at.tzinfo is None:
            refreshed_at = refreshed_at.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - refreshed_at).total_seconds() < ttl:
            return {**snapshot, 'refreshing': False}

    task_key = str(path.resolve())
    task = _refresh_tasks.get(task_key)
    if task is None or task.done():
        task = asyncio.create_task(_refresh_snapshot(root, state, path))
        _refresh_tasks[task_key] = task
    if snapshot_is_current and snapshot:
        return {**snapshot, 'refreshing': True}
    return await task
