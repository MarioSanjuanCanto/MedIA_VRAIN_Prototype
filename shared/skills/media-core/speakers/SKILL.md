---
name: media-core-speakers
description: Consulta hablantes e intervenciones de un vídeo diarizado mediante Media Core. Úsala para saber quién habla en un instante, listar los turnos de una persona o consultar el directorio de hablantes; no para identificar a quien solo aparece en imagen.
---

# Consultar hablantes con Media Core

Usa `media_speakers` sobre una transcripción con diarización para resolver quién habla y cuándo.

## Uso obligatorio de Media Core

Para cualquier tarea cubierta por esta skill, debes usar obligatoriamente el MCP de Media Core y su herramienta `media_speakers`. No sustituyas la consulta por inferencias visuales, scripts locales, otros MCP o servicios externos. Si falta una transcripción diarizada, prepara esa fuente con la herramienta de transcripción de Media Core antes de consultar hablantes.

## Elegir la consulta

- Proporciona `video_id` y `timestamp` para saber quién habla en un segundo concreto.
- Proporciona `video_id` y `person` para listar todas las intervenciones asociadas a un nombre o rol.
- Proporciona solo `video_id` para obtener el directorio de hablantes identificados en ese vídeo.
- Usa segundos para `timestamp`; convierte minutos y horas con cuidado y conserva el valor original del usuario para poder verificarlo.
- No envíes a la vez `timestamp` y `person` esperando combinar filtros: la consulta por instante tiene prioridad.

## Validar e interpretar

- Confirma que existe una transcripción con segmentos y diarización para la fuente solicitada.
- Distingue el identificador técnico del hablante, el nombre resuelto, el rol y el nombre mostrado cuando la respuesta los separe.
- Comprueba los límites temporales de cada turno. En pausas, solapamientos o cambios muy próximos, comunica la incertidumbre que refleje el resultado.
- No confundas «persona visible» con «persona que habla». Para identificar a alguien que aparece en imagen, usa las herramientas de escena.
- No conviertas automáticamente etiquetas como `SPEAKER_00` en identidades reales ni atribuyas cargos no confirmados.

## Errores y entrega

- Si falta la diarización, informa de ese requisito en lugar de inventar el hablante a partir del texto o la imagen.
- Si una búsqueda por persona no devuelve turnos, prueba variantes solo cuando estén respaldadas por el nombre o rol solicitado; no elijas otra persona parecida.
- Entrega el hablante, los intervalos y el número de turnos usando los campos devueltos por Media Core.
