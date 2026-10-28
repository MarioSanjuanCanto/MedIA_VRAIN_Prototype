---
name: media-core-scene-tools
description: Describe escenas concretas, recorta sujetos e investiga identidades visibles con Media Core. Úsala para inspeccionar o entregar un fotograma de un instante conocido; no para recorrer exhaustivamente todo un vídeo.
---

# Inspeccionar escenas y sujetos con Media Core

Usa `media_describe_scene` para interpretar un momento, `media_crop_subject` para aislar una persona u objeto y `media_identify_person_web` cuando el usuario solicite investigar la identidad de una persona desconocida.

## Uso obligatorio de Media Core

Para cualquier tarea cubierta por esta skill, debes usar obligatoriamente el MCP de Media Core y sus herramientas de escena. No sustituyas la descripción, localización o recorte por scripts locales, comandos de shell, otros MCP o servicios externos. Para identificar a una persona, usa la herramienta específica y conserva sus límites de confianza.

## Describir una escena

- Indica el `video_id` y el `timestamp` exactos. Usa `scene_id` solo cuando el usuario o un resultado previo hayan identificado esa escena; si se aporta, tiene prioridad sobre el timestamp.
- Formula `prompt`, `person` y `action_query` de manera concreta. No introduzcas como premisa una identidad o acción que todavía no esté demostrada.
- Mantén `multi_frame` cuando la pregunta dependa de movimiento, reacción o continuidad. Para texto u objetos estáticos puede bastar un único fotograma.
- Antes de responder, verifica que el timestamp y el fotograma devueltos correspondan al momento solicitado. Si Media Core devuelve otro instante o un archivo inexistente, informa del desajuste en vez de describirlo como correcto.

## Recortar un sujeto

- Reutiliza `keyframe_path` cuando proceda de una descripción válida del mismo instante; en otro caso proporciona `video_id` y `timestamp`.
- Describe el objetivo con rasgos visuales y posición relativa suficientes para distinguirlo.
- Usa `bbox` solo cuando las coordenadas sean fiables y respeta la escala admitida. Ajusta `padding_pct` si el usuario quiere contexto o un recorte más cerrado.
- Comprueba `status`, `source_frame`, `timestamp`, `box_2d`, dimensiones y ruta final. Entrega las referencias de imagen devueltas por la herramienta sin reconstruirlas.

## Investigar una identidad

- Usa `media_identify_person_web` únicamente cuando el usuario pida identificar a una persona o verificar una hipótesis de identidad.
- Aporta el recorte previo si existe y añade `context_hint` solo con contexto respaldado por la escena, la transcripción o el usuario.
- Distingue entre identidad confirmada, candidata probable y ausencia de evidencia. Una semejanza visual o una coincidencia contextual aislada no bastan para afirmarla como certeza.
- Si se genera una comparación, indica qué imagen procede del vídeo y cuál es la referencia externa.

## Errores y entrega

- Si no se encuentra el sujeto, no cambies silenciosamente a otra persona ni a otro fotograma.
- Si la imagen pedida no corresponde al timestamp, devuelve el error o desajuste y evita presentar el primer fotograma como sustituto.
- Incluye el fotograma, recorte o comparación usando `markdown_image`, `media_line` o la ruta exacta que devuelva Media Core, según lo que admita el cliente.
