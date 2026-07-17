// shared/util.js — small DOM/string helpers shared across all tools.
//
// Loaded with `<script src="../shared/util.js"></script>` (or `./shared/`
// from the root) BEFORE any module that needs it. Exposes window.IcpeUtil.
//
// Why a global instead of an ES module: this project deliberately runs
// from `file://` and from GitHub Pages without a build tool, so import
// statements would force a bundler. A namespaced global is the smallest
// shareable surface that works in both contexts.

(function () {
  'use strict';

  if (window.IcpeUtil) return; // idempotent re-load (e.g. inline tests)

  // HTML-escape a string for safe interpolation into innerHTML.
  // Escapes the five OWASP-recommended characters: & < > " '
  // (Single-quote inclusion matters when the value lands inside a
  // single-quoted attribute.)
  //
  // Returns '' for null/undefined so callers don't have to guard.
  function escapeHTML(s) {
    if (s == null) return '';
    return String(s).replace(/[&<>"']/g, function (c) {
      return {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
      }[c];
    });
  }

  // Coerce a value that *should* be a finite number into a display
  // string. NaN, Infinity, null, undefined, non-numeric strings → the
  // fallback. This exists so HTML interpolation of fields from a JSON
  // file (lat, lon, distance, count) cannot leak a non-numeric string
  // into the DOM if the upstream pipeline ever produces one.
  function safeNumber(value, fallback) {
    var n = Number(value);
    if (Number.isFinite(n)) return String(n);
    return fallback == null ? '' : String(fallback);
  }

  window.IcpeUtil = { escapeHTML: escapeHTML, safeNumber: safeNumber };
})();
