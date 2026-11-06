# Media Core MCP

Servidor MCP que transcribe vídeos, busca en transcripciones, genera resúmenes y
clips y sintetiza voz. Expone `http://127.0.0.1:8001/mcp`.

## Preparar

```bash
uv sync --python 3.11 --extra dev
cp .env.example .env
uv run python -m media_core.services.piper_tts --download
uv run python -m media_core.indexing --rebuild
```

Configura en `.env` las claves que utilices:

- `REPLICATE_API_KEY`: WhisperX y modelos remotos.
- `OPENROUTER_API_KEY`: generación heredada, indexación visual temporal y rutas especializadas. La preparación de evidencia para Hermes no necesita esta clave.
- `OPENROUTER_VISION_MODEL`: modelo de visión en OpenRouter; por defecto,
  `google/gemma-4-31b-it` (Apache 2.0). La herramienta de descripción también
  acepta otro modelo en cada petición.
- `HUGGINGFACE_ACCESS_TOKEN`: diarización cuando sea necesaria.

## Arrancar

Hermes puede resumir y seleccionar sin una segunda llamada generativa: usa
`media_read_transcript` (paginado, con revisión y tiempos), selecciona clips y
pasa `candidates` a `media_clips`, que valida todas las citas e intervalos antes
de cortar. `media_scene_context` prepara fotogramas, OCR y habla sin llamar al
VLM; Hermes debe inspeccionarlos con `vision_analyze`. La ruta visual aún puede
usar un auxiliar remoto si el modelo principal no admite visión.

`media_summarize`, `media_describe_scene` y la selección auto/editorial sin
candidates se conservan para clientes anteriores y siguen usando OpenRouter.

```bash
uv run python server.py
```

Para conectarlo con Hermes local:

```bash
hermes mcp add media-core --url http://127.0.0.1:8001/mcp
hermes mcp test media-core
```

Desde Hermes en Docker utiliza
`http://host.docker.internal:8001/mcp`.

## Búsqueda multimodal

`media_index_multimodal(video_id="TV04", max_items=2)` añade puntos de imagen,
audio y vídeo con EmbeddingGemma 2. Cada punto queda guardado de inmediato;
`media_multimodal_index_status` muestra la cobertura y una llamada posterior
reanuda los puntos pendientes. `media_search_multimodal` combina los candidatos
con la transcripción, OCR y el índice visual CLIP. Sus tiempos son aproximados:
Hermes debe comprobar el tramo antes de afirmar un hecho.

El modelo se descarga la primera vez y ocupa aproximadamente 1,4 GB en disco.
Funciona en CPU con precisión bfloat16; `MULTIMODAL_CPU_THREADS` limita sus
hilos (2 por defecto). FFmpeg extrae audio mono a 16 kHz y hasta 12 fotogramas
por tramo, por lo que no se necesita TorchCodec para vídeo en Windows. El
índice y los manifiestos viven bajo `data/`.

## Cortar intervenciones completas

`media_speakers(video_id="TV04", person="Salvador Illa")` devuelve los turnos
numerados desde 1. `media_clips` puede cortar esos mismos turnos por índice,
sin repetir una búsqueda textual ni consultar el LLM:

```text
media_clips(video_id="TV04", person="Salvador Illa", turn_index=6)
media_clips(video_id="TV04", person="Salvador Illa", turn_index=1, max_clips=5)
media_clips(video_id="TV04", person="Salvador Illa", turn_indices=[2, 4, 6])
media_clips(video_id="TV04", start_seconds=1092.48, end_seconds=1126.68)
```

Cada turno genera su propio MP4. Los cortes por turno o intervalo explícito se
recodifican y se comprueba su duración; las marcas de la diarización siguen
siendo la referencia para decidir dónde empieza y termina la voz.

## Doblar un vídeo o un clip

`media_dub_clip` traduce el diálogo por segmentos y ajusta cada locución a los
timestamps originales. Para una petición como «sácame un clip de TV05 del
accidente de Mieres y dóblalo al inglés», el agente debe:

1. localizar y verificar el intervalo;
2. llamar a `media_clips` para generar un único MP4;
3. pasar `cut_video_file`, `video_id`, `start_seconds` y `end_seconds` a
   `media_dub_clip(target_language="en")`;
4. si devuelve `job_id`, consultar `media_job_status(job_id=..., wait_seconds=25)` hasta
   `completed`, `failed` o `cancelled`, sin repetir la generación activa;
5. al completar, entregar `result.dubbed_video_file`.

El vídeo conserva sus fotogramas y duración. Las locuciones se aceleran o
ralentizan dentro de cada intervención y el audio original se atenúa mientras
habla la voz traducida, conservando música, ambiente y efectos fuera de esas
ventanas. Esta modalidad es sincronización temporal de audio; no modifica los
labios. Piper permite doblaje local en `es`, `ca` y `en`. Para traducir se
necesita `OPENROUTER_API_KEY`; otros idiomas requieren un proveedor TTS
compatible, por ejemplo `voice_provider="qwen"`.

El ajuste de velocidad se materializa por frase en WAV antes de mezclar: evita que
algunas versiones de FFmpeg trunquen un grafo que combina `atempo` y `amix` al
silencio inicial. Luego se construye la pista traducida y se mezcla con el original.
Se decodifica y valida la duración real y el audio audible de cada intervención,
tanto en la pista traducida como en el MP4 final, antes de publicar el resultado.
`audio_verification` informa de duración PCM, nivel y segmentos verificados.
Una salida incompleta/silenciosa falla y una caché inválida se regenera. No hace
falta llamar a ASR para comprobar el audio y no se envía el resultado a otro servicio.

## Subtitular un vídeo o clip

`media_subtitle_video` devuelve un MP4 con subtítulos visibles incrustados. Si
no se indica `target_language`, usa el texto original de la transcripción sin
llamar al traductor. Cuando se solicita otro idioma, traduce cada intervención
manteniendo sus timestamps originales.

Para subtitular un clip temático, el agente llama primero a `media_clips` y
pasa `cut_video_file`, `video_id`, `start_seconds` y `end_seconds` a la nueva
herramienta. El render usa overlays PNG transparentes generados localmente con
Pillow, por lo que no depende del filtro `subtitles`/libass de FFmpeg.

Por defecto espera hasta 25 segundos en Core y devuelve `subtitled_video_file` si
termina antes. Si sigue activo, devuelve `job_id`: llamar directamente a
`media_job_status(job_id=..., wait_seconds=25)` y usar `result.subtitled_video_file`.
La espera se interrumpe al terminar, no añade una pausa fija; no usar terminal ni
`sleep`. `wait_seconds=0` conserva la entrega inmediata de `job_id`.
Para combinar doblaje y subtítulos, esperar primero
al doblaje y pasar su `dubbed_video_file` como `source`, junto con el ID y los
tiempos absolutos originales. Así los subtítulos conservan el audio traducido.
`background=False` permite esperar de forma síncrona en llamadas internas;
los clientes MCP deben conservar el modo de fondo predeterminado para evitar timeouts.

El render de subtítulos usa una franja PNG inferior en YUVA y superposición YUV420,
evitando convertir cada fotograma completo a RGBA. Conserva tipografía Unicode,
tamaño proporcional, audio y resolución. Prueba aceleración real una vez por proceso:
VideoToolbox en macOS; Quick Sync, NVENC o AMF en Windows/Linux. Si no hay soporte
o falla la exportación, utiliza x264 `veryfast` con hasta 8 hilos. Se puede fijar
`MEDIA_SUBTITLE_ENCODER=libx264` (o un nombre de hardware admitido) en el entorno.
La traducción, si se solicita, sigue dependiendo del proveedor configurado.

## Avatar de voz del chat

El chat puede dictar peticiones con Whisper CPU local y leer un extracto breve
con Piper. Prepara el reconocedor con:

```bash
uv run python -m media_core.services.local_voice --download
```

Después reinicia Core y API y pulsa **Activar voz** en el chat. El avatar SVG es
original y está disponible bajo MIT. Consulta
`docs/architecture/VOICE-AVATAR.md` para configuración y límites.

## Comprobar

```bash
uv run pytest -q
```

El código está en `media_core/`, las herramientas MCP en
`media_core/tools/`, los proveedores en `media_core/services/` y las
pruebas en `tests/`. Los vídeos, modelos, índices y resultados viven en
`data/`.
