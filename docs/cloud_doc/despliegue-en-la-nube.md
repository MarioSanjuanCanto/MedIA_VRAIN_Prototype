# Preparar MedIA para desplegarlo en la nube

## Propósito

Este documento enumera el trabajo que debe completarse **dentro del repositorio** antes de desplegar MedIA. No contiene el aprovisionamiento de la máquina virtual; ese procedimiento está en [puesta-en-marcha-en-vm.md](puesta-en-marcha-en-vm.md).

MedIA funciona actualmente como prototipo local. Se considerará preparado para cloud cuando un clon limpio pueda arrancar con Docker Compose, conservar sus datos tras un reinicio y superar las validaciones indicadas al final.

## Arquitectura objetivo

```text
Usuario autorizado
        |
  HTTPS o Tailscale
        |
   media-web :3000
        |
   media-api :8010
      /        \
media-core   Hermes Agent
  :8001         :8642
      \        /
   almacenamiento persistente
```

Solo `media-web` debe publicarse. Media API, Media Core y Hermes deben comunicarse por una red privada de Docker.

## 1. Crear los artefactos de despliegue

Añadir al repositorio:

```text
Dockerfile.web
Dockerfile.api
Dockerfile.core
compose.yaml
.env.cloud.example
.github/workflows/ci.yml
scripts/backup-cloud.sh
scripts/restore-cloud.sh
```

Los Dockerfiles deben:

- Fijar versiones de las imágenes base.
- Instalar solamente dependencias de producción.
- Incluir FFmpeg y FFprobe en API y Core.
- Ejecutar procesos con un usuario sin privilegios.
- No copiar `.env`, vídeos, cachés ni credenciales dentro de las imágenes.
- Incorporar o descargar de forma controlada los modelos necesarios.

Los scripts de backup y restauración deben operar sobre los volúmenes declarados por Compose, cifrar o proteger las credenciales y fallar sin modificar datos cuando la entrada no sea válida.

### Piper y modelos locales

`Dockerfile.core` debe instalar el paquete bloqueado `piper-tts==1.8.0` junto con las dependencias de Media Core. Las voces no deben depender de la capa efímera del contenedor:

- Descargar los archivos `.onnx`, `.onnx.json` y `MODEL_CARD` mediante `python -m media_core.services.piper_tts --download`.
- Guardarlos en el volumen persistente montado en `/app/data/models/tts`.
- Conservar `manifest.json` junto a las voces.
- Descargar también el modelo de embeddings al volumen `/app/data/models/embeddings`.

La descarga es idempotente: el código omite los archivos que ya existen. La primera inicialización necesita acceso a Hugging Face; los siguientes arranques deben reutilizar el volumen.

## 2. Definir el stack de Docker Compose

| Servicio | Comando | Puerto interno | Acceso desde host |
|---|---|---:|---|
| `media-web` | `npm run start` | 3000 | `127.0.0.1:3000` |
| `media-api` | `uvicorn app.main:app` | 8010 | No publicar |
| `media-core` | `python server.py` | 8001 | No publicar |
| `hermes-agent` | Gateway de Hermes | 8642 | No publicar |
| `media-init` | Descarga Piper y prepara el índice | Sin puerto | No publicar |

El Compose debe incluir:

- Red interna entre los cuatro servicios.
- Health checks.
- Orden de arranque basado en disponibilidad, no solo en creación del contenedor.
- Política de reinicio.
- Límites de CPU y memoria.
- Volúmenes persistentes.
- Variables recibidas desde `.env` o un gestor de secretos.
- Tags de imagen concretos; evitar `latest`.

`media-init` debe reutilizar la imagen de Media Core y montar sus mismos volúmenes. Media Core debe arrancar solo cuando esta tarea termine correctamente, usando una dependencia con condición `service_completed_successfully`. Así, `docker compose up` prepara automáticamente una instalación nueva y reutiliza los modelos existentes en las siguientes ejecuciones.

## 3. Adaptar la configuración de red

Realizar estos cambios en la aplicación:

1. Permitir configurar los hosts aceptados por Media API.
2. Permitir configurar CORS sin listas codificadas para localhost.
3. Configurar Media Core para aceptar el hostname interno `media-core`.
4. Hacer que los procesos escuchen en `0.0.0.0` dentro de los contenedores.
5. Usar nombres de servicio en las conexiones internas:

   ```dotenv
   MEDIA_API_URL=http://media-api:8010
   MEDIA_MCP_URL=http://media-core:8001/mcp
   HERMES_API_URL=http://hermes-agent:8642
   ```

CORS y `TrustedHostMiddleware` reducen ciertos ataques, pero no autentican usuarios.

## 4. Añadir seguridad

Para una demo privada de dos colaboradores puede utilizarse Tailscale como barrera de red. Antes de ofrecer el servicio públicamente se necesita:

- Login individual mediante OIDC o SSO.
- Autorización aplicada en Media API, no solo en el frontend.
- Protección de vídeos, trabajos, conversaciones e informes de gasto.
- Rate limiting.
- Límite de tamaño y duración de las subidas.
- Registro de acciones sensibles.
- Política de conservación y borrado de datos.
- Flujo de aprobación para acciones peligrosas de Hermes.

No publicar directamente los puertos `8001`, `8010` o `8642`.

## 5. Preparar la persistencia

Declarar volúmenes para:

| Ruta | Contenido |
|---|---|
| `data/test-vectors/videos` | Vídeos subidos |
| `data/cache` | Transcripciones, OCR y fotogramas |
| `data/indexes` | ChromaDB |
| `data/models` | Embeddings y voces Piper |
| `data/outputs` | Clips y audios |
| `data/state` | SQLite, historial y consumo |
| Estado de Hermes | Configuración y sesiones del agente |

Los contenedores deben poder eliminarse y recrearse sin perder estos datos. Hay que proporcionar scripts o comandos documentados de backup y restauración.

Mientras Media API use SQLite y tareas `asyncio` dentro del proceso, mantener **una sola réplica**. Un reinicio puede interrumpir trabajos activos.

## 6. Completar la configuración cloud

Crear `.env.cloud.example` sin secretos y documentar, como mínimo:

```dotenv
# Servicios internos
MEDIA_API_URL=http://media-api:8010
MEDIA_MCP_URL=http://media-core:8001/mcp
HERMES_API_URL=http://hermes-agent:8642
HERMES_MODE=docker

# Rutas persistentes
MEDIA_SHARED_DIR=/app/shared
MEDIA_DATA_DIR=/app/data
MEDIA_WEB_DATA=/app/data/state

# Bind interno
MEDIA_HOST=0.0.0.0
MEDIA_PORT=8001

# Proveedores y secretos
HERMES_API_KEY=
OPENROUTER_API_KEY=
REPLICATE_API_KEY=
HUGGINGFACE_ACCESS_TOKEN=
FAL_ADMIN_KEY=

# Operación
MEDIA_UPLOAD_MAX_MB=4096
CHROMA_AUTO_INDEX=false
```

Revisar también las variables usadas por el código que todavía no aparecen en los ejemplos: `MEDIA_REPO_ROOT`, `MEDIA_USAGE_FILE`, `OPENROUTER_VISION_MODEL`, `QWEN_TTS_MODEL`, `TTS_HTTP_URL` y `TTS_HTTP_MODEL`.

La aplicación debe fallar al arrancar con un mensaje claro cuando falte una variable obligatoria.

## 7. Health checks y observabilidad

Separar:

- **Liveness:** confirma que el proceso sigue vivo.
- **Readiness:** confirma que API, Core, Hermes y almacenamiento están disponibles.

Una dependencia caída debe provocar que readiness responda con un código no satisfactorio.

Añadir:

- Logs estructurados sin secretos.
- Identificador de petición y trabajo.
- Métricas de errores, latencia, cola, CPU, memoria y disco.
- Alertas por reinicios, falta de espacio y errores de proveedores.

## 8. Automatizar la validación

La CI debe ejecutar:

```bash
(cd services/media-api && uv sync --frozen --extra dev && uv run pytest -q)
(cd services/media-core && uv sync --frozen --extra dev && HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 uv run pytest -q)
(cd apps/media-web && npm ci && npm run typecheck && npm run build)
docker compose config --quiet
docker compose build
```

Añadir una prueba funcional que cubra:

1. Subir un vídeo.
2. Transcribirlo.
3. Buscar y generar un clip.
4. Consultar Hermes.
5. Reiniciar los contenedores.
6. Confirmar que los datos siguen disponibles.

## 9. Preparar actualizaciones y recuperación

Documentar:

- Versiones compatibles de servicios, imágenes y modelos.
- Backup previo a cada actualización.
- Migraciones de datos, si aparecen.
- Rollback a las imágenes anteriores.
- Restauración completa en una máquina vacía.

El proyecto no se considera recuperable hasta haber probado una restauración.

## 10. Concurrencia futura

Para varios usuarios independientes se necesitará:

- PostgreSQL para usuarios, trabajos e historial.
- Almacenamiento de objetos para vídeos y resultados.
- Cola persistente y workers.
- Credenciales, cuotas y datos separados por tenant.
- Autorización por tenant en cada petición.

Kubernetes solo tiene sentido después de estos cambios. No se deben crear varias réplicas que escriban simultáneamente sobre el mismo SQLite o Chroma local.

## Definición de proyecto preparado

- [ ] Dockerfiles de Web, API y Core.
- [ ] `compose.yaml` probado desde un clon limpio.
- [ ] `.env.cloud.example` completo.
- [ ] Configuración de red parametrizada.
- [ ] Autenticación y autorización adecuadas al tipo de acceso.
- [ ] Volúmenes persistentes.
- [ ] Backup y restauración probados.
- [ ] Health checks de vida y disponibilidad.
- [ ] CI verde.
- [ ] Imágenes y modelos versionados.
- [ ] Prueba funcional tras reinicio.
- [ ] Licencia del proyecto definida.

Cuando se complete esta lista, continuar con [Puesta en marcha de MedIA en una VM](puesta-en-marcha-en-vm.md).

## Referencias del repositorio

- [README principal](../../README.md)
- [Arquitectura](../architecture/README.md)
- [Media API](../../services/media-api/README.md)
- [Media Core](../../services/media-core/README.md)
- [Media Web](../../apps/media-web/README.md)
