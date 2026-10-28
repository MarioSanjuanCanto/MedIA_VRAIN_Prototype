---
name: media-core-clips
description: Selecciona y, cuando sea posible, extrae clips de un vídeo con Media Core, priorizando la selección editorial salvo indicación contraria. Úsala sobre contenido ya transcrito; para un archivo nuevo que también deba transcribirse, usa el flujo completo de procesamiento.
---

# Seleccionar y extraer clips con Media Core

Usa `media_clips` conforme al esquema expuesto por el servidor conectado. La selección puede devolver metadatos de un fragmento aunque no exista un archivo de vídeo cortado; mantén esa distinción al responder.

## Uso obligatorio de Media Core

Para cualquier tarea cubierta por esta skill, debes usar obligatoriamente el MCP de Media Core y su herramienta `media_clips`. No sustituyas la selección o el corte por scripts locales, FFmpeg directo, otros MCP ni servicios externos. Si el MCP o la herramienta no están disponibles, informa de ello y solicita que se conecte Media Core; no ejecutes la tarea mediante una alternativa.

## Configurar la selección

- Identifica el vídeo exacto y confirma que dispone de segmentos o de una transcripción utilizable.
- Si el usuario pide intervenciones de una persona, consulta `media_speakers(video_id=..., person=...)` para obtener la lista numerada y usa `media_clips` con `person` y los índices del turno. No conviertas un ordinal ya conocido en una consulta textual: `media_clips(video_id="TV04", person="Salvador Illa", turn_index=6, cut_video_files=True)` corta el sexto turno completo.
- Para "los 5 primeros clips de las intervenciones de Salvador Illa", usa `media_clips(video_id="TV04", person="Salvador Illa", turn_index=1, max_clips=5, cut_video_files=True)`. `turn_index` es 1-based y `max_clips` indica cuántos turnos consecutivos cortar desde ahí. Para turnos no consecutivos, usa `turn_indices=[2, 4, 7]`. Para los últimos, consulta primero el total en `media_speakers` y calcula el primer índice.
- Si el usuario pide más de 10 intervenciones, llama a `media_clips` en lotes consecutivos de hasta 10 y entrega todas las rutas finales en orden.
- Si el usuario proporciona tiempos exactos, usa `media_clips(video_id=..., start_seconds=..., end_seconds=..., cut_video_files=True)`. Conserva los decimales de la fuente; no vuelvas a buscar el intervalo por nombre o frase.
- Si el usuario busca un asunto concreto, convierte su petición en un tema breve antes de llamar a la herramienta: usa entre 2 y 8 palabras significativas y elimina instrucciones como "hazme un clip", el identificador del vídeo y fórmulas de cortesía. Ejemplo: "hazme un clip de TV05 sobre el accidente de un motorista en Cabranes" → `query="accidente motorista Cabranes"`.
- Si solicita momentos destacados sin tema, usa una selección general compatible con la herramienta.
- Si el usuario no indica un modo de selección, usa `selection_mode="auto"`: intenta primero una coincidencia textual rápida y recurre automáticamente al selector editorial solo si no encuentra evidencia.
- Usa `selection_mode="editorial"` directamente para criterios subjetivos sin términos verificables, como "el momento más gracioso" o "el mejor momento".
- Usa `selection_mode="fast"` únicamente cuando el usuario lo solicite o priorice explícitamente rapidez o menor coste frente a la precisión editorial. No cambies automáticamente a `fast` solo porque la consulta contenga términos concretos.
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
