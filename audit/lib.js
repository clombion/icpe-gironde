/* ============================================================================
   audit/lib.js — Pure functions for the coordinate audit review tool.

   Everything here must be deterministic, side-effect-free, and testable
   in audit/test.html without DOM/network. Anything that touches DOM,
   fetch, localStorage, or Leaflet lives in app.js.
============================================================================ */

(function (root) {
  'use strict';

  const VALID_VERDICTS = new Set([
    'garder_stored',
    'utiliser_geocoded',
    'placer_manuellement',
    'terrain',
  ]);

  // ---- Bucket slicing -------------------------------------------------

  /**
   * Slice a flat list of items into buckets of at most bucket_size each.
   * Returns an array of buckets, each bucket being { index, items }.
   * Buckets are 1-indexed in the returned objects (matching the
   * filename convention bucket-{group}-01.json, -02.json, etc.).
   */
  function sliceBuckets(items, bucket_size) {
    if (!Array.isArray(items)) return [];
    if (!bucket_size || bucket_size < 1) bucket_size = 25;
    const buckets = [];
    for (let i = 0; i < items.length; i += bucket_size) {
      buckets.push({
        index: buckets.length + 1,
        items: items.slice(i, i + bucket_size),
      });
    }
    return buckets;
  }

  // ---- Validation of submitted review files ---------------------------

  /**
   * Validate a parsed review file (typed object) against the current
   * flagged.json metadata. Returns a discriminated union:
   *
   *   { status: 'valid',   reviewer, decisions } — fully usable
   *   { status: 'stale',   reviewer, decisions } — flagged_hash mismatch
   *   { status: 'invalid', reason }              — missing fields, etc.
   */
  function validateReviewFile(file, flagged, group_id, expected_bucket_index) {
    if (!file || typeof file !== 'object') {
      return { status: 'invalid', reason: 'not an object' };
    }
    const required = ['flagged_hash', 'group', 'bucket_index', 'reviewer', 'decisions'];
    for (const k of required) {
      if (!(k in file)) {
        return { status: 'invalid', reason: 'missing field: ' + k };
      }
    }
    if (file.group !== group_id) {
      return { status: 'invalid', reason: 'group mismatch: expected ' + group_id + ', got ' + file.group };
    }
    if (file.bucket_index !== expected_bucket_index) {
      return { status: 'invalid', reason: 'bucket_index mismatch: expected ' + expected_bucket_index + ', got ' + file.bucket_index };
    }
    if (!Array.isArray(file.decisions)) {
      return { status: 'invalid', reason: 'decisions is not an array' };
    }
    for (const d of file.decisions) {
      if (!d || typeof d !== 'object') {
        return { status: 'invalid', reason: 'decision is not an object' };
      }
      if (typeof d.id_icpe !== 'string') {
        return { status: 'invalid', reason: 'decision missing id_icpe' };
      }
      if (!VALID_VERDICTS.has(d.verdict)) {
        return { status: 'invalid', reason: 'decision has unknown verdict: ' + d.verdict };
      }
    }
    if (flagged && file.flagged_hash !== flagged.flagged_hash) {
      return {
        status: 'stale',
        reviewer: file.reviewer,
        decisions: file.decisions,
      };
    }
    return {
      status: 'valid',
      reviewer: file.reviewer,
      decisions: file.decisions,
    };
  }

  // ---- Bucket id ↔ filename helpers -----------------------------------

  /**
   * Build the canonical filename for a bucket review file:
   *   bucket-{group_id}-{NN}.json
   * where NN is a 2-digit zero-padded index.
   */
  function bucketFilename(group_id, bucket_index) {
    const nn = String(bucket_index).padStart(2, '0');
    return 'bucket-' + group_id + '-' + nn + '.json';
  }

  /** Inverse of bucketFilename. Returns {group, index} or null. */
  function parseBucketFilename(name) {
    const m = /^bucket-([a-z]+)-(\d{2,})\.json$/.exec(name);
    if (!m) return null;
    return { group: m[1], index: parseInt(m[2], 10) };
  }

  // ---- Decision → correction mapping ----------------------------------

  /**
   * Translate a per-item decision into a coordinate correction action.
   * Returns one of:
   *   { action: 'noop' }                     — keep stored coords
   *   { action: 'use_geocoded', lat, lon }   — use BAN/OpenCage point
   *   { action: 'use_manual', lat, lon }     — use the reviewer's pin
   *   { action: 'field_visit' }              — defer to in-person check
   *   { action: 'error', reason }            — malformed decision
   */
  function decisionToCorrection(decision, item) {
    if (!decision || !item) return { action: 'error', reason: 'missing decision or item' };
    switch (decision.verdict) {
      case 'garder_stored':
        return { action: 'noop' };
      case 'utiliser_geocoded':
        if (item.geocoded_lat == null || item.geocoded_lon == null) {
          return { action: 'error', reason: 'no geocoded coords available' };
        }
        return { action: 'use_geocoded', lat: item.geocoded_lat, lon: item.geocoded_lon };
      case 'placer_manuellement':
        if (decision.manual_lat == null || decision.manual_lon == null) {
          return { action: 'error', reason: 'manual verdict without manual_lat/manual_lon' };
        }
        return { action: 'use_manual', lat: decision.manual_lat, lon: decision.manual_lon };
      case 'terrain':
        return { action: 'field_visit' };
      default:
        return { action: 'error', reason: 'unknown verdict: ' + decision.verdict };
    }
  }

  // ---- Progress counts ------------------------------------------------

  /**
   * Count progress across groups + buckets.
   * `flagged` = parsed flagged.json object.
   * `reviewsByBucket` = map of "{group}-{NN}" → array of decisions.
   * Returns { total, reviewed, remaining } and per-group breakdown.
   */
  function countProgress(flagged, reviewsByBucket) {
    let total = 0;
    let reviewed = 0;
    const perGroup = {};
    if (!flagged || !Array.isArray(flagged.groups)) {
      return { total: 0, reviewed: 0, remaining: 0, perGroup };
    }
    for (const group of flagged.groups) {
      const groupTotal = group.count || 0;
      let groupReviewed = 0;
      const buckets = sliceBuckets(group.items || [], flagged.bucket_size || 25);
      for (const b of buckets) {
        const key = group.id + '-' + String(b.index).padStart(2, '0');
        const decisions = reviewsByBucket[key];
        if (decisions && decisions.length === b.items.length) {
          groupReviewed += b.items.length;
        }
      }
      perGroup[group.id] = { total: groupTotal, reviewed: groupReviewed };
      total += groupTotal;
      reviewed += groupReviewed;
    }
    return { total, reviewed, remaining: total - reviewed, perGroup };
  }

  // ---- Status strip formatter -----------------------------------------

  /**
   * Format the masthead status strip text.
   *   "2 890 sites · 169 cohérents · 2721 à vérifier · 42 revus · 2679 restants"
   */
  function formatStatusStrip(totalSites, coherent, total, reviewed) {
    const fr = (n) => Number(n).toLocaleString('fr-FR');
    const remaining = total - reviewed;
    const pct = totalSites > 0 ? Math.round((coherent / totalSites) * 100) : 0;
    return (
      fr(totalSites) + ' sites · ' +
      fr(coherent) + ' cohérents (' + pct + ' %) · ' +
      fr(total) + ' à vérifier · ' +
      fr(reviewed) + ' revus · ' +
      fr(remaining) + ' restants'
    );
  }

  // ---- Compose an export bucket file ----------------------------------

  /**
   * Build the JSON object that the reviewer downloads when exporting a bucket.
   * Validates that every decision has a valid verdict and id_icpe.
   */
  function composeExportFile(flagged_hash, audit_run_id, group_id, bucket_index, reviewer, decisions) {
    return {
      flagged_hash: flagged_hash,
      audit_run_id: audit_run_id,
      group: group_id,
      bucket_index: bucket_index,
      reviewer: reviewer,
      submitted_at: new Date().toISOString(),
      decisions: decisions,
    };
  }

  // ---- Public surface -------------------------------------------------

  root.AuditLib = {
    VALID_VERDICTS,
    sliceBuckets,
    validateReviewFile,
    bucketFilename,
    parseBucketFilename,
    decisionToCorrection,
    countProgress,
    formatStatusStrip,
    composeExportFile,
  };
}(typeof window !== 'undefined' ? window : this));
