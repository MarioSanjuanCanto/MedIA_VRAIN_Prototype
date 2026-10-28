"""CPU synthesis with resident ONNX sessions. Download is an explicit setup step."""
import json
import os
import threading
import wave
from pathlib import Path

from media_core.config import TTS_MODELS_DIR
from media_core.runtime import stage

MODEL_DIR = Path(os.getenv("PIPER_MODEL_DIR", "").strip() or TTS_MODELS_DIR)
VOICES = {
    "Spanish": os.getenv("PIPER_VOICE_ES", "").strip() or "es_ES-carlfm-x_low",
    "Catalan": "ca_ES-upc_ona-medium",
    "English": "en_US-ljspeech-medium",
}
_voices = {}
_lock = threading.RLock()


def model_path(language):
    name = VOICES[language]
    candidate = MODEL_DIR / (name + ".onnx")
    if candidate.is_file():
        return candidate
    legacy = Path(__file__).resolve().parents[3] / "experiments" / "tts_models" / (name + ".onnx")
    if legacy.is_file():
        return legacy
    return candidate


def synthesize(text, language, speaker, speed, output):
    from piper import PiperVoice, SynthesisConfig
    import onnxruntime as ort

    path = model_path(language)
    if not path.is_file():
        raise ValueError("Falta el modelo local. Ejecuta python -m media_core.services.piper_tts --download")
    speaker_id = int(speaker or 0)
    if not 0.5 <= speed <= 2.0:
        raise ValueError("speed_factor debe estar entre 0.5 y 2.0")
    signature = (str(path), path.stat().st_mtime_ns)
    # Bound CPU oversubscription and protect the shared espeak phonemizer.
    with _lock:
        if signature not in _voices:
            with stage("tts_model_load"):
                config_path = Path(str(path) + ".json")
                from piper.config import PiperConfig
                options = ort.SessionOptions()
                options.intra_op_num_threads = max(1, int(os.getenv("PIPER_THREADS", "2")))
                options.inter_op_num_threads = 1
                voice = PiperVoice(
                    session=ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"]),
                    config=PiperConfig.from_dict(json.loads(config_path.read_text(encoding="utf-8"))),
                )
                _voices[signature] = voice
        voice = _voices[signature]
        if not 0 <= speaker_id < voice.config.num_speakers:
            raise ValueError(f"speaker debe estar entre 0 y {voice.config.num_speakers - 1} para {path.stem}")
        with stage("tts_inference"), wave.open(str(output), "wb") as wav:
            voice.synthesize_wav(text, wav, syn_config=SynthesisConfig(speaker_id=speaker_id, length_scale=1.0 / speed))
    return path.stem


def download():
    import hashlib
    import urllib.request
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for name in VOICES.values():
        locale, voice, quality = name.split("-")
        folder = f"{locale.split('_')[0]}/{locale}/{voice}/{quality}"
        for filename in (name + ".onnx", name + ".onnx.json", "MODEL_CARD"):
            url = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/{folder}/{filename}"
            target = MODEL_DIR / (name + ".MODEL_CARD" if filename == "MODEL_CARD" else filename)
            if not target.exists():
                temporary = target.with_suffix(target.suffix + ".download")
                with urllib.request.urlopen(url, timeout=120) as response, temporary.open("wb") as out:
                    import shutil
                    shutil.copyfileobj(response, out)
                temporary.replace(target)
            manifest[target.name] = {"source": url, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
        print("Ready:", name)
    (MODEL_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true", required=True)
    parser.parse_args()
    download()
