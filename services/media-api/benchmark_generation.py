"""Compare real summary latency and outputs once Media Core and Hermes are available."""
import argparse
import asyncio
import json
from pathlib import Path
import time
import uuid

from app import main
from app.agent_generation import summarize


async def benchmark(video_id, language, query):
    if not await asyncio.to_thread(main.bridge.available):
        raise RuntimeError('Hermes no está disponible. Inicia/configura el gateway o la instalación local antes de comparar.')
    await asyncio.wait_for(main.mcp_ping(), timeout=10)
    results = []
    try:
        for backend in ('core', 'hermes'):
            started = time.perf_counter()
            job = {'conversation_id': 'benchmark-' + uuid.uuid4().hex}
            if backend == 'core':
                result = main.checked(await main.mcp_call('media_summarize', {
                    'video_id': video_id, 'style': 'breve', 'language': language, 'query': query,
                }))
            else:
                async def read(name, arguments):
                    return main.checked(await main.mcp_call(name, arguments))
                result = await summarize(read, lambda prompt: main.agent_text(job, prompt),
                                         video_id, language=language, query=query)
            results.append({
                'backend': backend, 'seconds': round(time.perf_counter() - started, 3),
                'summary': result.get('executive_summary'), 'model': result.get('model_used'),
                'hermes_generation_calls': job.get('generation_metrics', {}).get('calls', 0),
                'source_pages': result.get('source_pages'),
            })
    finally:
        main.bridge.close()
    return {
        'video_id': video_id, 'language': language, 'query': query, 'results': results,
        'measurement': 'Real wall time, including transport and session cleanup. Core response caching may apply. No USD saving is inferred from request counts; compare provider usage and summary quality too.',
    }


def cli():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--video-id', required=True)
    parser.add_argument('--language', choices=['es', 'ca', 'en'], default='es')
    parser.add_argument('--query', default='')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        result = asyncio.run(benchmark(args.video_id, args.language, args.query))
    except (RuntimeError, TimeoutError) as exc:
        parser.exit(1, str(exc) + '\n')
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding='utf-8')
    print(payload)


if __name__ == '__main__':
    cli()
