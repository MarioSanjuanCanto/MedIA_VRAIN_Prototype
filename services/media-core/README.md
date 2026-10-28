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
- `OPENROUTER_API_KEY`: resúmenes, selección de clips y análisis visual.
- `OPENROUTER_VISION_MODEL`: modelo de visión en OpenRouter; por defecto,
  `google/gemma-4-31b-it` (Apache 2.0). La herramienta de descripción también
  acepta otro modelo en cada petición.
- `HUGGINGFACE_ACCESS_TOKEN`: diarización cuando sea necesaria.

## Arrancar

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
