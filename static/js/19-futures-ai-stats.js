// 19-futures-ai-stats.js
// Renders the Futures-AI Stats page. Single fetch to /api/futures-ai/stats
// returns all panels' data. All DOM construction uses safe createElement /
// textContent (no innerHTML with interpolated data).

(function () {
  const _state = { window: '30d' };

  // ── helpers ────────────────────────────────────────────────────────────
  const _$ = (id) => document.getElementById(id);
  const _wipe = (el) => { if (el) el.replaceChildren(); };
  const _fmt = (n, dp = 2) =>
    (n == null ? '—' : (typeof n === 'number' ? n.toFixed(dp) : String(n)));
  const _fmtPnl = (n) => n == null ? '—' : (n >= 0 ? '+' : '') + '$' + Math.abs(n).toFixed(2) * Math.sign(n || 1);
  const _signed = (n, dp = 2) => n == null ? '—' :
    (n >= 0 ? '+' : '') + (typeof n === 'number' ? n.toFixed(dp) : String(n));
  const _pnlColor = (n) => n == null ? 'var(--muted)' : (n >= 0 ? 'var(--accent3)' : 'var(--red)');

  function _el(tag, attrs, text) {
    const e = document.createElement(tag);
    if (attrs) for (const k in attrs) {
      if (k === 'style') e.style.cssText = attrs[k];
      else if (k === 'class') e.className = attrs[k];
      else e.setAttribute(k, attrs[k]);
    }
    if (text != null) e.textContent = String(text);
    return e;
  }

  // ── window selector tabs ──────────────────────────────────────────────
  function _renderWindowTabs() {
    const wrap = _$('fais-window-tabs');
    if (!wrap) return;
    _wipe(wrap);
    ['7d', '30d', '90d', 'all'].forEach((w) => {
      const b = _el('button', {
        class: 'btn btn-sm',
        style: 'padding:4px 10px;'
          + (w === _state.window
            ? 'background:var(--accent);color:#fff'
            : 'background:rgba(108,99,255,.10);color:var(--accent)')
      }, w);
      b.onclick = () => { _state.window = w; loadFuturesAIStats(); };
      wrap.appendChild(b);
    });
  }

  // ── tile grid (top of page) ───────────────────────────────────────────
  function _renderTiles(t) {
    const grid = _$('fais-tiles-grid');
    _wipe(grid);
    const tiles = [
      ['Total P&L',        _signed(t.total_pnl) + ' USDT',     _pnlColor(t.total_pnl)],
      ['Win Rate',         t.wr_pct + '%  (' + t.wins + 'W/' + t.losses + 'L)',
                                                               t.wr_pct >= 50 ? 'var(--accent3)' : 'var(--red)'],
      ['Expectancy',       _signed(t.expectancy) + '/trade',   _pnlColor(t.expectancy)],
      ['Profit Factor',    t.profit_factor != null ? t.profit_factor.toFixed(2) : '—',
                                                               t.profit_factor != null && t.profit_factor >= 1 ? 'var(--accent3)' : 'var(--red)'],
      ['Total R',          _signed(t.total_r) + 'R',           _pnlColor(t.total_r)],
      ['Avg R',            _signed(t.avg_r) + 'R  (n=' + t.r_n + ')', _pnlColor(t.avg_r)],
      ['Sharpe (annual)',  t.sharpe != null ? t.sharpe.toFixed(2) : '—',
                                                               t.sharpe != null && t.sharpe >= 1 ? 'var(--accent3)' : 'var(--muted)'],
      ['Sortino',          t.sortino != null ? t.sortino.toFixed(2) : '—',
                                                               t.sortino != null && t.sortino >= 1 ? 'var(--accent3)' : 'var(--muted)'],
      ['Max DD',           t.max_dd_pct + '%',                 'var(--red)'],
      ['Calmar',           t.calmar != null ? t.calmar.toFixed(2) : '—',
                                                               t.calmar != null && t.calmar > 0 ? 'var(--accent3)' : 'var(--muted)'],
      ['Best Trade',       _signed(t.best_trade_pnl) + ' USDT', 'var(--accent3)'],
      ['Worst Trade',      _signed(t.worst_trade_pnl) + ' USDT', 'var(--red)'],
    ];
    tiles.forEach(([label, val, color]) => {
      const cell = _el('div', { style: 'padding:8px 10px;border-radius:6px;background:rgba(255,255,255,.02)' });
      cell.appendChild(_el('div', { style: 'font-size:.65rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em' }, label));
      cell.appendChild(_el('div', { style: 'font-size:1rem;font-weight:700;color:' + color + ';margin-top:4px' }, val));
      grid.appendChild(cell);
    });
  }

  // R-1: Advanced KPIs panel — quantstats-backed institutional metrics.
  // Empty when sample is too small (n<3 days). All shown as plain numbers
  // with sign/color cues; tooltips explain each.
  function _renderAdvancedKpis(adv) {
    const grid = _$('fais-advkpi-grid');
    if (!grid) return;
    _wipe(grid);
    if (!adv || Object.keys(adv).length === 0) {
      grid.appendChild(_el('div', { style: 'color:var(--muted);padding:14px;font-size:.85rem' },
        'Insufficient sample (need ≥3 trading days).'));
      return;
    }
    // Order: most-important first. Color rules where applicable.
    const rows = [
      ['PSR (Prob. Sharpe)',  adv.psr,                'P(true Sharpe > 0) — higher = more credible. Range 0–1.'],
      ['Smart Sharpe',        adv.smart_sharpe,       'Autocorrelation-adjusted Sharpe — penalizes serial correlation.'],
      ['Smart Sortino',       adv.smart_sortino,      'Autocorrelation-adjusted Sortino.'],
      ['K-Ratio',             adv.k_ratio,            'Kestner K-Ratio — rewards SMOOTH equity growth. >1 healthy.'],
      ['Ulcer Index',         adv.ulcer_index,        'Depth × duration of drawdowns. Lower is better.'],
      ['Ulcer-Perf (Martin)', adv.ulcer_performance,  'Return / Ulcer Index. Higher = better depth-adjusted return.'],
      ['Omega (τ=0)',         adv.omega,              'Area of CDF above 0 ÷ below 0. >1 = positive expectancy.'],
      ['Tail Ratio',          adv.tail_ratio,         '95th pctl ÷ |5th pctl| of returns. >1 = upside-heavy.'],
      ['Gain-to-Pain (GPR)',  adv.gain_to_pain,       'Σ wins ÷ |Σ losses| over period. >1 good, >2 excellent.'],
      ['Common-Sense Ratio',  adv.common_sense_ratio,'Tail × Profit Factor. Combines both quality dimensions.'],
      ['Recovery Factor',     adv.recovery_factor,    'Net profit ÷ max drawdown. Higher = faster bounce-back.'],
      ['CVaR (95%)',          adv.cvar_95,            'Expected loss in the worst 5% of days. Negative = bad.'],
      ['Risk of Ruin',        adv.risk_of_ruin,       'Probability of -100% drawdown given current edge. Lower = better.'],
      ['Kelly Fraction',      adv.kelly_fraction,     'Optimal fraction to risk per trade given edge. Use ¼ for safety.'],
    ];
    rows.forEach(([label, val, tip]) => {
      if (val == null) return;  // skip missing
      const cell = _el('div', {
        style: 'padding:8px 10px;border-radius:6px;background:rgba(108,99,255,.04);'
          + 'border:1px solid rgba(108,99,255,.10)',
        title: tip,
      });
      cell.appendChild(_el('div', { style: 'font-size:.62rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em' }, label));
      const displayVal = (typeof val === 'number') ? val.toFixed(3) : String(val);
      let color = 'var(--fg)';
      if (label.startsWith('PSR') && val >= 0.7) color = 'var(--accent3)';
      else if (label.startsWith('PSR') && val < 0.5) color = 'var(--red)';
      else if (label.startsWith('Omega') || label.startsWith('Tail') || label.startsWith('Gain-to-Pain'))
        color = val >= 1 ? 'var(--accent3)' : 'var(--red)';
      else if (label.startsWith('CVaR') || label.startsWith('Risk of Ruin'))
        color = 'var(--red)';
      cell.appendChild(_el('div', { style: 'font-size:.95rem;font-weight:700;color:' + color + ';margin-top:4px' }, displayVal));
      grid.appendChild(cell);
    });
  }

  // ── bar/line chart helpers (inline SVG, no library) ───────────────────
  function _equityChart(points) {
    const host = _$('fais-equity-curve');
    _wipe(host);
    if (!points || points.length < 2) {
      host.appendChild(_el('div', { style: 'color:var(--muted);padding:30px;text-align:center' }, 'No trades in window.'));
      return;
    }
    const w = host.clientWidth || 600, h = 260;
    const vs = points.map(p => p.v);
    const vmin = Math.min(0, ...vs);
    const vmax = Math.max(0, ...vs);
    const pad = 24;
    const sx = (i) => pad + (i / (points.length - 1)) * (w - pad * 2);
    const sy = (v) => h - pad - ((v - vmin) / (vmax - vmin || 1)) * (h - pad * 2);

    const NS = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
    svg.setAttribute('width', '100%');
    svg.setAttribute('height', h);

    // Zero baseline
    const yz = sy(0);
    const baseline = document.createElementNS(NS, 'line');
    baseline.setAttribute('x1', pad); baseline.setAttribute('x2', w - pad);
    baseline.setAttribute('y1', yz); baseline.setAttribute('y2', yz);
    baseline.setAttribute('stroke', 'rgba(255,255,255,.15)');
    baseline.setAttribute('stroke-dasharray', '3 3');
    svg.appendChild(baseline);

    // Path
    let d = '';
    points.forEach((p, i) => {
      d += (i === 0 ? 'M' : 'L') + sx(i) + ' ' + sy(p.v);
    });
    const path = document.createElementNS(NS, 'path');
    path.setAttribute('d', d);
    path.setAttribute('fill', 'none');
    const last = points[points.length - 1].v;
    path.setAttribute('stroke', last >= 0 ? '#26d96b' : '#ef5350');
    path.setAttribute('stroke-width', '2');
    svg.appendChild(path);

    // Y-axis labels (min, max, 0)
    [vmin, 0, vmax].forEach(v => {
      const lbl = document.createElementNS(NS, 'text');
      lbl.setAttribute('x', 2); lbl.setAttribute('y', sy(v) + 3);
      lbl.setAttribute('fill', 'rgba(255,255,255,.5)');
      lbl.setAttribute('font-size', '10');
      lbl.textContent = '$' + v.toFixed(0);
      svg.appendChild(lbl);
    });

    host.appendChild(svg);
  }

  function _dailyBars(bars) {
    const host = _$('fais-daily-bars');
    _wipe(host);
    if (!bars || !bars.length) {
      host.appendChild(_el('div', { style: 'color:var(--muted);padding:30px;text-align:center' }, 'No data.'));
      return;
    }
    const w = host.clientWidth || 400, h = 260;
    const pad = 20;
    const vs = bars.map(b => b.pnl);
    const vmin = Math.min(0, ...vs);
    const vmax = Math.max(0, ...vs);
    const bw = Math.max(2, (w - pad * 2) / bars.length - 1);
    const sx = (i) => pad + i * ((w - pad * 2) / bars.length);
    const sy = (v) => h - pad - ((v - vmin) / (vmax - vmin || 1)) * (h - pad * 2);
    const yz = sy(0);

    const NS = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
    svg.setAttribute('width', '100%');
    svg.setAttribute('height', h);

    bars.forEach((b, i) => {
      const y = sy(b.pnl);
      const rect = document.createElementNS(NS, 'rect');
      rect.setAttribute('x', sx(i));
      rect.setAttribute('width', bw);
      rect.setAttribute('y', Math.min(y, yz));
      rect.setAttribute('height', Math.max(1, Math.abs(y - yz)));
      rect.setAttribute('fill', b.pnl >= 0 ? '#26d96b' : '#ef5350');
      rect.setAttribute('opacity', '.85');
      const title = document.createElementNS(NS, 'title');
      title.textContent = b.d + ': ' + (b.pnl >= 0 ? '+' : '') + '$' + b.pnl.toFixed(2);
      rect.appendChild(title);
      svg.appendChild(rect);
    });

    // Zero baseline
    const baseline = document.createElementNS(NS, 'line');
    baseline.setAttribute('x1', pad); baseline.setAttribute('x2', w - pad);
    baseline.setAttribute('y1', yz); baseline.setAttribute('y2', yz);
    baseline.setAttribute('stroke', 'rgba(255,255,255,.15)');
    svg.appendChild(baseline);

    host.appendChild(svg);
  }

  // ── horizontal bar chart for histograms ──────────────────────────────
  function _histBars(host, items, valueKey) {
    _wipe(host);
    if (!items || !items.length || items.every(x => x[valueKey] === 0)) {
      host.appendChild(_el('div', { style: 'color:var(--muted);padding:20px;text-align:center;font-size:.85rem' }, 'No data.'));
      return;
    }
    const max = Math.max(...items.map(x => x[valueKey] || 0)) || 1;
    items.forEach(it => {
      const row = _el('div', { style: 'display:grid;grid-template-columns:80px 1fr 40px;align-items:center;gap:8px;margin:3px 0;font-size:.78rem' });
      row.appendChild(_el('div', { style: 'color:var(--muted);text-align:right' }, it.bucket));
      const barWrap = _el('div', { style: 'background:rgba(255,255,255,.05);border-radius:3px;height:14px;position:relative;overflow:hidden' });
      const fill = _el('div', {
        style: 'background:var(--accent);height:100%;border-radius:3px;'
          + 'width:' + ((it[valueKey] / max) * 100).toFixed(1) + '%'
      });
      barWrap.appendChild(fill);
      row.appendChild(barWrap);
      row.appendChild(_el('div', { style: 'text-align:right;color:var(--muted)' }, String(it[valueKey])));
      host.appendChild(row);
    });
  }

  // ── bucket table (used for setup_type / archetype / grade / dow / session / score) ──
  function _bucketTable(host, rows, keyLabel) {
    _wipe(host);
    if (!rows || !rows.length) {
      host.appendChild(_el('div', { style: 'color:var(--muted);padding:14px;text-align:center;font-size:.85rem' }, 'No data.'));
      return;
    }
    const tbl = _el('table', { style: 'width:100%;border-collapse:collapse;font-size:.78rem' });
    const thead = _el('thead');
    const hr = _el('tr');
    [keyLabel || 'Group', '#', 'WR%', 'Avg P&L', 'Total P&L', 'Avg R'].forEach(h => {
      hr.appendChild(_el('th', { style: 'text-align:left;color:var(--muted);font-weight:600;padding:5px 6px;border-bottom:1px solid var(--border)' }, h));
    });
    thead.appendChild(hr);
    tbl.appendChild(thead);
    const tb = _el('tbody');
    rows.forEach(r => {
      const tr = _el('tr');
      const cells = [
        [r.key, null],
        [r.count, null],
        [r.wr_pct + '%', r.wr_pct >= 50 ? 'var(--accent3)' : 'var(--red)'],
        [_signed(r.avg_pnl), _pnlColor(r.avg_pnl)],
        [_signed(r.total_pnl), _pnlColor(r.total_pnl)],
        [r.avg_r != null ? _signed(r.avg_r) + 'R' : '—', _pnlColor(r.avg_r)],
      ];
      cells.forEach(([v, c]) => {
        const td = _el('td', {
          style: 'padding:4px 6px;border-bottom:1px solid rgba(255,255,255,.04);'
            + (c ? 'color:' + c : 'color:var(--muted)')
        }, v);
        tr.appendChild(td);
      });
      tb.appendChild(tr);
    });
    tbl.appendChild(tb);
    host.appendChild(tbl);
  }

  // ── last 20 trades table ──────────────────────────────────────────────
  function _renderLast20(rows) {
    const host = _$('fais-last20');
    _wipe(host);
    if (!rows || !rows.length) {
      host.appendChild(_el('div', { style: 'color:var(--muted);padding:14px;text-align:center;font-size:.85rem' }, 'No closed trades in window.'));
      return;
    }
    const tbl = _el('table', { style: 'width:100%;border-collapse:collapse;font-size:.78rem' });
    const thead = _el('thead');
    const hr = _el('tr');
    ['Closed', 'Symbol', 'Dir', 'Entry', 'Close', 'P&L', 'R', 'Reason', 'Setup type', 'Archetype', 'Grade', 'Score']
      .forEach(h => hr.appendChild(_el('th', { style: 'text-align:left;color:var(--muted);font-weight:600;padding:5px 6px;border-bottom:1px solid var(--border);white-space:nowrap' }, h)));
    thead.appendChild(hr);
    tbl.appendChild(thead);
    const tb = _el('tbody');
    rows.forEach(r => {
      const tr = _el('tr');
      const cells = [
        [(r.close_time || '').replace('T', ' ').slice(5, 16), null],
        [r.symbol, null],
        [r.direction, null],
        [r.entry_price != null ? Number(r.entry_price).toFixed(6).replace(/\.?0+$/, '') : '—', null],
        [r.close_price != null ? Number(r.close_price).toFixed(6).replace(/\.?0+$/, '') : '—', null],
        [_signed(r.realized_pnl), _pnlColor(r.realized_pnl)],
        [r.r != null ? _signed(r.r) + 'R' : '—', _pnlColor(r.r)],
        [r.close_reason || '—', null],
        [r.setup_type || '—', null],
        [r.archetype || '—', null],
        [r.grade || '—', null],
        [r.score != null ? String(r.score) : '—', null],
      ];
      cells.forEach(([v, c]) => {
        tr.appendChild(_el('td', {
          style: 'padding:4px 6px;border-bottom:1px solid rgba(255,255,255,.04);white-space:nowrap;'
            + (c ? 'color:' + c : 'color:var(--fg)')
        }, v));
      });
      tb.appendChild(tr);
    });
    tbl.appendChild(tb);
    host.appendChild(tbl);
  }

  // ── main loader ───────────────────────────────────────────────────────
  window.loadFuturesAIStats = async function () {
    _renderWindowTabs();
    const sum = _$('fais-summary');
    if (sum) sum.textContent = 'Loading…';
    try {
      const r = await api('/api/futures-ai/stats?window=' + _state.window);
      if (!r.ok) throw new Error(r.error || 'failed');
      const d = r.data;
      if (sum) sum.textContent = d.n_total + ' closed trades · window=' + d.window;
      _renderTiles(d.tiles || {});
      _renderAdvancedKpis(d.advanced_kpis || {});
      _equityChart(d.equity_curve || []);
      _dailyBars(d.daily_bars || []);
      _histBars(_$('fais-r-hist'),  d.r_histogram || [], 'n');
      _histBars(_$('fais-hold'),    d.hold_buckets || [], 'n');
      _bucketTable(_$('fais-by-setup-type'),     d.by_setup_type     || [], 'setup_type');
      _bucketTable(_$('fais-by-archetype-open'), d.by_archetype_open || [], 'archetype');
      _bucketTable(_$('fais-by-trade-grade'),    d.by_trade_grade    || [], 'grade');
      _bucketTable(_$('fais-by-dow'),            d.by_dow            || [], 'day');
      _bucketTable(_$('fais-by-session'),        d.by_session        || [], 'session');
      _bucketTable(_$('fais-by-score'),          d.by_score          || [], 'score');
      _renderLast20(d.last20 || []);
      _loadL7Panels();
    } catch (e) {
      if (sum) sum.textContent = 'Failed: ' + e.message;
    }
  };

  // XSS-safe text escape — backend strings (param keys, reasons, archetype
  // names) flow through here before being inserted as HTML.
  function _esc(s) {
    if (s == null) return '';
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // ── L-7 panels: learned params, noise gates, reminders, edge decay ────
  async function _loadL7Panels() {
    try {
      const r = await api('/api/futures-ai/l7-panels');
      if (!r.ok) return;
      const d = r.data || {};
      _renderLearnedLog(d.learned_log || []);
      _renderNoiseGates(d.noise_gates || {});
      _renderReminders(d.reminders || []);
      _renderEdgeDecay(d.edge_decay || {});
      _renderCostVsPnl(d.cost_vs_pnl || {});
      _renderCacheStats(d.quick_score_cache || {});
    } catch (e) {
      console.warn('L-7 panels failed:', e);
    }
  }

  function _renderLearnedLog(rows) {
    const el = _$('fais-learned-log');
    if (!el) return;
    if (!rows.length) { el.innerHTML = '<em>(no learner activity yet)</em>'; return; }
    let html = '<table style="width:100%;border-collapse:collapse"><thead><tr>'
      + '<th style="text-align:left;padding:4px;border-bottom:1px solid var(--border)">Time</th>'
      + '<th style="text-align:left;padding:4px;border-bottom:1px solid var(--border)">Learner</th>'
      + '<th style="text-align:left;padding:4px;border-bottom:1px solid var(--border)">Key</th>'
      + '<th style="text-align:left;padding:4px;border-bottom:1px solid var(--border)">Change</th>'
      + '<th style="text-align:left;padding:4px;border-bottom:1px solid var(--border)">Action</th>'
      + '<th style="text-align:left;padding:4px;border-bottom:1px solid var(--border)">Reason</th>'
      + '</tr></thead><tbody>';
    rows.forEach(r => {
      const action = r.action || '?';
      const color = action === 'applied' ? '#28a745'
                  : action === 'skipped' ? 'var(--muted)'
                  : action === 'rejected_by_validator' ? '#ff8800' : '#ccc';
      html += `<tr>
        <td style="padding:4px;border-bottom:1px solid var(--border)">${_esc((r.ts||'').slice(5,16))}</td>
        <td style="padding:4px;border-bottom:1px solid var(--border)">${_esc(r.learner_name||'')}</td>
        <td style="padding:4px;border-bottom:1px solid var(--border)"><code>${_esc(r.param_key||'')}</code></td>
        <td style="padding:4px;border-bottom:1px solid var(--border)">${_esc(r.old_value||'—')} → <b>${_esc(r.new_value||'—')}</b></td>
        <td style="padding:4px;border-bottom:1px solid var(--border);color:${color}">${_esc(action)}</td>
        <td style="padding:4px;border-bottom:1px solid var(--border);color:var(--muted);font-size:.8rem">${_esc((r.gate_reason||'').slice(0,100))}</td>
      </tr>`;
    });
    html += '</tbody></table>';
    el.innerHTML = html;
  }

  function _renderNoiseGates(g) {
    const el = _$('fais-noise-gates');
    if (!el) return;
    const items = [
      ['rejected_low_score',          'Below min score'],
      ['rejected_consensus_variance', 'Variance gap (N-1)'],
      ['consensus_rejected',          'Consensus reject'],
      ['rejected_killswitch',         'Kill-switch'],
      ['rejected_sizing',             'Sizing failed'],
      ['rejected_vpin_toxicity',      'VPIN veto (N-4)'],
      ['rejected_cascade_risk',       'Cascade veto (A-E)'],
      ['red_team_veto_hard',          'Red-Team veto (A-A)'],
      ['red_team_penalty',            'Red-Team penalty (soft)'],
    ];
    el.innerHTML = items.map(([k, label]) => {
      const v = parseInt(g[k] || 0, 10) || 0;
      const tone = v > 50 ? '#ff4444' : v > 20 ? '#ffaa00' : v > 0 ? '#ccc' : 'var(--muted)';
      return `<div style="padding:8px;background:var(--bg-elev);border-radius:6px">
        <div style="color:var(--muted);font-size:.78rem">${_esc(label)}</div>
        <div style="font-size:1.4rem;font-weight:600;color:${tone}">${v}</div>
      </div>`;
    }).join('');
  }

  function _renderReminders(list) {
    const el = _$('fais-reminders');
    if (!el) return;
    if (!list.length) { el.innerHTML = '<em>No active reminders.</em>'; return; }
    el.innerHTML = list.map(r => {
      const days = parseInt(r.days_remaining, 10);
      const tone = days <= 0 ? '#ff4444' : days <= 3 ? '#ffaa00' : '#ccc';
      const due = days <= 0 ? '⚠ DUE NOW'
                : days === 1 ? 'in 1 day'
                : `in ${days} days`;
      return `<div>• <b>${_esc(r.title)}</b>: <span style="color:${tone}">${_esc(due)}</span>
        <span style="color:var(--muted);font-size:.85rem"> — ${_esc(r.note || '')}</span></div>`;
    }).join('');
  }

  function _renderCostVsPnl(c) {
    const grid = _$('fais-cost-pnl');
    const breakdown = _$('fais-cost-breakdown');
    if (!grid) return;
    if (!c || !c['24h']) {
      grid.innerHTML = '<em>No cost data yet — waiting for first cycle.</em>';
      if (breakdown) breakdown.innerHTML = '';
      return;
    }
    const cards = [];
    [['24h','24h'], ['7d','7d'], ['30d','30d']].forEach(([k, label]) => {
      const w = c[k] || {};
      const cost = w.api_cost_usd || 0;
      const pnl  = w.realized_pnl || 0;
      const net  = w.net || 0;
      const ratio = w.ratio;
      const netColor = net >= 0 ? '#28a745' : '#ff4444';
      const ratioStr = ratio == null ? '—'
                      : ratio >= 1.0 ? `<span style="color:#28a745">${ratio.toFixed(2)}×</span>`
                      : `<span style="color:#ff4444">${ratio.toFixed(2)}×</span>`;
      cards.push(`<div style="padding:10px;background:var(--bg-elev);border-radius:6px">
        <div style="color:var(--muted);font-size:.75rem;text-transform:uppercase">${_esc(label)}</div>
        <div style="font-size:.85rem;margin-top:4px">API cost: <b>$${cost.toFixed(2)}</b></div>
        <div style="font-size:.85rem">Realised P&amp;L: <b>$${pnl.toFixed(2)}</b></div>
        <div style="font-size:.95rem;margin-top:4px;color:${netColor}">Net: <b>$${net.toFixed(2)}</b></div>
        <div style="font-size:.75rem;color:var(--muted);margin-top:2px">
          ${w.trades || 0} trades · WR ${w.wr_pct == null ? '—' : w.wr_pct + '%'} · P&amp;L ÷ cost: ${ratioStr}
        </div>
      </div>`);
    });
    grid.innerHTML = cards.join('');

    if (breakdown) {
      const be = c.break_even || {};
      const eq = be.equity_now || 0;
      const todayPct = be.today_daily_pct;
      const trailPct = be.trailing_daily_pct;
      const beToday = be.break_even_equity_today;
      const beTrail = be.break_even_equity_trailing;
      let html = '<div style="font-weight:600;color:var(--text);margin-bottom:4px">Break-even analysis</div>';
      html += `<div>Equity today: <b>$${eq.toFixed(2)}</b> · Daily API spend: <b>$${(be.daily_api_cost_usd || 0).toFixed(2)}</b></div>`;
      if (todayPct != null) {
        html += `<div>Today's daily rate: <b>${todayPct >= 0 ? '+' : ''}${todayPct.toFixed(3)}%</b> → break-even equity at this pace: <b>${beToday == null ? 'never (negative rate)' : '$' + Math.round(beToday).toLocaleString()}</b></div>`;
      }
      if (trailPct != null) {
        html += `<div>7d average daily rate: <b>${trailPct >= 0 ? '+' : ''}${trailPct.toFixed(3)}%</b> → break-even equity at this pace: <b>${beTrail == null ? 'never (negative rate)' : '$' + Math.round(beTrail).toLocaleString()}</b></div>`;
      }
      html += '<div style="margin-top:6px;font-size:.78rem;color:var(--muted)">Attributed modules: call_analyzer (Opus+Sonnet consensus), scanner_quick, live_trade, red_team_agent, post_mortem, setup_classifier. Manual-chain API spend not counted here.</div>';
      breakdown.innerHTML = html;
    }
  }

  function _renderCacheStats(s) {
    const el = _$('fais-cache-stats');
    if (!el) return;
    if (!s || s.hits == null) { el.innerHTML = '<em>No cache data yet.</em>'; return; }
    const total = (s.hits || 0) + (s.misses || 0);
    const hitRate = s.hit_rate != null ? (s.hit_rate * 100).toFixed(1) + '%' : '—';
    const items = [
      ['Hit rate',     hitRate],
      ['Hits',         s.hits || 0],
      ['Misses',       s.misses || 0],
      ['Writes',       s.writes || 0],
      ['Evictions',    s.evictions || 0],
      ['Cache size',   s.size || 0],
      ['TTL (min)',    s.ttl_min || 0],
      ['Total checks', total],
    ];
    el.innerHTML = items.map(([k, v]) =>
      `<div style="padding:6px 10px;background:var(--bg-elev);border-radius:4px">
        <div style="color:var(--muted);font-size:.7rem">${_esc(k)}</div>
        <div style="font-weight:600">${_esc(String(v))}</div>
      </div>`
    ).join('');
  }

  function _renderEdgeDecay(d) {
    const el = _$('fais-edge-decay');
    if (!el) return;
    const keys = Object.keys(d);
    if (!keys.length) { el.innerHTML = '<em>(no archetype data)</em>'; return; }
    let html = '<table style="width:100%;border-collapse:collapse"><thead><tr>'
      + '<th style="text-align:left;padding:4px;border-bottom:1px solid var(--border)">Archetype</th>'
      + '<th style="padding:4px;border-bottom:1px solid var(--border)">N</th>'
      + '<th style="padding:4px;border-bottom:1px solid var(--border)">Recent μ</th>'
      + '<th style="padding:4px;border-bottom:1px solid var(--border)">CUSUM</th>'
      + '<th style="padding:4px;border-bottom:1px solid var(--border)">Page-Hinkley</th>'
      + '<th style="padding:4px;border-bottom:1px solid var(--border)">Severity</th>'
      + '</tr></thead><tbody>';
    keys.forEach(k => {
      const r = d[k];
      const sev = r.severity || 'ns';
      const tone = sev === 'alert' ? '#ff4444'
                 : sev === 'watch' ? '#ffaa00'
                 : sev === 'ok' ? '#28a745' : 'var(--muted)';
      html += `<tr>
        <td style="padding:4px;border-bottom:1px solid var(--border)">${_esc(k)}</td>
        <td style="padding:4px;border-bottom:1px solid var(--border);text-align:right">${parseInt(r.n||0,10)}</td>
        <td style="padding:4px;border-bottom:1px solid var(--border);text-align:right">${r.recent_mean?.toFixed?.(2) ?? '—'}</td>
        <td style="padding:4px;border-bottom:1px solid var(--border);text-align:right">${r.cusum_value?.toFixed?.(2) ?? '—'}${r.cusum_alert ? '!' : ''}</td>
        <td style="padding:4px;border-bottom:1px solid var(--border);text-align:right">${r.ph_value?.toFixed?.(2) ?? '—'}${r.ph_alert ? '!' : ''}</td>
        <td style="padding:4px;border-bottom:1px solid var(--border);color:${tone}"><b>${_esc(sev)}</b></td>
      </tr>`;
    });
    html += '</tbody></table>';
    el.innerHTML = html;
  }

  // Auto-load when nav switches to faistats
  document.addEventListener('DOMContentLoaded', () => {
    const orig = window.showPage;
    if (typeof orig === 'function') {
      window.showPage = function (name) {
        const result = orig.apply(this, arguments);
        if (name === 'faistats') loadFuturesAIStats();
        return result;
      };
    }
  });
})();
