---
name: media-core-process
description: Orquesta con Media Core el flujo completo desde un archivo audiovisual hasta una transcripción y uno o más clips. Úsala cuando la petición abarque el procesamiento completo, no para operar únicamente sobre una transcripción ya disponible.
---

# Procesar contenido audiovisual con Media Core

Usa la herramienta `media_process` del servidor Media Core para coordinar transcripción y extracción de clips en un solo trabajo. Consulta el nombre y el esquema registrados en el cliente antes de invocarla; algunos clientes añaden un prefijo al nombre de la herramienta.

## Uso obligatorio de Media Core

Para cualquier tarea cubierta por esta skill, debes usar obligatoriamente el MCP de Media Core y su herramienta `media_process`. No sustituyas este flujo por scripts locales, comandos de shell, bibliotecas, otros MCP ni servicios externos. Si el MCP o la herramienta no están disponibles, informa de ello y solicita que se conecte Media Core; no ejecutes la tarea mediante una alternativa.

## Preparar la operación

- Identifica la fuente exacta solicitada. No sustituyas el archivo o identificador por otro contenido disponible.
- Conserva la consulta temática del usuario cuando quiera clips sobre un asunto concreto; déjala vacía cuando quiera una selección general.
- Elige idioma de transcripción, diarización, modo de selección y cantidad de clips según la petición y las capacidades declaradas por el esquema. No deduzcas el idioma de la transcripción a partir del idioma en que el usuario quiere recibir la respuesta.
- Si falta una elección no esencial, usa el valor predeterminado de la herramienta. Pregunta solo cuando la decisión cambie materialmente el resultado.

## Ejecutar y seguir el trabajo

1. Llama una sola vez a `media_process` con la fuente y las opciones resueltas.
2. Conserva el `job_id` devuelto. No lances en paralelo transcripción ni extracción de clips para la misma solicitud.
3. Consulta `media_job_status` al intervalo sugerido por la respuesta. Continúa mientras el estado sea `queued` o `running` y termina al llegar a `completed` o `failed`.
4. Al usar `after_sequence`, conserva la última secuencia observada para no procesar eventos duplicados.

## Validar y entregar

- Considera completado el flujo solo si el estado final y el resultado indican éxito.
- Comprueba que la transcripción pertenece a la fuente solicitada y revisa sus advertencias.
- Para cada clip, distingue entre una propuesta temporal y un archivo generado. Solo presenta un clip como archivo cuando la salida incluya una ruta no vacía y un estado de corte satisfactorio.
- Devuelve únicamente rutas y metadatos presentes en la respuesta de la herramienta.
- Si falla una fase, informa de la fase, el mensaje devuelto y cualquier resultado parcial válido. No repitas llamadas equivalentes ni sustituyas el flujo por herramientas externas salvo que el usuario lo pida.
