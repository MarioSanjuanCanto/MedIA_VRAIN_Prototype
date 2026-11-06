# Media API

La generación usa por defecto `MEDIA_GENERATION_BACKEND=hermes`: resúmenes,
selección de clips y descripción visual puntual se resuelven en Hermes desde
la evidencia de Media Core. Las transcripciones largas se paginan y sintetizan
por partes; no se recortan silenciosamente. Las sesiones de generación son
transitorias y no acumulan la fuente en el historial del chat. Los clips elegidos
se validan (intervalos y citas literales) antes de producir archivos. Una locución
de clip se basa en su intervalo, no en el resumen general del vídeo.

Los trabajos registran `generation_metrics` (llamadas y segundos de Hermes).
La descripción visual devuelve también `vision_metrics`: distingue visión nativa
de llamadas auxiliares o de ruta desconocida; no representa dólares ahorrados.
`MEDIA_GENERATION_BACKEND=core` recupera las rutas anteriores de los botones y
del endpoint visual, sin modificar las skills compartidas del chat. No hay
fallback silencioso a Gemma si Hermes falla.

Después de actualizar, reinicia Media Core y la API y abre una sesión nueva de
Hermes para descubrir las nuevas herramientas. Si el perfil tiene una copia de
`media-video-archive/SKILL.md`, sincronízala desde
`shared/skills/media-core/media-video-archive.md`, como explica el README raíz.
La visión sin un segundo modelo requiere que el modelo principal admita imágenes.

Con ambos servicios activos, desde este directorio se puede comparar un resumen
real con `python benchmark_generation.py --video-id TV04 --output benchmark.json`.
El informe conserva tiempos y ambos textos para revisar fidelidad. No estima
dólares a partir del número de llamadas y advierte que el Core puede usar caché.

Backend FastAPI del frontend. Gestiona vídeos y trabajos, conecta con Media
Core y Hermes, consulta el uso de proveedores y genera el informe PDF.

## Preparar

```bash
uv sync --python 3.11 --extra dev
cp .env.example .env
```

En `.env` configura Hermes y las claves necesarias para el panel de uso:
`OPENROUTER_API_KEY`, `FAL_ADMIN_KEY` y `REPLICATE_API_KEY`. En local
también puede reutilizar las claves de `services/media-core/.env`.

## Arrancar

```bash
uv run uvicorn app.main:app --host 127.0.0.1 --port 8010
```

- API: http://127.0.0.1:8010
- Documentación: http://127.0.0.1:8010/docs
- Estado: http://127.0.0.1:8010/api/health

Media Core debe estar activo en `http://127.0.0.1:8001/mcp`. Reinicia la API
cuando cambies endpoints o variables de entorno.

Al subir un vídeo, la API deja disponible primero la transcripción indexada.
Después una cola persistente prepara CLIP y EmbeddingGemma 2 en segundo plano,
con pausas entre lotes si hay trabajos interactivos. La biblioteca muestra el
progreso y permite reintentar una indexación fallida. `POST /api/search/multimodal`
acepta texto y una imagen, audio o clip de referencia opcional (máximo 50 MB y
30 segundos para audio o vídeo). Los archivos de referencia se borran al acabar
la consulta. `POST /api/chat/jobs/with-media` acepta el mismo archivo junto al
mensaje del chat y lo borra cuando termina o se cancela el turno.
Las imágenes se entregan visualmente a Hermes; una captura que coincide con
un fotograma de un vídeo corto y acotado se coteja localmente antes de recurrir
al índice semántico. Las consultas repetidas reutilizan el embedding en memoria.

El chat puede detener un turno activo con `POST /api/chat/jobs/{job_id}/cancel`.
En Hermes local se interrumpe el turno y se conserva su sesión. Si el proceso
debe reiniciarse, la siguiente petición reanuda la sesión guardada.

## Comprobar

```bash
uv run pytest -q
```

El código está en `app/`, las pruebas en `tests/` y los datos locales se
guardan bajo `data/`.
