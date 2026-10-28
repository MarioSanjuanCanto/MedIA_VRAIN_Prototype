---
name: media-core-server-info
description: Inspecciona el estado operativo y las capacidades declaradas por un servidor Media Core. Úsala para comprobar disponibilidad, configuración, modelos, índices, archivos, trabajos o herramientas antes de diagnosticar o planificar una operación.
---

# Consultar el estado de Media Core

Usa `get_server_info` como fuente de verdad sobre el proceso de Media Core conectado. Descubre primero el nombre registrado y su esquema; el cliente puede añadir un prefijo.

## Uso obligatorio de Media Core

Para cualquier tarea cubierta por esta skill, debes consultar obligatoriamente el MCP de Media Core mediante `get_server_info`. No deduzcas su estado a partir de procesos locales, archivos de configuración, comandos de shell, otros MCP ni servicios externos. Si el MCP o la herramienta no están disponibles, informa de ello y solicita que se conecte Media Core; no presentes una fuente alternativa como estado actual del servidor.

## Flujo

1. Llama a `get_server_info` sin inventar argumentos que no aparezcan en el esquema.
2. Relaciona la pregunta del usuario con los campos pertinentes de la respuesta:
   - identidad, versión y conectividad del servicio;
   - transporte, puerto y entorno de ejecución;
   - herramientas y modelos declarados;
   - estado de credenciales o dependencias;
   - disponibilidad del índice y contenido registrado;
   - archivos locales o artefactos generados;
   - trabajos recientes y métricas operativas.
3. Informa solo de los campos necesarios para responder. Distingue entre una capacidad declarada, una dependencia configurada y una operación comprobada con éxito.

## Interpretación segura

- No expongas secretos, rutas irrelevantes ni detalles del entorno que el usuario no haya solicitado.
- Un proveedor configurado no demuestra que su servicio externo esté disponible; una herramienta listada no demuestra que una ejecución concreta vaya a completarse.
- La ausencia de una credencial puede limitar una función sin implicar que todo el servidor esté caído.
- Si la respuesta contradice el esquema del cliente o falta la herramienta, describe la incompatibilidad observada y recomienda reconectar o actualizar la conexión. No inventes el estado del servidor.
- Para verificar una operación concreta, usa su herramienta correspondiente cuando esté dentro del alcance solicitado; `get_server_info` es diagnóstico, no una prueba funcional completa.
