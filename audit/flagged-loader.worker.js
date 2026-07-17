// audit/flagged-loader.worker.js
//
// Web Worker that fetches and parses coordonnees-audit-flagged.json off
// the main thread. The file is ~12 MB / 81k lines, and JSON.parse on
// the main thread is a 80–200 ms blocking call on a mid-range laptop —
// long enough to drop a frame and freeze the UI on cold load.
//
// Protocol:
//   main → worker  postMessage({ type: 'load', url: '<absolute or relative URL>' })
//   worker → main  postMessage({ type: 'loaded', data: <parsed object> })
//                  or postMessage({ type: 'error', message: '<reason>' })
//
// The worker is created in app.js with `new Worker('flagged-loader.worker.js')`,
// terminated immediately after the response so it doesn't pin memory.

'use strict';

self.onmessage = async function (ev) {
  const msg = ev.data || {};
  if (msg.type !== 'load') {
    self.postMessage({ type: 'error', message: 'unknown message type: ' + msg.type });
    return;
  }
  if (!msg.url) {
    self.postMessage({ type: 'error', message: 'missing url' });
    return;
  }
  try {
    const r = await fetch(msg.url, { cache: 'no-store' });
    if (!r.ok) {
      self.postMessage({ type: 'error', message: 'flagged.json: HTTP ' + r.status });
      return;
    }
    // Response.json() inside a worker runs the parse on the worker
    // thread, which is exactly what we want.
    const data = await r.json();
    self.postMessage({ type: 'loaded', data: data });
  } catch (err) {
    self.postMessage({
      type: 'error',
      message: (err && err.message) ? err.message : String(err),
    });
  }
};
