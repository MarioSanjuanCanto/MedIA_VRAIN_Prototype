import asyncio
import json
import subprocess
import time
import wave
from pathlib import Path
from app import main as m
from app.deliverables import assemble
from app.turn_clips import parse_turn_clip_request


def make_wav(path, value, rate=16000):
    with wave.open(str(path), 'wb') as out:
        out.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        out.writeframes(int(value).to_bytes(2, 'little', signed=True) * rate)


def test_audio_join_keeps_all_chunks_in_order(tmp_path):
    first, second = tmp_path / 'first.wav', tmp_path / 'second.wav'
    make_wav(first, 1000)
    make_wav(second, -1000, 24000)
    result = assemble([first, second], 'audio', tmp_path)
    with wave.open(str(result), 'rb') as wav:
        assert abs(wav.getnframes() / wav.getframerate() - 2) < .01
        wav.setpos(wav.getframerate() // 2)
        assert int.from_bytes(wav.readframes(1), 'little', signed=True) > 0
        wav.setpos(wav.getframerate() * 3 // 2)
        assert int.from_bytes(wav.readframes(1), 'little', signed=True) < 0


def test_clip_join_is_a_single_decodable_file(tmp_path):
    paths = []
    for color in ['red', 'blue']:
        path = tmp_path / f'{color}.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', f'color={color}:s=32x32:r=25', '-t', '1', '-c:v', 'libx264', str(path)], check=True, capture_output=True)
        paths.append(path)
    result = assemble(paths, 'clip', tmp_path)
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'json', str(result)], check=True, capture_output=True)
    assert abs(float(json.loads(out.stdout)['format']['duration']) - 2) < .1
    subprocess.run(['ffmpeg', '-v', 'error', '-i', str(result), '-f', 'null', '-'], check=True, capture_output=True)


def test_chat_publishes_final_selection_not_exploratory_outputs(tmp_path, monkeypatch):
    root = tmp_path / 'clips'
    root.mkdir()
    old, final = root / 'discarded.mp4', root / 'chosen.mp4'
    old.write_bytes(b'old')
    final.write_bytes(b'final')
    monkeypatch.setattr(m, 'ROOTS', {'clip': root, 'audio': tmp_path / 'audio'})
    monkeypatch.setattr(m, 'STATE', tmp_path / 'state')
    monkeypatch.setattr(m, 'catalog', lambda: [{'filename': 'TV04.mp4', 'id': 'TV04', 'status': 'ready'}])
    async def ping(): pass
    monkeypatch.setattr(m, 'mcp_ping', ping)
    job = {'id': 'request', 'conversation_id': 'request', 'created_at': time.time(), 'artifacts': [], 'events': []}
    def run(conversation, prompt, history, callback):
        for path in [old, final]:
            callback('tool.start', {'name': 'media_clips'})
            callback('tool.complete', {'name': 'media_clips', 'result': [{'cut_video_file': str(path)}]})
        return f'El resultado elegido es {final}'
    monkeypatch.setattr(m.bridge, 'run', run)
    asyncio.run(m.execute(job, m.NewJob(video='TV04.mp4', action='chat', message='Dame un clip')))
    assert job['status'] == 'completed'
    assert [a['name'] for a in job['artifacts']] == ['chosen.mp4']
    assert len(job['events']) == 2


def test_chat_publishes_five_requested_turns_as_separate_clips(tmp_path, monkeypatch):
    root = tmp_path / 'clips'
    root.mkdir()
    paths = [root / f'turn_{index}.mp4' for index in range(1, 6)]
    for path in paths:
        path.write_bytes(b'clip')
    monkeypatch.setattr(m, 'ROOTS', {'clip': root, 'audio': tmp_path / 'audio'})
    groups = [[m.artifact(str(path)) for path in paths]]
    job = {'artifacts': []}
    asyncio.run(m.publish_deliverables(job, groups, ' '.join(str(path) for path in paths)))
    assert [item['name'] for item in job['artifacts']] == [path.name for path in paths]


def test_turn_clip_requests_select_all_first_five_and_followup_six():
    original = 'Mira, me podrías dar todos los clips ordenados de las intervenciones de Salvador Illa en TV04?'
    assert parse_turn_clip_request(original, []) == {
        'video_id': 'TV04', 'person': 'Salvador Illa', 'mode': 'all',
    }
    assert parse_turn_clip_request(
        'Dame los 5 primeros clips de las intervenciones de Salvador Illa en TV04', []
    ) == {'video_id': 'TV04', 'person': 'Salvador Illa', 'mode': 'first', 'count': 5}
    assert parse_turn_clip_request(
        'Dame los primeros cinco clips de las intervenciones de Salvador Illa en TV04', []
    ) == {'video_id': 'TV04', 'person': 'Salvador Illa', 'mode': 'first', 'count': 5}
    assert parse_turn_clip_request(
        'Dame el clip del número 6', [{'role': 'user', 'content': original}]
    ) == {'video_id': 'TV04', 'person': 'Salvador Illa', 'mode': 'index', 'index': 6}
    assert parse_turn_clip_request('Dame el clip del número 6', [
        {'role': 'user', 'content': original},
        {'role': 'user', 'content': 'Ahora estamos hablando de TV05'},
    ]) is None


def test_chat_cuts_all_diarized_turns_in_order_and_batches(tmp_path, monkeypatch):
    root = tmp_path / 'clips'
    root.mkdir()
    paths = [root / f'turn_{index}.mp4' for index in range(1, 14)]
    for path in paths:
        path.write_bytes(b'clip')
    turns = [{'start_seconds': index * 100.0, 'end_seconds': index * 100.0 + 20}
             for index in range(1, 14)]
    calls = []

    async def call(name, args):
        calls.append((name, args))
        if name == 'media_speakers':
            return {'status': 'success', 'query_person': args['person'], 'turns': turns}
        assert name == 'media_clips'
        first = args['turn_index']
        return [{
            'status': 'success', 'turn_index': index,
            'start_seconds': turns[index - 1]['start_seconds'],
            'end_seconds': turns[index - 1]['end_seconds'],
            'start_time': f'00:{index:02d}:00', 'end_time': f'00:{index:02d}:20',
            'cut_status': 'archivo_mp4_cortado_exitosamente',
            'cut_video_file': str(paths[index - 1]),
        } for index in range(first, first + args['max_clips'])]

    monkeypatch.setattr(m, 'ROOTS', {'clip': root})
    monkeypatch.setattr(m, 'mcp_call', call)
    monkeypatch.setattr(m, 'save', lambda job: None)
    monkeypatch.setattr(m, 'chat_jobs', lambda conversation_id: [])
    monkeypatch.setattr(m.bridge, 'run', lambda *args: (_ for _ in ()).throw(
        AssertionError('Hermes must not select these turns through semantic search')))
    job = {'id': 'all-turns', 'conversation_id': 'conversation', 'created_at': time.time(),
           'artifacts': [], 'events': [], 'status': 'queued'}
    asyncio.run(m.execute_chat(job, m.ChatRequest(
        message='Dame todos los clips ordenados de las intervenciones de Salvador Illa en TV04')))
    assert job['status'] == 'completed', job.get('error')
    assert [name for name, _ in calls] == ['media_speakers', 'media_clips', 'media_clips']
    assert [(args['turn_index'], args['max_clips']) for name, args in calls[1:]] == [(1, 10), (11, 3)]
    assert [item['name'] for item in job['artifacts']] == [path.name for path in paths]
    assert '13 clips' in job['answer']


def test_chat_followup_cuts_previous_numbered_turn(tmp_path, monkeypatch):
    root = tmp_path / 'clips'
    root.mkdir()
    path = root / 'turn_6.mp4'
    path.write_bytes(b'clip')
    monkeypatch.setattr(m, 'ROOTS', {'clip': root})
    monkeypatch.setattr(m, 'save', lambda job: None)
    monkeypatch.setattr(m, 'chat_jobs', lambda conversation_id: [{
        'id': 'previous', 'status': 'completed',
        'message': 'Enumera las intervenciones de Salvador Illa en TV04',
        'answer': 'La intervención 6 va de 18:12 a 18:47.',
    }])
    calls = []

    async def call(name, args):
        calls.append((name, args))
        if name == 'media_speakers':
            return {'status': 'success', 'turns': [
                {'start_seconds': float(index), 'end_seconds': float(index + 1)}
                for index in range(13)]}
        return [{'status': 'success', 'turn_index': 6,
                 'start_seconds': 5.0, 'end_seconds': 6.0,
                 'start_time': '00:18:12', 'end_time': '00:18:47',
                 'cut_status': 'archivo_mp4_cortado_exitosamente',
                 'cut_video_file': str(path)}]

    monkeypatch.setattr(m, 'mcp_call', call)
    monkeypatch.setattr(m.bridge, 'run', lambda *args: (_ for _ in ()).throw(
        AssertionError('Hermes must not select numbered turns')))
    job = {'id': 'current', 'conversation_id': 'conversation', 'created_at': time.time(),
           'artifacts': [], 'events': [], 'status': 'queued'}
    asyncio.run(m.execute_chat(job, m.ChatRequest(message='Dame el clip del número 6')))
    assert job['status'] == 'completed', job.get('error')
    assert [name for name, _ in calls] == ['media_speakers', 'media_clips']
    assert calls[1][1]['turn_index'] == 6
    assert [item['name'] for item in job['artifacts']] == ['turn_6.mp4']


def test_audio_action_uses_one_tts_call_for_the_whole_text(tmp_path, monkeypatch):
    root = tmp_path / 'audio'
    root.mkdir()
    audio = root / 'complete.wav'
    audio.write_bytes(b'audio')
    monkeypatch.setattr(m, 'ROOTS', {'audio': root})
    monkeypatch.setattr(m, 'STATE', tmp_path / 'state')
    monkeypatch.setattr(m, 'catalog', lambda: [{'filename': 'TV04.mp4', 'id': 'TV04', 'status': 'ready'}])
    calls = []
    async def call(name, args):
        calls.append(name)
        if name == 'media_summarize':
            return {'executive_summary': 'Primera frase. Segunda frase. Tercera frase.'}
        assert args['text'] == 'Primera frase. Segunda frase. Tercera frase.'
        return {'audio_file': str(audio)}
    monkeypatch.setattr(m, 'mcp_call', call)
    job = {'id': 'audio', 'conversation_id': 'audio', 'created_at': time.time(), 'artifacts': [], 'events': []}
    asyncio.run(m.execute(job, m.NewJob(video='TV04.mp4', action='audio')))
    assert job['status'] == 'completed'
    assert calls == ['media_summarize', 'media_tts']
    assert len(job['artifacts']) == 1


def test_failed_or_running_audio_does_not_publish_fragments():
    job = {'artifacts': []}
    m.collect({'status': 'running', 'events': [{'audio_file': 'partial.wav'}]}, job)
    m.collect({'status': 'error', 'completed_chunks': [{'audio_file': 'partial.wav'}]}, job)
    assert not job['artifacts']


def test_combined_clip_and_voice_request_uses_one_each(tmp_path, monkeypatch):
    monkeypatch.setattr(m, 'STATE', tmp_path / 'state')
    monkeypatch.setattr(m, 'catalog', lambda: [{'filename': 'TV04.mp4', 'id': 'TV04', 'status': 'ready'}])
    calls = []
    async def call(name, args):
        calls.append((name, args))
        if name == 'media_clips':
            return [{'start_time': '00:19:54', 'end_time': '00:22:10', 'cut_video_file': str(tmp_path / 'clip.mp4')}]
        if name == 'media_summarize':
            return {'executive_summary': 'Resumen del turno.'}
        return {'audio_file': str(tmp_path / 'voice.wav')}
    (tmp_path / 'clip.mp4').write_bytes(b'clip')
    (tmp_path / 'voice.wav').write_bytes(b'voice')
    monkeypatch.setattr(m, 'mcp_call', call)
    async def ping(): pass
    monkeypatch.setattr(m, 'mcp_ping', ping)
    job = {'id': 'combined', 'conversation_id': 'combined', 'created_at': time.time(), 'artifacts': [], 'events': []}
    asyncio.run(m.execute(job, m.NewJob(video='TV04.mp4', action='chat', message='Dime qué dice VOX, dame un clip y locútalo')))
    assert job['status'] == 'completed'
    assert [name for name, _ in calls] == ['media_clips', 'media_summarize', 'media_tts']
    assert job['clips'][0]['start_time'] == '00:19:54'
