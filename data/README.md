# Datos locales

Datos generados o descargados durante el uso de MedIA. Git conserva la
estructura de carpetas mediante `.gitkeep`, pero ignora su contenido.

| Carpeta | Contenido |
| --- | --- |
| `test-vectors/videos/` | Vídeos locales, por ejemplo `TV05.mp4`. |
| `cache/` | Transcripciones y respuestas reutilizables. |
| `indexes/` | Índice vectorial de ChromaDB. |
| `models/` | Embeddings y voces locales de Piper. |
| `outputs/` | Clips y audios generados. |
| `state/` | Historial, miniaturas y consumo de APIs. |

No guardes aquí archivos que deban compartirse. Las transcripciones canónicas
versionadas viven en `shared/test-vectors/transcripts/`.

Los modelos y el índice se preparan siguiendo la sección «Instalación» del
[README raíz](../README.md).
