# 🏷️ PiperTTS

> [!NOTE]
> **Categoría**: Audio - Text-to-Speech (TTS) / Síntesis de Voz  
> **Uso en MedIA**: Motor TTS local predeterminado  
> **Licencia del motor**: GPL-3.0-or-later  

---

## 📋 Resumen y Descripción

PiperTTS es un motor neuronal de síntesis de voz rápido y local. Ejecuta modelos
ONNX en CPU y utiliza `espeak-ng` para la conversión de texto a fonemas. En
Media Core permite generar audio sin enviar el texto a un proveedor externo.

---

## ⚙️ Ficha Técnica

| Propiedad | Detalle |
| :--- | :--- |
| **Modelo** | PiperTTS 1.8.0 |
| **Categoría** | Audio - Text-to-Speech (TTS) / Síntesis de Voz |
| **Ejecución** | Local, CPU, modelos ONNX |
| **Integración** | Self-hosted en `media-core` |
| **Idiomas configurados** | Español, catalán e inglés |
| **Voces actuales** | `es_ES-carlfm-x_low`, `ca_ES-upc_ona-medium`, `en_US-ljspeech-medium` |
| **Capacidades** | Voces fijas, selección de hablante cuando el modelo lo permite y control de velocidad |
| **Limitaciones** | No ofrece clonación de voz ni instrucciones de estilo |

---

## 🔗 Enlaces y Recursos

- **Repositorio oficial**: [OHF-Voice/piper1-gpl](https://github.com/OHF-Voice/piper1-gpl)
- **Paquete Python**: [piper-tts en PyPI](https://pypi.org/project/piper-tts/)
- **Catálogo de voces**: [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices)

---

## ⚖️ Licencias de las Voces

La licencia de cada voz depende de su dataset. Actualmente, `carlfm` y
`ljspeech` indican dominio público, mientras que `upc_ona` indica
CC BY-SA 3.0 ES. Consulta siempre el `MODEL_CARD` descargado antes de redistribuir
un modelo o su audio.
