"""Public MCP tools and registration."""

import asyncio
from functools import wraps

from media_core.tools.analyze_video import media_analyze_video
from media_core.tools.ocr import media_ocr_video
from media_core.tools.visual_search import media_index_visual, media_search_visual
from media_core.tools.multimodal import media_index_multimodal, media_multimodal_index_status, media_search_multimodal
from media_core.tools.vlm_tool import media_describe_scene, media_scene_context
from media_core.tools.context import media_read_transcript
from media_core.tools.visual_sweep import media_find_all_visual
from media_core.tools.jobs import media_process, media_speak, media_job_status, media_job_cancel
from media_core.tools.clips import media_clips
from media_core.tools.search import media_search
from media_core.tools.server_info import get_server_info
from media_core.tools.summarize import media_summarize
from media_core.tools.speakers import media_speakers
from media_core.tools.transcribe import media_transcribe
from media_core.tools.tts import media_tts
from media_core.tools.dub import media_dub_clip
from media_core.tools.subtitles import media_subtitle_video
from media_core.tools.image_crop import media_crop_subject
from media_core.tools.identify_person import media_identify_person_web
from media_core.tools.forget_video import media_forget_video
from media_core.tools.voice import media_voice_status, media_transcribe_microphone, media_voice_reply

PUBLIC_TOOLS = (
    media_read_transcript, media_scene_context,
    media_voice_status, media_transcribe_microphone, media_voice_reply,
    media_process, media_speak, media_job_status, media_job_cancel,
    media_transcribe,
    media_analyze_video,
    media_ocr_video,
    media_index_visual,
    media_search_visual,
    media_index_multimodal,
    media_multimodal_index_status,
    media_search_multimodal,
    media_describe_scene,
    media_crop_subject,
    media_identify_person_web,
    media_speakers,
    media_find_all_visual,
    media_summarize,
    media_clips,
    media_search,
    media_tts,
    media_dub_clip,
    media_subtitle_video,
    media_forget_video,
    get_server_info,
)


def register_tools(mcp) -> None:
    """Register every public Media Core tool on a FastMCP server."""
    for tool in PUBLIC_TOOLS:
        # FastMCP executes synchronous tools inline: offload without changing schemas.
        def wrap(function):
            @wraps(function)
            async def nonblocking(*args, **kwargs):
                return await asyncio.to_thread(function, *args, **kwargs)
            return nonblocking
        mcp.tool()(wrap(tool))


__all__ = [
    "media_read_transcript", "media_scene_context",
    "media_voice_status", "media_transcribe_microphone", "media_voice_reply",
    "get_server_info",
    "media_analyze_video",
    "media_clips",
    "media_crop_subject",
    "media_describe_scene",
    "media_find_all_visual",
    "media_forget_video",
    "media_identify_person_web",
    "media_index_visual",
    "media_job_cancel",
    "media_ocr_video",
    "media_search",
    "media_search_visual",
    "media_index_multimodal", "media_multimodal_index_status", "media_search_multimodal",
    "media_speakers",
    "media_summarize",
    "media_transcribe",
    "media_tts",
    "media_dub_clip",
    "media_subtitle_video",
    "register_tools",
]
