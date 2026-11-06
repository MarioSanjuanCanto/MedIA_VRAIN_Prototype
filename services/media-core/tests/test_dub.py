from pathlib import Path
from types import SimpleNamespace
import subprocess
import wave
import array
import math
import pytest

from media_core.services import dubbing
from media_core.tools import dub


def test_atempo_chain_supports_large_and_small_adjustments():
    assert dubbing._atempo_chain(4.0) == "atempo=2.00000000,atempo=2.00000000"
    assert dubbing._atempo_chain(0.25) == "atempo=0.50000000,atempo=0.50000000"


def test_translate_segments_preserves_ids_and_times(monkeypatch):
    monkeypatch.setattr(dubbing, "get_openrouter_key", lambda: "key")
    monkeypatch.setattr(dubbing, "call_openrouter", lambda *args, **kwargs: (
        '{"segments":[{"id":0,"text":"The accident"},{"id":1,"text":"in Mieres"}]}'
    ))
    source = [
        {"start_seconds": 0, "end_seconds": 2, "text": "El accidente"},
        {"start_seconds": 2.5, "end_seconds": 4, "text": "en Mieres"},
    ]

    result = dubbing.translate_segments(source, "en")

    assert [item["translated_text"] for item in result] == ["The accident", "in Mieres"]
    assert result[1]["start_seconds"] == 2.5


def test_translate_segments_batches_long_videos(monkeypatch):
    monkeypatch.setattr(dubbing, "get_openrouter_key", lambda: "key")
    calls = []

    def translate(messages, **kwargs):
        batch = __import__("json").loads(messages[1]["content"])
        calls.append(batch)
        return __import__("json").dumps({
            "segments": [{"id": item["id"], "text": f'EN {item["text"]}'} for item in batch]
        })

    monkeypatch.setattr(dubbing, "call_openrouter", translate)
    source = [{
        "start_seconds": index * 2, "end_seconds": index * 2 + 1.5,
        "text": f"Segment {index}",
    } for index in range(65)]

    result = dubbing.translate_segments(source, "en")

    assert [len(batch) for batch in calls] == [30, 30, 5]
    assert result[64]["translated_text"] == "EN Segment 64"


def test_render_locks_each_voice_to_its_window_and_copies_video(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video-source")
    voice = tmp_path / "voice.wav"
    voice.write_bytes(b"RIFF" + b"x" * 100)
    monkeypatch.setattr(dubbing, "OUTPUT_CLIPS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(dubbing, "probe_video", lambda _: {'duration': 5.0})
    monkeypatch.setattr(dubbing, 'verify_audio', lambda path, duration, *args: {'decoded_duration_seconds': duration})
    monkeypatch.setattr(dubbing, "synthesize_speech", lambda **kwargs: {
        "status": "success", "audio_file": str(voice), "duration_seconds": 4.0,
    })
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(b"x" * 2048)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(dubbing.subprocess, "run", run)
    result = dubbing.render_dubbed_video(source, [{
        "start_seconds": 1.0, "end_seconds": 3.0,
        "text": "Original", "translated_text": "Translated",
    }], "en")

    command = commands[0]
    filters = command[command.index("-af") + 1]
    assert "atempo=2.00000000" in filters
    assert "atrim=duration=2.000000" in filters
    voice_filters = commands[1][commands[1].index('-filter_complex') + 1]
    assert "adelay=1000|1000" in voice_filters
    assert 'apad=whole_dur=5.000000,atrim=duration=5.000000' in voice_filters
    assert commands[-1][commands[-1].index('-t') + 1] == '5.000000'
    assert commands[-1][commands[-1].index("-c:v") + 1] == "copy"
    assert Path(result["video_file"]).is_file()


def test_render_filter_graph_runs_in_real_ffmpeg(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    voice = tmp_path / "voice.wav"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=160x90:r=10:d=2",
        "-f", "lavfi", "-i", "sine=frequency=220:duration=2", "-shortest",
        "-c:v", "libx264", "-c:a", "aac", str(source),
    ], check=True, timeout=30)
    with wave.open(str(voice), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(array.array('h', (int(6000*math.sin(2*math.pi*440*i/16000)) for i in range(16000))).tobytes())
    monkeypatch.setattr(dubbing, "OUTPUT_CLIPS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(dubbing, "synthesize_speech", lambda **kwargs: {
        "status": "success", "audio_file": str(voice), "duration_seconds": 1.0,
    })

    result = dubbing.render_dubbed_video(source, [{
        "start_seconds": 0.5, "end_seconds": 1.5,
        "text": "Original", "translated_text": "Translated",
    }], "en")

    output = Path(result["video_file"])
    assert output.is_file() and output.stat().st_size > 1024
    probe = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(output),
    ], check=True, capture_output=True, text=True, timeout=10)
    assert 1.8 <= float(probe.stdout) <= 2.1
    # A plausible file size must never make a corrupt cached dub count as success.
    output.write_bytes(b'x'*2048)
    recovered = dubbing.render_dubbed_video(source, [{
        'start_seconds': .5, 'end_seconds': 1.5, 'text': 'Original', 'translated_text': 'Translated',
    }], 'en')
    assert recovered['cache_hit'] is False
    assert recovered['audio_verification']['verified_segments'] == 1


def test_long_dub_keeps_all_13_voices_and_full_audio_duration(tmp_path, monkeypatch):
    source, voice = tmp_path / 'long.mp4', tmp_path / 'voice.wav'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=s=160x90:r=10:d=94',
        '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-t', '94', '-c:v', 'libx264',
        '-preset', 'ultrafast', '-threads', '1', '-c:a', 'aac', str(source)], check=True, timeout=30)
    with wave.open(str(voice), 'wb') as audio:
        audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        audio.writeframes(array.array('h', (int(6000*math.sin(2*math.pi*880*i/16000)) for i in range(16000))).tobytes())
    monkeypatch.setattr(dubbing, 'OUTPUT_CLIPS_DIR', tmp_path / 'outputs')
    monkeypatch.setattr(dubbing, 'synthesize_speech', lambda **kwargs: {'status':'success','audio_file':str(voice),'duration_seconds':1})
    segments = [{'start_seconds': .64+i*7, 'end_seconds': 4.64+i*7, 'text':'Original', 'translated_text':'Voice'} for i in range(13)]
    result = dubbing.render_dubbed_video(source, segments, 'en')
    assert result['audio_verification']['decoded_duration_seconds'] >= 93.9
    decoded = tmp_path / 'final.wav'
    subprocess.run(['ffmpeg','-v','error','-i',result['video_file'],'-ar','16000','-ac','1',str(decoded)],check=True,timeout=15)
    with wave.open(str(decoded), 'rb') as audio:
        for segment in segments:
            audio.setpos(round((segment['start_seconds']+1)*16000))
            samples = array.array('h',audio.readframes(16000))
            assert math.sqrt(sum(int(v)**2 for v in samples)/len(samples)) > 100


def test_audio_verification_rejects_truncation_and_silence(tmp_path):
    silent = tmp_path / 'silent.wav'
    with wave.open(str(silent),'wb') as audio:
        audio.setparams((1,2,16000,0,'NONE','not compressed'))
        audio.writeframes(b'\0\0'*16000)
    with pytest.raises(ValueError,match='incompleto'):
        dubbing.verify_audio(silent,94)
    with pytest.raises(ValueError,match='silencio'):
        dubbing.verify_audio(silent,1)


def test_media_dub_clip_reuses_original_timestamps(monkeypatch, tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"clip")
    monkeypatch.setattr(dub, "resolve_video_file", lambda source: clip)
    monkeypatch.setattr(dub, "probe_video", lambda source: {"duration": 10.0})
    monkeypatch.setattr(dub, "load_test_vector", lambda video_id: {"segments": [{
        "start_seconds": 100.0, "end_seconds": 104.0,
        "speaker": "SPEAKER_00", "text": "Accidente en Mieres",
    }]})
    captured = {}
    monkeypatch.setattr(dub, "translate_segments", lambda segments, language: [
        {**item, "translated_text": "Accident in Mieres"} for item in segments
    ])

    def render(source, segments, language, **kwargs):
        captured.update(source=source, segments=segments, language=language)
        output = tmp_path / "dubbed.mp4"
        output.write_bytes(b"dubbed")
        return {"video_file": str(output), "cache_hit": False}

    monkeypatch.setattr(dub, "render_dubbed_video", render)
    result = dub.media_dub_clip(
        source=str(clip), video_id="TV05", start_seconds=98, end_seconds=108,
        target_language="inglés", background=False,
    )

    assert result["status"] == "success"
    assert result["target_language"] == "en"
    assert result["synchronization"] == "segment_timestamps_duration_locked"
    assert result["source_clip_name"] == "clip.mp4"
    assert "source_clip" not in result
    assert captured["segments"][0]["start_seconds"] == 2.0
    assert captured["segments"][0]["end_seconds"] == 6.0


def test_media_dub_clip_is_registered():
    from media_core.tools import PUBLIC_TOOLS

    assert dub.media_dub_clip in PUBLIC_TOOLS


def test_lip_sync_is_rejected_honestly():
    result = dub.media_dub_clip(source="clip.mp4", lip_sync=True)
    assert result["status"] == "error"
    assert "lip-sync" in result["message"]


def test_background_dub_returns_immediately_and_deduplicates(monkeypatch, tmp_path):
    import threading
    import time
    from media_core.tools.jobs import media_job_status

    entered, release = threading.Event(), threading.Event()
    source = tmp_path / 'clip.mp4'
    source.write_bytes(b'clip')
    monkeypatch.setattr(dub, 'resolve_video_file', lambda _: source)
    monkeypatch.setattr(dub, 'probe_video', lambda _: {'duration': 2})
    monkeypatch.setattr(dub, '_relative_segments', lambda *a: [{'start_seconds': 0, 'end_seconds': 2, 'text': 'Hola'}])
    def translate(rows, language):
        entered.set()
        assert release.wait(5)
        return [{**rows[0], 'translated_text': 'Hello'}]
    monkeypatch.setattr(dub, 'translate_segments', translate)
    monkeypatch.setattr(dub, 'render_dubbed_video', lambda *a, **k: {'video_file': 'dubbed.mp4', 'cache_hit': False})
    try:
        queued = dub.media_dub_clip(source=str(source), video_id='TEST', start_seconds=0, end_seconds=2, wait_seconds=0)
        assert entered.wait(3)
        duplicate = dub.media_dub_clip(source=str(source), video_id='TEST', start_seconds=0, end_seconds=2, wait_seconds=0)
        assert queued['job_id'] == duplicate['job_id']
        assert duplicate['deduplicated']
        assert media_job_status(queued['job_id'])['status'] == 'running'
    finally:
        release.set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = media_job_status(queued['job_id'])
        if state['status'] == 'completed': break
        time.sleep(.01)
    assert state['status'] == 'completed'
    assert state['result']['dubbed_video_file'] == 'dubbed.mp4'
