---
name: media-core-visual
description: Búsqueda visual multimodal (CLIP) e inspección de fotogramas con modelo de visión (VLM) para responder preguntas sobre apariencia física, vestimenta, personas en plató, objetos, escenarios o entregar fotos/fotogramas de los vídeos.
---

# Análisis Visual e Inspección de Fotogramas con Media Core

Usa las herramientas visuales de Media Core para responder preguntas sobre lo que **se ve** físicamente en pantalla, quién aparece, cómo va vestida una persona, qué decorado hay, o cuando el usuario pida una foto o fotograma.

## ⚠️ Regla crítica: No descartar por búsqueda de texto

- `media_search` busca en la transcripción de audio (Whisper) y en el texto detectado en pantalla (OCR).
- En muchos informativos y vídeos, los presentadores o invitados **no tienen rótulo** que diga "Presentador/a" o su nombre, y la transcripción los etiqueta genéricamente como `Hablante`.
- **NUNCA afirmes que una persona, presentador, objeto o escenario no existe basándote solo en que `media_search` no encontró la palabra.** Si la pregunta es sobre aspecto físico, personas o imágenes, debes recurrir a las herramientas visuales.

## Herramientas visuales disponibles

Para peticiones exhaustivas como “encuentra todas las apariciones” o “saca todos
los clips de X”, usa `media_find_all_visual`. Recorre toda la línea temporal en
bloques, devuelve un `job_id` y debe consultarse con `media_job_status` hasta que
termine. Informa siempre de la cobertura y distingue apariciones visuales de
identidades únicas.

1. **`media_search_visual(query=..., video_id=..., top_k=5)`**:
   - Realiza búsqueda semántica multimodal (CLIP) sobre los fotogramas clave del vídeo.
   - Ejemplos de `query`: `"presentadora de noticias"`, `"presentador en plató"`, `"estudio de televisión"`, `"reportera en la calle"`, `"coche de policía"`, `"helicóptero"`.
   - Devuelve las marcas de tiempo (`start_time`), puntuación y ruta del fotograma (`keyframe`).

2. **`media_describe_scene(video_id=..., timestamp=..., person=..., action_query=..., prompt=..., multi_frame=True)`**:
   - Envía el fotograma o ráfaga temporal de fotogramas al modelo de visión (VLM) para obtener una descripción visual rigurosa.
   - Parámetros clave:
     - `timestamp`: Segundo exacto a examinar (convierte minutos a segundos si el usuario dice "minuto 1:30" -> `90.0`).
     - `person`: Nombre o rol de la persona sobre la que focalizar el análisis (ej: `"Salvador Illa"`, `"reportera"`, `"presentador"`). El VLM verificará si aparece (`person_detected`) y su cargo/rol (`person_role_identified`).
     - `action_query`: Pregunta concreta sobre la acción o suceso (ej: `"¿qué está haciendo con las manos?"`, `"¿cómo reacciona al aplauso?"`).
     - `multi_frame`: (Por defecto `True`) analiza una secuencia temporal de 3 fotogramas contiguos para comprender movimiento continuo y lenguaje corporal.
     - `prompt`: Instrucción o detalle adicional opcional.

3. **`media_speakers(video_id=..., timestamp=..., person=...)`**:
   - Resuelve con certeza quién habla en cualquier minuto exacto y extrae todos los turnos de palabra de una persona:
     - `media_speakers(video_id="TV04", timestamp=1230.0)` $\rightarrow$ Devuelve quién habla en ese segundo (ej. Ignacio Garriga, VOX) con su rol y segmento exacto.
     - `media_speakers(video_id="TV04", person="Salvador Illa")` $\rightarrow$ Devuelve todas las intervenciones continuas de esa persona en el vídeo con sus marcas de inicio y fin.
     - `media_speakers(video_id="TV04")` $\rightarrow$ Directorio completo de oradores identificados en la sesión.

4. **`media_crop_subject(video_id=..., timestamp=..., target_description=..., bbox=...)`**:
   - Localiza espacialmente y recorta con PIL a cualquier persona, objeto o elemento del fotograma según la petición del usuario.
   - Ejemplos de `target_description`: `"la mujer musulmana con hiyab detrás del orador"`, `"el diputado a la derecha sin gafas en la bancada"`, `"el logotipo en la esquina"`.
   - Devuelve `crop_url`, `markdown_image`, `absolute_crop_path` y `media_line` listos para entregar.

5. **`media_identify_person_web(video_id=..., timestamp=..., person_description=..., crop_path=..., context_hint=...)`**:
   - Identifica a personas no rotuladas o desconocidas buscando por internet (OSINT, Wikipedia, bancadas parlamentarias, noticias).
   - Recorta automáticamente al sujeto con PIL si no se aporta un recorte previo.
   - Cruza el orador del momento y su grupo parlamentario con los rasgos físicos de la persona.
   - Descarga la foto oficial de referencia (Wikimedia/Wikipedia) y genera con PIL una composición lado a lado (`[ Foto oficial | Recorte del frame ]`).
   - Devuelve informe estructurado con nombre, partido, evidencias que lo respaldan, perfil biográfico y URLs de imágenes.


## Consultas de tipo "¿Qué está pasando en el minuto X con la persona Y?"

Cuando el usuario pregunte qué ocurre en un minuto concreto o qué hace una persona en ese momento:
1. **Convierte el tiempo a segundos**: Por ejemplo, minuto 2:15 = 135.0s, minuto 1 = 60.0s.
2. **Llama directamente a `media_describe_scene`**:
   `media_describe_scene(video_id="TV01", timestamp=60.0, person="Salvador Illa", action_query="¿qué está pasando o haciendo?")`
3. **Si el usuario no especifica el minuto exacto**:
   - Usa `media_search(query="Salvador Illa", video_id="TV01")` o `media_search_visual(query="Salvador Illa", video_id="TV01")` para ubicar el segundo donde interviene.
   - Seguidamente inspecciona ese instante con `media_describe_scene`.
4. **Respuesta final**: Explica de manera concisa y clara lo que muestra la imagen, la postura, la acción que realiza y los elementos contextuales detectados. Si es pertinente, adjunta el fotograma con `![Fotograma](/api/keyframes/{video_id}/{filename})`.

## Cómo entregar fotos o fotogramas

Cuando el usuario pida una foto ("Dame una foto", "enséñame el fotograma", "¿tienes una imagen?"):
- `media_describe_scene` ya devuelve directamente los campos listos para usar:
  - `markdown_image`: `![Descripción](/api/keyframes/{video_id}/{filename})`
  - `media_line`: `MEDIA:<ruta_absoluta>`
  - `keyframe_url`: `/api/keyframes/{video_id}/{filename}`
- Incluye el fotograma en tu respuesta usando `markdown_image` o el enlace `/api/keyframes/...` para que la interfaz web lo renderice visualmente de inmediato.
- **No intentes buscar el archivo en el disco** ni uses `search_files`: el MCP ya se encarga de resolver y proveer todas las rutas y URLs válidas.
