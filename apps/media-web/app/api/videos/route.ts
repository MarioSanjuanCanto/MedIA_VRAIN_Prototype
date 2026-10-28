// Keep video uploads out of Next's rewrite proxy, which buffers only 10 MB.
// Forward the request stream to Media API without materializing the video in memory.
export const runtime = 'nodejs';

const mediaApiUrl = (process.env.MEDIA_API_URL || 'http://127.0.0.1:8010').replace(/\/$/, '');

export async function GET(request: Request) {
  try {
    const response = await fetch(`${mediaApiUrl}/api/videos`, {
      cache: 'no-store',
      signal: request.signal,
    });
    return new Response(response.body, {
      status: response.status,
      headers: { 'content-type': response.headers.get('content-type') || 'application/json' },
    });
  } catch {
    return Response.json({ detail: 'No se pudo conectar con Media API.' }, { status: 502 });
  }
}

export async function POST(request: Request) {
  if (!request.body) {
    return Response.json({ detail: 'No se recibió ningún archivo.' }, { status: 400 });
  }

  try {
    const response = await fetch(`${mediaApiUrl}/api/videos`, {
      method: 'POST',
      headers: { 'content-type': request.headers.get('content-type') || 'application/octet-stream' },
      body: request.body,
      duplex: 'half',
      signal: request.signal,
    } as RequestInit & { duplex: 'half' });
    return new Response(response.body, {
      status: response.status,
      headers: { 'content-type': response.headers.get('content-type') || 'application/json' },
    });
  } catch {
    return Response.json({ detail: 'Se perdió la conexión con Media API durante la subida.' }, { status: 502 });
  }
}
