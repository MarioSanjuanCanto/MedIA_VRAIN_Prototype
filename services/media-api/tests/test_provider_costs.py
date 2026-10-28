from datetime import datetime, timezone
import asyncio
import json

import httpx
import pytest

from app import provider_costs as costs_module
from app.provider_costs import estimate_replicate_prediction, fal_cost, local_usage, openrouter_cost


def test_replicate_estimates_supported_models_only():
    assert estimate_replicate_prediction({
        'model': 'qwen/qwen3-tts', 'input': {'text': 'x' * 1000}
    }, 0.0014) == pytest.approx(0.02)
    assert estimate_replicate_prediction({
        'model': 'victor-upmeet/whisperx', 'metrics': {'predict_time': 10}
    }, 0.0014) == pytest.approx(0.014)
    assert estimate_replicate_prediction({'model': 'unknown/model'}, 0.0014) is None


def test_local_usage_ignores_old_invalid_and_unknown_rows(tmp_path):
    state = tmp_path / 'data/state'
    state.mkdir(parents=True)
    state.joinpath('api-usage.jsonl').write_text('\n'.join([
        json.dumps({'created_at': '2026-09-10T10:00:00+00:00', 'provider': 'openrouter', 'cost_usd': 0.25}),
        json.dumps({'created_at': '2026-08-10T10:00:00+00:00', 'provider': 'openrouter', 'cost_usd': 9}),
        json.dumps({'created_at': '2026-09-10T10:00:00+00:00', 'provider': 'other', 'cost_usd': 9}),
        'broken',
    ]), encoding='utf-8')

    usage = local_usage(state, datetime(2026, 9, 1, tzinfo=timezone.utc))

    assert usage['openrouter'] == {'cost_usd': 0.25, 'requests': 1, 'measured_requests': 1, 'estimated_requests': 0}
    assert usage['fal']['requests'] == 0


def test_openrouter_reads_monthly_usage_from_current_key():
    def handler(request):
        assert request.headers['Authorization'] == 'Bearer secret'
        return httpx.Response(200, json={'data': {'usage_monthly': 3.75, 'is_management_key': False}})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await openrouter_cost(client, 'secret')
    result = asyncio.run(run())
    assert result['cost_usd'] == 3.75
    assert result['cost_type'] == 'actual'
    assert 'secret' not in json.dumps(result)


def test_fal_sums_billing_events():
    def handler(request):
        assert request.headers['Authorization'] == 'Key admin-secret'
        return httpx.Response(200, json={'billing_events': [
            {'cost_estimate_nano_usd': 1_500_000}, {'cost_estimate_nano_usd': 2_000_000}
        ], 'has_more': False, 'next_cursor': None})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fal_cost(client, 'admin-secret',
                datetime(2026, 9, 1, tzinfo=timezone.utc), datetime(2026, 9, 17, tzinfo=timezone.utc))
    result = asyncio.run(run())
    assert result['cost_usd'] == 0.0035
    assert result['requests'] == 2


def test_stale_snapshot_returns_immediately_then_refreshes(tmp_path, monkeypatch):
    state = tmp_path / 'data/state'
    state.mkdir(parents=True)
    old = {'currency': 'USD', 'period': datetime.now(timezone.utc).strftime('%Y-%m'),
        'actual_cost_usd': 1, 'estimated_cost_usd': 0, 'total_cost_usd': 1,
        'complete': True, 'providers': [], 'refreshed_at': '2020-01-01T00:00:00+00:00',
        'refreshing': False}
    state.joinpath('provider-costs.json').write_text(json.dumps(old), encoding='utf-8')
    fresh = {**old, 'actual_cost_usd': 2, 'total_cost_usd': 2}

    async def fetch(root, target_state):
        await asyncio.sleep(0)
        return fresh
    monkeypatch.setattr(costs_module, '_fetch_provider_costs', fetch)
    costs_module._refresh_tasks.clear()

    async def run():
        stale = await costs_module.provider_costs(tmp_path, state)
        assert stale['total_cost_usd'] == 1
        assert stale['refreshing'] is True
        await asyncio.gather(*costs_module._refresh_tasks.values())
        updated = await costs_module.provider_costs(tmp_path, state)
        assert updated['total_cost_usd'] == 2
        assert updated['refreshing'] is False
    asyncio.run(run())
