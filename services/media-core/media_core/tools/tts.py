"""MCP speech synthesis tool using Qwen3-TTS."""

from media_core.services.tts import synthesize_speech


def media_tts(
    text: str = "",
    video_id: str = "",
    summary_style: str = "ejecutivo",
    language: str = "es",
    speaker: str = "",
    style_instruction: str = "",
    voice_description: str = "",
    speed_factor: float | None = None,
    output_filename: str = "",
    reference_audio: str = "",
    provider: str = "auto",
    format_mp3: bool = False,
) -> dict:
    """Sintetiza texto o resúmenes con Piper local en CPU; Qwen opcional con provider=qwen.

    Voces locales: español de España, catalán central e inglés. No garantiza acento valenciano.
    Piper usa speaker=0 con las voces predeterminadas; no admite instrucciones de estilo ni clonación.
    Para reproducción progresiva usa media_speak. WAV por defecto evita conversión MP3.

    Args:
        text: Texto directo a locutar. Si se deja vacío y se indica video_id, se resume y locuta automáticamente.
        video_id: Identificador del vídeo ('TV01', 'TV05') si se desea locutar el resumen del vídeo.
        summary_style: Estilo del resumen si se usa video_id ('ejecutivo', 'detallado', 'redes').
        language: Código o nombre del idioma ('es' para español de España, 'ca'/'val', 'en', 'auto').
        speaker: Piper: '0' con las voces predeterminadas. Qwen: Aiden, Ryan, etc.
        style_instruction: Instrucción de estilo opcional (ej: 'locución seria de telediario', 'tono entusiasta').
        voice_description: Descripción en lenguaje natural para diseñar una voz personalizada.
        speed_factor: Factor de velocidad (ej: 1.15 para ritmo ágil de telediario; 1.0 para normal).
        output_filename: Nombre personalizado del archivo generado (opcional).
        provider: auto usa TTS_PROVIDER, por defecto piper; qwen es remoto; http usa endpoint configurado.
        reference_audio: Audio para clonación; requiere backend que la admita.
        format_mp3: False devuelve WAV directamente, recomendado para baja latencia.

    Returns:
        dict con la ruta del archivo de audio generado (.mp3), duración, tamaño y metadatos.
    """
    clean_text = text.strip()

    if not clean_text and video_id:
        from media_core.tools.summarize import media_summarize

        summary_result = media_summarize(video_id=video_id, style=summary_style, language=language)
        if summary_result.get("status") == "error":
            return {
                "status": "error",
                "message": f"No se pudo generar el resumen previo para locutar: {summary_result.get('message')}",
            }
        clean_text = summary_result.get("executive_summary", "")
        if not output_filename:
            output_filename = f"resumen_{video_id.strip().lower()}_{summary_style}.mp3"

    if not clean_text:
        return {
            "status": "error",
            "message": "Debe especificar un texto para locutar en 'text' o indicar un 'video_id' válido para resumir y locutar.",
        }

    return synthesize_speech(
        text=clean_text,
        language=language,
        speaker=speaker,
        style_instruction=style_instruction,
        voice_description=voice_description,
        speed_factor=speed_factor,
        output_filename=output_filename,
        reference_audio=reference_audio, provider=provider, format_mp3=format_mp3,
    )
