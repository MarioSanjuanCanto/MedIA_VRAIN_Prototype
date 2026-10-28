---
name: media-core-visual-indexing
description: Analiza e indexa visualmente vídeos con Media Core para generar fotogramas clave y embeddings reutilizables. Úsala al preparar o actualizar el índice visual de una fuente; no para consultar un índice ya existente.
---

# Analizar e indexar vídeo visualmente con Media Core

Usa `media_analyze_video` para detectar escenas y extraer fotogramas clave. Usa después `media_index_visual` para generar los embeddings que permiten buscar conceptos visibles.

## Uso obligatorio de Media Core

Para cualquier tarea cubierta por esta skill, debes usar obligatoriamente el MCP de Media Core y sus herramientas `media_analyze_video` y `media_index_visual`. No sustituyas el análisis, la extracción de fotogramas ni la indexación por scripts locales, comandos de shell, otros MCP o servicios externos. Si Media Core no está disponible, informa de ello y solicita que se conecte.

## Preparar el análisis

- Identifica la fuente exacta mediante el `video_id` o la ruta admitida. No elijas un vídeo predeterminado distinto del solicitado.
- Conserva los valores estándar de `threshold` y `max_interval` salvo que la petición requiera más sensibilidad a los cortes o una cobertura temporal distinta.
- Mantén `extract_keyframes` activado cuando el resultado vaya a indexarse, describirse, recortarse o procesarse con OCR.
- No uses `force` por rutina. Actívalo solo si el usuario pide reprocesar o existe evidencia de que la fuente, el manifiesto o el índice están desactualizados o dañados.

## Ejecutar el flujo

1. Llama a `media_analyze_video` con la fuente correcta.
2. Comprueba el estado, la identidad de la fuente, el uso de caché y la presencia de escenas y fotogramas utilizables.
3. Si el objetivo incluye búsquedas visuales posteriores, llama a `media_index_visual` para el mismo vídeo.
4. Verifica por separado que el índice se haya generado o reutilizado correctamente. Un manifiesto visual válido no implica por sí solo que existan embeddings consultables.

## Validar y entregar

- Confirma que los identificadores, rutas y metadatos devueltos pertenecen al vídeo solicitado.
- Trata los fotogramas como muestras representativas, no como una copia de cada instante del vídeo.
- Si se modifican los parámetros de muestreo, explica brevemente el efecto sobre cobertura, coste y precisión temporal.
- Entrega solo rutas, métricas y estados presentes en la respuesta de Media Core. No inventes nombres de archivos ni cifras de escenas.

## Errores

- Si falta el vídeo, comunica el identificador o la ruta que no pudo resolverse.
- Si el análisis termina pero la indexación falla, informa de ambos estados por separado y conserva el manifiesto como resultado parcial válido.
- Ante una caché incompatible, realiza como máximo un reprocesamiento seguro. No encadenes reintentos con parámetros arbitrarios.
