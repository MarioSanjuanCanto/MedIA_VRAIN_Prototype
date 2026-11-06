---
name: media-core-clips
description: Selecciona clips con Hermes desde evidencia de Media Core y valida y corta los MP4 con media_clips, evitando un segundo selector generativo.
---

# Seleccionar y extraer clips con Media Core

Usa `media_clips` conforme al esquema expuesto por el servidor conectado. La selección puede devolver metadatos de un fragmento aunque no exista un archivo de vídeo cortado; mantén esa distinción al responder.

## Uso obligatorio de Media Core

Usa Media Core para obtener evidencia, validar intervalos y cortar los archivos. Hermes realiza la decisión editorial; no uses scripts locales ni FFmpeg directo para sustituir el corte.

## Configurar la selección

- Identifica el vídeo exacto y confirma que dispone de segmentos o de una transcripción utilizable.
- Si el usuario pide intervenciones de una persona, consulta `media_speakers(video_id=..., person=...)` para obtener la lista numerada y usa `media_clips` con `person` y los índices del turno. No conviertas un ordinal ya conocido en una consulta textual: `media_clips(video_id="TV04", person="Salvador Illa", turn_index=6, cut_video_files=True)` corta el sexto turno completo.
- Para "los 5 primeros clips de las intervenciones de Salvador Illa", usa `media_clips(video_id="TV04", person="Salvador Illa", turn_index=1, max_clips=5, cut_video_files=True)`. `turn_index` es 1-based y `max_clips` indica cuántos turnos consecutivos cortar desde ahí. Para turnos no consecutivos, usa `turn_indices=[2, 4, 7]`. Para los últimos, consulta primero el total en `media_speakers` y calcula el primer índice.
- Si el usuario pide más de 10 intervenciones, llama a `media_clips` en lotes consecutivos de hasta 10 y entrega todas las rutas finales en orden.
- Si el usuario proporciona tiempos exactos, usa `media_clips(video_id=..., start_seconds=..., end_seconds=..., cut_video_files=True)`. Conserva los decimales de la fuente; no vuelvas a buscar el intervalo por nombre o frase.
- Si el usuario busca un asunto concreto, convierte su petición en un tema breve antes de llamar a la herramienta: usa entre 2 y 8 palabras significativas y elimina instrucciones como "hazme un clip", el identificador del vídeo y fórmulas de cortesía. Ejemplo: "hazme un clip de TV05 sobre el accidente de un motorista en Cabranes" → `query="accidente motorista Cabranes"`.
- Para un tema, busca candidatos con los índices y lee la transcripción para comprobarlos. Para destacados o criterios subjetivos, lee `media_read_transcript` siguiendo next_offset; decide tú desde la fuente.
- Envía la selección a `media_clips(video_id=..., candidates=[{"start_seconds":10,"end_seconds":40,"title":"Título","match_evidence":"cita literal del intervalo"}], max_clips=1, cut_video_files=True)`. Cada cita debe tener al menos 12 caracteres y aparecer literalmente dentro del intervalo. Todos los candidatos se validan antes de cortar.
- No uses auto ni editorial sin candidates: esas rutas heredadas llaman a un segundo modelo. fast permite coincidencia textual sin LLM, pero no sirve para elegir el mejor momento subjetivo.
- Ajusta el número máximo de clips y la creación de archivos a la petición. No impongas una plataforma, audiencia, duración, idioma ni criterio editorial adicional que el usuario no haya indicado.
- Usa `transcript_text` solo cuando corresponda a la fuente solicitada y no haya una transcripción asociada disponible.

## Ejecutar y validar

1. Llama a `media_clips` con los parámetros resueltos después de consultar `media_speakers` cuando corresponda. Para más de 10 turnos, usa llamadas consecutivas de hasta 10.
2. Revisa cada elemento por separado: estado, tiempos de inicio y fin, duración, cobertura del contexto y modo de selección.
3. Descarta como éxito cualquier intervalo inválido o resultado marcado como error.
4. Si se pidió cortar los archivos, confirma tanto el estado de corte satisfactorio como una ruta de salida no vacía antes de afirmar que el clip fue generado.
5. Devuelve títulos, justificaciones, tiempos y rutas tal como proceden de la herramienta, aclarando cuándo los cortes son aproximados.

## Recuperación

- Si faltan segmentos, solicita o ejecuta la transcripción solo cuando esté dentro del alcance del usuario.
- Si una selección general no puede completarse y existe un resumen completo válido, puede intentarse una única selección temática basada en ese resumen. Preséntala como alternativa temática, no como clasificación objetiva de los mejores momentos.
- Si no existe vídeo local, entrega la propuesta temporal cuando sea útil, pero no inventes un archivo.
- Ante un error, informa del mensaje concreto y evita repetir llamadas equivalentes o realizar cortes externos salvo petición expresa.
