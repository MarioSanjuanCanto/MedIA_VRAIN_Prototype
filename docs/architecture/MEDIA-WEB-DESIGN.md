# Diseño del estudio MedIA

La unidad de trabajo es un vídeo y las piezas que se crean a partir de él.

## Dirección

Interfaz de edición audiovisual: los fotogramas dan carácter al archivo. El reproductor ocupa la mayor superficie; resultados y controles permanecen cerca de su fuente. Todo el texto se alinea a la izquierda. La biblioteca utiliza imágenes sin contenedores de tarjeta adicionales.

Paleta: blanco #FFFFFF, fondo de trabajo #EEF1F5, texto #202A3B, acción azul #244CCB, separación #D8DFE9 y error #B33842. DM Sans local, títulos de 26–34 px y controles de 12–15 px. Sin tipografías ni imágenes de servicios externos.

Esquema de escritorio:

```text
Archivo persistente | Nombre del vídeo                 
                    | Reproductor       | Resultados    
Vídeos con miniatura| Transcripción     | Texto / medios
                    | Tipo de pieza    | Historial     
Conexiones          | Idioma + Crear   | del vídeo     
```

En móvil, el archivo se convierte en una tira horizontal y el resultado pasa debajo de los controles. Conexiones sigue accesible.

## Decisiones de interacción

- Seleccionar un vídeo recupera su último trabajo.
- Resumen, clip, audio y petición libre son opciones previas a una acción explícita de creación.
- El idioma está junto al botón de creación; cada resultado conserva el suyo.
- Los campos de instrucciones solo aparecen en clip y petición libre: son las acciones que los admiten en la API actual.
- La transcripción se consulta junto al reproductor.
- El historial del vídeo incluye sus distintas conversaciones.
- La consulta de trabajos continúa mientras haya peticiones pendientes, aunque se cambie de pantalla.
- Un fallo de carga tiene un estado propio y un botón para reintentar; no se presenta como una biblioteca vacía.

## Alcance

Se conserva el contrato de FastAPI y las integraciones con Hermes y media-core. No se añaden dependencias de frontend. Los problemas de selección editorial de clips y la configuración de proveedores son previos al rediseño.
