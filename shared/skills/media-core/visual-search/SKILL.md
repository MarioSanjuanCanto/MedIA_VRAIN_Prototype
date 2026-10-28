---
name: media-core-visual-search
description: Busca objetos, personas, acciones y escenarios visibles en vídeos mediante el índice multimodal de Media Core. Úsala para localizar contenido visual aunque no se mencione en la transcripción; no para buscar palabras pronunciadas.
---

# Buscar contenido visual con Media Core

Usa `media_search_visual` para recuperar rápidamente las escenas más similares a un concepto. Usa `media_find_all_visual` cuando el usuario necesite recorrer todo un vídeo y localizar todas las apariciones estimadas.

## Uso obligatorio de Media Core

Para cualquier tarea cubierta por esta skill, debes usar obligatoriamente el MCP de Media Core y sus herramientas de búsqueda visual. No sustituyas la consulta por búsqueda textual en transcripciones, inspección manual, scripts locales, otros MCP o servicios externos. Si Media Core no está disponible, informa de ello y solicita que se conecte.

## Elegir el tipo de búsqueda

- Usa `media_search_visual` para preguntas como «¿dónde aparece una gasolinera?» o «encuentra un presentador en plató». Formula `query` como una descripción visual breve y concreta.
- Limita por `video_id` cuando el usuario haya indicado una fuente. No mezcles resultados de otros vídeos.
- Ajusta `top_k` al número de candidatos que sea útil revisar; no lo conviertas en un supuesto de exhaustividad.
- Usa `media_find_all_visual` para «todas las apariciones», recuentos aproximados o cobertura completa de la línea temporal. Esta operación devuelve un `job_id` y puede generar clips.
- No descartes una entidad visible porque `media_search` no la encuentre en la transcripción. La búsqueda textual y la visual responden preguntas distintas.

## Seguir búsquedas exhaustivas

1. Conserva el `job_id` devuelto por `media_find_all_visual`.
2. Consulta `media_job_status` al intervalo sugerido mientras el estado sea `queued` o `running`.
3. Detén las consultas al alcanzar `completed` o `failed`.
4. Ordena y deduplica las coincidencias según los campos temporales devueltos. No inventes apariciones entre muestras.

## Validar y responder

- Comprueba vídeo, consulta, timestamp, puntuación y fotograma o clip de cada resultado cuando estén disponibles.
- Presenta los resultados como coincidencias semánticas estimadas, no como detecciones infalibles.
- Para confirmar un candidato ambiguo, usa la descripción de escena correspondiente antes de afirmarlo como hecho.
- Un recuento de apariciones no equivale a un número de individuos únicos salvo que el resultado confirme seguimiento de identidad.
- Si no hay resultados, comprueba primero que el vídeo esté indexado visualmente; no concluyas de inmediato que el objeto nunca aparece.
