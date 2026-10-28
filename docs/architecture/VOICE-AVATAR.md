# Avatar de voz local para MedIA

El chat incorpora un avatar SVG original, con código e ilustración bajo MIT
(`apps/media-web/app/voice-avatar.LICENSE`). No usa servicios de avatares,
modelos de vídeo ni una GPU. La boca responde al volumen del audio reproducido;
no es una animación fonética de labios.

## Uso

1. Abre el chat y pulsa el avatar flotante de la esquina inferior derecha: te saludará.
2. Selecciona castellano, catalán o inglés.
3. Pulsa **Hablar**, concede permiso al micrófono y habla.
4. Una pausa de aproximadamente 1,4 segundos cierra el micrófono, transcribe y envía
   automáticamente la petición a Hermes. También puedes terminar manualmente.
5. Hermes trabaja con las mismas herramientas MCP que el chat escrito.
6. Al terminar, Piper reproduce una intervención conversacional de hasta dos frases,
   45 palabras y 320 caracteres. La respuesta completa y sus archivos permanecen en el chat.

Se puede detener o volver a escuchar el audio. Si el navegador bloquea la
reproducción automática, usa **Escuchar**. El micrófono nunca se abre
automáticamente. La grabación se detiene antes de un minuto. Cambiar de chat,
desactivar la voz o iniciar otra petición descarta respuestas de voz pendientes.

Si una petición sigue en curso, el avatar acompaña la espera con frases cortas
como «Estoy en ello». La primera se programa a los 12 segundos, la siguiente
a los 45 y la última a los 90: máximo tres por petición, rotando las frases.
Se cancelan al llegar el resultado, detener la tarea, cambiar de chat o apagar
la voz. Detener una frase silencia los siguientes avisos de espera de ese turno.
Las frases son neutras, no afirman haber encontrado resultados ni inventan
porcentajes de progreso. Se sintetizan con Piper y se reutiliza su caché local;
no requieren consultas adicionales a Hermes.

## Preparación

Desde `services/media-core`:

```powershell
uv sync --extra dev
uv run python -m media_core.services.local_voice --download
uv run python -m media_core.services.piper_tts --download
```

Reinicia Media Core y Media API y recarga la web. El navegador necesita HTTPS
o un origen local como `http://localhost:3000` para acceder al micrófono.

`VOICE_WHISPER_MODEL=base` selecciona Whisper multilingüe en CPU e int8.
`VOICE_WHISPER_THREADS=2` limita los hilos. Se puede elegir `small` y descargarlo
con el mismo comando para evaluar mayor precisión, con mayor memoria y latencia.
El modelo no se descarga durante una petición.

## Arquitectura y privacidad

- Navegador: MediaRecorder captura una grabación y solicita la transcripción.
- API: `/api/voice/status`, `/api/voice/transcribe`, `/api/voice/reply`, `/api/voice/greeting`.
- MCP: `media_voice_status`, `media_transcribe_microphone`, `media_voice_reply`.
- Whisper: faster-whisper y CTranslate2 CPU int8; VAD y filtro de silencio.
- Piper: voces locales ya utilizadas por MedIA.
- Avatar: SVG y Web Audio AnalyserNode para medir la señal.

La grabación (máximo 8 MB y 60 segundos) se procesa en un directorio temporal
que se elimina al terminar. FFmpeg decodifica solo archivos locales. Las
locuciones se guardan en `data/outputs/audio`; el modelo Whisper en
`data/models/voice-whisper/<modelo>`.

Hermes prepara una frase conversacional en su misma respuesta habitual y en el
idioma seleccionado. La API separa el bloque final `<media_voice>` del texto
escrito y lo pasa a Piper. No hay una segunda consulta a un LLM para resumir.
El saludo es local y fijo. Para respuestas antiguas o motores que omitan el bloque
se mantiene un extracto local como alternativa. No hay llamadas adicionales
a OpenRouter o Replicate para el circuito de voz.
Hermes mantiene su proveedor habitual para entender y resolver la petición:
esto no convierte el razonamiento del chat en un modelo local.

## Límites

No es una conversación con interrupciones y reconocimiento continuo. Es voz
por turnos, con envío automático tras una pausa. El detector usa energía del
micrófono y puede verse afectado por ruido o pausas largas en mitad de una frase.
Tras diez segundos sin voz cancela sin enviar; el límite absoluto sigue siendo
un minuto. La calidad de Whisper depende del ruido, acento y nombres propios.
El resumen breve no garantiza incluir todos los
resultados o advertencias de una respuesta larga. La respuesta escrita es la
referencia completa. Los audios del avatar se generan por completo antes de
reproducirse; no hay streaming de Piper en esta versión.

## Verificación local (22/10/2026)

Se comprobó el circuito con una petición sintética generada por Piper en
catalán: 4,31 segundos de audio, 6,31 segundos de transcripción con Whisper base
y 6,73 segundos a través de web/API/MCP. Hubo errores en palabras y nombres
propios. Small tardó 19,45 segundos en esa muestra sin corregirlos; estas dos
ejecuciones no constituyen una evaluación de calidad con voz humana.

También se comprobó que la ruta de respuesta entrega un WAV reproducible de
Piper y un extracto limitado, y se revisó el avatar en la web. Queda probar el
micrófono físico del usuario y la reproducción automática según los permisos de
su navegador. Las pruebas automatizadas cubren límites de tamaño/duración,
limpieza de grabaciones, selección del proveedor local y respuestas del chat
completadas; el tipado y la compilación del frontend también se verificaron.
