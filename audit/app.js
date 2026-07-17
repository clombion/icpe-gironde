/* ============================================================================
   audit/app.js — Coordinate audit review tool

   State machine + mini-map + Contents API discovery + per-item review form.
   Pure functions live in audit/lib.js.
============================================================================ */

(function () {
  'use strict';

  const Lib = window.AuditLib;
  if (!Lib) {
    console.error('AuditLib not loaded — check that lib.js is included before app.js');
    return;
  }

  // ---- Constants ------------------------------------------------------

  // GitHub repo for the Contents API (DD #5: hardcoded constant)
  const GH_REPO = 'bononlouis-del/Les-ICPE-en-r-serve-naturelle-nationale';
  // Path to the reviews directory inside the repo (URL-encoded by encodeURI)
  const REVIEWS_PATH = 'données-georisques/audit/coordonnees-audit-reviews';
  const FLAGGED_JSON_URL = '../données-georisques/audit/coordonnees-audit-flagged.json';

  // Background poll cadence (DD #20)
  // Adaptive backoff based on X-RateLimit-Remaining:
  //   ≥20 → 15 min, 10-19 → 30 min, <10 → 60 min, 0 → paused until reset
  const POLL_INTERVAL_NORMAL_MS = 15 * 60 * 1000;
  const POLL_INTERVAL_AMBER_MS = 30 * 60 * 1000;
  const POLL_INTERVAL_RED_MS = 60 * 60 * 1000;
  const POLL_JITTER_MS = 2 * 60 * 1000;
  const RATE_LIMIT_AMBER_THRESHOLD = 20;
  const RATE_LIMIT_RED_THRESHOLD = 10;
  const FETCH_LOCK_KEY = 'audit:last-fetch-at';
  const LOCK_WINDOW_MS = 14 * 60 * 1000; // slightly less than poll interval
  const SESSION_CACHE_KEY = 'audit:reviews-cache';
  const SESSION_CACHE_TTL_MS = 5 * 60 * 1000;
  const TOASTS_SUPPRESSED_KEY = 'audit:toasts-suppressed';

  // localStorage keys
  const KEY_REVIEWER = 'audit:reviewer';
  const KEY_GUIDE_SEEN = 'audit:guide-seen';
  const DRAFT_PREFIX = 'audit:draft:';

  // Map layers (DD #22 — verified URL template)
  const PLAN_TILE_URL = 'https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png';
  const PLAN_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>';
  const ORTHO_TILE_URL = 'https://data.geopf.fr/wmts?REQUEST=GetTile&SERVICE=WMTS&VERSION=1.0.0&LAYER=HR.ORTHOIMAGERY.ORTHOPHOTOS&STYLE=normal&TILEMATRIXSET=PM&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&FORMAT=image/jpeg';
  const ORTHO_ATTRIBUTION = '&copy; <a href="https://www.geoportail.gouv.fr/">IGN-F/Géoportail</a>';

  // CSS variables (read once)
  const CSS = (() => {
    const s = getComputedStyle(document.documentElement);
    const get = (k) => s.getPropertyValue(k).trim();
    return {
      ink: get('--ink'),
      paper: get('--paper'),
      rust: get('--rust'),
      ochre: get('--ochre'),
      lead: get('--lead'),
      moss: get('--moss'),
      rustDeep: get('--rust-deep'),
    };
  })();

  // ---- State ----------------------------------------------------------

  const state = {
    flagged: null,            // parsed flagged.json
    bucketsByGroup: {},       // { groupId: [{index, items}, ...] }
    reviewsByBucket: {},      // { "groupId-NN": [decisions] }
    reviewersByBucket: {},    // { "groupId-NN": "reviewer initials" }
    staleByBucket: {},        // { "groupId-NN": true } if flagged_hash mismatch

    currentGroup: null,
    currentBucketIndex: null, // 1-based
    currentItemIndex: null,   // 0-based within bucket
    drafts: {},               // { itemKey: {verdict, note, manual_lat, manual_lon, pertinent_enquete} }

    rateLimit: { remaining: null, limit: 60, reset: null },
    pollTimer: null,
    miniMap: null,
    storedMarker: null,
    geocodedMarker: null,
    distanceLine: null,
    distanceLabel: null,
    communeLayer: null,
    reserveLayer: null,
    manualMarker: null,

    reviewerName: '',
    suppressToasts: false,
    broadcastChannel: null,
    fetchAbortController: null,  // aborts in-flight fetchReviews on re-entry

    // Sidebar render bookkeeping. _sidebarRendered flips true after the
    // first full innerHTML build; subsequent renderSidebar() calls do a
    // keyed in-place update (className/icon/reviewer-suffix) instead of
    // tearing down and rebuilding ~50-100 <li> elements.
    _sidebarRendered: false,

    // Memoized hasLocalDraft results, keyed by bucketKey ("group-NN").
    // Populated lazily on first read; invalidated by saveDraft /
    // saveManualToDraft (we delete the entry; next read recomputes by
    // walking the bucket once). Cuts per-render localStorage reads from
    // ~2500 to ~0 for the steady state.
    _draftCache: new Map(),
  };

  // ---- DOM helpers ----------------------------------------------------

  function $(id) { return document.getElementById(id); }

  // Shared HTML-escape from window.IcpeUtil (shared/util.js). Single
  // source of truth across audit / donnees / carte to prevent drift.
  const escapeHTML = window.IcpeUtil.escapeHTML;
  const safeNumber = window.IcpeUtil.safeNumber;

  // ---- Initialization -------------------------------------------------

  // Global last-line guard so any promise rejection that escapes the
  // explicit .catch coverage is at least visible in the console with
  // enough context for the instructor to file a bug.
  window.addEventListener('unhandledrejection', function (ev) {
    console.error('[audit] unhandled rejection:', ev.reason);
  });

  async function init() {
    state.reviewerName = localStorage.getItem(KEY_REVIEWER) || '';
    try {
      state.suppressToasts = sessionStorage.getItem(TOASTS_SUPPRESSED_KEY) === '1';
    } catch (_) { /* ignore */ }

    try {
      await loadFlagged();
    } catch (err) {
      console.error('Failed to load flagged.json:', err);
      $('status-strip').textContent = "Impossible de charger l'audit. Le script Python a-t-il été exécuté ?";
      return;
    }

    setupBroadcastChannel();

    try {
      await fetchReviews();
    } catch (err) {
      console.warn('Initial fetchReviews failed:', err);
    }

    renderStatusStrip();
    renderSidebar();
    hydrateFromHash();

    if (localStorage.getItem(KEY_GUIDE_SEEN) !== '1') {
      openGuide();
    }

    setupKeyboard();
    setupVisibilityHandler();
    schedulePoll();

    // Wire UI
    $('guide-button').addEventListener('click', openGuide);
    $('btn-close-guide').addEventListener('click', closeGuide);
    $('sidebar-toggle').addEventListener('click', toggleSidebar);
    $('btn-prev').addEventListener('click', () => navigateItem(-1));
    $('btn-next').addEventListener('click', () => navigateItem(1));
    $('btn-skip').addEventListener('click', () => navigateItem(1));
    $('btn-save').addEventListener('click', saveAndAdvance);
    $('btn-exit').addEventListener('click', exitToEmpty);
    $('btn-export-download').addEventListener('click', exportCurrentBucket);
    $('btn-close-drawer').addEventListener('click', () => $('export-drawer').hidden = true);

    document.querySelectorAll('input[name="verdict"]').forEach((radio) => {
      radio.addEventListener('change', onVerdictChange);
    });
    $('note-field').addEventListener('input', onFormChange);
    $('pertinent-enquete').addEventListener('change', onFormChange);

    window.addEventListener('hashchange', hydrateFromHash);
  }

  // ---- Flagged.json loading -------------------------------------------

  // Loads coordonnees-audit-flagged.json (~12 MB) off the main thread
  // via a Web Worker so JSON.parse doesn't block the UI for 80–200 ms
  // on cold load. Falls back to a main-thread fetch if Worker is not
  // available (legacy browser, file:// edge cases). The worker is
  // terminated immediately after the response so it doesn't pin memory.
  async function loadFlagged() {
    let data;
    if (typeof Worker !== 'undefined') {
      data = await _loadFlaggedViaWorker();
    } else {
      const r = await fetch(FLAGGED_JSON_URL, { cache: 'no-store' });
      if (!r.ok) throw new Error('flagged.json: HTTP ' + r.status);
      data = await r.json();
    }
    state.flagged = data;
    // Pre-slice buckets for each group
    state.bucketsByGroup = {};
    for (const g of state.flagged.groups || []) {
      state.bucketsByGroup[g.id] = Lib.sliceBuckets(g.items || [], state.flagged.bucket_size || 25);
    }
  }

  function _loadFlaggedViaWorker() {
    return new Promise((resolve, reject) => {
      let worker;
      try {
        worker = new Worker('flagged-loader.worker.js');
      } catch (err) {
        // Some sandboxed contexts (file://, certain CSPs) reject Worker
        // construction. Fall back to main-thread fetch.
        console.warn('[audit] Worker construction failed, falling back to main thread', err);
        return fetch(FLAGGED_JSON_URL, { cache: 'no-store' })
          .then((r) => {
            if (!r.ok) throw new Error('flagged.json: HTTP ' + r.status);
            return r.json();
          })
          .then(resolve, reject);
      }
      worker.onmessage = (ev) => {
        const msg = ev.data || {};
        worker.terminate();
        if (msg.type === 'loaded') {
          resolve(msg.data);
        } else {
          reject(new Error(msg.message || 'unknown worker error'));
        }
      };
      worker.onerror = (err) => {
        worker.terminate();
        reject(new Error(err.message || 'worker crashed'));
      };
      // Pass the absolute URL so the worker resolves it from its own
      // location instead of the main document's.
      worker.postMessage({
        type: 'load',
        url: new URL(FLAGGED_JSON_URL, location.href).href,
      });
    });
  }

  // ---- GitHub Contents API discovery ----------------------------------

  function cachedFreshEnough() {
    try {
      const cached = sessionStorage.getItem(SESSION_CACHE_KEY);
      if (!cached) return false;
      const parsed = JSON.parse(cached);
      if (Date.now() - parsed.fetched_at > SESSION_CACHE_TTL_MS) return false;
      state.reviewsByBucket = parsed.reviewsByBucket || {};
      state.reviewersByBucket = parsed.reviewersByBucket || {};
      state.staleByBucket = parsed.staleByBucket || {};
      return true;
    } catch (_) {
      return false;
    }
  }

  function writeSessionCache() {
    try {
      sessionStorage.setItem(SESSION_CACHE_KEY, JSON.stringify({
        fetched_at: Date.now(),
        reviewsByBucket: state.reviewsByBucket,
        reviewersByBucket: state.reviewersByBucket,
        staleByBucket: state.staleByBucket,
      }));
    } catch (_) { /* quota exceeded — ignore */ }
  }

  function updateRateLimitFromHeaders(headers) {
    const limit = headers.get('X-RateLimit-Limit');
    const remaining = headers.get('X-RateLimit-Remaining');
    const reset = headers.get('X-RateLimit-Reset');
    if (limit) state.rateLimit.limit = parseInt(limit, 10);
    if (remaining != null) state.rateLimit.remaining = parseInt(remaining, 10);
    if (reset) state.rateLimit.reset = parseInt(reset, 10);
  }

  async function fetchReviews({ force = false } = {}) {
    if (!force && cachedFreshEnough()) {
      console.log('[audit] sessionStorage cache fresh, skipping fetch');
      return;
    }

    // Cross-tab lock — applies to BOTH the polling path and the
    // manual-refresh (force=true) path. Even when the user explicitly
    // clicks refresh, if another tab is mid-poll there's no point
    // burning a duplicate API call: the BroadcastChannel will deliver
    // the in-flight poll's results within seconds. Behavior auditor #12.
    //
    // The lock is best-effort: localStorage isn't transactional, but
    // claiming the lock as the very first side effect (before any
    // network call or even the freshness check) narrows the race
    // window to a single setItem/getItem pair on the same thread.
    const lastFetchAt = parseInt(localStorage.getItem(FETCH_LOCK_KEY) || '0', 10);
    if (Date.now() - lastFetchAt < LOCK_WINDOW_MS) {
      console.log('[audit] cross-tab lock active, skipping fetch (force=' + force + ')');
      return;
    }
    localStorage.setItem(FETCH_LOCK_KEY, String(Date.now()));

    // Abort any prior in-flight fetchReviews so a manual refresh during
    // a slow poll cannot race the polling fan-out into state mutation.
    if (state.fetchAbortController) {
      state.fetchAbortController.abort();
    }
    const ctrl = new AbortController();
    state.fetchAbortController = ctrl;

    const url = 'https://api.github.com/repos/' + GH_REPO + '/contents/' + encodeURI(REVIEWS_PATH);

    let res;
    try {
      res = await fetch(url, {
        headers: { Accept: 'application/vnd.github.v3+json' },
        signal: ctrl.signal,
      });
    } catch (err) {
      if (err && err.name === 'AbortError') return;
      console.warn('audit: GitHub Contents API unreachable', err);
      return;
    }

    updateRateLimitFromHeaders(res.headers);

    if (res.status === 403) {
      console.warn('audit: GitHub API rate-limited (403)');
      return;
    }
    if (res.status === 429) {
      // Secondary/burst rate limit. Honour Retry-After: GitHub may return
      // either a delta-seconds value or an HTTP-date.
      const retryAfter = res.headers.get('Retry-After');
      let waitMs = 60_000;
      if (retryAfter) {
        const seconds = parseInt(retryAfter, 10);
        if (Number.isFinite(seconds)) {
          waitMs = seconds * 1000;
        } else {
          const dateMs = Date.parse(retryAfter);
          if (Number.isFinite(dateMs)) waitMs = Math.max(0, dateMs - Date.now());
        }
      }
      state.rateLimit.reset = Math.floor((Date.now() + waitMs) / 1000);
      console.warn('audit: GitHub API burst-limited (429), backing off',
        Math.round(waitMs / 1000), 's');
      return;
    }
    if (res.status === 404) {
      // Folder doesn't exist yet — no reviews submitted
      state.reviewsByBucket = {};
      state.reviewersByBucket = {};
      state.staleByBucket = {};
      writeSessionCache();
      return;
    }
    if (!res.ok) {
      console.warn('audit: GitHub Contents API returned', res.status);
      return;
    }

    let listing;
    try {
      listing = await res.json();
    } catch (err) {
      // A 200 with a non-JSON body (CDN error page during an outage)
      // would otherwise throw out of this async setTimeout callback,
      // killing the poll loop permanently for the session.
      console.warn('audit: GitHub Contents API returned non-JSON body', err);
      return;
    }
    if (!Array.isArray(listing)) {
      console.warn('audit: unexpected listing shape', listing);
      return;
    }

    const reviewFiles = listing.filter((entry) => entry.type === 'file' && /\.json$/.test(entry.name));
    const newReviews = {};
    const newReviewers = {};
    const newStale = {};

    await Promise.all(reviewFiles.map(async (entry) => {
      const parsed = Lib.parseBucketFilename(entry.name);
      if (!parsed) return;
      const key = parsed.group + '-' + String(parsed.index).padStart(2, '0');
      try {
        const fileRes = await fetch(entry.download_url, { signal: ctrl.signal });
        if (!fileRes.ok) {
          console.warn('audit: failed to download', entry.name, '— HTTP', fileRes.status);
          return;
        }
        const fileJson = await fileRes.json();
        const validation = Lib.validateReviewFile(fileJson, state.flagged, parsed.group, parsed.index);
        if (validation.status === 'valid') {
          newReviews[key] = validation.decisions;
          newReviewers[key] = validation.reviewer || '';
        } else if (validation.status === 'stale') {
          newReviews[key] = validation.decisions;
          newReviewers[key] = validation.reviewer || '';
          newStale[key] = true;
        } else {
          console.warn('audit: ignored', entry.name, '—', validation.reason);
        }
      } catch (err) {
        if (err && err.name === 'AbortError') return;
        console.warn('audit: failed to fetch', entry.name, err);
      }
    }));

    // If a newer fetchReviews started while this one was awaiting, the
    // controller has been replaced — drop the partial result rather than
    // letting it overwrite the newer state.
    if (state.fetchAbortController !== ctrl) return;

    // DD: compute toast diff before swapping in the new state.
    // New ✓ buckets (present in newReviews & complete & not stale, absent in old)
    // and bucket regressions (present in old, absent or shrunk in new) get a toast.
    const oldKeys = Object.keys(state.reviewsByBucket || {});
    const newKeys = Object.keys(newReviews);
    const newlyDoneKeys = [];
    for (const k of newKeys) {
      if (!state.reviewsByBucket[k] && !newStale[k]) {
        newlyDoneKeys.push(k);
      }
    }
    const removedKeys = [];
    for (const k of oldKeys) {
      if (!newReviews[k]) removedKeys.push(k);
    }

    state.reviewsByBucket = newReviews;
    state.reviewersByBucket = newReviewers;
    state.staleByBucket = newStale;
    writeSessionCache();
    broadcastUpdate();
    renderStatusStrip();
    renderSidebar();

    // Emit toasts for changes detected during this poll. Skipped during the
    // initial load (oldKeys empty) so we don't spam on first visit.
    if (oldKeys.length > 0) {
      for (const k of newlyDoneKeys) {
        showToast('✓ nouveau bucket terminé · ' + k, 'success');
      }
      for (const k of removedKeys) {
        showToast('↩ bucket retiré · ' + k, 'warn');
      }
    }
  }

  // ---- BroadcastChannel for cross-tab dedup (DD #54) ------------------

  function setupBroadcastChannel() {
    if (typeof BroadcastChannel === 'undefined') {
      state.broadcastChannel = null;
      return;
    }
    const bc = new BroadcastChannel('audit-refresh');
    bc.onmessage = (ev) => {
      if (ev.data && ev.data.type === 'reviews-updated') {
        state.reviewsByBucket = ev.data.reviewsByBucket || {};
        state.reviewersByBucket = ev.data.reviewersByBucket || {};
        state.staleByBucket = ev.data.staleByBucket || {};
        renderStatusStrip();
        renderSidebar();
      }
    };
    state.broadcastChannel = bc;
  }

  function broadcastUpdate() {
    if (state.broadcastChannel) {
      state.broadcastChannel.postMessage({
        type: 'reviews-updated',
        reviewsByBucket: state.reviewsByBucket,
        reviewersByBucket: state.reviewersByBucket,
        staleByBucket: state.staleByBucket,
      });
    }
  }

  // ---- Background poll ------------------------------------------------

  function computePollDelay() {
    // Adaptive backoff based on rate-limit headroom (DD #20):
    //   ≥20 → normal 15 min, 10-19 → 30 min, <10 → 60 min, 0 → wait for reset
    let base = POLL_INTERVAL_NORMAL_MS;
    let tier = 'normal';
    const remaining = state.rateLimit.remaining;
    if (remaining != null) {
      if (remaining === 0 && state.rateLimit.reset) {
        const resetMs = state.rateLimit.reset * 1000;
        const wait = Math.max(60_000, resetMs - Date.now() + 30_000);
        // Log the exhaustion explicitly so an instructor reading the
        // browser console knows why the bucket list stopped updating.
        console.log(
          '[audit] rate-limit exhausted — pausing poll for',
          Math.round(wait / 60000), 'min until reset'
        );
        return wait;
      }
      if (remaining < RATE_LIMIT_RED_THRESHOLD) {
        base = POLL_INTERVAL_RED_MS; tier = 'red';
      } else if (remaining < RATE_LIMIT_AMBER_THRESHOLD) {
        base = POLL_INTERVAL_AMBER_MS; tier = 'amber';
      }
    }
    // Log only on tier change to avoid spamming the console.
    if (state.rateLimit.lastTier !== tier) {
      console.log(
        '[audit] rate-limit tier:', tier,
        '(remaining=' + (remaining != null ? remaining : '?') + ')',
        '— next poll in', Math.round(base / 60000), 'min'
      );
      state.rateLimit.lastTier = tier;
    }
    const jitter = (Math.random() * 2 - 1) * POLL_JITTER_MS;
    return base + jitter;
  }

  function schedulePoll() {
    if (state.pollTimer) clearTimeout(state.pollTimer);
    const delay = computePollDelay();
    state.pollTimer = setTimeout(async () => {
      if (document.visibilityState === 'visible') {
        try {
          await fetchReviews();
        } catch (err) {
          // Last-line guard: any unexpected throw inside fetchReviews
          // would otherwise reject this async setTimeout callback and
          // permanently kill the poll loop for the rest of the session.
          console.error('[audit] poll iteration crashed (loop continuing)', err);
        }
      }
      schedulePoll();
    }, delay);
  }

  // ---- Toasts (#3 — bottom-right notifications) -----------------------

  function showToast(message, kind) {
    if (state.suppressToasts) return;
    const container = $('toast-container');
    if (!container) return;
    const toast = document.createElement('div');
    toast.className = 'toast toast--' + (kind || 'info');
    toast.textContent = message;
    const cancelBtn = document.createElement('button');
    cancelBtn.textContent = 'Annuler';
    cancelBtn.className = 'toast__cancel';
    cancelBtn.addEventListener('click', () => {
      state.suppressToasts = true;
      try { sessionStorage.setItem(TOASTS_SUPPRESSED_KEY, '1'); } catch (_) {}
      container.querySelectorAll('.toast').forEach((t) => t.remove());
    });
    toast.appendChild(cancelBtn);
    container.appendChild(toast);
    setTimeout(() => {
      toast.classList.add('toast--fading');
      setTimeout(() => toast.remove(), 300);
    }, 6000);
  }

  function setupVisibilityHandler() {
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') {
        // Immediate refresh on tab reveal
        fetchReviews().catch((err) => console.warn(err));
      }
    });
  }

  // ---- Status strip ---------------------------------------------------

  function renderStatusStrip() {
    if (!state.flagged) return;
    // #6: read total_sites_audited from flagged.json metadata field, fall back
    // to 2890 only if the field is missing (backwards compatibility with older
    // audit runs that pre-date this field).
    const totalSites = state.flagged.total_sites_audited || 2890;
    const total = (state.flagged.groups || []).reduce((acc, g) => acc + (g.count || 0), 0);
    const coherent = totalSites - total;

    const progress = Lib.countProgress(state.flagged, state.reviewsByBucket);
    const text = Lib.formatStatusStrip(totalSites, coherent, total, progress.reviewed);

    // #5: rate-limit color state.
    let rateText = '';
    let rateClass = '';
    if (state.rateLimit.remaining != null) {
      const remaining = state.rateLimit.remaining;
      const minToReset = state.rateLimit.reset
        ? Math.max(0, Math.round((state.rateLimit.reset * 1000 - Date.now()) / 60000))
        : null;
      rateText = ' · ↻ ' + remaining + '/' + state.rateLimit.limit;
      if (minToReset != null) rateText += ' · ⟳ ' + minToReset + ' min';
      if (remaining === 0) rateClass = ' rate-limit--exhausted';
      else if (remaining < RATE_LIMIT_RED_THRESHOLD) rateClass = ' rate-limit--red';
      else if (remaining < RATE_LIMIT_AMBER_THRESHOLD) rateClass = ' rate-limit--amber';
      else rateClass = ' rate-limit--neutral';
    }
    const rateHtml = rateText
      ? '<span class="rate-limit' + rateClass + '">' + escapeHTML(rateText) + '</span>'
      : '';
    const refreshDisabled = state.rateLimit.remaining === 0 ? ' disabled' : '';
    const refreshLabel = state.rateLimit.remaining === 0 ? 'Limite atteinte' : 'Actualiser';
    $('status-strip').innerHTML =
      escapeHTML(text) + rateHtml +
      ' <button class="refresh-btn" id="refresh-btn" type="button"' + refreshDisabled + '>' +
      escapeHTML(refreshLabel) + '</button>';
    const refreshBtn = $('refresh-btn');
    if (refreshBtn && !refreshBtn.disabled) {
      refreshBtn.addEventListener('click', async () => {
        sessionStorage.removeItem(SESSION_CACHE_KEY);
        localStorage.removeItem(FETCH_LOCK_KEY);
        await fetchReviews({ force: true });
      });
    }
  }

  // ---- Sidebar --------------------------------------------------------

  // _buildBucketState computes the icon/class/reviewer for a single bucket.
  // Shared between the first-render and the keyed-update paths.
  function _buildBucketState(group, b) {
    const key = group.id + '-' + String(b.index).padStart(2, '0');
    const decisions = state.reviewsByBucket[key];
    const reviewer = state.reviewersByBucket[key];
    const isStale = state.staleByBucket[key];
    const isDone = decisions && decisions.length === b.items.length && !isStale;
    const hasDraft = !isDone && hasLocalDraft(group.id, b.index, b.items);

    let icon = '○';
    let cls = '';
    if (isDone)      { icon = '✓'; cls = ' bucket--done'; }
    else if (isStale) { icon = '⚠'; cls = ' bucket--stale'; }
    else if (hasDraft) { icon = '●'; cls = ' bucket--draft'; }

    const isCurrent = (state.currentGroup === group.id && state.currentBucketIndex === b.index);
    return { key, icon, cls, isDone, isStale, reviewer, isCurrent };
  }

  function renderSidebar() {
    if (!state.flagged) return;
    const nav = $('bucket-nav');

    // --- Keyed update path (subsequent renders) --------------------------
    // After the first innerHTML build, all <li> elements carry a data-key
    // attribute. Walk them and update className/icon/reviewer text instead
    // of tearing down and rebuilding ~50-100 DOM nodes + re-attaching
    // reimport listeners. This drops the per-render localStorage hits from
    // ~2500 to ~0 on steady state (draft cache).
    if (state._sidebarRendered) {
      for (const group of state.flagged.groups || []) {
        const buckets = state.bucketsByGroup[group.id] || [];
        for (const b of buckets) {
          const bs = _buildBucketState(group, b);
          const li = nav.querySelector('[data-key="' + bs.key + '"]');
          if (!li) continue;

          // Update className in place
          li.className = 'bucket' + bs.cls + (bs.isCurrent ? ' bucket--current' : '');

          // Update icon
          const iconSpan = li.querySelector('.bucket__icon');
          if (iconSpan) iconSpan.textContent = bs.icon;

          // Update reviewer suffix
          let revSpan = li.querySelector('.bucket__reviewer');
          if (bs.isDone && bs.reviewer) {
            if (!revSpan) {
              revSpan = document.createElement('span');
              revSpan.className = 'bucket__reviewer';
              const link = li.querySelector('.bucket__link');
              if (link) link.appendChild(revSpan);
            }
            revSpan.textContent = bs.reviewer;
          } else if (revSpan) {
            revSpan.remove();
          }

          // Update reimport button presence
          let reimportBtn = li.querySelector('.bucket__reimport');
          if (bs.isStale && !reimportBtn) {
            reimportBtn = document.createElement('button');
            reimportBtn.className = 'bucket__reimport';
            reimportBtn.setAttribute('data-key', bs.key);
            reimportBtn.type = 'button';
            reimportBtn.title = 'Réimporter les décisions de cette revue obsolète vers le nouvel audit';
            reimportBtn.textContent = 'Réimporter';
            li.appendChild(reimportBtn);
          } else if (!bs.isStale && reimportBtn) {
            reimportBtn.remove();
          }
        }
      }
      return;
    }

    // --- First-render path (full innerHTML build) -------------------------
    const html = [];
    for (const group of state.flagged.groups || []) {
      const buckets = state.bucketsByGroup[group.id] || [];
      const accent = group.id === 'reserves' ? 'rust' : group.id === 'grand' ? 'ochre' : 'lead';
      html.push('<section class="group group--' + accent + '">');
      html.push('<h2 class="group__title">' + escapeHTML(group.label || group.id) +
                ' <span class="group__count">' + safeNumber(group.count, '0') + '</span></h2>');
      html.push('<ul class="bucket-list">');
      for (const b of buckets) {
        const bs = _buildBucketState(group, b);
        const label = 'bucket-' + String(b.index).padStart(2, '0') + ' · ' + safeNumber(b.items.length, '0') + ' sites';
        const reviewerSuffix = (bs.isDone && bs.reviewer) ? ' <span class="bucket__reviewer">' + escapeHTML(bs.reviewer) + '</span>' : '';
        const staleSuffix = bs.isStale
          ? ' <button class="bucket__reimport" data-key="' + escapeHTML(bs.key) +
            '" type="button" title="Réimporter les décisions de cette revue obsolète vers le nouvel audit">Réimporter</button>'
          : '';
        html.push(
          '<li class="bucket' + bs.cls + (bs.isCurrent ? ' bucket--current' : '') + '" data-key="' + escapeHTML(bs.key) + '">' +
            '<a href="#' + group.id + '-' + String(b.index).padStart(2, '0') + '/1" class="bucket__link">' +
              '<span class="bucket__icon">' + bs.icon + '</span>' +
              '<span class="bucket__label">' + escapeHTML(label) + '</span>' +
              reviewerSuffix +
            '</a>' +
            staleSuffix +
          '</li>'
        );
      }
      html.push('</ul></section>');
    }
    nav.innerHTML = html.join('');
    state._sidebarRendered = true;

    // Delegated handler for reimport buttons — ONE listener on the nav
    // element instead of per-li. The handler survives subsequent keyed
    // updates because it's on the parent, not on the (stable) child nodes.
    nav.addEventListener('click', (e) => {
      const btn = e.target.closest('.bucket__reimport');
      if (!btn) return;
      e.preventDefault();
      e.stopPropagation();
      const key = btn.getAttribute('data-key');
      if (key) reimportStaleBucket(key);
    });
  }

  // ---- Stale-review reimport (#2) -------------------------------------

  /**
   * Map decisions from a committed-but-stale review file (one whose
   * flagged_hash doesn't match the current audit) onto the current
   * buckets, by id_icpe lookup.
   *
   * For each decision in the stale file:
   *   - if its id_icpe is in the current bucket → save as a draft for
   *     the matching item position
   *   - if its id_icpe still exists in the audit but in a different bucket →
   *     also save it (the new bucket will then surface as "in progress")
   *   - if its id_icpe no longer exists in the audit at all → log to
   *     console for manual archival, do nothing
   */
  function reimportStaleBucket(staleKey) {
    const decisions = state.reviewsByBucket[staleKey];
    if (!decisions || !decisions.length) {
      showToast('Pas de décisions à réimporter pour ' + staleKey, 'warn');
      return;
    }
    if (!state.flagged) return;

    // Build an id_icpe → {group, bucketIndex, itemIndex} index from the
    // CURRENT flagged.json so we can place each stale decision into its
    // new bucket position.
    const idToPosition = new Map();
    for (const group of state.flagged.groups || []) {
      const buckets = state.bucketsByGroup[group.id] || [];
      for (const b of buckets) {
        b.items.forEach((item, itemIndex) => {
          idToPosition.set(item.id_icpe, {
            group: group.id,
            bucketIndex: b.index,
            itemIndex: itemIndex,
          });
        });
      }
    }

    let imported = 0;
    let orphaned = 0;
    const orphanedIds = [];
    for (const dec of decisions) {
      const pos = idToPosition.get(dec.id_icpe);
      if (!pos) {
        orphaned++;
        orphanedIds.push(dec.id_icpe);
        continue;
      }
      // Stash as a localStorage draft at the new position
      const k = draftKey(pos.group, pos.bucketIndex, pos.itemIndex);
      const draft = {
        verdict: dec.verdict,
        note: dec.note || '',
        pertinent_enquete: !!dec.pertinent_enquete,
      };
      if (dec.manual_lat != null && dec.manual_lon != null) {
        draft.manual_lat = dec.manual_lat;
        draft.manual_lon = dec.manual_lon;
      }
      try {
        localStorage.setItem(k, JSON.stringify(draft));
        imported++;
      } catch (err) {
        console.warn('reimport: localStorage write failed for', k, err);
      }
    }

    if (orphanedIds.length) {
      console.warn(
        '[reimport] ' + orphaned + ' décision(s) orpheline(s) — ids absents du nouvel audit, ' +
        'à archiver manuellement :', orphanedIds
      );
    }

    // Invalidate draft cache for all affected buckets so the next
    // renderSidebar recomputes hasLocalDraft for them.
    state._draftCache.clear();

    showToast(
      'Réimporté ' + imported + ' décisions depuis ' + staleKey +
        (orphaned ? ' · ' + orphaned + ' orpheline(s) en console' : ''),
      'success'
    );

    // Re-render the sidebar so the new draft state appears immediately.
    renderSidebar();
  }

  function _bucketCacheKey(group, bucketIndex) {
    return group + '-' + String(bucketIndex).padStart(2, '0');
  }

  function hasLocalDraft(group, bucketIndex, items) {
    const cacheKey = _bucketCacheKey(group, bucketIndex);
    const cached = state._draftCache.get(cacheKey);
    if (cached !== undefined) return cached;
    let found = false;
    for (let i = 0; i < items.length; i++) {
      const key = draftKey(group, bucketIndex, i);
      if (localStorage.getItem(key)) { found = true; break; }
    }
    state._draftCache.set(cacheKey, found);
    return found;
  }

  // Invalidate the cached hasLocalDraft result for the bucket that
  // contains a given (group, bucketIndex). The next renderSidebar will
  // recompute by walking that one bucket. Called from saveDraft /
  // saveManualToDraft / reimportStaleBucket — anywhere a draft mutates.
  function invalidateDraftCache(group, bucketIndex) {
    state._draftCache.delete(_bucketCacheKey(group, bucketIndex));
  }

  function toggleSidebar() {
    document.body.classList.toggle('sidebar-collapsed');
  }

  // ---- Hash routing ---------------------------------------------------

  function hydrateFromHash() {
    const hash = location.hash.slice(1);
    if (!hash) {
      exitToEmpty();
      return;
    }
    // Format: #reserves-01/7
    const m = /^([a-z]+-\d+)\/(\d+)$/.exec(hash);
    if (!m) return;
    const bucketKey = m[1];
    const itemIdx = parseInt(m[2], 10) - 1;
    const parsed = Lib.parseBucketFilename('bucket-' + bucketKey + '.json');
    if (!parsed) return;

    state.currentGroup = parsed.group;
    state.currentBucketIndex = parsed.index;
    state.currentItemIndex = itemIdx;
    renderReview();
  }

  function setHash() {
    const key = state.currentGroup + '-' + String(state.currentBucketIndex).padStart(2, '0');
    const itemNum = (state.currentItemIndex || 0) + 1;
    const newHash = '#' + key + '/' + itemNum;
    if (location.hash !== newHash) {
      history.replaceState(null, '', newHash);
    }
  }

  function exitToEmpty() {
    state.currentGroup = null;
    state.currentBucketIndex = null;
    state.currentItemIndex = null;
    history.replaceState(null, '', location.pathname);
    $('empty-state').hidden = false;
    $('review').hidden = true;
    $('export-drawer').hidden = true;
    renderEmptyState();
    renderSidebar();
  }

  function renderEmptyState() {
    if (!state.flagged) return;
    const progress = Lib.countProgress(state.flagged, state.reviewsByBucket);
    const html = ['<h2>Audit des coordonnées</h2>'];
    // Coerce numeric counts through safeNumber so a malformed flagged.json
    // can never inject HTML via these interpolations.
    html.push('<p>' + safeNumber(progress.total, '0') + ' sites à vérifier au total. ' +
              safeNumber(progress.reviewed, '0') + ' déjà revus. ' +
              safeNumber(progress.remaining, '0') + ' restants.</p>');
    if (progress.perGroup.reserves && progress.perGroup.reserves.total > 0) {
      const r = progress.perGroup.reserves;
      html.push('<div class="callout callout--rust">');
      html.push('<strong>' + safeNumber(r.total, '0') + ' sites dans la zone critique</strong>');
      html.push('<p>Désaccord stored vs geocoded sur l\'appartenance à une réserve naturelle. À traiter en priorité.</p>');
      html.push('</div>');
    }
    html.push('<p class="empty-hint">Choisis un bucket dans la liste à gauche pour commencer.</p>');
    $('empty-state').innerHTML = html.join('');
  }

  // ---- Review rendering -----------------------------------------------

  function getCurrentBucket() {
    if (!state.currentGroup || state.currentBucketIndex == null) return null;
    const buckets = state.bucketsByGroup[state.currentGroup] || [];
    return buckets[state.currentBucketIndex - 1] || null;
  }

  function getCurrentItem() {
    const bucket = getCurrentBucket();
    if (!bucket) return null;
    return bucket.items[state.currentItemIndex] || null;
  }

  function renderReview() {
    const bucket = getCurrentBucket();
    if (!bucket) {
      exitToEmpty();
      return;
    }
    // On mobile, auto-close the sidebar when a bucket is selected
    // so the review panel is visible.
    if (window.matchMedia('(max-width: 719px)').matches) {
      document.body.classList.add('sidebar-collapsed');
    }
    if (state.currentItemIndex < 0) state.currentItemIndex = 0;
    if (state.currentItemIndex >= bucket.items.length) state.currentItemIndex = bucket.items.length - 1;

    const item = bucket.items[state.currentItemIndex];
    if (!item) return;

    setHash();
    $('empty-state').hidden = true;
    $('review').hidden = false;

    // Breadcrumb + progress
    const groupLabel = (state.flagged.groups.find((g) => g.id === state.currentGroup) || {}).label || state.currentGroup;
    $('review-breadcrumb').innerHTML =
      '<a href="#" class="breadcrumb-home">Audit</a> › ' +
      escapeHTML(groupLabel) +
      ' › bucket-' + String(state.currentBucketIndex).padStart(2, '0');
    $('review-progress').textContent = 'site ' + (state.currentItemIndex + 1) + ' / ' + bucket.items.length;

    // Identity
    const idHtml = [];
    idHtml.push('<h3>Identité</h3>');
    idHtml.push('<p class="identity__name">' + escapeHTML(item.nom_complet || '') + '</p>');
    idHtml.push('<dl class="kv">');
    idHtml.push('<dt>id ICPE</dt><dd>' + escapeHTML(item.id_icpe || '') + '</dd>');
    if (item.siret) idHtml.push('<dt>SIRET</dt><dd>' + escapeHTML(item.siret) + '</dd>');
    if (item.regime_icpe) idHtml.push('<dt>Régime</dt><dd>' + escapeHTML(item.regime_icpe) + '</dd>');
    if (item.categorie_seveso) idHtml.push('<dt>Seveso</dt><dd>' + escapeHTML(item.categorie_seveso) + '</dd>');
    if (item.priorite_nationale) idHtml.push('<dt>Priorité nationale</dt><dd>oui</dd>');
    if (item.directive_ied) idHtml.push('<dt>IED</dt><dd>oui</dd>');
    idHtml.push('</dl>');
    if (item.url_fiche_georisques) {
      idHtml.push('<p><a href="' + escapeHTML(item.url_fiche_georisques) + '" target="_blank" rel="noopener">Fiche Géorisques ↗</a></p>');
    }
    $('info-identity').innerHTML = idHtml.join('');

    // Stored
    // Numeric fields from flagged.json must be coerced through safeNumber
    // before HTML interpolation. The Python pipeline always produces
    // floats, but if it ever shipped a string here, the previous code
    // would have inserted it raw into the DOM. safeNumber returns ''
    // (or the fallback) for anything that isn't a finite number.
    const storedHtml = [
      '<h3>Coordonnées enregistrées</h3>',
      '<p class="address">' + escapeHTML(item.adresse || '(adresse vide)') + '<br>' +
        escapeHTML(item.code_postal || '') + ' ' + escapeHTML(item.commune || '') + '</p>',
      '<p class="coords">' + safeNumber(item.stored_lat, '?') + ', ' + safeNumber(item.stored_lon, '?') + '</p>',
    ];
    $('info-stored').innerHTML = storedHtml.join('');

    // Geocoded
    const geoHtml = [
      '<h3>Adresse géocodée</h3>',
      '<p class="address">' + escapeHTML(item.geocoded_label || '(non géocodé)') + '</p>',
      item.geocoded_lat != null
        ? '<p class="coords">' + safeNumber(item.geocoded_lat, '?') + ', ' + safeNumber(item.geocoded_lon, '?') + '</p>'
        : '',
      item.forward_distance_m != null
        ? '<p class="distance">distance : <strong>' + safeNumber(Math.round(Number(item.forward_distance_m)), '?') + ' m</strong> · type : ' + escapeHTML(item.geocoded_type || '?') + '</p>'
        : '',
    ];
    $('info-geocoded').innerHTML = geoHtml.join('');

    // Reverse
    const revHtml = [
      '<h3>Adresse au point enregistré (reverse)</h3>',
      '<p>' + escapeHTML(item.reverse_label || '(non disponible)') + '</p>',
    ];
    $('info-reverse').innerHTML = revHtml.join('');

    // Signals
    const signalsHtml = ['<h3>Signaux audit</h3>', '<ul class="signals">'];
    signalsHtml.push('<li><span class="signal__label">classe</span>: <code>' + escapeHTML(item.audit_class || '') + '</code></li>');
    if (item.reserve_ambiguous) signalsHtml.push('<li class="signal--rust">⚠ reserve_ambiguous</li>');
    if (item.reserve_boundary_proximity) signalsHtml.push('<li class="signal--ochre">▲ reserve_boundary_proximity</li>');
    if (item.stored_in_reserve && item.stored_in_reserve !== 'none') {
      signalsHtml.push('<li>stored ∈ <code>' + escapeHTML(item.stored_in_reserve) + '</code></li>');
    }
    if (item.geocoded_in_reserve && item.geocoded_in_reserve !== 'none') {
      signalsHtml.push('<li>geocoded ∈ <code>' + escapeHTML(item.geocoded_in_reserve) + '</code></li>');
    }
    signalsHtml.push('</ul>');
    $('info-signals').innerHTML = signalsHtml.join('');

    // Mini-map
    renderMiniMap(item);

    // Form state
    loadDraftIntoForm(item);

    renderSidebar();
  }

  // ---- Mini-map -------------------------------------------------------

  function ensureMiniMap() {
    if (state.miniMap) return state.miniMap;
    const planLayer = L.tileLayer(PLAN_TILE_URL, { attribution: PLAN_ATTRIBUTION, maxZoom: 19 });
    const orthoLayer = L.tileLayer(ORTHO_TILE_URL, { attribution: ORTHO_ATTRIBUTION, maxZoom: 19 });
    const map = L.map('minimap', {
      center: [44.84, -0.58],
      zoom: 12,
      layers: [planLayer],
      zoomControl: true,
    });
    L.control.layers({ 'Plan': planLayer, 'Ortho': orthoLayer }).addTo(map);
    L.control.scale({ imperial: false }).addTo(map);
    state.miniMap = map;
    return map;
  }

  function renderMiniMap(item) {
    const map = ensureMiniMap();
    // Clear dynamic layers
    if (state.storedMarker) { map.removeLayer(state.storedMarker); state.storedMarker = null; }
    if (state.geocodedMarker) { map.removeLayer(state.geocodedMarker); state.geocodedMarker = null; }
    if (state.distanceLine) { map.removeLayer(state.distanceLine); state.distanceLine = null; }
    if (state.distanceLabel) { map.removeLayer(state.distanceLabel); state.distanceLabel = null; }
    if (state.manualMarker) { map.removeLayer(state.manualMarker); state.manualMarker = null; }

    const stored = (item.stored_lat != null && item.stored_lon != null) ? [item.stored_lat, item.stored_lon] : null;
    const geocoded = (item.geocoded_lat != null && item.geocoded_lon != null) ? [item.geocoded_lat, item.geocoded_lon] : null;

    if (stored) {
      state.storedMarker = L.circleMarker(stored, {
        radius: 8, color: CSS.ink, weight: 2,
        fillColor: CSS.rust, fillOpacity: 0.95,
      }).addTo(map).bindTooltip('Coordonnées enregistrées', { direction: 'top' });
    }
    if (geocoded) {
      state.geocodedMarker = L.circleMarker(geocoded, {
        radius: 8, color: CSS.ochre, weight: 3,
        fillColor: CSS.paper, fillOpacity: 0.5,
      }).addTo(map).bindTooltip('Adresse géocodée (BAN/OpenCage)', { direction: 'top' });
    }
    if (stored && geocoded) {
      state.distanceLine = L.polyline([stored, geocoded], {
        color: CSS.lead, weight: 1.5, dashArray: '4,6',
      }).addTo(map);
      const midLat = (stored[0] + geocoded[0]) / 2;
      const midLon = (stored[1] + geocoded[1]) / 2;
      const d = item.forward_distance_m != null ? Math.round(item.forward_distance_m) + ' m' : '';
      if (d) {
        state.distanceLabel = L.marker([midLat, midLon], {
          icon: L.divIcon({
            className: 'distance-label',
            html: '<span>' + d + '</span>',
            iconSize: [0, 0],
          }),
        }).addTo(map);
      }
      map.fitBounds(L.latLngBounds([stored, geocoded]), { padding: [60, 60], maxZoom: 18 });
    } else if (stored) {
      map.setView(stored, 15);
    } else if (geocoded) {
      map.setView(geocoded, 15);
    }

    // Manual marker (restore from draft if applicable)
    const itemKey = draftKey(state.currentGroup, state.currentBucketIndex, state.currentItemIndex);
    const draftRaw = localStorage.getItem(itemKey);
    if (draftRaw) {
      try {
        const draft = JSON.parse(draftRaw);
        if (draft.verdict === 'placer_manuellement' && draft.manual_lat != null && draft.manual_lon != null) {
          showManualMarker(draft.manual_lat, draft.manual_lon);
        }
      } catch (_) { /* ignore */ }
    }
  }

  function showManualMarker(lat, lon) {
    const map = state.miniMap;
    if (!map) return;
    if (state.manualMarker) {
      state.manualMarker.setLatLng([lat, lon]);
    } else {
      state.manualMarker = L.marker([lat, lon], {
        draggable: true,
        title: 'Point manuel — déplace pour ajuster',
      }).addTo(map);
      state.manualMarker.on('dragend', () => {
        const pos = state.manualMarker.getLatLng();
        saveManualToDraft(pos.lat, pos.lng);
      });
    }
  }

  function enterManualPlacementMode() {
    const map = state.miniMap;
    if (!map) return;
    map.getContainer().style.cursor = 'crosshair';
    $('minimap-hint').hidden = false;
    const handler = (e) => {
      showManualMarker(e.latlng.lat, e.latlng.lng);
      saveManualToDraft(e.latlng.lat, e.latlng.lng);
      map.getContainer().style.cursor = '';
      $('minimap-hint').hidden = true;
      map.off('click', handler);
    };
    map.on('click', handler);
  }

  // ---- Keyboard manual placement (#1 — M key) -------------------------
  //
  // Press M to enter keyboard placement mode. A crosshair Leaflet marker
  // appears at the map center. Arrow keys move it (1 px tap, Shift+arrow
  // for 10 px steps). Enter confirms the position as the manual pin.
  // Escape cancels and removes the crosshair.

  const KB_PLACEMENT_STATE = {
    active: false,
    crosshair: null, // Leaflet marker
    keyHandler: null,
  };

  function enterKeyboardPlacementMode() {
    const map = state.miniMap;
    if (!map) return;
    if (KB_PLACEMENT_STATE.active) return;

    KB_PLACEMENT_STATE.active = true;
    $('minimap-hint').textContent =
      'Mode clavier : ↑↓←→ pour déplacer le réticule, Maj+flèche pour bouger plus vite, Entrée pour valider, Échap pour annuler.';
    $('minimap-hint').hidden = false;

    const center = map.getCenter();
    const crosshairIcon = L.divIcon({
      className: 'kb-crosshair',
      html: '<div class="kb-crosshair__inner">+</div>',
      iconSize: [28, 28],
      iconAnchor: [14, 14],
    });
    KB_PLACEMENT_STATE.crosshair = L.marker(center, {
      icon: crosshairIcon,
      keyboard: false,
      interactive: false,
    }).addTo(map);

    KB_PLACEMENT_STATE.keyHandler = (ev) => {
      // Only catch keys in placement mode; let other handlers run otherwise.
      if (!KB_PLACEMENT_STATE.active) return;

      if (ev.key === 'Escape') {
        cancelKeyboardPlacementMode();
        ev.preventDefault();
        ev.stopPropagation();
        return;
      }
      if (ev.key === 'Enter') {
        const pos = KB_PLACEMENT_STATE.crosshair.getLatLng();
        showManualMarker(pos.lat, pos.lng);
        saveManualToDraft(pos.lat, pos.lng);
        cancelKeyboardPlacementMode();
        ev.preventDefault();
        ev.stopPropagation();
        return;
      }
      if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(ev.key)) {
        const step = ev.shiftKey ? 10 : 1;
        const pos = KB_PLACEMENT_STATE.crosshair.getLatLng();
        const px = map.latLngToContainerPoint(pos);
        if (ev.key === 'ArrowUp') px.y -= step;
        else if (ev.key === 'ArrowDown') px.y += step;
        else if (ev.key === 'ArrowLeft') px.x -= step;
        else if (ev.key === 'ArrowRight') px.x += step;
        const newLatLng = map.containerPointToLatLng(px);
        KB_PLACEMENT_STATE.crosshair.setLatLng(newLatLng);
        ev.preventDefault();
        ev.stopPropagation();
      }
    };
    document.addEventListener('keydown', KB_PLACEMENT_STATE.keyHandler, true);
  }

  function cancelKeyboardPlacementMode() {
    if (!KB_PLACEMENT_STATE.active) return;
    KB_PLACEMENT_STATE.active = false;
    if (KB_PLACEMENT_STATE.crosshair && state.miniMap) {
      state.miniMap.removeLayer(KB_PLACEMENT_STATE.crosshair);
    }
    KB_PLACEMENT_STATE.crosshair = null;
    if (KB_PLACEMENT_STATE.keyHandler) {
      document.removeEventListener('keydown', KB_PLACEMENT_STATE.keyHandler, true);
    }
    KB_PLACEMENT_STATE.keyHandler = null;
    $('minimap-hint').hidden = true;
    $('minimap-hint').textContent = 'Clique sur la carte pour placer un point manuel.';
  }

  // ---- Form / draft handling -----------------------------------------

  function draftKey(group, bucketIndex, itemIndex) {
    if (!state.flagged) return '';
    return DRAFT_PREFIX + state.flagged.flagged_hash + ':' + group + '-' +
      String(bucketIndex).padStart(2, '0') + ':' + itemIndex;
  }

  function loadDraftIntoForm(item) {
    const key = draftKey(state.currentGroup, state.currentBucketIndex, state.currentItemIndex);
    const raw = localStorage.getItem(key);
    let draft = null;
    if (raw) {
      try { draft = JSON.parse(raw); } catch (_) { draft = null; }
    }
    document.querySelectorAll('input[name="verdict"]').forEach((r) => {
      r.checked = draft && draft.verdict === r.value;
    });
    $('note-field').value = (draft && draft.note) || '';
    $('pertinent-enquete').checked = !!(draft && draft.pertinent_enquete);
  }

  function readFormToDraft() {
    const verdictEl = document.querySelector('input[name="verdict"]:checked');
    return {
      verdict: verdictEl ? verdictEl.value : null,
      note: $('note-field').value || '',
      pertinent_enquete: $('pertinent-enquete').checked,
    };
  }

  function saveDraft() {
    const key = draftKey(state.currentGroup, state.currentBucketIndex, state.currentItemIndex);
    if (!key) return;
    const draft = readFormToDraft();
    if (!draft.verdict) {
      localStorage.removeItem(key);
      // Removal: invalidate the cache so the next render walks this
      // bucket once and discovers whether any other items still have
      // drafts. Cheaper than peeking at every key here.
      invalidateDraftCache(state.currentGroup, state.currentBucketIndex);
      return;
    }
    // Preserve manual coords if existing
    const raw = localStorage.getItem(key);
    if (raw) {
      try {
        const old = JSON.parse(raw);
        if (old.manual_lat != null && draft.verdict === 'placer_manuellement') {
          draft.manual_lat = old.manual_lat;
          draft.manual_lon = old.manual_lon;
        }
      } catch (_) { /* ignore */ }
    }
    try {
      localStorage.setItem(key, JSON.stringify(draft));
      // Set: this bucket definitely has a draft now. Skip the lazy
      // recompute and write the boolean directly.
      state._draftCache.set(_bucketCacheKey(state.currentGroup, state.currentBucketIndex), true);
    } catch (e) {
      console.warn('saveDraft: localStorage quota?', e);
    }
  }

  function saveManualToDraft(lat, lon) {
    const key = draftKey(state.currentGroup, state.currentBucketIndex, state.currentItemIndex);
    if (!key) return;
    const raw = localStorage.getItem(key);
    let draft = {};
    if (raw) {
      try { draft = JSON.parse(raw); } catch (_) { draft = {}; }
    }
    draft.verdict = 'placer_manuellement';
    draft.manual_lat = lat;
    draft.manual_lon = lon;
    if (!draft.note) draft.note = '';
    if (draft.pertinent_enquete == null) draft.pertinent_enquete = false;
    try {
      localStorage.setItem(key, JSON.stringify(draft));
      state._draftCache.set(_bucketCacheKey(state.currentGroup, state.currentBucketIndex), true);
    } catch (e) {
      console.warn('saveManualToDraft: localStorage write failed for', key, e);
    }
  }

  function onVerdictChange(e) {
    saveDraft();
    if (e.target.value === 'placer_manuellement') {
      // Enter placement mode if no manual point yet
      if (!state.manualMarker) {
        enterManualPlacementMode();
      }
    } else {
      // Remove manual marker if present
      if (state.manualMarker) {
        state.miniMap.removeLayer(state.manualMarker);
        state.manualMarker = null;
      }
    }
  }

  function onFormChange() {
    saveDraft();
  }

  // ---- Navigation -----------------------------------------------------

  function navigateItem(delta) {
    saveDraft();
    const bucket = getCurrentBucket();
    if (!bucket) return;
    const next = state.currentItemIndex + delta;
    if (next < 0) return;
    if (next >= bucket.items.length) {
      // Past the end — show export drawer if bucket is complete
      maybeShowExportDrawer();
      return;
    }
    state.currentItemIndex = next;
    renderReview();
  }

  function saveAndAdvance() {
    saveDraft();
    navigateItem(1);
  }

  function maybeShowExportDrawer() {
    const bucket = getCurrentBucket();
    if (!bucket) return;
    let allDone = true;
    const decisions = [];
    for (let i = 0; i < bucket.items.length; i++) {
      const key = draftKey(state.currentGroup, state.currentBucketIndex, i);
      const raw = localStorage.getItem(key);
      if (!raw) { allDone = false; continue; }
      try {
        const d = JSON.parse(raw);
        if (!d.verdict) { allDone = false; continue; }
        decisions.push({
          id_icpe: bucket.items[i].id_icpe,
          verdict: d.verdict,
          note: d.note || '',
          pertinent_enquete: !!d.pertinent_enquete,
          ...(d.manual_lat != null ? { manual_lat: d.manual_lat, manual_lon: d.manual_lon } : {}),
        });
      } catch (_) { allDone = false; }
    }
    if (allDone) {
      $('export-summary').textContent =
        bucket.items.length + ' verdicts prêts à exporter pour le bucket ' +
        state.currentGroup + '-' + String(state.currentBucketIndex).padStart(2, '0') + '.';
      $('export-filename').textContent = Lib.bucketFilename(state.currentGroup, state.currentBucketIndex);
      $('export-target-path').textContent =
        REVIEWS_PATH + '/' + Lib.bucketFilename(state.currentGroup, state.currentBucketIndex);
      $('export-drawer').hidden = false;
    } else {
      // Wrap around to first incomplete item
      for (let i = 0; i < bucket.items.length; i++) {
        const key = draftKey(state.currentGroup, state.currentBucketIndex, i);
        if (!localStorage.getItem(key)) {
          state.currentItemIndex = i;
          renderReview();
          return;
        }
      }
    }
  }

  function exportCurrentBucket() {
    if (!state.reviewerName) {
      const name = prompt('Tes initiales (pour identifier ta revue) :');
      if (!name) return;
      state.reviewerName = name;
      localStorage.setItem(KEY_REVIEWER, name);
    }

    const bucket = getCurrentBucket();
    if (!bucket) return;

    const decisions = [];
    for (let i = 0; i < bucket.items.length; i++) {
      const key = draftKey(state.currentGroup, state.currentBucketIndex, i);
      const raw = localStorage.getItem(key);
      if (!raw) continue;
      try {
        const d = JSON.parse(raw);
        decisions.push({
          id_icpe: bucket.items[i].id_icpe,
          verdict: d.verdict,
          note: d.note || '',
          pertinent_enquete: !!d.pertinent_enquete,
          ...(d.manual_lat != null ? { manual_lat: d.manual_lat, manual_lon: d.manual_lon } : {}),
        });
      } catch (_) { /* ignore */ }
    }

    const exportObj = Lib.composeExportFile(
      state.flagged.flagged_hash,
      state.flagged.audit_run_id,
      state.currentGroup,
      state.currentBucketIndex,
      state.reviewerName,
      decisions
    );

    const blob = new Blob([JSON.stringify(exportObj, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = Lib.bucketFilename(state.currentGroup, state.currentBucketIndex);
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  // ---- Keyboard shortcuts ---------------------------------------------

  function setupKeyboard() {
    document.addEventListener('keydown', (ev) => {
      // Don't intercept while typing in the note field or when guide is open
      if (ev.target && (ev.target.tagName === 'TEXTAREA' || ev.target.tagName === 'INPUT')) return;
      if (!$('guide-drawer').hidden && ev.key === 'Escape') {
        closeGuide();
        ev.preventDefault();
        return;
      }
      if (!$('guide-drawer').hidden) return;
      if ($('review').hidden) return;

      if (ev.key === 'ArrowLeft') { navigateItem(-1); ev.preventDefault(); return; }
      if (ev.key === 'ArrowRight') { navigateItem(1); ev.preventDefault(); return; }
      if (ev.key === 'Enter') { saveAndAdvance(); ev.preventDefault(); return; }
      if (ev.key === 'Escape') { exitToEmpty(); ev.preventDefault(); return; }
      if (ev.key === 'm' || ev.key === 'M') {
        // #1: keyboard placement mode for the manual pin. Auto-selects
        // the placer_manuellement verdict so the rest of the form makes sense.
        const radio = document.querySelector('input[name="verdict"][value="placer_manuellement"]');
        if (radio) {
          radio.checked = true;
          // Don't dispatch 'change' (would trigger click placement); we want
          // keyboard mode instead.
          saveDraft();
        }
        enterKeyboardPlacementMode();
        ev.preventDefault();
        return;
      }
      if (['1', '2', '3', '4'].includes(ev.key)) {
        const verdicts = ['garder_stored', 'utiliser_geocoded', 'placer_manuellement', 'terrain'];
        const idx = parseInt(ev.key, 10) - 1;
        const radio = document.querySelector('input[name="verdict"][value="' + verdicts[idx] + '"]');
        if (radio) {
          radio.checked = true;
          radio.dispatchEvent(new Event('change'));
        }
        ev.preventDefault();
      }
    });
  }

  // ---- Guide drawer ---------------------------------------------------

  // Element that had focus when the drawer opened, so we can return
  // focus there on close. js-a11y P2 (WCAG 2.4.3 — Focus Order).
  let _guideOpenerFocus = null;
  let _guideTrapHandler = null;

  function _focusableInside(root) {
    return root.querySelectorAll(
      'a[href], button:not([disabled]), input:not([disabled]), ' +
      'textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'
    );
  }

  function openGuide() {
    const drawer = $('guide-drawer');
    _guideOpenerFocus = document.activeElement;
    drawer.hidden = false;
    document.body.classList.add('guide-open');

    // Move focus to the close button so the user can dismiss with Enter
    // or Tab into the drawer body.
    const closeBtn = $('btn-close-guide');
    if (closeBtn) closeBtn.focus();

    // Trap Tab inside the drawer while it's open.
    _guideTrapHandler = function (ev) {
      if (ev.key !== 'Tab') return;
      if (drawer.hidden) return;
      const focusables = _focusableInside(drawer);
      if (focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (ev.shiftKey && document.activeElement === first) {
        last.focus();
        ev.preventDefault();
      } else if (!ev.shiftKey && document.activeElement === last) {
        first.focus();
        ev.preventDefault();
      }
    };
    document.addEventListener('keydown', _guideTrapHandler, true);
  }

  function closeGuide() {
    $('guide-drawer').hidden = true;
    document.body.classList.remove('guide-open');
    localStorage.setItem(KEY_GUIDE_SEEN, '1');
    if (_guideTrapHandler) {
      document.removeEventListener('keydown', _guideTrapHandler, true);
      _guideTrapHandler = null;
    }
    // Return focus to the element that opened the drawer.
    if (_guideOpenerFocus && typeof _guideOpenerFocus.focus === 'function') {
      _guideOpenerFocus.focus();
    }
    _guideOpenerFocus = null;
  }

  // ---- Boot -----------------------------------------------------------

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
