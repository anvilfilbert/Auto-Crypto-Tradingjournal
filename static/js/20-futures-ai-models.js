/* Model Comparison page — driven by /api/futures-ai/model-comparison.
 * Renders 4 sections: insights, per-model aggregate table, score histogram,
 * per-trade detail with shadow scores side-by-side.
 *
 * Defensive coding: all dynamic values (symbols, model names, insight text,
 * etc.) go through textContent. innerHTML is reserved for static markup
 * with no interpolation. This avoids XSS even though all inputs are
 * server-controlled today — future inputs (e.g. user-typed labels) won't
 * accidentally bypass the boundary.
 */
(() => {
  const _state = { window: '24h', threshold: 6 };

  function _fmt_pct(x) {
    if (x == null) return '—';
    return (x * 100).toFixed(1) + '%';
  }
  function _fmt_num(x, d = 2) {
    if (x == null) return '—';
    return (typeof x === 'number') ? x.toFixed(d) : String(x);
  }
  function _fmt_lat(ms) {
    if (ms == null) return '—';
    if (ms > 1000) return (ms / 1000).toFixed(1) + 's';
    return ms + 'ms';
  }
  function _short(model) {
    return (model || '').split('/').pop().substring(0, 36);
  }

  // Safe DOM-element builder. textContent guarantees no HTML interpretation.
  function _el(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    if (attrs) {
      for (const k in attrs) {
        if (k === 'style')      e.style.cssText = attrs[k];
        else if (k === 'text')  e.textContent   = attrs[k];
        else if (k === 'class') e.className     = attrs[k];
        else if (k.startsWith('on')) e[k] = attrs[k];
        else                    e.setAttribute(k, attrs[k]);
      }
    }
    kids.forEach(k => {
      if (k == null || k === false) return;
      if (typeof k === 'string')      e.appendChild(document.createTextNode(k));
      else if (typeof k === 'number') e.appendChild(document.createTextNode(String(k)));
      else                            e.appendChild(k);
    });
    return e;
  }

  function _buildWindowTabs() {
    const c = document.getElementById('faim-window-tabs');
    if (!c) return;
    c.replaceChildren();
    ['24h', '7d', '30d', 'all'].forEach(w => {
      const active = w === _state.window;
      const b = _el('button', {
        class: 'btn btn-secondary btn-sm',
        style:
          'padding:3px 10px;font-size:.8rem;' +
          (active
            ? 'background:var(--accent);color:white;border-color:var(--accent)'
            : ''),
        text: w,
        onclick: () => { _state.window = w; loadFaiModels(); },
      });
      c.appendChild(b);
    });
    const lbl = document.getElementById('faim-window-label');
    if (lbl) lbl.textContent = _state.window;
  }

  function _renderInsights(insights) {
    const c = document.getElementById('faim-insights');
    if (!c) return;
    c.replaceChildren();
    if (!insights || !insights.length) {
      c.appendChild(_el('div', { style: 'color:var(--muted)',
                                 text: 'No insights surfaced.' }));
      return;
    }
    const SEV = {
      high:   { bg: 'rgba(255,99,99,.12)',  bd: 'var(--red)',     ic: '🚨' },
      medium: { bg: 'rgba(255,180,80,.12)', bd: '#ffb450',        ic: '⚠️' },
      low:    { bg: 'rgba(108,99,255,.10)', bd: 'var(--accent)',  ic: 'ℹ️' },
      info:   { bg: 'rgba(140,140,160,.08)', bd: 'var(--muted)',  ic: '·' },
    };
    insights.forEach(i => {
      const s = SEV[i.severity] || SEV.info;
      const box = _el('div', { style:
        `background:${s.bg};border-left:3px solid ${s.bd};padding:8px 12px;` +
        `border-radius:4px;display:flex;align-items:flex-start;gap:8px;line-height:1.4` });
      box.appendChild(_el('span', { style: 'font-size:1rem', text: s.ic }));
      const body = _el('div');
      body.appendChild(_el('span', {
        style: 'font-weight:600;font-size:.75rem;color:var(--muted);text-transform:uppercase',
        text:  i.tag || 'info',
      }));
      body.appendChild(_el('br'));
      body.appendChild(_el('span', { text: i.text || '' }));
      box.appendChild(body);
      c.appendChild(box);
    });
  }

  function _renderModelsTable(primary, models) {
    const c = document.getElementById('faim-models-table');
    if (!c) return;
    c.replaceChildren();
    const rows = [primary, ...models];
    const headers = ['Model','n','Mean','Long%','Open%','Agree','|Δ|≥3','Dir-Flip','Lat p50','Lat p95','Cost','$/call','Err'];
    const t = _el('table', { style: 'width:100%;border-collapse:collapse;font-size:.83rem' });
    const thead = _el('thead');
    const hrow  = _el('tr');
    headers.forEach(h => hrow.appendChild(_el('th', {
      style: 'text-align:left;color:var(--muted);font-weight:600;' +
             'padding:6px 8px;border-bottom:1px solid var(--border);white-space:nowrap',
      text: h,
    })));
    thead.appendChild(hrow);
    t.appendChild(thead);
    const tb = _el('tbody');
    rows.forEach(m => {
      const tr = _el('tr');
      const isPri = !!m.is_primary;
      const bg = isPri ? 'background:rgba(108,99,255,.08)' : '';
      const nameTd = _el('td', { style:
        `padding:5px 8px;border-bottom:1px solid var(--border);${bg};` +
        `color:var(--text);white-space:nowrap` });
      nameTd.appendChild(_el('span', {
        style: 'font-weight:' + (isPri ? 700 : 500),
        text:  _short(m.model),
      }));
      if (isPri) {
        nameTd.appendChild(_el('span', {
          style: 'font-size:.65rem;background:var(--accent);color:white;' +
                 'padding:1px 5px;border-radius:3px;margin-left:4px',
          text:  'PRIMARY',
        }));
      }
      tr.appendChild(nameTd);
      const vals = [
        m.n_with_score,
        _fmt_num(m.mean_score),
        _fmt_pct(m.long_share),
        _fmt_pct(m.would_open_rate),
        _fmt_pct(m.agreement_rate),
        _fmt_pct(m.strong_disagree_rate),
        m.direction_flips,
        _fmt_lat(m.latency_p50_ms),
        _fmt_lat(m.latency_p95_ms),
        '$' + (m.cost_usd || 0).toFixed(4),
        '$' + (m.cost_per_call || 0).toFixed(6),
        _fmt_pct(m.error_rate),
      ];
      vals.forEach(v => tr.appendChild(_el('td', {
        style: `padding:5px 8px;border-bottom:1px solid var(--border);${bg};` +
               `color:var(--text);white-space:nowrap`,
        text:  v == null ? '—' : String(v),
      })));
      tb.appendChild(tr);
    });
    t.appendChild(tb);
    c.appendChild(t);
  }

  function _renderScoreHist(primary, models) {
    const c = document.getElementById('faim-score-hist');
    if (!c) return;
    c.replaceChildren();
    const rows = [primary, ...models].filter(m => m.n_with_score > 0);
    rows.forEach(m => {
      const card = _el('div', { style:
        'background:var(--bg2);padding:10px 12px;border-radius:6px;border:1px solid var(--border)' });
      const title = _el('div', { style:
        'font-weight:600;font-size:.82rem;margin-bottom:6px;color:var(--text);' +
        'display:flex;justify-content:space-between;align-items:baseline' });
      title.appendChild(_el('span', { text: _short(m.model) }));
      title.appendChild(_el('span', {
        style: 'color:var(--muted);font-weight:400;font-size:.7rem',
        text:  `μ ${_fmt_num(m.mean_score)} · n=${m.n_with_score}`,
      }));
      card.appendChild(title);

      const max = Math.max(...Object.values(m.score_histogram || {}), 1);
      const hist = _el('div', { style:
        'display:grid;grid-template-columns:repeat(11,1fr);gap:2px;' +
        'align-items:end;height:80px;margin-top:4px' });
      for (let i = 0; i <= 10; i++) {
        const cnt = (m.score_histogram && m.score_histogram[i]) || 0;
        const h = max > 0 ? (cnt / max) * 70 : 0;
        const color = i < 4 ? 'var(--red)' :
                      i < 6 ? '#ffb450' :
                              'var(--accent3)';
        const bar = _el('div', {
          style: `background:${color};height:${h}px;min-height:${cnt > 0 ? 2 : 0}px;` +
                 `border-radius:2px 2px 0 0`,
          title: `score=${i}: ${cnt} calls`,
        });
        hist.appendChild(bar);
      }
      card.appendChild(hist);
      const lbl = _el('div', { style:
        'display:grid;grid-template-columns:repeat(11,1fr);gap:2px;' +
        'font-size:.62rem;color:var(--muted);margin-top:3px;text-align:center' });
      for (let i = 0; i <= 10; i++) lbl.appendChild(_el('span', { text: String(i) }));
      card.appendChild(lbl);
      c.appendChild(card);
    });
  }

  function _renderCostSummary(cs) {
    const c = document.getElementById('faim-cost-summary');
    if (!c) return;
    c.replaceChildren();
    if (!cs) {
      c.appendChild(_el('div', { style: 'color:var(--muted)', text: 'No cost data.' }));
      return;
    }
    const pnl = cs.total_realized_pnl_usd ?? 0;
    const cost = cs.total_attributed_cost_usd ?? 0;
    const net = cs.net_pnl_usd ?? 0;
    const roi = cs.roi_on_llm_spend;
    const tiles = [
      { label: 'Closed trades', value: String(cs.trades_closed ?? 0),
        sub: `${cs.trades_won ?? 0}W · ${cs.trades_lost ?? 0}L · ${cs.trades_breakeven ?? 0}BE` },
      { label: 'Open',           value: String(cs.trades_open ?? 0), sub: '' },
      { label: 'Win rate',
        value: cs.win_rate == null ? '—' : (cs.win_rate * 100).toFixed(1) + '%',
        sub: '' },
      { label: 'Total P&L',
        value: (pnl >= 0 ? '+$' : '-$') + Math.abs(pnl).toFixed(2),
        sub: '',
        color: pnl >= 0 ? 'var(--accent3)' : 'var(--red)' },
      { label: 'LLM cost (attributed)',
        value: '$' + cost.toFixed(4),
        sub: '' },
      { label: 'NET (P&L − cost)',
        value: (net >= 0 ? '+$' : '-$') + Math.abs(net).toFixed(2),
        sub: '',
        color: net >= 0 ? 'var(--accent3)' : 'var(--red)' },
      { label: 'Avg P&L / trade',
        value: cs.avg_pnl_per_trade == null ? '—' :
               (cs.avg_pnl_per_trade >= 0 ? '+$' : '-$') + Math.abs(cs.avg_pnl_per_trade).toFixed(2),
        sub: '' },
      { label: 'Avg cost / trade',
        value: cs.avg_cost_per_trade == null ? '—' :
               '$' + cs.avg_cost_per_trade.toFixed(4),
        sub: '' },
      { label: 'ROI on LLM spend',
        value: roi == null ? '—' :
               (roi >= 0 ? '+' : '') + (roi * 100).toFixed(0) + '%',
        sub: '(P&L − cost) / cost',
        color: roi == null ? 'var(--muted)' :
               (roi >= 0 ? 'var(--accent3)' : 'var(--red)') },
      { label: 'Window total shadow spend',
        value: '$' + (cs.window_shadow_cost_usd ?? 0).toFixed(4),
        sub: 'all shadow calls in window' },
    ];
    tiles.forEach(t => {
      const tile = _el('div', { style:
        'background:var(--bg2);border:1px solid var(--border);border-radius:6px;' +
        'padding:8px 10px;display:flex;flex-direction:column;gap:2px' });
      tile.appendChild(_el('div', {
        style: 'color:var(--muted);font-size:.7rem;text-transform:uppercase;letter-spacing:.04em',
        text:  t.label,
      }));
      tile.appendChild(_el('div', {
        style: `font-size:1.1rem;font-weight:600;color:${t.color || 'var(--text)'}`,
        text:  t.value,
      }));
      if (t.sub) {
        tile.appendChild(_el('div', {
          style: 'color:var(--muted);font-size:.7rem',
          text:  t.sub,
        }));
      }
      c.appendChild(tile);
    });
  }

  function _renderTrades(trades, allModels) {
    const c = document.getElementById('faim-trades-table');
    if (!c) return;
    c.replaceChildren();
    if (!trades || !trades.length) {
      c.appendChild(_el('div', {
        style: 'color:var(--muted);font-size:.85rem;padding:20px;text-align:center',
        text:  'No paper/auto_ai trades in this window yet. Per-trade detail will populate as ' +
               'the scanner opens trades on the clean $1000 baseline.',
      }));
      return;
    }
    // Build column set from BOTH the per-trade matches AND the aggregate
    // model list — so even when a trade pre-dates the symbol column (no
    // shadow match), the user still sees one column per known model
    // populated with "—". Better UX than dropping the columns silently.
    const modelSet = new Set();
    trades.forEach(t => Object.keys(t.models || {}).forEach(m => modelSet.add(m)));
    (allModels || []).forEach(m => { if (m && m.model) modelSet.add(m.model); });
    const modelCols = Array.from(modelSet).sort((a, b) => {
      if ((a || '').includes('primary')) return -1;
      if ((b || '').includes('primary')) return 1;
      return (a || '').localeCompare(b || '');
    });
    // Surface "no shadow match" when applicable so the user knows WHY a
    // row shows "—" across the board (vs the column not existing).
    const totalMatches = trades.reduce(
      (s, t) => s + Object.keys(t.models || {}).length, 0);
    if (totalMatches === 0) {
      c.appendChild(_el('div', {
        style: 'background:rgba(255,180,80,.10);border-left:3px solid #ffb450;' +
               'padding:8px 12px;border-radius:4px;font-size:.8rem;color:var(--muted);' +
               'margin-bottom:8px;line-height:1.4',
        text:  '⚠️ No shadow-row matches for these trades. They opened before the ' +
               'symbol-tagging fix went live, so the ±10min symbol-join finds nothing. ' +
               'New trades opening after 2026-06-02 ~11:04 UTC will populate.',
      }));
    }

    const hint = _el('div', {
      style: 'font-size:.75rem;color:var(--muted);margin-bottom:6px',
      text:  '▶ click a row to see per-model reasoning. Cost = attributed LLM spend; Net = P&L − Cost.',
    });
    c.appendChild(hint);

    const t = _el('table', { style: 'width:100%;border-collapse:collapse;font-size:.82rem' });
    const thead = _el('thead'); const hrow = _el('tr');
    ['', 'Symbol','Dir','Opened','Status','Score','P&L','Cost','Net', ...modelCols.map(_short)].forEach(h =>
      hrow.appendChild(_el('th', {
        style: 'text-align:left;color:var(--muted);font-weight:600;padding:6px 8px;' +
               'border-bottom:1px solid var(--border);white-space:nowrap',
        text:  h,
      })));
    thead.appendChild(hrow); t.appendChild(thead);
    const tb = _el('tbody');

    trades.forEach((tr_data, rowIdx) => {
      const tr = _el('tr', { style: 'cursor:pointer' });
      const tdStyle = 'padding:5px 8px;border-bottom:1px solid var(--border);' +
                      'color:var(--text);white-space:nowrap';
      // Caret column
      const caretTd = _el('td', { style: tdStyle });
      const caret = _el('span', {
        style: 'color:var(--muted);font-size:.85rem;display:inline-block;width:14px;' +
               'transition:transform .12s',
        text:  '▶',
      });
      caretTd.appendChild(caret);
      tr.appendChild(caretTd);

      // Symbol / Direction / Opened
      tr.appendChild(_el('td', { style: tdStyle, text: tr_data.symbol || '—' }));
      tr.appendChild(_el('td', { style: tdStyle, text: tr_data.direction || '—' }));
      const opened = (tr_data.open_time || '').replace('T', ' ').slice(5, 16) || '—';
      tr.appendChild(_el('td', { style: tdStyle, text: opened }));

      // Status pill
      const status = tr_data.status || (tr_data.close_time ? 'closed' : 'open');
      const stTd = _el('td', { style: tdStyle });
      stTd.appendChild(_el('span', {
        style: 'font-size:.72rem;padding:1px 6px;border-radius:3px;' +
               (status === 'open'
                 ? 'background:rgba(108,99,255,.15);color:var(--accent)'
                 : 'background:rgba(140,140,160,.15);color:var(--muted)'),
        text:  status,
      }));
      tr.appendChild(stTd);

      // Consensus score, P&L, Cost, Net
      tr.appendChild(_el('td', {
        style: tdStyle,
        text:  tr_data.score_consensus != null ? tr_data.score_consensus + '/10' : '—',
      }));
      const pnl = parseFloat(tr_data.realized_pnl || 0);
      const pnlStr = (tr_data.realized_pnl == null)
        ? '—'
        : (pnl >= 0 ? '+$' : '-$') + Math.abs(pnl).toFixed(2);
      const pnlColor = (tr_data.realized_pnl == null) ? 'var(--muted)' :
                       (pnl >= 0 ? 'var(--accent3)' : 'var(--red)');
      const pnlTd = _el('td', { style: tdStyle });
      pnlTd.appendChild(_el('span', {
        style: `color:${pnlColor};font-weight:500`,
        text:  pnlStr,
      }));
      tr.appendChild(pnlTd);

      const costVal = parseFloat(tr_data.total_cost_usd || 0);
      tr.appendChild(_el('td', {
        style: tdStyle,
        text:  '$' + costVal.toFixed(4),
      }));
      const net = tr_data.net_pnl;
      const netStr = net == null ? '—' :
        (net >= 0 ? '+$' : '-$') + Math.abs(net).toFixed(2);
      const netColor = net == null ? 'var(--muted)' :
        (net >= 0 ? 'var(--accent3)' : 'var(--red)');
      const netTd = _el('td', { style: tdStyle });
      netTd.appendChild(_el('span', {
        style: `color:${netColor};font-weight:600`,
        text:  netStr,
      }));
      tr.appendChild(netTd);

      // Per-model score cells
      modelCols.forEach(m => {
        const v = (tr_data.models || {})[m];
        const td = _el('td', { style: tdStyle });
        if (!v) {
          td.appendChild(_el('span', { style: 'color:var(--muted)', text: '—' }));
        } else {
          const s = v.score;
          const d = v.direction || '';
          const color = s < 4 ? 'var(--red)' :
                        s < 6 ? '#ffb450' :
                                'var(--accent3)';
          const arrow = d === 'long' ? '↑' : d === 'short' ? '↓' : '';
          td.appendChild(_el('span', {
            style: `color:${color};font-weight:600`,
            text:  String(s),
          }));
          td.appendChild(_el('span', {
            style: 'color:var(--muted);font-size:.7rem;margin-left:3px',
            text:  arrow,
          }));
        }
        tr.appendChild(td);
      });

      tb.appendChild(tr);

      // Expansion row (hidden by default) — per-model reasoning grid
      const expTr = _el('tr', { style: 'display:none' });
      const expTd = _el('td', {
        colspan: String(9 + modelCols.length),
        style:   'padding:12px 16px;background:rgba(108,99,255,.04);' +
                 'border-bottom:1px solid var(--border)',
      });
      const expWrap = _el('div', {
        style: 'display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px',
      });
      modelCols.forEach(m => {
        const v = (tr_data.models || {})[m];
        const card = _el('div', { style:
          'background:var(--card);border:1px solid var(--border);border-radius:6px;' +
          'padding:10px 12px;display:flex;flex-direction:column;gap:6px' });
        const head = _el('div', { style:
          'display:flex;justify-content:space-between;align-items:baseline' });
        head.appendChild(_el('span', {
          style: 'font-weight:600;font-size:.82rem;color:var(--text)',
          text:  _short(m),
        }));
        if (v) {
          const sc = v.score;
          const color = sc < 4 ? 'var(--red)' : sc < 6 ? '#ffb450' : 'var(--accent3)';
          const arrow = v.direction === 'long' ? '↑' : v.direction === 'short' ? '↓' : '';
          head.appendChild(_el('span', {
            style: `font-weight:700;font-size:1rem;color:${color}`,
            text:  String(sc) + ' ' + arrow,
          }));
        } else {
          head.appendChild(_el('span', {
            style: 'color:var(--muted);font-size:.85rem', text: '—',
          }));
        }
        card.appendChild(head);
        const reason = v && v.reason ? v.reason :
                      (v ? '(no reasoning text in this row)' : '(no shadow match within ±10min)');
        card.appendChild(_el('div', {
          style: 'color:var(--muted);font-size:.78rem;line-height:1.45',
          text:  reason,
        }));
        if (v && v.delta_sec != null) {
          card.appendChild(_el('div', {
            style: 'color:var(--muted);font-size:.66rem;margin-top:2px',
            text:  `match Δ${v.delta_sec}s`,
          }));
        }
        expWrap.appendChild(card);
      });
      expTd.appendChild(expWrap);
      expTr.appendChild(expTd);
      tb.appendChild(expTr);

      // Toggle handler
      tr.onclick = () => {
        const isOpen = expTr.style.display !== 'none';
        expTr.style.display = isOpen ? 'none' : 'table-row';
        caret.textContent = isOpen ? '▶' : '▼';
      };
    });
    t.appendChild(tb);
    c.appendChild(t);
  }

  window.loadFaiModels = async function () {
    const sum = document.getElementById('faim-summary');
    if (sum) sum.textContent = 'Loading…';
    _buildWindowTabs();

    const sel = document.getElementById('faim-threshold');
    if (sel) {
      sel.onchange = () => {
        _state.threshold = parseInt(sel.value, 10);
        loadFaiModels();
      };
    }

    try {
      const r = await api(
        `/api/futures-ai/model-comparison?window=${_state.window}&min_score=${_state.threshold}`
      );
      if (!r || !r.ok) {
        if (sum) sum.textContent = 'Error loading';
        return;
      }
      const d = r.data;
      if (sum) {
        sum.textContent =
          `${d.n_primary_reqs} primary calls · ${d.n_shadow_rows} shadow rows · ` +
          `${d.trades.length} trades joined`;
      }
      _renderInsights(d.insights);
      _renderCostSummary(d.cost_summary);
      _renderModelsTable(d.primary, d.models);
      _renderScoreHist(d.primary, d.models);
      _renderTrades(d.trades, d.models);
    } catch (e) {
      console.error('loadFaiModels failed', e);
      if (sum) sum.textContent = 'Error: ' + (e && e.message ? e.message : String(e));
    }
  };

  // Hook into the global showPage so we auto-load on tab switch.
  document.addEventListener('DOMContentLoaded', () => {
    const orig = window.showPage;
    if (typeof orig === 'function') {
      window.showPage = function (name) {
        const result = orig.apply(this, arguments);
        if (name === 'faimodels') loadFaiModels();
        return result;
      };
    }
  });
})();
