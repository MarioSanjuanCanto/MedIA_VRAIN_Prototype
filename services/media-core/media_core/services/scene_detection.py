"""Scene cut detection, keyframing, and perceptual deduplication with FFmpeg."""

import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path
from PIL import Image

from media_core.config import KEYFRAMES_DIR, VISUAL_CACHE_DIR
from media_core.runtime import progress, stage


def compute_dhash(image_path: Path) -> int:
    """Compute 64-bit difference hash (dHash) for an image."""
    try:
        with Image.open(image_path) as img:
            # Resize to 9x8 grayscale
            resized = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
            get_data_fn = getattr(resized, "get_flattened_data", resized.getdata)
            pixels = list(get_data_fn())
            diff = 0
            for row in range(8):
                row_offset = row * 9
                for col in range(8):
                    if pixels[row_offset + col] > pixels[row_offset + col + 1]:
                        diff |= 1 << (row * 8 + col)
            return diff
    except Exception as error:
        print(f"[dHash Warning] Error hashing {image_path}: {error}", file=sys.stderr)
        return 0


def hamming_distance(hash1: int, hash2: int) -> int:
    """Compute bitwise Hamming distance between two 64-bit integer hashes."""
    return bin(hash1 ^ hash2).count("1")


def probe_video(video_path: Path) -> dict:
    """Extract technical stream metadata (duration, width, height, fps) via ffprobe."""
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate,duration,nb_frames:format=duration",
        "-of",
        "json",
        str(video_path),
    ]
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    result = subprocess.run(command, capture_output=True, text=True, check=True, creationflags=creationflags)
    data = json.loads(result.stdout)
    stream = data.get("streams", [{}])[0]
    fmt = data.get("format", {})

    duration_str = stream.get("duration") or fmt.get("duration") or "0"
    duration = float(duration_str)

    fps_parts = stream.get("r_frame_rate", "25/1").split("/")
    fps = float(fps_parts[0]) / float(fps_parts[1]) if len(fps_parts) == 2 and float(fps_parts[1]) != 0 else 25.0

    return {
        "duration": duration,
        "width": int(stream.get("width", 0)),
        "height": int(stream.get("height", 0)),
        "fps": round(fps, 2),
    }


def analyze_video_frames(
    video_path: Path,
    video_id: str,
    output_dir: Path | None = None,
    threshold: float = 0.35,
    max_interval: float = 2.5,
    dedup_hamming_threshold: int = 5,
    whisper_segments: list[dict] | None = None,
    remove_duplicate_files: bool = True,
    target_height: int = 540,
) -> dict:
    """Run single-pass scene detection and keyframe extraction, followed by perceptual deduplication.

    Returns the visual manifest dictionary.
    """
    clean_id = video_id.strip().upper()
    dest_dir = (output_dir or (KEYFRAMES_DIR / clean_id)).resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)

    with stage("video_probing"):
        progress("probing", video_id=clean_id)
        info = probe_video(video_path)
        total_duration = info["duration"]

    # Pattern for extracted JPEGs
    frame_pattern = str(dest_dir / "frame_%04d.jpg")

    # Clean existing frames in destination if re-running
    for old_file in dest_dir.glob("frame_*.jpg"):
        try:
            old_file.unlink()
        except OSError:
            pass

    filter_expr = (
        f"select='gt(scene\\,{threshold})+isnan(prev_selected_t)+gte(t-prev_selected_t\\,{max_interval})',"
        f"metadata=print:file=-,scale=-2:{target_height}"
    )

    command = [
        "ffmpeg",
        "-y",
        "-an",
        "-i",
        str(video_path),
        "-vf",
        filter_expr,
        "-fps_mode",
        "vfr",
        "-q:v",
        "3",
        frame_pattern,
    ]

    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

    with stage("scene_and_keyframe_extraction"):
        progress("extracting_frames", video_id=clean_id, duration=total_duration)
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=creationflags,
        )
        stdout, stderr = proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg falló al procesar escenas de {video_path}: {stderr[-400:]}")

    # Parse metadata from stdout
    raw_frames = []
    current_pts_time = 0.0
    current_scene_score = 0.0
    frame_index = 0

    pts_regex = re.compile(r"pts_time:([0-9.]+)")
    score_regex = re.compile(r"lavfi\.scene_score=([0-9.]+)")

    for line in stdout.splitlines():
        line = line.strip()
        pts_match = pts_regex.search(line)
        if pts_match:
            current_pts_time = float(pts_match.group(1))
        score_match = score_regex.search(line)
        if score_match:
            current_scene_score = float(score_match.group(1))
            frame_index += 1
            raw_frames.append({
                "index": frame_index,
                "timestamp": round(current_pts_time, 2),
                "scene_score": round(current_scene_score, 4),
                "is_scene_boundary": current_scene_score >= threshold or frame_index == 1,
            })

    # Fallback if metadata stdout was routed to stderr
    if not raw_frames and stderr:
        for line in stderr.splitlines():
            line = line.strip()
            pts_match = pts_regex.search(line)
            if pts_match:
                current_pts_time = float(pts_match.group(1))
            score_match = score_regex.search(line)
            if score_match:
                current_scene_score = float(score_match.group(1))
                frame_index += 1
                raw_frames.append({
                    "index": frame_index,
                    "timestamp": round(current_pts_time, 2),
                    "scene_score": round(current_scene_score, 4),
                    "is_scene_boundary": current_scene_score >= threshold or frame_index == 1,
                })

    # Verify extracted frame files on disk
    extracted_files = sorted(dest_dir.glob("frame_*.jpg"))
    if len(extracted_files) != len(raw_frames):
        # Match as many as exist on disk
        max_len = min(len(extracted_files), len(raw_frames))
        raw_frames = raw_frames[:max_len]

    # Assign scenes
    scenes = []
    current_scene_id = 0
    current_scene_start = 0.0
    current_scene_keyframe = ""

    frames = []
    for i, meta in enumerate(raw_frames):
        filename = f"frame_{meta['index']:04d}.jpg"
        file_path = dest_dir / filename

        if meta["is_scene_boundary"] or current_scene_id == 0:
            if current_scene_id > 0:
                scenes.append({
                    "id": current_scene_id,
                    "start_seconds": round(current_scene_start, 2),
                    "end_seconds": round(meta["timestamp"], 2),
                    "duration_seconds": round(meta["timestamp"] - current_scene_start, 2),
                    "keyframe": current_scene_keyframe,
                })
            current_scene_id += 1
            current_scene_start = meta["timestamp"]
            current_scene_keyframe = f"keyframes/{clean_id}/{filename}"

        frame_dict = {
            "index": meta["index"],
            "timestamp": meta["timestamp"],
            "scene_id": current_scene_id,
            "is_scene_boundary": meta["is_scene_boundary"],
            "scene_score": meta["scene_score"],
            "filename": filename,
            "path": f"keyframes/{clean_id}/{filename}",
            "file_size_bytes": file_path.stat().st_size if file_path.exists() else 0,
            "dhash": "",
            "is_duplicate": False,
            "duplicate_of": None,
        }

        # Map to whisper speech segment if available
        if whisper_segments:
            seg = next((s for s in whisper_segments if s.get("start_seconds", 0) <= meta["timestamp"] <= s.get("end_seconds", 0)), None)
            if seg:
                frame_dict["speaker"] = seg.get("speaker")
                frame_dict["speaker_text"] = seg.get("text", "")[:120]

        frames.append(frame_dict)

    # Close final scene
    if current_scene_id > 0:
        scenes.append({
            "id": current_scene_id,
            "start_seconds": round(current_scene_start, 2),
            "end_seconds": round(total_duration, 2),
            "duration_seconds": round(max(0.0, total_duration - current_scene_start), 2),
            "keyframe": current_scene_keyframe,
        })

    # Perceptual deduplication with dHash
    with stage("perceptual_deduplication"):
        progress("deduplicating", video_id=clean_id, total_frames=len(frames))
        duplicates_count = 0
        last_kept_hash = None
        last_kept_filename = None
        last_scene_id = None

        for frame in frames:
            file_path = dest_dir / frame["filename"]
            if not file_path.exists():
                continue
            h = compute_dhash(file_path)
            frame["dhash"] = f"{h:016x}"

            # Only deduplicate within the same scene
            if last_kept_hash is not None and frame["scene_id"] == last_scene_id:
                dist = hamming_distance(h, last_kept_hash)
                # Keep scene boundaries always; deduplicate periodic interior frames
                if dist < dedup_hamming_threshold and not frame["is_scene_boundary"]:
                    frame["is_duplicate"] = True
                    frame["duplicate_of"] = last_kept_filename
                    duplicates_count += 1
                    if remove_duplicate_files:
                        try:
                            file_path.unlink(missing_ok=True)
                        except OSError:
                            pass
                    continue

            last_kept_hash = h
            last_kept_filename = frame["filename"]
            last_scene_id = frame["scene_id"]

    return {
        "status": "success",
        "video_id": clean_id,
        "duration_seconds": round(total_duration, 2),
        "video_info": info,
        "scenes_count": len(scenes),
        "keyframes_count": len(frames),
        "kept_keyframes_count": len(frames) - duplicates_count,
        "deduplicated_count": duplicates_count,
        "scenes": scenes,
        "frames": frames,
    }
