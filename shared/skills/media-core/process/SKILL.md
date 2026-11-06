---
name: media-core-process
description: Prepara con Media Core un archivo nuevo, selecciona clips con Hermes y genera los cortes validados. Úsala para transcripción y clips en un mismo flujo.
---

# Procesar un vídeo

1. Identifica la fuente y llama a `media_transcribe(source=..., language="auto", diarization=False, index_transcript=True, background=True)`. Activa diarización cuando se necesite.
2. Conserva job_id y espera con `media_job_status` hasta completed o failed. Comprueba el resultado; no dupliques la transcripción.
3. Busca con los índices para un tema concreto, o lee `media_read_transcript` y sus páginas para destacados generales. Selecciona tú desde la evidencia.
4. Llama a `media_clips` con candidates: tiempos en segundos, título y match_evidence literal de al menos 12 caracteres del intervalo. Para turnos de una persona, usa `media_speakers` y sus índices; para tiempos conocidos, pásalos directamente.
5. Confirma cut_status y la ruta de cada MP4 antes de entregarlo. Una propuesta temporal sin archivo no es un clip generado.

No uses `media_process` para la selección habitual: la ruta heredada delega de nuevo en un modelo editorial. No uses scripts locales ni FFmpeg manual. Para narración, redacta desde el intervalo elegido y pasa text a `media_tts` con provider="piper". Ante errores, conserva los resultados válidos y explica qué parte falta.
