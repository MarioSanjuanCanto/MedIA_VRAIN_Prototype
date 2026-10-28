---
name: media-core-video-indexing
description: Transcribe e indexa contenido audiovisual mediante Media Core para dejarlo disponible en búsquedas posteriores. Úsala al incorporar una fuente nueva o actualizar deliberadamente una transcripción; no para consultar contenido que ya está indexado.
---

# Transcribir e indexar vídeo con Media Core

Usa `media_transcribe` para obtener una transcripción reutilizable y añadirla al índice de Media Core. Ajusta siempre la llamada al nombre y al esquema que exponga el servidor conectado; el cliente puede añadir un prefijo a las herramientas.

## Uso obligatorio de Media Core

Para cualquier tarea cubierta por esta skill, debes usar obligatoriamente el MCP de Media Core y su herramienta `media_transcribe`. No sustituyas la transcripción o indexación por scripts locales, comandos de shell, bibliotecas, otros MCP ni servicios externos. Si el MCP o la herramienta no están disponibles, informa de ello y solicita que se conecte Media Core; no completes la tarea mediante una alternativa.

## Preparar la transcripción

- Identifica la fuente exacta indicada por el usuario mediante su identificador, nombre o ruta admitida. No selecciones una fuente predeterminada ni la sustituyas por otro contenido disponible.
- Elige el idioma de reconocimiento según la petición, los metadatos fiables de la fuente o la detección automática admitida por la herramienta. No lo deduzcas del idioma en que el usuario quiere recibir la respuesta.
- Activa la diarización cuando el usuario necesite distinguir hablantes o cuando sea necesaria para el resultado; ten en cuenta que puede aumentar el coste y el tiempo de procesamiento.
- Mantén habilitada la indexación de la transcripción para las tareas cubiertas por esta skill.
- Solicita los segmentos completos solo cuando sean necesarios para la entrega o la verificación; una respuesta compacta suele bastar para confirmar el proceso.
- No fuerces una nueva transcripción si existe una caché compatible. Usa la opción de reprocesamiento únicamente cuando el usuario lo solicite o haya evidencia de que la fuente o la transcripción almacenada están desactualizadas o son incompatibles.

## Ejecutar y seguir el trabajo

1. Para una operación que pueda tardar, llama a `media_transcribe` en segundo plano cuando el esquema lo permita.
2. Conserva el identificador de vídeo y el `job_id` devueltos. No inicies trabajos equivalentes en paralelo para la misma fuente y configuración.
3. Consulta `media_job_status` al intervalo sugerido por la herramienta mientras el estado sea `queued` o `running`.
4. Detén las consultas al llegar a `completed` o `failed`. Un trabajo aceptado o en ejecución todavía no es una transcripción terminada.
5. Si la operación se ejecuta de forma síncrona, aplica directamente las mismas comprobaciones al resultado final.

## Validar la transcripción y la indexación

- Confirma que el resultado final corresponde a la fuente y al identificador solicitados.
- Comprueba que el estado de la transcripción sea satisfactorio y que exista contenido transcrito utilizable.
- Revisa idioma detectado o aplicado, duración, segmentos, diarización, uso de caché y advertencias cuando esos campos estén presentes.
- Verifica la indexación por separado. Una transcripción correcta no implica que el índice se haya actualizado: comprueba el indicador de indexación y cualquier error asociado.
- Cuando haga falta una comprobación funcional, usa `media_search` a través del mismo MCP con una consulta pertinente y limitada al vídeo indexado. Valora la identidad y el contexto de los resultados; no impongas un umbral universal de puntuación.

## Tratar errores y entregar

- Usa el código y el mensaje de error para decidir el siguiente paso. No atribuyas automáticamente el fallo a un proveedor o componente concreto sin evidencia.
- Corrige primero entradas inválidas, una fuente ausente o capacidades no configuradas. No cambies arbitrariamente el idioma ni desactives funciones solo para obtener una respuesta aparentemente satisfactoria.
- Realiza como máximo un reintento automático cuando el error señale una modificación concreta, segura y coherente con la petición. Para cambios que alteren materialmente el resultado, pide confirmación.
- Si la transcripción se completa pero la indexación falla, informa de ambos estados por separado y conserva como resultado parcial la transcripción válida.
- Entrega únicamente identificadores, metadatos, texto y rutas presentes en la respuesta de Media Core. No generes además resúmenes o clips salvo que el usuario los haya solicitado.
