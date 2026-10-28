# MedIA: primer prototipo local

El código vive en este mismo repositorio:

- `apps/media-web`: Next.js + React. Biblioteca, vídeo, conversación, resumen, clips, audio e historial.
- `services/media-api`: FastAPI. Sirve medios locales con HTTP Range, valida subidas, conserva trabajos en SQLite y conecta con Hermes y media-core.
- `services/media-core`: sigue siendo el único propietario del procesamiento y de Chroma.

## Arranque en Windows

Requisitos: Node.js 22 o superior, Python 3.11 o superior y FFmpeg/FFprobe en PATH. Instala media-core siguiendo su README y configura sus claves y voces. Hermes debe tener configurado ese MCP y las skills disponibles en `shared/skills/media-core/`.

1. En una terminal, arranca el MCP existente: `cd services/media-core`, después `uv run python server.py`.
2. Desde la raíz del repositorio, ejecuta `./scripts/start-media-web.ps1 -Install` la primera vez. En los siguientes arranques: `./scripts/start-media-web.ps1`.
3. Abre http://127.0.0.1:3000. La API está en http://127.0.0.1:8010/docs.

La API tiene un entorno propio; no instala Piper en Hermes ni cambia el entorno del MCP. Ctrl+C detiene los procesos del lanzador. No arranques dos instancias en los mismos puertos.

En Linux/macOS arranca `python -m uvicorn app.main:app --host 127.0.0.1 --port 8010` desde `services/media-api` y `npm run dev` desde `apps/media-web`. La configuración de Hermes se describe en «Conexión con Hermes».

## Conexión con Hermes

Copia `services/media-api/.env.example` a `services/media-api/.env`. `HERMES_MODE` admite tres valores:

- `auto` (predeterminado): usa el Gateway Docker si responde, acepta la clave y, en caso contrario, prueba la instalación local.
- `docker`: exige el Gateway HTTP configurado y no hace fallback.
- `local`: usa exclusivamente el protocolo JSON-RPC del Hermes instalado en el equipo.

Para Docker, configura la misma clave que usa `API_SERVER_KEY` en el `docker-compose` de Hermes:

```dotenv
HERMES_MODE=auto
HERMES_API_URL=http://127.0.0.1:8642
HERMES_API_KEY=la-misma-clave-del-gateway
HERMES_DOCKER_WORKDIR=/workspace
```

La clave permanece en el backend y nunca se entrega al navegador. El proyecto del Mac debe estar montado en el contenedor en la ruta indicada por `HERMES_DOCKER_WORKDIR`. Si la propia API se ejecuta en Docker, usa como URL de red interna `http://hermes-agent:8642`.

Para Hermes local, configura `HERMES_AGENT_DIR` y `HERMES_PYTHON` si no está en la ruta predeterminada. En Windows se detecta automáticamente `%LOCALAPPDATA%/hermes/hermes-agent/venv/Scripts/python.exe`; en Linux/macOS, `~/hermes/hermes-agent/venv/bin/python`.

## Recorrido

Selecciona un vídeo en Biblioteca. Elige Resumen, Clip o Audio, selecciona el idioma y pulsa el botón de creación. Puedes escribir un tema en Clip; sin tema se pide selección editorial. Petición libre abre el campo para conversar con Hermes. El idioma elegido es el del resultado; la primera transcripción pide detección automática.

La opción «Petición libre» envía la petición, el vídeo seleccionado y el idioma al agente. En modo local, la API usa el protocolo JSON-RPC de Hermes (`tui_gateway.entry`, `session.create`, `prompt.submit`) y conserva una sesión por conversación. En Docker usa el Gateway HTTP compatible con OpenAI en el puerto 8642 y envía el historial de la conversación. `app/bridge.py` concentra ambos transportes. No se modifica la instalación de Hermes.

Si Hermes solicita aprobaciones o secretos, la petición web se interrumpe e indica que continúes en Desktop; este prototipo no concede permisos automáticamente. Las instrucciones restringen el recorrido a media-core, pero no constituyen un sandbox de herramientas del agente.

La locución utiliza Piper a través de `media_speak`. Los fragmentos aparecen conforme están listos; pulsa reproducir para escucharlos (sin reproducción automática). El avatar y la sincronización labial quedan para la siguiente fase. Esta versión ya expone URLs reproducibles que podrá consumir el avatar.

## Datos y límites

- Los vídeos viven en `data/test-vectors/videos`. Una subida recibe un sufijo único; nunca sobrescribe otro vídeo ni reutiliza su ID.
- Una transcripción se considera válida solo si coincide el ID y la huella del archivo. «Por verificar» indica un JSON antiguo o una fuente modificada.
- `data/state/` guarda historial SQLite, consumo y fotogramas; está excluida de Git. Solo se sirven medios desde las carpetas permitidas, nunca rutas arbitrarias.
- Hasta cuatro peticiones admitidas, dos ejecutándose; Hermes procesa una conversación cada vez. Una única instancia/worker de la API. No es un servicio multiusuario ni debe exponerse a Internet.
- Los trabajos se consultan cada 1,8 s. Tras reiniciar la API, las peticiones pendientes se marcan interrumpidas; resultados terminados se conservan. Una inferencia que ya empezó en el MCP puede continuar allí.
- El navegador no recibe claves. Transcripción, resumen y conversación usan los proveedores configurados en media-core/Hermes: «local» describe la interfaz y el almacenamiento, no garantiza inferencia offline.
- Se conserva el resumen si falla el audio; se muestra el error y se permite reintentar. No se sustituye silenciosamente un resumen por búsquedas parciales.

## Comprobaciones

Desde `services/media-api`: `uv run --extra dev pytest -q` (o `.venv/Scripts/python.exe -m pytest -q` en el entorno Windows ya instalado).
Desde apps/media-web: `npm run typecheck` y `npm run build`.

Ensayo manual: biblioteca → TV01 → resumen en español → audio; TV05 → clip de un tema conocido; subir vídeo nuevo → transcribir → comprobar texto y fuente; petición a Hermes → «Ahora en catalán y más breve, con audio»; refrescar la página y recuperar resultados. Estas acciones pueden consumir los proveedores externos ya configurados.

## Validación realizada (11 septiembre 2026)

Compilación Next y TypeScript correctos; nueve pruebas de API pasan. Verificados transporte Hermes y un turno sintético completo, lectura local de TV04 con HTTP Range, subida de vídeo sintético sin sobrescritura, historial, adaptación a móvil y cierre de diálogos con Escape. Piper catalán produjo 6,26 s de audio en 5,08 s de tiempo total en una prueba con el modelo ya cargado.

Se observó un resumen de TV04 completado en el historial. La selección editorial sin tema devolvió un error de media-core; no se ha corregido el selector del MCP en esta entrega. La prueba adicional de extracción con TV04 fue bloqueada por revisión automática por posible envío a OpenRouter. No se considera validado el recorrido completo de clips con datos reales.

La base Chroma vive en `data/indexes/chroma` y no se versiona; puede reconstruirse desde las transcripciones compartidas.


## Rediseño del estudio (14 septiembre 2026)

El archivo permanece accesible al abrir un vídeo. La transcripción se muestra junto al reproductor, y el panel de resultados permite recuperar todas las piezas del vídeo, incluidas otras conversaciones. Los trabajos pendientes se actualizan al cambiar de pantalla. La dirección visual y las decisiones están en `docs/architecture/MEDIA-WEB-DESIGN.md`.

Validación del rediseño: TypeScript y build de producción correctos. Revisión visual en escritorio y a 390 px con TV04, recuperación de un resumen guardado, apertura/cierre de transcripción y selección del formulario de clips. Se comprobó también el estado de error con la API detenida. No se ejecutaron nuevas inferencias ni se validó la generación completa de clips/audio. media-core estaba desconectado.
