// 18-futures-ai.js — Futures-AI page controller + AUTO-TRADER badge

const _FAI_STATE_LABELS = {
  active:             { text: '▶ ACTIVE',                   bg: 'rgba(38,217,107,.15)',  fg: 'var(--accent3)' },
  pause_after_close:  { text: '⏸ Pause after close',         bg: 'rgba(255,179,0,.12)',   fg: 'var(--yellow)' },
  pause_now:          { text: '⏹ Paused',                    bg: 'rgba(239,83,80,.12)',   fg: 'var(--red)' },
  circuit_breaker:    { text: '⚠ Circuit breaker tripped',   bg: 'rgba(239,83,80,.18)',   fg: 'var(--red)' },
};

async function loadFuturesAI() {
  try {
    const r = await api('/api/futures-ai/state');
    if (!r.ok) throw new Error(r.error || 'failed');
    const d = r.data;
    _renderFuturesAI(d);
    _updateAutoTraderBadge(d);
  } catch (e) {
    const el = document.getElementById('fai-decisions');
    if (el) el.textContent = 'Failed to load: ' + e.message;
  }
}

// Build a line of mixed text + bold spans using safe DOM methods.
// `parts` is an array of strings; even indices are plain text, odd indices
// are bolded. Example: _bold(['Risk per trade: ', '2%', ' of equity'])
function _bold(parts) {
  const span = document.createElement('div');
  parts.forEach((p, i) => {
    if (i % 2 === 0) {
      span.appendChild(document.createTextNode(p));
    } else {
      const b = document.createElement('strong');
      b.textContent = p;
      span.appendChild(b);
    }
  });
  return span;
}

function _renderFuturesAI(d) {
  const cfg  = d.config || {};
  const rt   = d.runtime || {};
  const lbl  = _FAI_STATE_LABELS[rt.state] || _FAI_STATE_LABELS.pause_now;

  const sb = document.getElementById('fai-status-badge');
  if (sb) {
    sb.textContent = lbl.text;
    sb.style.color = lbl.fg;
    sb.style.background = lbl.bg;
    sb.style.padding = '3px 10px';
    sb.style.borderRadius = '12px';
    sb.style.display = 'inline-block';
  }

  const md = document.getElementById('fai-mode');
  if (md) {
    md.textContent = (cfg.enabled ? String(cfg.mode || '').toUpperCase() : 'DISABLED');
    md.style.color = cfg.enabled
      ? (cfg.mode === 'real' ? 'var(--red)' : 'var(--accent)')
      : 'var(--muted)';
  }

  const eq = document.getElementById('fai-equity');
  if (eq) eq.textContent = '$' + (rt.equity_usdt != null ? rt.equity_usdt.toFixed(2) : '—');
  const pos = document.getElementById('fai-positions');
  if (pos) pos.textContent = (rt.open_positions ?? 0) + ' / ' + (rt.max_concurrent ?? cfg.max_concurrent_positions ?? 0);

  const pn = document.getElementById('fai-pnl-24h');
  if (pn) {
    const pct = rt.daily_pnl_pct ?? 0;
    pn.textContent = (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%';
    pn.style.color = pct >= 0 ? 'var(--accent3)' : 'var(--red)';
  }

  // 7d + 30d realized P&L — dollar + percent (matches Unrealized pill format).
  // % is of starting_equity (parallel to 24h pill's denominator).
  const _fmtWindowPnl = (usd, pct) => {
    const sign = usd >= 0 ? '+' : '';
    return `${sign}$${usd.toFixed(2)} (${sign}${pct.toFixed(2)}%)`;
  };
  const p7 = document.getElementById('fai-pnl-7d');
  if (p7) {
    const usd = rt.pnl_7d_usd ?? 0;
    const pct = rt.pnl_7d_pct ?? 0;
    p7.textContent = _fmtWindowPnl(usd, pct);
    p7.style.color = usd >= 0 ? 'var(--accent3)' : 'var(--red)';
  }
  const p30 = document.getElementById('fai-pnl-30d');
  if (p30) {
    const usd = rt.pnl_30d_usd ?? 0;
    const pct = rt.pnl_30d_pct ?? 0;
    p30.textContent = _fmtWindowPnl(usd, pct);
    p30.style.color = usd >= 0 ? 'var(--accent3)' : 'var(--red)';
  }

  // Headline one-liner — combines state + equity + breaker + hedge + streak
  // into a single read-at-a-glance summary. Color-coded against state.
  const headline = document.getElementById('fai-headline');
  if (headline) {
    const eqv = rt.equity_usdt ?? 0;
    const totalPct = rt.total_pnl_pct ?? 0;
    const dailyPct = rt.daily_pnl_pct ?? 0;
    const openN = rt.open_positions ?? 0;
    const capN = rt.max_concurrent ?? cfg.max_concurrent_positions ?? 0;
    const wins = rt.consecutive_wins_since_reset ?? 0;
    const losses = rt.consecutive_losses_since_reset ?? 0;
    const streak = rt.streak_multiplier ?? 1;
    const hedge = rt.active_hedge;
    const state = rt.state || '—';

    // State color — green if active+OK, red if breaker, yellow if paused
    const stateColors = {
      'active':            'var(--accent3)',
      'circuit_breaker':   'var(--red)',
      'pause_now':         'var(--yellow,#ffb300)',
      'pause_after_close': 'var(--yellow,#ffb300)',
    };
    const stateColor = stateColors[state] || 'var(--muted)';

    const totalColor = totalPct >= 0 ? 'var(--accent3)' : 'var(--red)';
    const dailyColor = dailyPct >= 0 ? 'var(--accent3)' : 'var(--red)';

    // Build a single-line summary with multiple colored spans
    while (headline.firstChild) headline.removeChild(headline.firstChild);
    const spans = [
      [`${state.toUpperCase()}`, stateColor, 700],
      [' · ', null, null],
      [`equity $${eqv.toFixed(2)}`, null, 600],
      [' (', null, null],
      [`${totalPct >= 0 ? '+' : ''}${totalPct.toFixed(2)}%`, totalColor, 700],
      [' total, ', null, null],
      [`${dailyPct >= 0 ? '+' : ''}${dailyPct.toFixed(2)}%`, dailyColor, 600],
      [' 24h)', null, null],
      [' · ', null, null],
      [`${openN}/${capN} open`, null, 600],
    ];
    if (wins > 0) {
      spans.push([' · ', null, null]);
      spans.push([`${wins} win streak`, 'var(--accent3)', 600]);
      if (streak > 1) spans.push([` (×${streak.toFixed(1)})`, 'var(--accent3)', 600]);
    }
    if (losses > 0) {
      spans.push([' · ', null, null]);
      spans.push([`${losses} loss streak`, 'var(--red)', 600]);
    }

    // 7d + 30d winrate (only show if any closes in window)
    const wr7Total  = rt.winrate_7d_total  ?? 0;
    const wr30Total = rt.winrate_30d_total ?? 0;
    if (wr7Total > 0) {
      const wr  = rt.winrate_7d_pct ?? 0;
      const wins = rt.winrate_7d_wins ?? 0;
      spans.push([' · ', null, null]);
      spans.push([`7d WR ${wr.toFixed(0)}% (${wins}/${wr7Total})`,
                  wr >= 50 ? 'var(--accent3)' : 'var(--red)', 600]);
    }
    if (wr30Total > 0) {
      const wr  = rt.winrate_30d_pct ?? 0;
      const wins = rt.winrate_30d_wins ?? 0;
      spans.push([' · ', null, null]);
      spans.push([`30d WR ${wr.toFixed(0)}% (${wins}/${wr30Total})`,
                  wr >= 50 ? 'var(--accent3)' : 'var(--red)', 600]);
    }

    if (hedge) {
      spans.push([' · ', null, null]);
      spans.push([`🛡 hedge active`, 'var(--yellow,#ffb300)', 700]);
    }
    spans.forEach(([text, color, weight]) => {
      const s = document.createElement('span');
      s.textContent = text;
      if (color)  s.style.color = color;
      if (weight) s.style.fontWeight = weight;
      headline.appendChild(s);
    });
  }

  const rc = document.getElementById('fai-risk-config');
  if (rc) {
    while (rc.firstChild) rc.removeChild(rc.firstChild);
    const dd  = (cfg.daily_dd_breaker_pct ?? 0) * 100;
    const tot = (cfg.total_dd_breaker_pct ?? 0) * 100;
    const cl  = cfg.consecutive_loss_breaker ?? 0;
    const muls = Object.values(cfg.risk_score_multipliers || {}).join(' / ×');

    rc.appendChild(_bold([
      'Risk per trade: ',
      ((cfg.risk_per_trade_pct ?? 0) * 100).toFixed(1) + '%',
      ' of equity, Kelly-scaled by score (×' + muls + ')',
    ]));
    rc.appendChild(_bold([
      'Max notional per trade: ',
      '$' + (cfg.max_notional_usdt ?? 0),
      '',
    ]));
    rc.appendChild(_bold([
      'Max leverage: ',
      (cfg.max_leverage ?? 10) + 'x',
      ' · Max concurrent: ',
      String(cfg.max_concurrent_positions ?? 3),
    ]));
    rc.appendChild(_bold([
      'Daily DD breaker at ',
      dd.toFixed(1) + '%',
      ' · Total DD breaker at ',
      tot.toFixed(1) + '%',
      ' · ' + cl + ' consecutive losses',
    ]));
    rc.appendChild(_bold([
      'Strategic filters: ',
      'none at this layer',
      ' — day/symbol/direction handled by the data-driven scoring + rulebook',
    ]));

    const reasonLine = document.createElement('div');
    reasonLine.appendChild(document.createTextNode('Current state: '));
    const stateSpan = document.createElement('strong');
    stateSpan.style.color = lbl.fg;
    stateSpan.textContent = rt.state || '—';
    reasonLine.appendChild(stateSpan);
    reasonLine.appendChild(document.createTextNode(' — ' + (rt.reason || 'ok')));
    rc.appendChild(reasonLine);
  }

  _loadFuturesAILog();
  _loadFuturesAIPositions();

  // Feature 10 — update streak-mode pill from config
  const sm = document.getElementById('fai-streak-mode-current');
  if (sm && d.config) {
    const mode = d.config.streak_mode || 'compound';
    sm.textContent = (mode === 'euphoria_dampener') ? '↓ Euphoria dampener (shrink after 3+ wins)'
                    : (mode === 'off')              ? 'Off (always 1×)'
                                                    : '↑ Compound (grow with wins)';
  }
}


// Feature 10 — switch streak mode via UI toggle.
async function setStreakMode(mode) {
  if (!['compound', 'euphoria_dampener', 'off'].includes(mode)) return;
  try {
    const res = await api('/api/futures-ai/streak-mode', 'POST', { mode });
    if (res.ok) {
      notify('Streak mode set to: ' + mode, 'success');
      loadFuturesAI();   // refresh
    } else {
      notify('Failed: ' + (res.error || 'unknown'), 'danger');
    }
  } catch (e) {
    notify('Error: ' + e.message, 'danger');
  }
}


// Sum unrealized P&L across open auto-trader positions and write the
// total + % into the #fai-unrealized pill. Called from the positions
// loader since that's where we already have the open-rows data.
function _setFuturesAIUnrealized(openRows, equityUsdt) {
  const el = document.getElementById('fai-unrealized');
  if (!el) return;
  if (!openRows || !openRows.length) {
    el.textContent = '$0.00';
    el.style.color = 'var(--muted)';
    return;
  }
  const totalUnrl = openRows.reduce(
    (s, p) => s + (parseFloat(p.unrealized_pnl) || 0), 0
  );
  const pct = equityUsdt ? (totalUnrl / equityUsdt) * 100 : 0;
  const sign = totalUnrl >= 0 ? '+' : '';
  el.textContent = `${sign}$${totalUnrl.toFixed(2)} (${sign}${pct.toFixed(2)}%)`;
  el.style.color = totalUnrl >= 0 ? 'var(--accent3)' : 'var(--red)';
}


async function _loadFuturesAIPositions() {
  const openEl = document.getElementById('fai-open-positions');
  const closedEl = document.getElementById('fai-closed-positions');
  if (!openEl) return;
  try {
    const r = await api('/api/futures-ai/positions?closed=15');
    if (!r.ok) throw new Error(r.error || 'failed');
    const d = r.data;
    const source = d.source;   // 'real' or 'paper'
    const openRows = d.open || [];
    const closedRows = d.recent_closed || [];
    const equityNow  = parseFloat(d.equity_usdt) || 0;

    // Header counts
    const openCount = document.getElementById('fai-open-count');
    const closedCount = document.getElementById('fai-closed-count');
    if (openCount) openCount.textContent = `(${openRows.length} ${source})`;
    if (closedCount) closedCount.textContent = `(${closedRows.length} ${source})`;

    // Open positions table
    while (openEl.firstChild) openEl.removeChild(openEl.firstChild);
    if (!openRows.length) {
      openEl.textContent = 'No open auto-trader positions.';
    } else {
      openEl.appendChild(_buildOpenPositionsTable(openRows, source));
    }

    // Unrealized total pill — uses equity from the equity element (already
    // populated by the state loader); falls back to no % if unavailable.
    const eqEl = document.getElementById('fai-equity');
    const eqVal = eqEl ? parseFloat((eqEl.textContent || '').replace('$', '')) : 0;
    _setFuturesAIUnrealized(openRows, eqVal || 100);

    // Closed positions table
    while (closedEl.firstChild) closedEl.removeChild(closedEl.firstChild);
    if (!closedRows.length) {
      closedEl.textContent = 'No closed auto-trader trades yet.';
    } else {
      closedEl.appendChild(_buildClosedPositionsTable(closedRows, source, equityNow));
    }
  } catch (e) {
    openEl.textContent = 'Failed: ' + e.message;
  }
}


// Format a tp_levels JSON array as a multi-line cell. Each line shows
// "TPn  price  (pct%)" so the operator can see the full Opus-emitted
// ladder at a glance. Falls back to preset_tp (legacy single-TP) when
// no ladder is present.
function _formatTpLevels(p) {
  const levels = Array.isArray(p.tp_levels) ? p.tp_levels : [];
  if (!levels.length) {
    return _num(p.preset_tp) || '—';
  }
  // Stack one per line inside the cell. Active (not-yet-hit) levels in
  // brighter text; hit levels dimmed + struck through + "· hit MM-DD HH:MM"
  // appended in non-struck-through trailing span so the hit time is readable.
  const frag = document.createDocumentFragment();
  levels.forEach((lvl, i) => {
    const line = document.createElement('div');
    const idx = lvl.idx ?? (i + 1);
    const price = _num(lvl.price) || '?';
    const pct = lvl.pct != null ? `(${Math.round(lvl.pct)}%)` : '';

    if (lvl.hit) {
      // Struck-through "TPn price (pct%)" + plain "· hit MM-DD HH:MM"
      const struck = document.createElement('span');
      struck.textContent = `TP${idx}  ${price}  ${pct}`.trim();
      struck.style.cssText = 'text-decoration:line-through;opacity:.55';
      line.appendChild(struck);
      const ts = (lvl.hit_at || '').replace('T', ' ').slice(5, 16);
      if (ts) {
        const tag = document.createElement('span');
        tag.textContent = `  · hit ${ts}`;
        tag.style.cssText = 'opacity:.75;color:var(--accent3)';
        line.appendChild(tag);
      }
      line.style.cssText = 'font-size:.74rem;line-height:1.4';
    } else {
      line.textContent = `TP${idx}  ${price}  ${pct}`.trim();
      line.style.cssText = 'font-size:.74rem;line-height:1.4';
    }
    frag.appendChild(line);
  });
  return frag;
}


function _buildOpenPositionsTable(rows, source) {
  const tbl = document.createElement('table');
  tbl.style.cssText = 'width:100%;border-collapse:collapse;font-size:.8rem';
  const thead = tbl.createTHead();
  const hrow = thead.insertRow();
  // Paper = Live (1:1). Paper mode now ships the same enriched fields
  // (mark_price, unrealized_pnl, achieved_profits, size_contracts, …)
  // from the API, so one column set + one render path serves both.
  const headers = ['Symbol','Dir','Entered','Entry','Mark','% Move','Unrl P&L','Realised P&L','Size','Notional','Lev','SL','TPs'];
  headers.forEach(h => {
    const th = document.createElement('th');
    th.textContent = h;
    th.style.cssText = 'text-align:left;color:var(--muted);font-weight:600;padding:5px 8px;border-bottom:1px solid var(--border)';
    hrow.appendChild(th);
  });
  const tb = tbl.createTBody();
  rows.forEach(p => {
    const tr = tb.insertRow();
    const _enteredStr = (rec) => {
      const t = rec.open_time || rec.opened_at || '';
      return t ? t.replace('T', ' ').slice(5, 16) : '—';
    };

    // Realised on an OPEN position = partial-close profits already
    // booked (e.g. TP1 partial close on a multi-tier ladder). Bitget
    // calls this achieved_profits; defaults to 0 when no partial fired.
    const _ach = parseFloat(p.achieved_profits || 0) || 0;
    const cells = [
      p.symbol,
      p.direction,
      _enteredStr(p),
      _num(p.entry_price),
      _num(p.mark_price),
      ((p.unrealized_pct >= 0 ? '+' : '') + (p.unrealized_pct ?? 0).toFixed(2) + '%'),
      ((p.unrealized_pnl >= 0 ? '+' : '') + '$' + (p.unrealized_pnl ?? 0).toFixed(2)),
      (_ach === 0 ? '$0.00' : (_ach > 0 ? '+' : '') + '$' + _ach.toFixed(2)),
      _num(p.size_contracts),
      '$' + (p.notional_usdt ?? 0).toFixed(2),
      p.leverage + 'x',
      _num(p.preset_sl) || '—',
      _formatTpLevels(p),
    ];
    cells.forEach((v, i) => {
      const td = tr.insertCell();
      if (v instanceof DocumentFragment) td.appendChild(v);
      else td.textContent = v;
      const isTpCell = i === 12;
      let cls = 'padding:4px 8px;border-bottom:1px solid var(--border);color:var(--muted)';
      if (isTpCell) cls += ';vertical-align:top';
      // Color the % Move / Unrl P&L / Realised P&L cells.
      if (i === 5 || i === 6 || i === 7) {
        let val;
        if (i === 5)      val = p.unrealized_pct || 0;
        else if (i === 6) val = p.unrealized_pnl || 0;
        else              val = _ach;
        // Muted neutral for zero realised — don't paint green/red on no data.
        if (!(val === 0 && i === 7)) {
          cls += ';color:' + (val >= 0 ? 'var(--accent3)' : 'var(--red)');
        }
      }
      td.style.cssText = cls;
    });
  });
  return tbl;
}


// Friendly labels for the short close_reason codes the executor writes.
// Anything not in this map is rendered verbatim (handles hedge_unwind: <r>
// suffixes and any custom strings).
const _CLOSE_REASON_LABELS = {
  'SL':                'SL hit',
  'TP':                'TP hit',
  'TP1':               'TP1 hit',
  'TP2':               'TP2 hit',
  'BE':                'Break-even stop (manual lifecycle)',
  'BE_stop':           'Stopped at BE level',  // SL fired after BE-move (+ fee buffer)
  'MAE_cut':           'MAE auto-close',
  'trail_stop':        'Trailing stop',
  'manual_close':      'Manual close',
  'early_close':       'Early/manual',
  'hedge_unwind':      'Hedge unwound',
  'pending_reconcile': 'Reconcile pending',
  'unknown':           '—',
};

function _formatCloseReason(raw, source) {
  if (!raw) return source === 'real' ? 'Reconcile pending' : '—';
  // hedge_unwind:<reason> → "Hedge unwound: <reason>"
  if (raw.startsWith('hedge_unwind:')) {
    return 'Hedge unwound: ' + raw.slice('hedge_unwind:'.length).trim();
  }
  return _CLOSE_REASON_LABELS[raw] || raw;
}


function _buildClosedPositionsTable(rows, source, equityNow) {
  const tbl = document.createElement('table');
  tbl.style.cssText = 'width:100%;border-collapse:collapse;font-size:.78rem;table-layout:fixed';
  const thead = tbl.createTHead();
  const hrow = thead.insertRow();
  // Explicit column widths so the Reason column has room to wrap legibly
  // when a hedge unwind or other longer string lands there.
  // 'Trade%'  — return on the margin used for this trade (PnL ÷ margin).
  // 'Port%'   — impact on total portfolio (PnL ÷ equity_now).
  const headerSpec = [
    ['Symbol',  '95px'],
    ['Dir',     '50px'],
    ['Entry',   '85px'],
    ['Close',   '85px'],
    ['P&L',     '80px'],
    ['Trade%',  '70px'],
    ['Port%',   '65px'],
    ['Reason',  'auto'],
    ['Opened',  '95px'],
    ['Closed',  '95px'],
  ];
  headerSpec.forEach(([h, w]) => {
    const th = document.createElement('th');
    th.textContent = h;
    th.style.cssText = 'text-align:left;color:var(--muted);font-weight:600;'
      + 'padding:4px 8px;border-bottom:1px solid var(--border);width:' + w;
    hrow.appendChild(th);
  });
  const tb = tbl.createTBody();
  rows.forEach(p => {
    const tr = tb.insertRow();
    const pnl = p.realized_pnl ?? 0;
    const closePrice = source === 'real' ? p.close_price : p.tp2_price;
    const isHedge = !!p.is_hedge;
    const symbolLabel = isHedge ? `${p.symbol} 🛡` : p.symbol;

    // Trade% — return on margin invested. margin = notional / leverage.
    // Falls back to '—' when we can't reconstruct margin (legacy rows).
    const notional = parseFloat(p.size_usdt ?? p.notional_usdt) || 0;
    const lev      = parseFloat(p.leverage) || 0;
    const margin   = (notional > 0 && lev > 0) ? (notional / lev) : 0;
    const tradePct = margin > 0 ? (pnl / margin) * 100 : null;
    const tradeCell = tradePct == null
      ? '—'
      : ((tradePct >= 0 ? '+' : '') + tradePct.toFixed(2) + '%');

    // Port% — impact on total portfolio.
    const portPct = (equityNow && equityNow > 0) ? (pnl / equityNow) * 100 : null;
    const portCell = portPct == null
      ? '—'
      : ((portPct >= 0 ? '+' : '') + portPct.toFixed(2) + '%');

    const cells = [
      symbolLabel,
      p.direction,
      _num(p.entry_price),
      _num(closePrice),
      ((pnl >= 0 ? '+' : '') + '$' + pnl.toFixed(2)),
      tradeCell,
      portCell,
      _formatCloseReason(p.close_reason, source),
      (p.opened_at || p.open_time || '').slice(5, 16),
      (p.closed_at || p.close_time || '').slice(5, 16),
    ];
    const baseStyle = 'padding:4px 8px;border-bottom:1px solid var(--border);'
      + 'color:var(--muted);vertical-align:top';
    cells.forEach((v, i) => {
      const td = tr.insertCell();
      td.textContent = v;
      let cls = baseStyle;
      if (i === 4) {        // P&L column
        cls += ';color:' + (pnl >= 0 ? 'var(--accent3)' : 'var(--red)');
        cls += ';white-space:nowrap';
      } else if (i === 5) { // Trade%
        cls += ';color:' + (tradePct == null ? 'var(--muted)'
                            : tradePct >= 0 ? 'var(--accent3)' : 'var(--red)');
        cls += ';white-space:nowrap';
      } else if (i === 6) { // Port%
        cls += ';color:' + (portPct == null ? 'var(--muted)'
                            : portPct >= 0 ? 'var(--accent3)' : 'var(--red)');
        cls += ';white-space:nowrap';
      } else if (i === 7) { // Reason column — wrap long strings
        cls += ';white-space:normal;word-break:break-word';
      } else {
        cls += ';white-space:nowrap';
      }
      td.style.cssText = cls;
    });
  });
  return tbl;
}


// Helper: format a number to at most 6 significant digits, drop trailing zeros
function _num(v) {
  if (v == null || v === '') return '—';
  const n = parseFloat(v);
  if (isNaN(n)) return v;
  // Adapt precision to magnitude
  if (Math.abs(n) >= 100)   return n.toFixed(2);
  if (Math.abs(n) >= 1)     return n.toPrecision(5);
  return n.toPrecision(4);
}

async function _loadFuturesAILog() {
  const el = document.getElementById('fai-decisions');
  if (!el) return;
  try {
    const r = await api('/api/futures-ai/log?n=30');
    if (!r.ok) throw new Error(r.error || 'failed');
    const rows = (r.data || {}).rows || [];
    if (!rows.length) {
      el.textContent = 'No decisions logged yet. The chain only fires when state=active AND a scanner+AI consensus signal arrives.';
      return;
    }
    const tbl = document.createElement('table');
    tbl.style.cssText = 'width:100%;border-collapse:collapse;font-size:.78rem;table-layout:fixed';
    const thead = tbl.createTHead();
    const hr = thead.insertRow();
    // Explicit widths on the fixed-content columns leave Details with
    // all remaining width so its JSON has room to wrap legibly.
    const headerSpec = [
      ['Time',    '70px'],
      ['Event',   '160px'],
      ['Symbol',  '90px'],
      ['Dir',     '50px'],
      ['Score',   '50px'],
      ['Details', 'auto'],
    ];
    headerSpec.forEach(([h, w]) => {
      const th = document.createElement('th');
      th.textContent = h;
      th.style.cssText = 'text-align:left;color:var(--muted);font-weight:600;'
        + 'padding:4px 8px;border-bottom:1px solid var(--border);width:' + w;
      hr.appendChild(th);
    });
    const tb = tbl.createTBody();
    rows.forEach(row => {
      const tr = tb.insertRow();
      let details = row.payload_json || '';
      try { details = JSON.stringify(JSON.parse(details), null, 1); } catch {}
      const cells = [
        (row.ts || '').slice(11, 19),
        row.event,
        row.symbol || '',
        row.direction || '',
        row.score || '',
        details,
      ];
      const base = 'padding:4px 8px;border-bottom:1px solid var(--border);color:var(--muted);vertical-align:top';
      cells.forEach((v, i) => {
        const td = tr.insertCell();
        td.textContent = v;
        if (i === 5) {
          // Details column — wrap long JSON, monospace, give it room.
          td.style.cssText = base
            + ';white-space:pre-wrap;word-break:break-word'
            + ';font-family:ui-monospace,SFMono-Regular,monospace;font-size:.72rem';
        } else {
          td.style.cssText = base + ';white-space:nowrap';
        }
      });
    });
    while (el.firstChild) el.removeChild(el.firstChild);
    el.appendChild(tbl);
  } catch (e) {
    el.textContent = 'Failed: ' + e.message;
  }
}

async function setFuturesAIState(newState) {
  const labels = {
    active:             'Activate the auto-trader chain?',
    pause_after_close:  'Pause after current trades close (no new opens)?',
    pause_now:          'PAUSE NOW and CLOSE ALL open positions immediately?',
  };
  if (!confirm(labels[newState] || ('Set state to ' + newState + '?'))) return;
  try {
    const r = await api('/api/futures-ai/state', 'POST', { state: newState });
    if (!r.ok) {
      notify(r.error || 'state-change refused', 'err');
      return;
    }
    notify('state → ' + r.data.state, 'ok');
    loadFuturesAI();
  } catch (e) {
    notify('Failed: ' + e.message, 'err');
  }
}

function _updateAutoTraderBadge(d) {
  const badge = document.getElementById('autotrader-badge');
  const navBadge = document.getElementById('nav-futuresai-badge');
  if (!badge && !navBadge) return;

  const cfg = d.config || {};
  const rt  = d.runtime || {};
  const isActive = cfg.enabled && (rt.state === 'active' || rt.open_positions > 0);

  if (badge) badge.style.display = isActive ? 'inline-block' : 'none';

  if (navBadge) {
    if (cfg.enabled && rt.state === 'active') {
      navBadge.textContent = 'LIVE';
      navBadge.style.background = 'rgba(38,217,107,.18)';
      navBadge.style.color = 'var(--accent3)';
      navBadge.style.display = 'inline-block';
    } else if (rt.state === 'circuit_breaker') {
      navBadge.textContent = '⚠';
      navBadge.style.background = 'rgba(239,83,80,.18)';
      navBadge.style.color = 'var(--red)';
      navBadge.style.display = 'inline-block';
    } else if (rt.state === 'pause_after_close' && rt.open_positions > 0) {
      navBadge.textContent = 'CLOSING';
      navBadge.style.background = 'rgba(255,179,0,.15)';
      navBadge.style.color = 'var(--yellow)';
      navBadge.style.display = 'inline-block';
    } else {
      navBadge.style.display = 'none';
    }
  }
}

setInterval(async () => {
  try {
    const r = await api('/api/futures-ai/state');
    if (r.ok) _updateAutoTraderBadge(r.data);
  } catch {}
}, 30000);

setTimeout(async () => {
  try {
    const r = await api('/api/futures-ai/state');
    if (r.ok) _updateAutoTraderBadge(r.data);
  } catch {}
}, 1500);
