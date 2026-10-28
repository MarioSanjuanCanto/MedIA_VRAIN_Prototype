"""Persist non-sensitive provider usage for the local cost dashboard."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading

from media_core.config import DATA_DIR


_write_lock = threading.Lock()


def record_usage(provider: str, model: str, cost_usd: float | None, **units) -> None:
    """Append one API usage event without prompts, outputs, or credentials."""
    target = Path(
        os.getenv("MEDIA_USAGE_FILE", str(DATA_DIR / "state" / "api-usage.jsonl"))
    ).expanduser()
    event = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "provider": provider,
        "model": model,
        "cost_usd": round(float(cost_usd), 8) if cost_usd is not None else None,
        **{key: value for key, value in units.items() if value is not None},
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with _write_lock, target.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:
        # Usage telemetry must never make media generation fail.
        return
