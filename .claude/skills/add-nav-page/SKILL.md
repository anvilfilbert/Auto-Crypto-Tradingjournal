---
name: add-nav-page
description: Use when adding a new UI page to the trading journal (new nav entry + page-view + JS handler). Triggers on "add a Score Comparison page", "create a new analytics view", "wire up a new tool".
---

# Add a New UI Page

The frontend uses a single-page architecture: nav clicks call `showPage(name)`, which toggles `.active` on `.page-view` divs. State is in `currentPage`. Recent example (2026-05-24): Score Comparison page.

## Three-file pattern

| File | Change |
|---|---|
| `templates/index.html` | Add nav entry + `#page-<name>` div |
| `static/js/13-init.js` | Extend `showPage` switch to dispatch your loader |
| `static/js/<module>.js` | Implement loader + render functions |

## Reference implementation

The **Score Comparison page** (added 2026-05-24) is the clean reference:
- Nav entry: `templates/index.html` (search `nav-score-comparison`)
- Page div: `templates/index.html` (search `page-score-comparison`)
- showPage dispatch: `static/js/13-init.js:5+13` (extras list + loader call)
- Loader: `static/js/09-analysis.js::loadScoreComparison`
- API endpoint: `routes/analytics.py::api_score_comparison`

## Checklist

### 1. Decide section + icon
- Existing sections: Trading, AI-Trade, Manual-Trade, Analysis, Tools, Docs, Acki Nacki — DoDEX, System
- Choose an emoji icon (1 char, prefix the span).

### 2. Add nav entry
- File: `templates/index.html`
- Find the section's existing nav-item block.
- Add:
  ```html
  <div class="nav-item" onclick="showPage('<page-name>')" id="nav-<page-name>" data-section-of="<section-key>">
    <span class="nav-icon">⚖️</span><span>Display Name</span>
  </div>
  ```
- The `data-section-of` value matches the section's `data-section` (e.g., `analysis`, `tools`). Required for the collapsible-nav feature added 2026-05-24.

### 3. Add page-view div
- File: `templates/index.html`
- Find the location matching the section's other pages.
- Add:
  ```html
  <!-- ══════════════════════════════════════════════════════════════════ -->
  <!-- N. <SECTION>                                                       -->
  <!-- ══════════════════════════════════════════════════════════════════ -->
  <div id="page-<page-name>" class="page-view">
    <div class="page-title">⚖️ Display Name</div>
    <div class="page-subtitle">One-line description of the page</div>

    <div id="<page>-header" style="margin:16px 0;..."></div>
    <div id="<page>-content"></div>
  </div>
  ```
- Containers ONLY. No content. JS populates them on `showPage` dispatch.

### 4. Wire showPage dispatch
- File: `static/js/13-init.js`
- Add the page name to the `extras` array at the top.
- Add a dispatch line:
  ```js
  if (name === '<page-name>') { load<PageName>(); }
  ```

### 5. Implement the loader
- File: `static/js/09-analysis.js` (or a new module if scope justifies).
- Pattern:
  ```js
  async function load<PageName>(forceRefresh) {
    const header = document.getElementById('<page>-header');
    if (!header) return;
    header.textContent = '';
    // Loading state
    const loading = document.createElement('small');
    loading.style.color = 'var(--muted)';
    loading.textContent = '⧗ Loading…';
    header.appendChild(loading);

    try {
      const res = forceRefresh
        ? await api('/api/<path>/recompute', 'POST')
        : await api('/api/<path>');
      if (!res.ok) throw new Error(res.error || 'failed');
      _render<PageName>(res.data || {});
    } catch (e) {
      header.textContent = '';
      const err = document.createElement('small');
      err.style.color = 'var(--red)';
      err.textContent = 'Error: ' + e.message;
      header.appendChild(err);
    }
  }
  ```

### 6. Implement render helpers
- **ALWAYS use safe DOM methods (createElement + textContent)**. NEVER use innerHTML.
- The security hook blocks innerHTML edits — see Score Comparison's `_scRenderAggregates` for the pattern.
- For tables: build `<table>` → `<thead>` → `<tbody>` with appendChild chains, set `textContent` for cell values, set `style.cssText` for layout.

### 7. Bump JS cache version
- File: `templates/index.html`
- Find your module's `<script src="/static/js/XX-name.js?v=X.Y">` and bump to next minor.
- Also bump `13-init.js` cache version since you modified it.
- WITHOUT this bump, browsers will serve the cached old JS and your new page won't work.

### 8. Browser verification (mandatory per CLAUDE.md)
- Per CLAUDE.md: any deploy touching `static/js/*.js` or `templates/*.html` requires browser verification.
- Use Playwright (already installed). Reference: `/tmp/verify_*.py` scripts from past deploys.
- Verify: page renders, no console errors, no JS exceptions, content populates as expected.

### 9. Deploy
- Sync via `/tmp/deploy_audit.exp`
- Restart: `restart_pi.exp` (frontend only — no snapshot/config touch).
- Verify HTTP 200 + browser test.
- Backup `bash ~/trading-journal/scripts/backup_db.sh`.

## Red flags

- "I'll use innerHTML for the dynamic HTML" → stop. Security hook will block. Use createElement + textContent.
- "I won't bump the cache version" → stop. Browsers serve stale JS without it; nothing will work.
- "I'll skip the data-section-of attribute" → stop. Without it, the collapsible-nav feature breaks the new entry (item never hides on section collapse).
- "I'll wire showPage but forget to add to extras list" → stop. The dispatch only runs for items in `extras`. Falls through to `_origShowPage` otherwise.
- "I'll skip browser verification because it's a small change" → stop. JS rendering bugs are easy to introduce + hard to spot without rendering in a browser.
