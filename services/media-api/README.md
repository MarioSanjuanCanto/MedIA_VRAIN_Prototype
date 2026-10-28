# Media API

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

El chat puede detener un turno activo con `POST /api/chat/jobs/{job_id}/cancel`.
En Hermes local se cierra el proceso bloqueado; la siguiente petición recupera
el historial de la conversación y crea una sesión nueva.

## Comprobar

```bash
uv run pytest -q
```

El código está en `app/`, las pruebas en `tests/` y los datos locales se
guardan bajo `data/`.
