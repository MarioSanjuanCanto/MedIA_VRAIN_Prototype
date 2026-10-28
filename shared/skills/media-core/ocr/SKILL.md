---
name: media-core-ocr
description: Extrae e indexa texto visible en vídeos con el OCR de Media Core, incluidos rótulos, faldones, titulares y diapositivas. Úsala para leer texto en pantalla; no para recuperar palabras pronunciadas.
---

# Extraer texto en pantalla con Media Core

Usa `media_ocr_video` para analizar los fotogramas clave de un vídeo y recuperar texto visible con sus marcas temporales y clasificación.

## Uso obligatorio de Media Core

Para cualquier tarea cubierta por esta skill, debes usar obligatoriamente el MCP de Media Core y su herramienta `media_ocr_video`. No sustituyas el OCR por bibliotecas locales, comandos de shell, otros MCP o servicios externos. Si Media Core no está disponible, informa de ello y solicita que se conecte.

## Preparar el OCR

- Identifica la fuente exacta mediante `video_id` o una ruta admitida.
- `video_id` es obligatorio. Nunca invoques `media_ocr_video` sin él ni aceptes TV01 como sustituto de un vídeo pedido.
- Para leer un momento concreto, convierte el tiempo a segundos y llama a `media_ocr_video(video_id="TV05", timestamp=270.0)` para 4:30. Esta modalidad extrae un fotograma en ese instante; no devuelve el OCR de todo el vídeo.
- Usa `min_confidence` para controlar el equilibrio entre cobertura y falsos positivos. Conserva el valor estándar salvo que la petición justifique otra sensibilidad.
- Mantén `sample_representatives_only` para un análisis rápido de fotogramas representativos. Desactívalo únicamente cuando el usuario necesite mayor cobertura y acepte el coste adicional.
- No uses `force` si existe una caché válida. Reprocesa solo a petición del usuario o cuando la fuente o el resultado almacenado hayan cambiado.

## Ejecutar y validar

1. Llama a `media_ocr_video` con `video_id` explícito y, si se pregunta por un instante, con `timestamp`. Sin `timestamp`, la herramienta puede generar automáticamente el manifiesto visual y los fotogramas que falten para el vídeo completo.
2. Comprueba estado, identidad del vídeo, uso de caché, parámetros efectivos y textos extraídos.
3. Revisa timestamps, confianza y tipo de elemento visual antes de atribuir un texto a un instante o categoría.
   En una consulta puntual, entrega `all_text` y usa `media_line` si se necesita mostrar el fotograma en Hermes.
4. Agrupa duplicados evidentes de un mismo rótulo persistente sin perder el intervalo temporal que cubren.

## Interpretar el resultado

- Distingue el texto visible del audio transcrito. Que un nombre aparezca en un faldón no significa que se haya pronunciado.
- Conserva mayúsculas, números y nombres propios cuando sean legibles; señala caracteres inciertos en vez de corregirlos silenciosamente.
- Trata los resultados cercanos al umbral como estimaciones y evita afirmaciones categóricas cuando la imagen sea ambigua.
- Si se usaron solo fotogramas representativos, no presentes la ausencia de texto como prueba de que nunca apareció entre muestras.

## Errores

- Si faltan el vídeo o los fotogramas y la preparación automática falla, comunica la etapa exacta.
- Si el resultado está vacío, informa de los parámetros usados y diferencia entre «no detectado» y «no existe».
- Entrega únicamente texto, tiempos, clasificaciones y rutas devueltos por Media Core.
