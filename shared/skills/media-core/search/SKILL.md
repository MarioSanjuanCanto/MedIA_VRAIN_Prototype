---
name: media-core-search
description: Busca pasajes relevantes dentro de transcripciones indexadas por Media Core y devuelve contexto con marcas de tiempo. Úsala para localizar dónde se trata un tema o responder con evidencia puntual, no como sustituto de un resumen completo.
---

# Buscar en transcripciones con Media Core

Usa `media_search` con una consulta que conserve la intención del usuario. El servidor puede resolverla mediante búsqueda semántica o mediante un mecanismo alternativo; interpreta cada resultado según el motor declarado.

## Uso obligatorio de Media Core

Para cualquier tarea cubierta por esta skill, debes usar obligatoriamente el MCP de Media Core y su herramienta `media_search`. No sustituyas la búsqueda por lectura manual de transcripciones, consultas directas al índice, scripts locales, otros MCP ni servicios externos. Si el MCP o la herramienta no están disponibles, informa de ello y solicita que se conecte Media Core; no completes la tarea mediante una alternativa.

## Definir el alcance

- Redacta `query` con los conceptos relevantes de la petición sin añadir supuestos sobre personas, lugares, idioma o conclusiones.
- Incluye `video_id` cuando el usuario limite la búsqueda a un vídeo. Déjalo sin filtro únicamente cuando la petición abarque el archivo completo.
- Ajusta `top_k` a la cantidad de evidencia necesaria y a los límites del esquema; no lo uses como umbral de calidad.

## Ejecutar y evaluar

1. Llama a `media_search` con el alcance resuelto.
2. Distingue resultados normales de estados como consulta inválida, ausencia de resultados o error.
3. En cada coincidencia, valida el identificador del vídeo, especialmente si había un filtro.
4. Usa `full_context` para interpretar el pasaje y `start_time`/`end_time` para ubicarlo. `key_excerpt` por sí solo puede carecer de contexto.
5. Trata las puntuaciones según el motor indicado. No compares como si fueran equivalentes una similitud semántica y una puntuación léxica.

## Responder con evidencia

- Sintetiza lo que respaldan los fragmentos y conserva las atribuciones e incertidumbres presentes.
- Indica vídeo y marcas de tiempo cuando ayuden a verificar la respuesta.
- No concluyas que un tema está ausente de todo el contenido solo porque una consulta no devolvió resultados; prueba una reformulación únicamente cuando cambie de manera razonable los términos de recuperación.
- Si se requiere una visión general del vídeo, utiliza la herramienta de resumen. Si se solicita un archivo recortado, pasa el tema y el vídeo confirmados a la herramienta de clips.
- No inventes coincidencias, transcripciones, puntuaciones ni rutas de archivo.
