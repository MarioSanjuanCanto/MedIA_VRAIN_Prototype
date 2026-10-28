---
name: media-core-tts
description: Convierte texto o un resumen de vídeo en audio mediante las capacidades TTS de Media Core. Úsala cuando el usuario solicite una locución o archivo de voz; conserva por separado el idioma del contenido, la voz, el proveedor y el formato.
---

# Sintetizar voz con Media Core

Usa `media_tts` para una salida de audio completa o `media_speak` cuando el usuario necesite fragmentos reproducibles mientras continúa la generación.

## Uso obligatorio de Media Core

Para cualquier tarea cubierta por esta skill, debes usar obligatoriamente el MCP de Media Core y sus herramientas `media_tts` o `media_speak`. No sustituyas la generación por bibliotecas locales, comandos de shell, otros MCP ni servicios externos. Si el MCP o la herramienta necesaria no están disponibles, informa de ello y solicita que se conecte Media Core; no generes el audio mediante una alternativa.

## Preparar la locución

- Usa `text` cuando el contenido que debe locutarse ya esté definido.
- Usa `video_id` solo cuando el usuario quiera que Media Core resuma ese vídeo y locute el resumen. Ajusta el estilo del resumen a la petición.
- El idioma de salida debe corresponder al texto que se va a pronunciar y a una opción admitida por el proveedor. La síntesis no implica traducción: traduce o redacta antes únicamente si el usuario lo ha pedido.
- Selecciona proveedor, voz, velocidad, estilo, descripción de voz, audio de referencia y formato a partir de la petición y de las capacidades del proveedor. Omite campos incompatibles en lugar de simular que se aplicaron.
- No presupongas un único idioma, voz, acento, género, proveedor o formato como adecuado para todas las solicitudes.

## Ejecutar y validar

1. Llama a `media_tts` con texto no vacío o con un vídeo válido para el flujo resumen-a-voz cuando necesites un archivo final único.
2. Comprueba `status`, proveedor efectivo, idioma, ruta del archivo, formato y duración antes de comunicar el resultado.
3. Devuelve la ruta exacta proporcionada por la herramienta. No reconstruyas ni inventes nombres de archivo.
4. Para generación progresiva, llama a `media_speak` con `text` o `video_id`, conserva el `job_id` y consulta `media_job_status` al intervalo sugerido.
5. Ordena los fragmentos por `chunk_index`, conserva sus tiempos y considera terminado el trabajo solo al alcanzar un estado final. Que exista un primer evento `audio_ready` no significa que la locución completa haya terminado.

## Límites y fallos

- Distingue entre una preferencia solicitada y una capacidad confirmada. Si el proveedor no admite clonación, control expresivo, una voz o un formato, informa de la limitación.
- No afirmes que el acento, estilo o identidad vocal solicitados se aplicaron si la respuesta no los confirma.
- Si falla el resumen previo, la síntesis o la conversión de formato, comunica la etapa y el mensaje devuelto; entrega cualquier audio parcial válido solo si está claramente identificado.
- `media_speak` produce archivos por frases y no confirma visemas ni sincronización fonética. No prometas esas capacidades si la respuesta no las incluye.
- No instales modelos, cambies la configuración del servidor ni envíes el texto a otro servicio salvo que el usuario haya solicitado ese trabajo adicional.
