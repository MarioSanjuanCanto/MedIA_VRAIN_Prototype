import asyncio
import os
from types import SimpleNamespace

import server
from media_core import config
from media_core.tools import server_info


def test_all_public_tools_are_registered():
    tools = asyncio.run(server.mcp.list_tools())

    assert [tool.name for tool in tools] == [
        "media_voice_status", "media_transcribe_microphone", "media_voice_reply",
        "media_process", "media_speak", "media_job_status", "media_job_cancel",
        "media_transcribe",
        "media_analyze_video",
        "media_ocr_video",
        "media_index_visual",
        "media_search_visual",
        "media_describe_scene",
        "media_crop_subject",
        "media_identify_person_web",
        "media_speakers",
        "media_find_all_visual",
        "media_summarize",
        "media_clips",
        "media_search",
        "media_tts",
        "media_forget_video",
        "get_server_info",
    ]


def test_transport_security_allows_local_and_docker_hosts():
    security = server.mcp.settings.transport_security

    assert security.enable_dns_rebinding_protection is True
    assert "127.0.0.1:*" in security.allowed_hosts
    assert "localhost:*" in security.allowed_hosts
    assert "host.docker.internal:*" in security.allowed_hosts
    assert "http://host.docker.internal:*" in security.allowed_origins


def test_get_server_info_reports_runtime_state(tmp_path, monkeypatch):
    videos = tmp_path / "videos"
    clips = tmp_path / "clips"
    videos.mkdir()
    clips.mkdir()
    (videos / "TV01.mp4").touch()
    (clips / "TV01_clip_1.mp4").touch()

    monkeypatch.setattr(server_info, "VIDEOS_DIR", videos)
    monkeypatch.setattr(server_info, "OUTPUT_CLIPS_DIR", clips)
    monkeypatch.setattr(server_info, "get_openrouter_key", lambda: "key")
    monkeypatch.setattr(server_info, "get_replicate_token", lambda: "key")
    monkeypatch.setattr(server_info, "get_hf_token", lambda: "key")
    monkeypatch.setattr(
        server_info,
        "get_all_test_vectors",
        lambda: [{"id": "TV01", "segments": [{"text": "segment"}]}],
    )

    result = server_info.get_server_info()

    assert result["status"] == "online"
    assert result["port"] == 8001
    assert result["local_video_files"] == ["TV01.mp4"]
    assert result["output_clips_generated"] == ["TV01_clip_1.mp4"]


def test_config_reads_and_normalizes_credentials(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", " openrouter ")
    monkeypatch.setenv("REPLICATE_API_KEY", " replicate ")
    monkeypatch.setenv("HUGGINGFACE_ACCESS_TOKEN", " huggingface ")

    assert config.get_openrouter_key() == "openrouter"
    assert config.get_replicate_token() == "replicate"
    assert config.get_hf_token() == "huggingface"
    assert os.environ["REPLICATE_API_TOKEN"] == "replicate"


def test_config_reloads_missing_credentials(monkeypatch):
    for name in [
        "OPENROUTER_API_KEY",
        "REPLICATE_API_KEY",
        "REPLICATE_API_TOKEN",
        "HUGGINGFACE_ACCESS_TOKEN",
        "HF_TOKEN",
        "HUGGING_FACE_HUB_TOKEN",
    ]:
        monkeypatch.delenv(name, raising=False)

    def fake_load_dotenv(path, override=True):
        monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter")
        monkeypatch.setenv("REPLICATE_API_TOKEN", "replicate")
        monkeypatch.setenv("HF_TOKEN", "huggingface")

    monkeypatch.setattr(config, "load_dotenv", fake_load_dotenv)

    assert config.get_openrouter_key() == "openrouter"
    monkeypatch.delenv("REPLICATE_API_KEY", raising=False)
    assert config.get_replicate_token() == "replicate"
    assert config.get_hf_token() == "huggingface"


def test_launcher_reexecutes_global_python_in_project_venv(monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(server.sys, "prefix", "C:/global-python")
    monkeypatch.setattr(Path, "is_file", lambda self: True)
    monkeypatch.delenv("MEDIA_CORE_REEXEC", raising=False)
    captured = []
    if os.name == "nt":
        import pytest
        def child(command, env):
            captured.append((command[0], command, env))
            return 17
        monkeypatch.setattr(server.subprocess, "call", child)
        with pytest.raises(SystemExit) as error:
            server.ensure_project_python()
        assert error.value.code == 17
    else:
        monkeypatch.setattr(server.os, "execve", lambda *args: captured.append(args))
        server.ensure_project_python()
    assert len(captured) == 1
    executable, argv, env = captured[0]
    assert ".venv" in executable
    assert argv[1].endswith("server.py")


def test_launcher_keeps_project_python(monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(server.sys, "prefix", str(Path(server.__file__).resolve().parent / ".venv"))
    monkeypatch.setattr(Path, "is_file", lambda self: True)
    def unexpected(*args):
        raise AssertionError("Already running in project venv")
    monkeypatch.setattr(server.os, "execve", unexpected)
    monkeypatch.setattr(server.subprocess, "call", unexpected)
    server.ensure_project_python()


def test_server_preloads_embeddings_before_serving(monkeypatch):
    events = []
    embedder = SimpleNamespace(warm_up=lambda: events.append("embeddings"))
    monkeypatch.setattr(server, "get_chroma_store", lambda: SimpleNamespace(embedder=embedder))
    monkeypatch.setattr(server, "CHROMA_AUTO_INDEX", False)
    monkeypatch.setattr(server.mcp, "run", lambda **kwargs: events.append("serving"))

    server.start_server()

    assert events == ["embeddings", "serving"]


def test_server_still_starts_when_embedding_preload_fails(monkeypatch):
    def fail():
        raise OSError("model unavailable")

    embedder = SimpleNamespace(warm_up=fail)
    monkeypatch.setattr(server, "get_chroma_store", lambda: SimpleNamespace(embedder=embedder))
    monkeypatch.setattr(server, "CHROMA_AUTO_INDEX", False)
    served = []
    monkeypatch.setattr(server.mcp, "run", lambda **kwargs: served.append(kwargs))

    server.start_server()

    assert served == [{"transport": server.SERVER_TRANSPORT}]
