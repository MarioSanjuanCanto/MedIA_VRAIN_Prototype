# MedIA

Prototipo audiovisual con biblioteca de vídeos, chat con Hermes Agent y herramientas
MCP para transcripción, búsqueda, OCR, análisis visual, resúmenes, clips y voz.

Este README explica **cómo preparar un clon desde cero y comprobar que funciona**.
El recorrido principal ejecuta los servicios en el mismo ordenador, utiliza
OpenRouter para los modelos generativos y Replicate para transcribir vídeos nuevos.
No requiere una GPU local.

## Índice

1. [Arquitectura y servicios](#1-arquitectura-y-servicios)
2. [Requisitos e instalación del sistema](#2-requisitos-e-instalación-del-sistema)
3. [Clonar e instalar dependencias](#3-clonar-e-instalar-dependencias)
4. [Cuentas, claves y configuración](#4-cuentas-claves-y-configuración)
5. [Modelos utilizados y preparación](#5-modelos-utilizados-y-preparación)
6. [Instalar y configurar Hermes](#6-instalar-y-configurar-hermes)
7. [Arrancar Core y conectar el MCP](#7-arrancar-core-y-conectar-el-mcp)
8. [Arrancar API y frontend](#8-arrancar-api-y-frontend)
9. [Añadir vídeos y probar la aplicación](#9-añadir-vídeos-y-probar-la-aplicación)
10. [Avatar, micrófono y Piper](#10-avatar-micrófono-y-piper)
11. [Uso diario y actualizaciones](#11-uso-diario-y-actualizaciones)
12. [Resolución de problemas](#12-resolución-de-problemas)
13. [Pruebas de desarrollo](#13-pruebas-de-desarrollo)
14. [Estructura, datos y colaboración](#14-estructura-datos-y-colaboración)
15. [Conexiones alternativas y nube](#15-conexiones-alternativas-y-nube)

> **Convenciones:** salvo indicación expresa, parte de la raíz del clon, donde
> está este README. Sustituye las rutas de ejemplo por las tuyas. Los bloques
> que cambian de carpeta incluyen la vuelta a la raíz. `powershell` corresponde
> a Windows y `bash` a macOS/Linux; los bloques indicados como comunes sirven
> en ambos. No ejecutes a la vez el arranque manual y el lanzador.

## 1. Arquitectura y servicios

```text
Navegador: Media Web (Next.js, 3000)
    |
    v
Media API (FastAPI, 8010)
    |                              |
    | MCP                          | puente de conversación
    v                              v
Media Core (8001/mcp) <----------- Hermes Agent
    |                                |
    |                                +--> OpenRouter: modelo del agente
    +--> OpenRouter: texto y visión
    +--> Replicate: WhisperX / Qwen TTS opcional
    +--> Local: Piper, Whisper de micrófono, OCR, embeddings y FFmpeg
```

- **Media Web:** biblioteca, chat, archivos generados, avatar y panel de uso.
- **Media API:** subidas, trabajos, historial y conexión con Core/Hermes.
- **Media Core:** servidor MCP y ejecución multimedia; mantiene los índices.
  Utiliza transporte **Streamable HTTP**.
- **Hermes:** interpreta la petición y utiliza las herramientas MCP. Su modelo
  y sus credenciales se configuran en su propio perfil.

En el modo local de esta guía, **Media API inicia Hermes cuando lo necesita**.
No hay que dejar abierto `hermes chat`, ejecutar `hermes gateway` ni instalar
el escritorio de Hermes para usar el chat de MedIA.

| Componente | Dirección | Uso |
| --- | --- | --- |
| Media Web | <http://127.0.0.1:3000> | Interfaz principal |
| Media API | <http://127.0.0.1:8010> | Backend de la web |
| Documentación API | <http://127.0.0.1:8010/docs> | Consultar/probar endpoints |
| Estado conjunto | <http://127.0.0.1:8010/api/health> | Diagnóstico |
| Media Core MCP | <http://127.0.0.1:8001/mcp> | Herramientas multimedia |
| Gateway HTTP Hermes | `http://127.0.0.1:8642` | Solo transporte alternativo |
| WebUI Hermes | `http://127.0.0.1:8787` | Opcional; distinta de Media Web |

El MCP Hello World de `examples/` es independiente y **no se necesita** para
esta instalación.

## 2. Requisitos e instalación del sistema

### 2.1. Requisitos

- Windows con PowerShell, macOS o Linux. Si eliges WSL2, instala y ejecuta todo
  dentro de WSL2 siguiendo los pasos Linux.
- **Python 3.11**, instalado con uv en los pasos siguientes.
- **Node.js 24 LTS recomendado**, o una versión reciente de Node 22.
- Git, uv, npm, FFmpeg y FFprobe disponibles en la terminal.
- Internet para paquetes, pesos y llamadas a proveedores.
- Orientación de desarrollo: **16 GB de RAM**, preferiblemente 32 GB si se
  indexan vídeos y se mantienen varios servicios. Reserva inicialmente
  **15–25 GB libres**, más el espacio de tus vídeos. El consumo real depende
  de modelos, cachés, entornos y material procesado.

La ejecución remota tiene su propia facturación. Los modelos descargados
localmente no sustituyen las llamadas generativas a OpenRouter o Replicate.

### 2.2. Windows: PowerShell

Con WinGet, o mediante los instaladores oficiales si tu equipo no lo tiene:

```powershell
winget install --id Git.Git -e
winget install --id OpenJS.NodeJS.LTS -e
winget install --id Gyan.FFmpeg -e
winget install --id astral-sh.uv -e
```

Abre una PowerShell nueva para actualizar `PATH` y comprueba:

```powershell
git --version
node --version
npm.cmd --version
uv --version
ffmpeg -version
ffprobe -version
uv python install 3.11
```

Si se bloquea `npm.ps1`, utiliza **`npm.cmd`**. No hace falta activar manualmente
los entornos Python: la guía utiliza `uv run` o su ejecutable específico.

### 2.3. macOS

Instala [Homebrew](https://brew.sh/) si no lo tienes. Después:

```bash
brew install git ffmpeg uv node@24
export PATH="$(brew --prefix node@24)/bin:$PATH"
uv python install 3.11

git --version
node --version
npm --version
uv --version
ffmpeg -version
ffprobe -version
```

Conserva el ajuste de `PATH` de Node en la configuración de tu shell si lo
necesitas en próximas terminales. Si ya tienes Node 22 reciente o 24, puedes
utilizar esa instalación.

### 2.4. Linux: Ubuntu/Debian

```bash
sudo apt-get update
sudo apt-get install -y git curl ca-certificates ffmpeg build-essential
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Abre una terminal nueva o carga la ruta indicada por el instalador de uv.
Instala Node 24 desde [Node.js](https://nodejs.org/en/download) o mediante el
repositorio de [NodeSource](https://github.com/nodesource/distributions):

```bash
curl -fsSL https://deb.nodesource.com/setup_24.x -o /tmp/media-nodesource-setup.sh
sudo bash /tmp/media-nodesource-setup.sh
sudo apt-get install -y nodejs
uv python install 3.11

git --version
node --version
npm --version
uv --version
ffmpeg -version
ffprobe -version
```

No utilices el `nodejs` antiguo de una distribución sin comprobar su versión.
Referencia de uv: [instalación oficial](https://docs.astral.sh/uv/getting-started/installation/).

## 3. Clonar e instalar dependencias

### 3.1. Clonar

Reemplaza el marcador por la URL facilitada por el equipo:

```bash
git clone <URL_DEL_REPOSITORIO> MedIA
cd MedIA
git status
```

En Windows se recomienda una ruta corta como `C:\MedIA` para evitar problemas
de longitud de rutas. El repositorio contiene Hermes en `docker/hermes-agent/`:
**el nombre de esa carpeta no obliga a usar Docker**. Instalaremos esa copia
como proceso local para conservar la versión incluida en el clon.

### 3.2. Instalar Core, API y frontend

Comandos comunes a PowerShell y bash, desde la raíz:

```bash
cd services/media-core
uv sync --python 3.11 --locked --extra dev
cd ../..

cd services/media-api
uv sync --python 3.11 --locked --extra dev
cd ../..

cd apps/media-web
npm ci
cd ../..
```

En Windows puedes sustituir `npm ci` por `npm.cmd ci`.

Cada servicio Python tiene su `.venv` y `uv.lock`; frontend utiliza
`package-lock.json`. `--locked` verifica que manifiesto y lock coinciden.
**No ejecutes `uv add`, `uv lock --upgrade` ni `npm update` para instalar un
clon**: modificarían las dependencias compartidas.

Se instalan las librerías de Piper, faster-whisper, ChromaDB, Sentence
Transformers, RapidOCR/ONNX y Replicate, entre otras. Los **pesos** se preparan
en el apartado 5. Hermes tiene un entorno independiente, descrito en el 6.

## 4. Cuentas, claves y configuración

### 4.1. Credenciales por función

| Credencial | Obtener en | Función |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | [OpenRouter](https://openrouter.ai/keys) | Modelo de Hermes y generación de texto/visión del Core |
| `REPLICATE_API_KEY` | [Replicate](https://replicate.com/account/api-tokens) | WhisperX para vídeos nuevos y Qwen TTS opcional |
| `HUGGINGFACE_ACCESS_TOKEN` o `HF_TOKEN` | [Hugging Face](https://huggingface.co/settings/tokens) | Acceso a pesos protegidos de diarización en WhisperX |
| `FAL_ADMIN_KEY` | Cuenta FAL del equipo, si se utiliza | Consultar gasto FAL; opcional para el flujo básico |

Para probar el recorrido completo con vídeos nuevos, prepara OpenRouter y
Replicate con acceso y saldo. El token HF se utiliza cuando se solicita
diarización; no sustituye a la clave de Replicate.

Para diarización, acepta con **la misma cuenta del token** las condiciones de
[pyannote/speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1)
y las de otros modelos protegidos que solicite la versión de WhisperX
desplegada. Un token sin permisos puede devolver 403.

Sin credenciales remotas se pueden probar algunas operaciones sobre material
preparado, como OCR, búsqueda local o corte por tiempos. No estará disponible
todo el flujo de transcripción, razonamiento y resumen de vídeos nuevos.

### 4.2. Crear la configuración local

Haz estas copias **solo en un clon nuevo**; no sobrescribas tus claves al actualizar.

**Windows:**

```powershell
Copy-Item services/media-core/.env.example services/media-core/.env
Copy-Item services/media-api/.env.example services/media-api/.env
Copy-Item apps/media-web/.env.example apps/media-web/.env.local
```

**macOS/Linux:**

```bash
cp services/media-core/.env.example services/media-core/.env
cp services/media-api/.env.example services/media-api/.env
cp apps/media-web/.env.example apps/media-web/.env.local
```

### 4.3. Core: `services/media-core/.env`

Completa sus valores en un editor. Configuración básica de referencia:

```dotenv
OPENROUTER_API_KEY=<TU_CLAVE>
OPENROUTER_MODEL=google/gemma-4-31b-it
OPENROUTER_VISION_MODEL=google/gemma-4-31b-it
REPLICATE_API_KEY=<TU_CLAVE>
REPLICATE_MODEL=victor-upmeet/whisperx:655845d6190ef70573c669245f245892cd039df4b880a1e3a65852c09252f5cc
HUGGINGFACE_ACCESS_TOKEN=<TU_TOKEN_SI_USAS_DIARIZACION>

EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
CHROMA_COLLECTION=media_transcripts_v1
CHROMA_AUTO_INDEX=false
TTS_PROVIDER=piper
PIPER_VOICE_ES=es_ES-carlfm-x_low
PIPER_THREADS=2
VOICE_WHISPER_MODEL=base
VOICE_WHISPER_THREADS=2
MEDIA_HOST=127.0.0.1
MEDIA_PORT=8001
LLM_TIMEOUT_SECONDS=90
LLM_CACHE_TTL_SECONDS=86400
```

Reemplaza los marcadores por claves reales o deja vacías las opcionales; textos
como `<TU_CLAVE>` no son valores válidos. Deja `HF_TOKEN` vacío si ya utilizas
`HUGGINGFACE_ACCESS_TOKEN`.

Core también lee un `.env` raíz como alternativa heredada. Para un clon nuevo
utiliza el del servicio y evita valores contradictorios. Las variables
exportadas en la terminal pueden influir en la configuración. Reinicia Core
cuando cambies modelos, claves o rutas.

### 4.4. API: `services/media-api/.env`

Configura rutas **absolutas** al clon y su entorno de Hermes. En Windows, por
ejemplo si has clonado en `C:\MedIA`:

```dotenv
HERMES_MODE=local
HERMES_AGENT_DIR=C:/MedIA/docker/hermes-agent
HERMES_PYTHON=C:/MedIA/docker/hermes-agent/.venv/Scripts/python.exe
HERMES_HOME=C:/MedIA/data/state/hermes
HERMES_TUI_TOOLSETS=skills,media-core
MEDIA_MCP_URL=http://127.0.0.1:8001/mcp
```

En macOS/Linux, por ejemplo para `/home/usuario/MedIA`:

```dotenv
HERMES_MODE=local
HERMES_AGENT_DIR=/home/usuario/MedIA/docker/hermes-agent
HERMES_PYTHON=/home/usuario/MedIA/docker/hermes-agent/.venv/bin/python
HERMES_HOME=/home/usuario/MedIA/data/state/hermes
HERMES_TUI_TOOLSETS=skills,media-core
MEDIA_MCP_URL=http://127.0.0.1:8001/mcp
```

El apartado 6 prepara ese `HERMES_HOME`. Debe ser **el mismo perfil** en la
terminal de configuración y en la API. En `.env` y YAML de Windows puedes usar
barras `/` para evitar escapes ambiguos.

En modo `local` no se necesitan `HERMES_API_URL`, `HERMES_API_KEY` ni
`HERMES_DOCKER_WORKDIR`. El valor `auto` de la plantilla intenta HTTP y después
local; aquí se selecciona `local` para que el arranque sea predecible.

Para su panel de gasto, API local puede reutilizar las claves de
`services/media-core/.env`. Añade `FAL_ADMIN_KEY` solo si necesitas esa consulta.
El frontend no necesita claves de proveedores.

### 4.5. Frontend: `apps/media-web/.env.local`

```dotenv
MEDIA_API_URL=http://127.0.0.1:8010
```

Es opcional si usas el puerto predeterminado. Next.js reenvía `/api/*` a esa
dirección; reinícialo si la cambias. Deja `NEXT_PUBLIC_MEDIA_UPLOAD_URL` sin
definir para utilizar la ruta de subida por streaming incluida.

## 5. Modelos utilizados y preparación

### 5.1. Inventario del stack actual

| Función | Modelo/motor | Dónde se ejecuta |
| --- | --- | --- |
| Agente Hermes | Modelo seleccionado en su perfil OpenRouter | Remoto |
| Resúmenes y selección editorial | `google/gemma-4-31b-it` | OpenRouter; `OPENROUTER_MODEL` |
| Interpretación visual | `google/gemma-4-31b-it` | OpenRouter; `OPENROUTER_VISION_MODEL` |
| Transcripción de vídeos | WhisperX con faster-whisper large-v3 en el servicio publicado | Replicate; versión fijada en `REPLICATE_MODEL` |
| Diarización | pyannote a través de WhisperX | Remota; permisos HF cuando se solicita |
| Búsqueda semántica textual | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | Local + ChromaDB |
| Búsqueda visual | `sentence-transformers/clip-ViT-B-32` | Local + ChromaDB |
| OCR | RapidOCR ONNX, PP-OCRv4 detección/reconocimiento y clasificador mobile v2 | Local; pesos incluidos en el paquete |
| TTS castellano | Piper `es_ES-carlfm-x_low` | Local |
| TTS catalán | Piper `ca_ES-upc_ona-medium` | Local |
| TTS inglés | Piper `en_US-ljspeech-medium` | Local |
| Dictado del micrófono | Whisper multilingüe `base`, CPU INT8 | Local mediante faster-whisper |
| Detección facial auxiliar | YuNet `face_detection_yunet_2023mar.onnx` | Local |
| Cotejo facial auxiliar | SFace `face_recognition_sface_2021dec.onnx` | Local |
| TTS alternativo | Qwen3-TTS | Replicate; opcional |
| Extracción, conversión y cortes | FFmpeg/FFprobe | Binarios del sistema |

Los modelos generativos remotos **no se descargan al ordenador**. El modelo del
agente es independiente del modelo del Core: cambiar `OPENROUTER_MODEL` no
cambia el perfil de Hermes.

El perfil de referencia observado al revisar esta guía utiliza
`deepseek/deepseek-v4.1-flash-1017` en OpenRouter para Hermes. **El repositorio
no impone ese modelo ni incluye el perfil personal del desarrollador**. El
apartado 6 explica cómo seleccionar uno con soporte de herramientas.

Algunas rutas especializadas de identificación también llaman directamente a
Gemma en el código. Cambiar una variable de modelo no reemplaza todas esas
llamadas. `docs/model-catalog/` incluye evaluaciones y propuestas; no hay que
instalar todos los modelos del catálogo.

### 5.2. Piper: descargar las tres voces

Desde la raíz, comandos comunes:

```bash
cd services/media-core
uv run --locked --extra dev python -m media_core.services.piper_tts --download
cd ../..
```

Se guardan en `data/models/tts/`: cada `.onnx`, su `.onnx.json`, su ficha
`*.MODEL_CARD` y un `manifest.json` con origen y SHA-256. Comprueba que existen
las tres voces y sus configuraciones. Revisa las condiciones de cada voz en
su ficha. El motor `piper-tts` ya se instaló con las dependencias; no hace falta
instalar otro Piper en Hermes.

### 5.3. Whisper local del micrófono

```bash
cd services/media-core
uv run --locked --extra dev python -m media_core.services.local_voice --download
cd ../..
```

Con `VOICE_WHISPER_MODEL=base`, descarga `Systran/faster-whisper-base` en
`data/models/voice-whisper/base/`. Debe existir `model.bin` y sus configuraciones.
Es opcional si no vas a utilizar el micrófono.

Para probar otro tamaño, cambia la variable antes de descargarlo y reinicia Core.
Un tamaño mayor consume más recursos y no garantiza menor latencia. Esta
descarga **no reemplaza WhisperX para transcribir vídeos**.

### 5.4. MiniLM e índice textual

```bash
cd services/media-core
uv run --locked --extra dev python -m media_core.indexing
cd ../..
```

Sin `--rebuild`, sincroniza las transcripciones JSON disponibles. MiniLM se
guarda en `data/models/embeddings/` y Chroma en `data/indexes/chroma/`. Si no hay
transcripciones y quieres descargar/cargar el modelo de antemano:

```bash
cd services/media-core
uv run --locked --extra dev python -c "from media_core.services.embeddings import MultilingualEmbedder; MultilingualEmbedder().warm_up(); print('MiniLM preparado')"
cd ../..
```

Para regenerar la colección textual, **detén primero Core** y ejecuta:

```bash
cd services/media-core
uv run --locked --extra dev python -m media_core.indexing --rebuild
cd ../..
```

`--rebuild` elimina y vuelve a construir la colección textual; no es un paso
de cada arranque ni genera el índice visual. Si cambias el modelo de embeddings,
reconstruye o utiliza una colección nueva compatible; no mezcles sus vectores.

### 5.5. Preparar CLIP y OCR

Para adelantar la primera carga:

```bash
cd services/media-core
uv run --locked --extra dev python -c "from media_core.services.visual_embeddings import get_visual_embedding_model; get_visual_embedding_model(); print('CLIP preparado')"
uv run --locked --extra dev python -c "from media_core.services.video_ocr import get_ocr_engine; get_ocr_engine(); print('OCR preparado')"
cd ../..
```

CLIP usa por defecto la caché Hugging Face/Sentence Transformers del usuario,
normalmente bajo `~/.cache/huggingface/`. **No todos los pesos están en
`data/models/`**. Se puede controlar la caché con `HF_HOME` en el entorno de Core.
RapidOCR incluye sus ONNX en el paquete instalado.

Cargar CLIP no indexa todos los vídeos. `media_index_visual` o la indexación de
la web crean los embeddings de fotogramas de cada vídeo en la colección
`media_visual_keyframes_v1`.

### 5.6. Modelos faciales, opcionales

YuNet y SFace se descargan en la primera operación que los necesita. Para
prepararlos expresamente:

```bash
cd services/media-core
uv run --locked --extra dev python -c "from media_core.services.face_detector import ensure_yunet_model; from media_core.services.face_matching import ensure_sface_model; paths=[ensure_yunet_model(), ensure_sface_model()]; assert all(paths), 'No se pudieron descargar los modelos'; print(*paths, sep='\n')"
cd ../..
```

Se guardan en `data/models/faces/`. Detectar o cotejar un rostro no garantiza
identificar a cualquier persona. Las rutas de investigación web también
necesitan conectividad, fuentes y, según la operación, OpenRouter. No se necesita
configurar Google Lens para el recorrido básico.

### 5.7. Qwen TTS remoto, opcional

`TTS_PROVIDER=piper` mantiene la voz local. El proveedor alternativo usa por
defecto el identificador fijado en `media_core/config.py`:

```text
qwen/qwen3-tts:d490a561cf1171a8dc3d96d1e57efffea7dd34607148bb641f3d9de4e38c472e
```

Puede cambiarse con `QWEN_TTS_MODEL`. Requiere Replicate y genera consumo remoto.
La integración actual no admite catalán con ese proveedor: usa Piper para
catalán. No hay que activarlo para el avatar ni descargar sus pesos.

## 6. Instalar y configurar Hermes

### 6.1. Instalar la copia incluida

Desde la raíz, con un entorno independiente de Core/API:

```bash
cd docker/hermes-agent
uv sync --python 3.11 --locked --extra mcp
cd ../..
```

La copia incluida declara Hermes `0.21.0`; el commit del monorepo determina el
código exacto. El extra `mcp` instala su cliente MCP. No se necesitan todos los
extras de mensajería, navegador, escritorio o voz propios de Hermes.

### 6.2. Seleccionar un perfil aislado

El perfil contiene configuración, claves, memoria, sesiones y logs. Usaremos
`data/state/hermes/`, ignorado por Git. Los ejemplos definen un comando de
sesión para llamar a la copia del clon, evitando utilizar otro Hermes global.

**Windows, desde la raíz:**

```powershell
$mediaRepo = (Get-Location).Path
$env:HERMES_HOME = Join-Path $mediaRepo 'data/state/hermes'
$env:MEDIA_HERMES_CLI = Join-Path $mediaRepo 'docker/hermes-agent/.venv/Scripts/hermes.exe'
New-Item -ItemType Directory -Force -Path $env:HERMES_HOME | Out-Null
function hermes { & $env:MEDIA_HERMES_CLI @args }
hermes --help
```

**macOS/Linux, desde la raíz:**

```bash
export MEDIA_REPO="$(pwd)"
export HERMES_HOME="$MEDIA_REPO/data/state/hermes"
export MEDIA_HERMES_CLI="$MEDIA_REPO/docker/hermes-agent/.venv/bin/hermes"
mkdir -p "$HERMES_HOME"
hermes() { "$MEDIA_HERMES_CLI" "$@"; }
hermes --help
```

Repite el bloque en una terminal nueva si necesitas la CLI: la función y las
variables solo duran en esa sesión. No confundas **perfil `HERMES_HOME`** con
**código `HERMES_AGENT_DIR`**. El valor de `HERMES_HOME` debe coincidir con el
configurado en `services/media-api/.env`.

### 6.3. Configurar OpenRouter y el modelo del agente

En la misma terminal:

```bash
hermes setup
hermes model
```

En el asistente:

1. Selecciona **OpenRouter**.
2. Introduce tu clave; se guardará en el perfil de Hermes. La clave del Core
   no configura automáticamente un perfil de Hermes nuevo.
3. Elige el modelo acordado por el equipo con soporte de herramientas. Para
   reproducir la referencia de esta revisión: `deepseek/deepseek-v4.1-flash-1017`,
   si sigue disponible para la cuenta. Puede utilizarse otro identificador.
4. Omite integraciones y proveedores que no se utilicen en MedIA.

El bloque de `data/state/hermes/config.yaml` tiene esta forma:

```yaml
model:
  provider: openrouter
  default: deepseek/deepseek-v4.1-flash-1017
```

La clave va en **`data/state/hermes/.env`**, no en ese YAML:

```dotenv
OPENROUTER_API_KEY=<TU_CLAVE>
```

### 6.4. Cargar las skills compartidas

Añade o integra en `data/state/hermes/config.yaml` la ruta absoluta de tu clon:

```yaml
skills:
  external_dirs:
    - "C:/MedIA/shared/skills"
```

En macOS/Linux usa una ruta como `/home/usuario/MedIA/shared/skills`. Si existe
`skills:`, integra el campo sin duplicar la clave YAML. Las skills externas se
leen del repositorio y reciben sus actualizaciones con Git.

El documento general `shared/skills/media-core/media-video-archive.md` necesita
además la estructura `SKILL.md` de Hermes. Instálalo en el perfil:

**Windows, en la terminal con `HERMES_HOME` seleccionado:**

```powershell
$archiveSkillDir = Join-Path $env:HERMES_HOME 'skills/media-video-archive'
New-Item -ItemType Directory -Force -Path $archiveSkillDir | Out-Null
Copy-Item shared/skills/media-core/media-video-archive.md (Join-Path $archiveSkillDir 'SKILL.md')
```

**macOS/Linux:**

```bash
mkdir -p "$HERMES_HOME/skills/media-video-archive"
cp shared/skills/media-core/media-video-archive.md "$HERMES_HOME/skills/media-video-archive/SKILL.md"
```

Actualiza esa copia cuando cambie el documento general. Las demás skills
seguirán leyéndose directamente mediante `external_dirs`.

### 6.5. Instrucciones y herramientas del agente

Integra estas reglas en `data/state/hermes/SOUL.md`, conservando otras
instrucciones que necesite el perfil:

```markdown
Eres el asistente audiovisual de MedIA.
Para trabajar con vídeos utiliza las herramientas MCP de media-core y consulta
la skill media-video-archive y las skills compartidas pertinentes.
No ejecutes código, terminales ni scripts propios para reemplazar operaciones
que debe resolver media-core. Usa los identificadores y rutas de las herramientas.
No inventes resultados, identidades ni tiempos. Explica los errores y límites
del MCP y conserva las fuentes y enlaces que devuelvan sus resultados.
```

Las instrucciones orientan el comportamiento; la selección de herramientas
limita lo expuesto al agente. **Después de registrar el MCP en el apartado 7**,
integra en `config.yaml`:

```yaml
platform_toolsets:
  cli:
    - skills
    - media-core
```

Para el puente de Media API, `HERMES_TUI_TOOLSETS=skills,media-core` fija esa
misma selección y evita el modo automático de herramientas de programación
por estar dentro de un repositorio. El MCP debe registrarse con el nombre
`media-core`. Las herramientas de skills permiten consultar instrucciones;
las operaciones audiovisuales se ejecutan en Core.

No es necesario habilitar terminal, Python, navegador o TTS propios de Hermes
para el recorrido normal.

## 7. Arrancar Core y conectar el MCP

### 7.1. Terminal A: Media Core

Desde la raíz, abre una terminal dedicada:

```bash
cd services/media-core
uv run --locked --extra dev python server.py
```

Déjala abierta. El endpoint será `http://127.0.0.1:8001/mcp`.
Core carga el modelo textual antes de aceptar peticiones: espera a que anuncie
que está escuchando. `CHROMA_AUTO_INDEX=false` evita lanzar además la
sincronización automática al arrancar; el índice se preparó en el apartado 5.

Abrir `/mcp` en el navegador no es una prueba completa: es un endpoint de
protocolo, no una página HTML, y puede responder 405/406 sin los encabezados
y sesión MCP adecuados.

### 7.2. Registrar y probar la conexión

Con Core disponible, vuelve a la terminal del perfil Hermes del apartado 6:

```bash
hermes mcp add media-core --url http://127.0.0.1:8001/mcp
hermes mcp list
hermes mcp test media-core
```

El registro puede mostrar una selección interactiva. Para reproducir el
prototipo, habilita todas las herramientas de Media Core. La prueba debe
conectar y enumerar herramientas como `get_server_info`, `media_search`,
`media_transcribe`, `media_summarize` y `media_clips`.

La configuración se guarda en el **perfil Hermes**, no en el `.env` del Core:

```yaml
mcp_servers:
  media-core:
    url: http://127.0.0.1:8001/mcp
```

Si ya existe, revisa la entrada o ejecuta `hermes mcp configure media-core`
para seleccionar herramientas. No registres el mismo MCP bajo varios nombres
por accidente.

Integra ahora `platform_toolsets` del apartado 6.5 y comprueba:

```bash
hermes tools list --platform cli
hermes skills list
```

Las herramientas pueden aparecer con prefijo MCP; es normal. Deben encontrarse
las skills de MedIA y `media-video-archive`.

### 7.3. Probar Hermes en terminal, opcional

```bash
hermes chat
```

Pide: **«Consulta get_server_info de media-core y dime qué vídeos hay
disponibles»**. Comprueba modelo, clave de Hermes y MCP; puede generar consumo
OpenRouter. Sal cuando termines: esta sesión no necesita permanecer abierta
para utilizar la web.

## 8. Arrancar API y frontend

### 8.1. Terminal B: Media API

Desde otra terminal situada en la raíz:

```bash
cd services/media-api
uv run --locked --extra dev uvicorn app.main:app --host 127.0.0.1 --port 8010
```

Déjala abierta. Su `.env` debe apuntar a la copia instalada de Hermes y al mismo
perfil que contiene modelo, credenciales, skills y MCP. API iniciará el agente
cuando una petición lo necesite.

Utiliza un proceso de API y uno de Core en este recorrido. Los trabajos en
memoria y el puente local no se preparan aquí para varios workers. `--reload`
puede interrumpir tareas, por lo que no se usa en estos comandos.

### 8.2. Terminal C: Media Web

Desde otra terminal situada en la raíz:

```bash
cd apps/media-web
npm run dev
```

En Windows: `npm.cmd run dev`. Abre **<http://127.0.0.1:3000>**.

### 8.3. Alternativa: lanzador de API y frontend

Después de instalar, configurar y arrancar Core, se puede utilizar una sola
terminal para API/frontend desde la raíz:

**Windows:**

```powershell
.\scripts_arranque\start-media-web.ps1
```

Si PowerShell bloquea el script, para esa ejecución:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts_arranque\start-media-web.ps1
```

**macOS/Linux:**

```bash
bash scripts_arranque/start-media-web.sh
```

Los lanzadores **no instalan ni arrancan Core ni configuran Hermes**.
`-Install` / `--install` preparan API/frontend; para reproducir toda la
instalación utiliza primero el apartado 3. Un mensaje heredado del lanzador
puede mencionar `scripts/`; la carpeta actual es `scripts_arranque/`.

### 8.4. Estado conjunto

**Windows:**

```powershell
Invoke-RestMethod http://127.0.0.1:8010/api/health
```

**macOS/Linux:**

```bash
curl --fail http://127.0.0.1:8010/api/health
```

Resultado orientativo antes del primer chat:

```json
{
  "api": true,
  "media_core": true,
  "hermes_installed": true,
  "hermes_running": false,
  "hermes_configured_mode": "local",
  "hermes_mode": "local"
}
```

`hermes_running=false` puede ser normal antes de conversar. En modo local,
`hermes_installed=true` verifica principalmente las rutas del intérprete y
del puente; **no demuestra que la clave o el modelo remoto funcionen**.
Para eso hay que completar una conversación real.

## 9. Añadir vídeos y probar la aplicación

### 9.1. Material de ejemplo

Los vídeos originales no se distribuyen por Git. Pide al equipo los archivos
que se puedan compartir y colócalos, por ejemplo, en:

```text
data/test-vectors/videos/TV04.mp4
data/test-vectors/videos/TV05.mp4
```

Las transcripciones canónicas versionadas están en
`shared/test-vectors/transcripts/`. Nombre e identificador deben corresponder:
`TV04.mp4` con `TV04.json`. Tener solo el JSON permite consultar texto, pero
no recortar ni inspeccionar un vídeo ausente.

Copiar manualmente un vídeo no lanza todas las etapas de procesamiento. Usa
la indexación de la interfaz o las herramientas MCP. La CLI del apartado 5
solo sincroniza la colección textual, no genera los fotogramas y el índice visual.

### 9.2. Subir un vídeo nuevo

1. Con los tres servicios activos, abre la biblioteca.
2. Selecciona **Añadir vídeo** y sube primero un archivo corto de 30–60 segundos
   con audio comprensible.
3. API valida la pista de vídeo con FFprobe, guarda el archivo e inicia la
   transcripción y la indexación visual en segundo plano.
4. Espera y verifica texto, resultados visuales y registros de API/Core.

El límite predeterminado de API es **4.096 MB**; se configura con
`MEDIA_UPLOAD_MAX_MB` en su `.env`. Acepta `.mp4`, `.mov`, `.mkv`, `.webm` y
`.m4v` si FFprobe puede leer una pista de vídeo.

**Importante:** la subida automática solicita `language=auto` y
**`diarization=false`**. Para obtener turnos por hablante hay que solicitar
expresamente diarización. Algunas etapas del flujo actual registran sus fallos
como avisos y permiten finalizar: comprueba los resultados, no solo el estado
«listo» de la biblioteca.

Las operaciones remotas pueden enviar audio, texto o fotogramas a sus
proveedores. Usa material autorizado para las primeras pruebas.

### 9.3. Prueba funcional desde el chat

Sustituye los identificadores por vídeos realmente disponibles:

1. **«Qué vídeos hay disponibles»**: debe consultar el inventario real.
2. **«Busca en TV04 dónde se habla de educación»**: comprueba texto y tiempos.
3. **«Resume TV04»**: comprueba el LLM del Core.
4. **«Qué pone en pantalla en el minuto 4:30 de TV05; usa OCR»**: verifica el
   vídeo indicado y la lectura del fotograma.
5. **«Qué ocurre visualmente en el minuto 1 de TV04»**: comprueba el VLM.
6. **«Dame un clip de TV04 del segundo 30 al 45»**: abre el MP4 generado.
7. Con diarización e identidad atribuida: **«Dame los cinco primeros clips de
   las intervenciones de Salvador Illa en TV04, en orden»**.

Separar voces no asigna por sí solo nombres reales. Las peticiones por persona
dependen también de las identidades y transcripciones disponibles.

### 9.4. Referencia de llamadas MCP

Estas expresiones ilustran herramientas; **no son comandos de shell**:

```text
get_server_info()
media_transcribe(source="TV04", language="ca", diarization=True)
media_index_visual(video_id="TV04")
media_search_visual(query="presentador en plató", video_id="TV04", top_k=3)
media_describe_scene(video_id="TV04", timestamp=60.0)
media_speakers(video_id="TV04", person="Salvador Illa")
media_clips(video_id="TV04", person="Salvador Illa", turn_index=6)
media_clips(video_id="TV04", person="Salvador Illa", turn_index=1, max_clips=5)
media_clips(video_id="TV04", start_seconds=30.0, end_seconds=45.0)
```

Registro completo:
[`services/media-core/media_core/tools/__init__.py`](services/media-core/media_core/tools/__init__.py).
Para un corte exacto, reutiliza tiempos o índices del MCP en vez de repetir
una búsqueda textual para recuperar una intervención numerada.

## 10. Avatar, micrófono y Piper

Tras descargar Piper/Whisper y reiniciar Core/API:

1. En el chat, pulsa el avatar flotante de la esquina inferior derecha.
2. Debe reproducir un saludo con Piper. Selecciona la voz y el idioma que
   necesites: castellano, catalán o inglés.
3. Pulsa **Hablar**, concede permiso al micrófono y dicta una petición.
4. Una pausa de unos 1,4 segundos termina la captura, transcribe y envía.
   También puedes terminar manualmente.
5. La respuesta completa queda escrita y Piper reproduce una intervención breve.

El micrófono requiere localhost, `127.0.0.1` o HTTPS; una IP remota por HTTP
puede bloquearlo. Si se bloquea el audio automático, pulsa **Escuchar**.

Diagnóstico con Core/API activos:

```text
GET http://127.0.0.1:8010/api/voice/status
```

Comprueba `asr_ready` y las voces disponibles. El dictado usa CPU INT8 y dos
hilos por defecto; la grabación está limitada a 60 segundos y 8 MB. Es voz
por turnos, no reconocimiento continuo con interrupciones.

Más detalle: [avatar y voz](docs/architecture/VOICE-AVATAR.md).

## 11. Uso diario y actualizaciones

### Arranque y parada

Después de instalar, no repitas las descargas ni el setup:

1. Arranca Core y espera al puerto 8001.
2. Arranca API/frontend manualmente o con `scripts_arranque/`.
3. Abre la web y consulta `/api/health` si hay problemas.

Para detener, espera a que terminen las tareas importantes y pulsa `Ctrl+C`
en frontend/API y después Core. El lanzador detiene su API al cerrar. Los
trabajos de Core en memoria no son una cola durable: se interrumpen al parar
el proceso; los archivos e índices ya persistidos permanecen.

### Actualizar el clon

Guarda y revisa cambios locales antes de integrar los del equipo. Después:

```bash
git pull --ff-only

cd services/media-core
uv sync --python 3.11 --locked --extra dev
cd ../..

cd services/media-api
uv sync --python 3.11 --locked --extra dev
cd ../..

cd docker/hermes-agent
uv sync --python 3.11 --locked --extra mcp
cd ../..

cd apps/media-web
npm ci
cd ../..
```

Revisa las novedades de `.env.example` sin copiarlas encima de tus claves.
Actualiza la copia de `media-video-archive/SKILL.md` si cambió; las skills de
`external_dirs` ya leen el repositorio. Reinicia los procesos.

No ejecutes `hermes update` sobre la copia incluida por tu cuenta: su
actualización debe integrarse y revisarse en el monorepo.

## 12. Resolución de problemas

### No se encuentra uv, Node, FFmpeg o FFprobe

Abre una terminal nueva y repite las comprobaciones del apartado 2. FFmpeg y
FFprobe deben estar en el `PATH` de **Core y API**, no solo en otra terminal
o dentro de un entorno aislado de Hermes.

### PowerShell bloquea npm o el lanzador

Usa `npm.cmd` o la ejecución puntual del apartado 8.3. También puedes arrancar
manualmente los procesos sin modificar la política general del equipo.

### Lock desactualizado o compilación de dependencias

Comprueba servicio, commit y Python 3.11. No borres el lock para continuar:
si cambia un `pyproject.toml`, debe versionarse su lock correspondiente.
Si se compilan dependencias nativas, revisa arquitectura y disponibilidad de
wheels. Linux puede necesitar herramientas de compilación y Windows el
Microsoft Visual C++ Redistributable para ciertas DLL. No copies `.venv`
entre equipos ni mezcles los tres entornos Python. Cada dependencia puede
tener limitaciones en arquitecturas concretas.

### Core tarda en arrancar

La primera carga de MiniLM puede incluir una descarga antes de abrir el puerto.
Comprueba red, espacio y logs; prepara modelos con el apartado 5. En CPU la
indexación puede tardar, especialmente con otros procesos usando memoria.

### `/api/health` muestra `media_core=false`

Verifica que Core escucha y `MEDIA_MCP_URL` termina en `/mcp`. Repite
`hermes mcp test media-core` en el perfil correcto. Comprueba puertos y hosts
permitidos. No utilices el puerto 8000 del ejemplo Hello World.

### Hermes no está instalado, se cierra o no ve el MCP

- Comprueba `HERMES_AGENT_DIR`, `HERMES_PYTHON` y `tui_gateway/entry.py`.
- Revisa el extra `mcp` y prueba su ejecutable directamente con `--help`/`chat`.
- Comprueba que Core estaba disponible al iniciar Hermes.
- Verifica que terminal y API usan el **mismo `HERMES_HOME`**.
- Repite `hermes mcp test media-core`, `hermes tools list --platform cli` y
  `hermes skills list` en ese perfil.
- Revisa `skills.external_dirs`, `skills/media-video-archive/SKILL.md` y
  `HERMES_TUI_TOOLSETS=skills,media-core`.
- Reinicia API/Hermes y abre un chat nuevo tras cambiar su configuración.

### OpenRouter devuelve errores de clave, saldo, límites o modelo

Revisa la clave del componente que falla: perfil Hermes o `.env` del Core.
Comprueba acceso al modelo y saldo. Un chat que funciona no demuestra que
Core tenga clave, ni al revés. Los códigos 401/403 suelen indicar acceso,
402 saldo y 429 límites; revisa también el detalle del proveedor.

### No hay transcripción o no aparecen hablantes

Comprueba Replicate, saldo, pista de audio y logs. Whisper del micrófono no
reemplaza WhisperX. La subida usa `diarization=false`; solicita diarización
expresamente si la necesitas. Sin token HF la integración puede desactivarla;
un 403 de pyannote requiere revisar las condiciones aceptadas por esa cuenta.
`SPEAKER_XX` tampoco es por sí solo una identidad real.

### Vídeo no encontrado, caché o resultados que no corresponden

Comprueba archivo, identificador y parámetros `source`/`video_id`. No sustituyas
un vídeo por otro conservando su identificador y material antiguo. Core valida
la identidad de los archivos para reutilizar cachés; revisa o regenera el
material correspondiente.

### Búsqueda visual, semántica u OCR sin resultados

Un JSON no crea fotogramas ni índice visual. Comprueba vídeo, CLIP, RapidOCR,
MiniLM y colecciones; solicita `media_index_visual` y OCR cuando corresponda.
La búsqueda léxica puede funcionar aunque falle la semántica: consulta el
estado y logs para distinguirlas.

### El avatar no tiene voz o no transcribe

Repite las descargas en el entorno Core. Piper necesita `.onnx` y `.onnx.json`.
Revisa `PIPER_MODEL_DIR`, `VOICE_WHISPER_MODEL`, permisos del navegador y
`/api/voice/status`. Reinicia Core tras cambiar sus variables.

### Subida fallida, límite o `socket hang up`

Comprueba API, FFprobe y espacio para el temporal y el archivo final. Usa la
ruta de subida actual, revisa `MEDIA_UPLOAD_MAX_MB` y cualquier proxy externo.
La ruta `/api/videos` incluida hace streaming; no la reemplaces por una ruta
que acumule todo el vídeo en memoria.

### Chroma bloqueado o colección incompatible

Evita varios Core escribiendo en el mismo directorio y no copies un índice
mientras se modifica. Detén Core antes de reconstruir la colección textual;
conserva los modelos y transcripciones de origen.

### Puerto ocupado

**Windows:**

```powershell
Get-NetTCPConnection -State Listen -LocalPort 3000,8010,8001
```

**macOS/Linux:**

```bash
lsof -nP -iTCP:3000 -iTCP:8010 -iTCP:8001 -sTCP:LISTEN
```

Cierra la instancia anterior desde su terminal. Si cambias puertos, actualiza
Core, MCP en Hermes, URL MCP de API y destino de Next según corresponda.

## 13. Pruebas de desarrollo

Con las dependencias de desarrollo instaladas:

```bash
cd services/media-core
uv run --locked --extra dev pytest -q
cd ../..

cd services/media-api
uv run --locked --extra dev pytest -q
cd ../..

cd apps/media-web
npm test
npm run typecheck
npm run build
cd ../..
```

En Windows usa `npm.cmd` si hace falta. Detén `npm run dev` antes de compilar
el mismo frontend para evitar interferencias sobre `.next`.

Las pruebas automatizadas no verifican por sí solas saldo/acceso real a APIs,
permisos de micrófono o calidad de diarización. Completa la prueba funcional
con material autorizado antes de dar por preparada una instalación nueva.

Para comprobar los locks sin instalar paquetes:

```bash
uv lock --check --project services/media-core
uv lock --check --project services/media-api
uv lock --check --project docker/hermes-agent
```

## 14. Estructura, datos y colaboración

```text
apps/media-web/                  Next.js: biblioteca, chat y avatar
services/media-api/              FastAPI y puente con Hermes
services/media-core/             MCP y procesamiento multimedia
docker/hermes-agent/             Código de Hermes incluido
shared/skills/media-core/        Instrucciones compartidas
shared/test-vectors/transcripts/ Transcripciones canónicas versionadas
scripts_arranque/                Lanzadores de API y frontend
data/test-vectors/videos/        Vídeos locales
data/models/                    Pesos de los descargadores del Core
data/indexes/chroma/             Índices de texto y fotogramas
data/cache/                     Transcripciones, fotogramas, OCR y otras cachés
data/outputs/audio/              WAV/MP3 generados
data/outputs/clips/              MP4 generados
data/state/                     history.sqlite y estado de la aplicación
data/state/hermes/               Perfil Hermes propuesto por esta guía
docs/architecture/              Documentación técnica
docs/model-catalog/             Modelos evaluados
docs/cloud_doc/                 Planes y requisitos de despliegue
examples/mcp-hello-world/        Ejemplo independiente
```

### Rutas configurables

- `MEDIA_SHARED_DIR`: recursos compartidos y transcripciones.
- `MEDIA_DATA_DIR`: datos locales de Core/API.
- `MEDIA_WEB_DATA`: estado de API, por defecto `data/state/`.
- `CHROMA_PERSIST_DIR`: persistencia vectorial.
- `PIPER_MODEL_DIR`: voces alternativas.
- `HF_HOME`: caché Hugging Face si se define en el entorno.

Deja vacías las rutas opcionales para usar los valores predeterminados. Si
cambias `MEDIA_DATA_DIR` o `MEDIA_SHARED_DIR`, configura **Core y API** de forma
coherente; Hermes debe ver las rutas de los resultados.

### Git y persistencia

Se comparten código, locks, `.env.example`, skills y transcripciones canónicas
aprobadas por el equipo. No se comparten credenciales, `.venv`, pesos, vídeos,
cachés, resultados ni historiales personales. No copies el perfil Hermes a Git.

Una transcripción nueva se guarda normalmente en `data/cache/`. Para convertirla
en referencia compartida, revisa su contenido e intégrala deliberadamente en
`shared/test-vectors/transcripts/`.

Para reproducir un problema, registra commit, sistema, versiones, modelo del
agente, modelos del Core y vídeo utilizado. Comparte logs sin credenciales ni
datos confidenciales; evita actualizar modelos/dependencias al instalar sin
documentarlo.

Conserva originales, transcripciones no regenerables e historiales/perfiles que
necesites. Los índices y cachés se regeneran con coste de tiempo o API. Detén
procesos antes de copiar SQLite/Chroma. Reinstalar `.venv` no implica borrar datos.

## 15. Conexiones alternativas y nube

### Hermes ya instalado fuera del clon

Puede reutilizarse si contiene `tui_gateway/entry.py` y las dependencias MCP.
Configura sus rutas reales y el perfil que contiene credenciales, modelo,
skills y MCP. Los instaladores oficiales se describen en
[README de Hermes](docker/hermes-agent/README.md). Una instalación externa
puede tener otra versión; para reproducibilidad se recomienda el apartado 6.

### Hermes por HTTP o Docker

API también admite un gateway HTTP ya preparado:

```dotenv
HERMES_MODE=docker
HERMES_API_URL=http://127.0.0.1:8642
HERMES_API_KEY=<CLAVE_INTERNA_DEL_GATEWAY>
HERMES_DOCKER_WORKDIR=/workspace
```

El nombre `docker` selecciona el transporte HTTP; no crea contenedores.
La clave interna es distinta de la de OpenRouter. El gateway debe exponer su
API y el repositorio debe estar montado en la ruta indicada si se ejecuta en
contenedor. Consulta el [API Server de Hermes](docker/hermes-agent/website/docs/user-guide/features/api-server.md).

Desde un contenedor hacia Core en el host suele utilizarse
`http://host.docker.internal:8001/mcp`. Core debe escuchar en una interfaz
accesible, por ejemplo `MEDIA_HOST=0.0.0.0`, y Docker debe resolver el nombre;
Linux puede necesitar un mapeo `host-gateway`. Revisa cortafuegos y protección
del MCP. Entre contenedores se deben ajustar nombres de servicio y hosts
permitidos a la red elegida.

Los Compose de `docker/hermes-agent/` son de **Hermes**, no de todo MedIA.
No existe en esta versión un `compose.yaml` raíz que prepare todos los servicios.

### Acceso público

Este recorrido configura desarrollo local en loopback. Una URL pública
requiere HTTPS, autenticación, permisos, persistencia y límites de uso.
[Los documentos de nube](docs/cloud_doc/) contienen preparación y propuestas;
algunos comandos presuponen infraestructura que hay que implementar. No
sustituyen la instalación local ejecutable de este README.

### Más documentación

- [Media Core](services/media-core/README.md).
- [Media API](services/media-api/README.md).
- [Media Web](apps/media-web/README.md).
- [Skills de Media Core](shared/skills/media-core/README.md).
- [Arquitectura web](docs/architecture/MEDIA-WEB.md).
- [Avatar y voz](docs/architecture/VOICE-AVATAR.md).
- [Hermes incluido](docker/hermes-agent/README.md).
- [WhisperX en Replicate](https://replicate.com/victor-upmeet/whisperx).

## Lista final de instalación

- [ ] Git, uv, Python 3.11, Node/npm y FFmpeg/FFprobe disponibles.
- [ ] Core, API, frontend y Hermes instalados desde sus locks.
- [ ] `.env` locales preparados, con claves reales y sin marcadores.
- [ ] Perfil Hermes con un modelo accesible en OpenRouter que utilice herramientas.
- [ ] Piper y Whisper del micrófono descargados si se utiliza el avatar.
- [ ] MiniLM, CLIP y OCR preparados; material disponible indexado.
- [ ] Skills compartidas y `media-video-archive` en el perfil correcto.
- [ ] Core activo y `hermes mcp test media-core` satisfactorio.
- [ ] API/frontend activos y `/api/health` con los componentes disponibles.
- [ ] Conversación real resuelta con las herramientas Core.
- [ ] Vídeo corto transcrito, buscado y recortado; archivos reproducibles.
- [ ] Diarización probada expresamente si se necesitan turnos por persona.
- [ ] Micrófono y Piper comprobados si se activa voz.
