// Forward the multipart body as a stream so reference clips do not fill Next's memory.
export const runtime = 'nodejs';

const mediaApiUrl = (process.env.MEDIA_API_URL || 'http://127.0.0.1:8010').replace(/\/$/, '');

export async function POST(request: Request) {
  if (!request.body) {
    return Response.json({ detail: 'No se recibió ningún archivo.' }, { status: 400 });
  }
  try {
    const response = await fetch(`${mediaApiUrl}/api/chat/jobs/with-media`, {
      method: 'POST',
      headers: { 'content-type': request.headers.get('content-type') || 'multipart/form-data' },
      body: request.body,
      duplex: 'half',
      signal: request.signal,
    } as RequestInit & { duplex: 'half' });
    return new Response(response.body, {
      status: response.status,
      headers: { 'content-type': response.headers.get('content-type') || 'application/json' },
    });
  } catch {
    return Response.json({ detail: 'No se pudo conectar con Media API para enviar el archivo.' }, { status: 502 });
  }
}
