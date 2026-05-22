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
      'Symbol blocklist: ',
      (cfg.symbol_blocklist || []).join(', ') || '(none)',
      '',
    ]));
    rc.appendChild(_bold([
      'Shorts allowed: ',
      cfg.shorts_allowed ? 'yes' : 'no',
      ' · Bad hours: UTC 13/15/19/20 (already enforced by scanner)',
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
    tbl.style.cssText = 'width:100%;border-collapse:collapse;font-size:.78rem';
    const thead = tbl.createTHead();
    const hr = thead.insertRow();
    ['Time','Event','Symbol','Dir','Score','Details'].forEach(h => {
      const th = document.createElement('th');
      th.textContent = h;
      th.style.cssText = 'text-align:left;color:var(--muted);font-weight:600;padding:4px 8px;border-bottom:1px solid var(--border)';
      hr.appendChild(th);
    });
    const tb = tbl.createTBody();
    rows.forEach(row => {
      const tr = tb.insertRow();
      let details = row.payload_json || '';
      try { details = JSON.stringify(JSON.parse(details)); } catch {}
      if (details.length > 110) details = details.slice(0, 107) + '…';
      const cells = [
        (row.ts || '').slice(11, 19),
        row.event,
        row.symbol || '',
        row.direction || '',
        row.score || '',
        details,
      ];
      cells.forEach(v => {
        const td = tr.insertCell();
        td.textContent = v;
        td.style.cssText = 'padding:4px 8px;border-bottom:1px solid var(--border);color:var(--muted)';
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
