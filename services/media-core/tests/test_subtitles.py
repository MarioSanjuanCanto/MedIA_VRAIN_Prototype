import subprocess
from pathlib import Path

from media_core.services import subtitles
from media_core.tools import subtitles as subtitle_tool
from PIL import Image, ImageFont
from types import SimpleNamespace


def test_caption_font_is_scalable_and_supports_accents(tmp_path):
    font = subtitles._font(54)
    assert isinstance(font, ImageFont.FreeTypeFont)
    assert font.size == 54
    assert bytes(font.getmask('á')) != bytes(font.getmask('a'))
    assert bytes(font.getmask('ñ')) != bytes(font.getmask('\ufffd'))
    output = tmp_path / 'caption.png'
    subtitles._caption_png(1920, 1080, 'Señora Sáenz, Cataluña está\na disposición del país.', output)
    with Image.open(output) as image:
        box = image.getbbox()
        assert box and box[2] - box[0] > 650
        assert box[3] - box[1] > 100
        assert box[0] >= 0 and box[2] <= 1920


def test_cropped_overlay_preserves_all_pixels_and_accents(tmp_path):
    full, strip = tmp_path / 'full.png', tmp_path / 'strip.png'
    text = 'Señora Sáenz, Cataluña está\na disposición del país.'
    subtitles._caption_png(1920, 1080, text, full)
    subtitles._caption_png(1920, 1080, text, strip, crop_top=810)
    with Image.open(full) as original, Image.open(strip) as cropped:
        assert cropped.size == (1920, 270)
        assert original.crop((0, 810, 1920, 1080)).tobytes() == cropped.tobytes()


def test_failed_hardware_export_retries_cpu_without_losing_output_options(monkeypatch):
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=1 if len(commands) == 1 else 0, stderr='')
    monkeypatch.setattr(subtitles.subprocess, 'run', run)
    command = ['ffmpeg', '-i', 'source.mp4', '-c:v', 'h264_qsv', *subtitles.ENCODERS['h264_qsv'],
               '-pix_fmt', 'yuv420p', '-c:a', 'copy', 'output.mp4']
    assert subtitles._export(command, 'h264_qsv').returncode == 0
    fallback = commands[1]
    assert fallback[fallback.index('-c:v') + 1] == 'libx264'
    assert '-global_quality' not in fallback
    assert fallback[-5:] == ['-pix_fmt', 'yuv420p', '-c:a', 'copy', 'output.mp4']


def test_unavailable_hardware_uses_cpu(monkeypatch):
    monkeypatch.setattr(subtitles.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1))
    subtitles._select_encoder.cache_clear()
    try:
        assert subtitles._select_encoder('auto') == 'libx264'
    finally:
        subtitles._select_encoder.cache_clear()


def test_srt_timestamp_and_utf8_output(tmp_path):
    destination = tmp_path / "captions.srt"
    subtitles.write_srt([{
        "start_seconds": 61.234, "end_seconds": 63.5,
        "subtitle_text": "Información original en español",
    }], destination)

    content = destination.read_text(encoding="utf-8")
    assert "00:01:01,234 --> 00:01:03,500" in content
    assert "Información original" in content


def test_long_caption_is_split_into_two_line_cues_without_losing_text():
    text = " ".join(f"palabra{index}" for index in range(30))
    result = subtitles.split_caption_segments([{
        "start_seconds": 10.0, "end_seconds": 20.0, "subtitle_text": text,
    }], line_width=28)

    assert len(result) > 1
    assert all(item["subtitle_text"].count("\n") <= 1 for item in result)
    assert result[0]["start_seconds"] == 10.0
    assert result[-1]["end_seconds"] == 20.0
    assert " ".join(item["subtitle_text"].replace("\n", " ") for item in result) == text


def test_real_ffmpeg_burns_subtitles_and_preserves_audio(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=320x180:r=10:d=2",
        "-f", "lavfi", "-i", "sine=frequency=220:duration=2", "-shortest",
        "-c:v", "libx264", "-c:a", "aac", str(source),
    ], check=True, timeout=30)
    monkeypatch.setattr(subtitles, "OUTPUT_CLIPS_DIR", tmp_path / "outputs")

    result = subtitles.render_subtitled_video(source, [{
        "start_seconds": 0.25, "end_seconds": 1.75, "subtitle_text": "English caption",
    }], "en")

    output = Path(result["video_file"])
    assert output.is_file() and output.stat().st_size > 1024
    probe = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "stream=codec_type,pix_fmt",
        "-of", "json", str(output),
    ], check=True, capture_output=True, text=True, timeout=10)
    streams = __import__("json").loads(probe.stdout)["streams"]
    assert {stream["codec_type"] for stream in streams} == {"video", "audio"}
    assert next(stream for stream in streams if stream["codec_type"] == "video")["pix_fmt"] == "yuv420p"


def _mock_source(monkeypatch, tmp_path):
    video = tmp_path / "TV05.mp4"
    video.write_bytes(b"video")
    monkeypatch.setattr(subtitle_tool, "resolve_video_file", lambda source: video)
    monkeypatch.setattr(subtitle_tool, "probe_video", lambda source: {"duration": 10.0})
    monkeypatch.setattr(subtitle_tool, "_relative_segments", lambda *args: [{
        "start_seconds": 1.0, "end_seconds": 4.0, "text": "Texto original",
    }])
    return video


def test_original_language_never_calls_translation(monkeypatch, tmp_path):
    _mock_source(monkeypatch, tmp_path)
    monkeypatch.setattr(subtitle_tool, "translate_segments", lambda *args: (_ for _ in ()).throw(
        AssertionError("Original subtitles must not call a translator")))
    captured = {}

    def render(source, segments, language, **kwargs):
        captured.update(segments=segments, language=language)
        output = tmp_path / "original.mp4"
        sidecar = tmp_path / "original.srt"
        output.write_bytes(b"video")
        sidecar.write_text("captions")
        return {"video_file": str(output), "subtitle_file": str(sidecar), "cache_hit": False}

    monkeypatch.setattr(subtitle_tool, "render_subtitled_video", render)
    result = subtitle_tool.media_subtitle_video(video_id="TV05", background=False)

    assert result["status"] == "success"
    assert result["translated"] is False
    assert result["subtitle_language"] == "original"
    assert captured["segments"][0]["subtitle_text"] == "Texto original"


def test_requested_language_translates_but_keeps_timestamps(monkeypatch, tmp_path):
    _mock_source(monkeypatch, tmp_path)
    monkeypatch.setattr(subtitle_tool, "translate_segments", lambda segments, language: [{
        **segments[0], "translated_text": "English text",
    }])
    captured = {}

    def render(source, segments, language, **kwargs):
        captured.update(segments=segments, language=language)
        output = tmp_path / "english.mp4"
        sidecar = tmp_path / "english.srt"
        output.write_bytes(b"video")
        sidecar.write_text("captions")
        return {"video_file": str(output), "subtitle_file": str(sidecar), "cache_hit": False}

    monkeypatch.setattr(subtitle_tool, "render_subtitled_video", render)
    result = subtitle_tool.media_subtitle_video(video_id="TV05", target_language="inglés", background=False)

    assert result["status"] == "success"
    assert result["translated"] is True
    assert result["subtitle_language"] == "en"
    assert captured["segments"][0]["subtitle_text"] == "English text"
    assert captured["segments"][0]["start_seconds"] == 1.0


def test_media_subtitle_video_is_registered():
    from media_core.tools import PUBLIC_TOOLS

    assert subtitle_tool.media_subtitle_video in PUBLIC_TOOLS


def test_subtitles_on_dubbed_clip_keep_supplied_video_and_relative_times(tmp_path, monkeypatch):
    original, dubbed = tmp_path / 'TENNIS.mp4', tmp_path / 'TENNIS_dub_en.mp4'
    original.write_bytes(b'original'); dubbed.write_bytes(b'dubbed')
    monkeypatch.setattr(subtitle_tool, 'resolve_video_file', lambda name: original if name == 'TENNIS' else dubbed)
    monkeypatch.setattr(subtitle_tool, 'probe_video', lambda _: {'duration': 60})
    def relative(video_id, start, end):
        assert (video_id, start, end) == ('TENNIS', 430., 490.)
        return [{'start_seconds': 1, 'end_seconds': 3, 'text': 'Hello'}]
    monkeypatch.setattr(subtitle_tool, '_relative_segments', relative)
    monkeypatch.setattr(subtitle_tool, 'media_clips', lambda **kwargs: (_ for _ in ()).throw(AssertionError('Must not recut original')))
    monkeypatch.setattr(subtitle_tool, 'translate_segments', lambda rows, language: [{**rows[0], 'translated_text': 'Hola'}])
    def render(source, rows, language, **kwargs):
        assert source == dubbed and language == 'ca'
        assert rows[0]['start_seconds'] == 1
        return {'video_file': str(tmp_path / 'dub_sub_ca.mp4'), 'subtitle_file': str(tmp_path / 'ca.srt'), 'cache_hit': False}
    monkeypatch.setattr(subtitle_tool, 'render_subtitled_video', render)
    result = subtitle_tool.media_subtitle_video(source=str(dubbed), video_id='TENNIS', start_seconds=430,
        end_seconds=490, target_language='ca', background=False)
    assert result['status'] == 'success'
    assert result['subtitled_video_file'].endswith('dub_sub_ca.mp4')


def test_subtitle_default_submits_job(monkeypatch):
    from media_core.tools import jobs
    captured = {}
    def submit(operation, arguments):
        captured.update(arguments)
        return {'status': 'queued', 'job_id': 'subtitle-job'}
    monkeypatch.setattr(jobs, '_submit', submit)
    monkeypatch.setattr(jobs, 'wait_for_media_result', lambda submitted, wait_seconds: submitted)
    result = subtitle_tool.media_subtitle_video(source='dubbed.mp4', video_id='TENNIS', start_seconds=430, end_seconds=490, target_language='ca')
    assert result['job_id'] == 'subtitle-job'
    assert captured['source'] == 'dubbed.mp4' and captured['background'] is False
