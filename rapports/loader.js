/**
 * loader.js — Shared download helper for the /rapports/ sql.js pages.
 *
 * Streams a URL with real download progress. Shared by app.js (Vérifier) and
 * explorer.js so the ~12 MB gzipped fiches.sqlite download reports progress
 * consistently. No DOM access — the caller supplies an onProgress callback and
 * owns its own progress UI.
 *
 * Note: under gzip transfer, Content-Length is the compressed size while the
 * stream yields decompressed bytes, so received can exceed total — callers
 * should clamp their percentage display.
 */

/**
 * Fetch a URL with real download progress via ReadableStream.
 * Falls back to a plain fetch if Content-Length is missing.
 * @param {string} url
 * @param {(received:number, total:number)=>void} onProgress
 * @returns {Promise<Uint8Array>}
 */
export async function fetchWithProgress(url, onProgress) {
  const resp = await fetch(url);
  const total = +resp.headers.get('Content-Length');
  if (!total || !resp.body) return new Uint8Array(await resp.arrayBuffer());
  const reader = resp.body.getReader();
  const chunks = [];
  let received = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    received += value.length;
    onProgress(received, total);
  }
  const result = new Uint8Array(received);
  let pos = 0;
  for (const chunk of chunks) { result.set(chunk, pos); pos += chunk.length; }
  return result;
}
