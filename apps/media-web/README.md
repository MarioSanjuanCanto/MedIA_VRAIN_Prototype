# Media Web

Frontend Next.js de MedIA. Incluye la biblioteca, el chat con Hermes y el panel
de uso de APIs. Consume Media API mediante rutas `/api/*`.

## Preparar

```bash
npm ci
cp .env.example .env.local  # opcional
```

## Arrancar

```bash
npm run dev
```

Abre http://127.0.0.1:3000. Media API debe estar disponible en el puerto 8010.

## Comprobar

```bash
npm run typecheck
npm run build
```

La interfaz está en `app/`; la dirección de Media API se configura en
`next.config.ts` o mediante `MEDIA_API_URL`.

La subida a `/api/videos` se transmite por streaming a Media API. Para subir
vídeos grandes, inicia Media API y usa el botón «Añadir vídeo» normalmente;
no hace falta cambiar el límite de 10 MB de Next.js.
