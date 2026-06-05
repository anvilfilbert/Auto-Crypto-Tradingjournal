
// ══════════════════════════════════════════════════════════════════════════════
// LIVE TRADES — Call Match + Targets Panel (split from 08-live.js v2.1)
// ══════════════════════════════════════════════════════════════════════════════

// Coin-amount formatter — keeps decimals visible for sub-1 positions.
//   15000   → "15k"
//   30e6    → "30M"
//   234.5   → "234"
//   12.345  → "12.35"
//   0.1234  → "0.1234"
//   0.00012 → "0.00012"
// Previously rounded everything <1000 to nearest integer which lost all
// decimals for high-priced coins (0.1 BTC → "0", 0.5 ETH → "1").
function _fmtCoins(n) {
  const raw = parseFloat(n);
  if (!Number.isFinite(raw) || raw === 0) return '0';
  const v = Math.abs(raw);
  if (v >= 1e9) return (v / 1e9).toFixed(v >= 10e9 ? 0 : 1) + 'B';
  if (v >= 1e6) return (v / 1e6).toFixed(v >= 10e6 ? 0 : 1) + 'M';
  if (v >= 1e3) return (v / 1e3).toFixed(v >= 10e3 ? 0 : 1) + 'k';
  if (v >= 100) return raw.toFixed(0);     // 234
  if (v >= 10)  return raw.toFixed(2);     // 23.45
  if (v >= 1)   return raw.toFixed(3);     // 1.234
  if (v >= 0.01) return raw.toFixed(4);    // 0.1234
  // Sub-cent: up to 6 decimals, trim trailing zeros
  return raw.toFixed(6).replace(/0+$/, '').replace(/\.$/, '');
}

function renderMatchBanners(pendingMatches, positions) {
  const container = document.getElementById('match-confirmations');
  const entries   = Object.entries(pendingMatches);
  if (!entries.length) { container.innerHTML = ''; return; }

  container.innerHTML = entries.map(([key, call]) => {
    const pos = positions.find(p => p.symbol + '_' + p.direction === key);
    if (!pos) return '';
    const pnlStr = pos.unrealized_pnl >= 0 ? `+${fmtC(pos.unrealized_pnl)}` : fmtC(pos.unrealized_pnl);
    return `
    <div class="warn-box" style="margin-bottom:16px;display:flex;align-items:flex-start;gap:14px;flex-wrap:wrap" id="match-banner-${call.id}">
      <div style="flex:1;min-width:200px">
        <strong style="font-size:.9rem;color:var(--text)">📡 Call Match Detected: ${call.symbol} ${call.direction}</strong>
        <div style="margin-top:4px;font-size:.8rem;line-height:1.5">
          You have an open ${pos.direction} on <strong>${pos.symbol}</strong> (${pnlStr} USDT unrealized)
          that matches your saved call from ${(call.created_at||'').slice(0,10)}.
          <br>Setup: ${call.setup_score||'?'}/10 ${call.setup_label||''} · ${call.trade_type||''} ·
          SL: <span style="color:var(--red)">${call.sl_price||'—'}</span> ·
          TP1: <span style="color:var(--accent3)">${call.tp1_price||'—'}</span>
        </div>
      </div>
      <div style="display:flex;gap:8px;align-items:center;flex-shrink:0">
        <button class="btn btn-primary btn-sm" onclick="confirmMatch(${call.id}, '${key}', ${pos.id || 'null'}, '${pos.exchange || 'bitget'}')">✅ Yes, this is that trade</button>
        <button class="btn btn-secondary btn-sm" onclick="dismissMatch(${call.id})">✗ Not this trade</button>
      </div>
    </div>`;
  }).join('');
}

async function confirmMatch(callId, key, positionId, exchange) {
  const res = await api('/api/calls/' + callId + '/confirm-match', 'POST', {
    position_id: positionId || null,
    exchange: exchange || 'bitget',
  });
  if (!res.ok) {
    notify('Could not confirm match — ' + (res.error || 'server error'), 'err');
    return;
  }
  document.getElementById('match-banner-' + callId)?.remove();
  const savedRes = await api('/api/calls/saved');
  if (savedRes.ok) {
    const call = savedRes.data.find(c => c.id === callId);
    if (call) liveCallMatches[key] = call;
  }
  // Use cached liveWaitingLimits so limit badges don't disappear after confirm
  const exchF2 = (typeof _globalExchange !== 'undefined') ? _globalExchange : 'all';
  const displayPos = exchF2 === 'all'
    ? livePositionsCache
    : livePositionsCache.filter(p => (p.exchange || 'bitget') === exchF2);
  renderPositionCards(displayPos, liveWaitingLimits);
  notify('Call linked — will auto-close when position closes', 'ok');
}

async function dismissMatch(callId) {
  await api('/api/calls/' + callId + '/dismiss', 'POST');
  document.getElementById('match-banner-' + callId)?.remove();
}

// ── Position TP/SL cards ─────────────────────────────────────────────────────
// Renders the position's REAL configured TPs and SL as cards (read from the
// position object — these are the actual Bitget plan orders, not the call's
// stale targets). Each card shows: price · % from mark · $ PnL at the
// position's configured size. If SL is missing, the SL card shows
// "SL not set" explicitly so the operator can't miss the gap.
//
// 2026-05-25 redesign — replaced the upper-right "TP / SL" stat summary
// AND the lower call-driven TP/SL cards with this single position-driven row.
function renderPositionTpCards(pos) {
  const mark           = parseFloat(pos.mark_price    || 0);
  const entry          = parseFloat(pos.entry_price   || 0);
  const totalContracts = parseFloat(pos.total         || 0);   // size in coins
  const dir            = pos.direction === 'Long' ? 1 : -1;

  // pos.tp_levels[i].size = REAL configured amount per TP (in coins/contracts),
  // populated by bitget_client._get_plan_orders_grouped() from each plan order.
  // pos.tp_levels[i].pct is a SYNTHETIC even-split (100/N) — not the real
  // configured ratio — so we ignore it and price PnL off `size` directly.
  // For Bitget's legacy "Final TP" (the position-level take_profit field that
  // closes ALL remaining when hit), we append it as the last tier with
  // size = total − sum(prior plan sizes).
  const tiers = Array.isArray(pos.tp_levels) ? pos.tp_levels.slice() : [];
  if (pos.take_profit) {
    const tpFinal = parseFloat(pos.take_profit);
    const already = tiers.some(t =>
      Math.abs(parseFloat(t.price) - tpFinal) / tpFinal < 1e-6
    );
    if (!already) {
      const usedContracts = tiers.reduce(
        (s, t) => s + (parseFloat(t.size) || 0), 0
      );
      const remaining = Math.max(0, totalContracts - usedContracts);
      tiers.push({
        idx: tiers.length + 1, price: tpFinal,
        size: remaining || null, hit: false, _is_final: true,
      });
    }
  }

  const sl = pos.stop_loss ? parseFloat(pos.stop_loss) : null;

  // Nothing to show if neither TPs nor SL are configured
  if (tiers.length === 0 && !sl) return '';

  const cards = [];

  tiers.forEach((t, idx) => {
    const tp        = parseFloat(t.price);
    const tpSize    = parseFloat(t.size || 0);   // contracts that close at this TP
    const distMark  = mark > 0  ? ((tp - mark)  / mark  * 100 * dir) : 0;
    const distEntry = entry > 0 ? ((tp - entry) / entry * 100 * dir) : 0;
    // PnL in USDT = contracts × Δprice × direction. Falls back to using the
    // full notional when size is unknown (single-TP legacy without plan-order
    // size info).
    const pnl = tpSize > 0
      ? tpSize * (tp - entry) * dir
      : (parseFloat(pos.size_usdt || 0) * distEntry / 100);
    // Show the actual coin amount on the badge instead of the misleading
    // synthetic percentage. "Final" tag for the legacy close-all TP.
    const amtTxt = tpSize > 0
      ? ` <span style="opacity:.55">(${_fmtCoins(tpSize)})</span>`
      : '';
    const finalTag = t._is_final ? ' <span style="opacity:.6;font-size:.65rem">final</span>' : '';
    const hitMark  = t.hit ? '✓ ' : '';
    const distCol  = distMark >= 0 ? 'color:var(--accent3)' : 'color:var(--red)';
    cards.push(`
      <div class="target-cell target-tp">
        <div class="target-cell-label">${hitMark}TP${idx + 1}${amtTxt}${finalTag}</div>
        <div class="target-cell-price" style="color:var(--accent3)">${tp}</div>
        <div class="target-cell-dist" style="${distCol}">${distMark >= 0 ? '+' : ''}${distMark.toFixed(2)}% from mark</div>
        <div class="target-cell-dist" style="color:var(--accent3);font-weight:600">${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)} USDT</div>
      </div>`);
  });

  if (sl !== null) {
    const distMark = mark > 0  ? ((sl - mark)  / mark  * 100 * dir) : 0;
    // SL closes the FULL remaining position by convention → use total contracts.
    const pnl      = totalContracts > 0
      ? totalContracts * (sl - entry) * dir
      : 0;
    const distCol  = distMark >= 0 ? 'color:var(--accent3)' : 'color:var(--red)';
    cards.push(`
      <div class="target-cell target-sl">
        <div class="target-cell-label">SL</div>
        <div class="target-cell-price" style="color:var(--red)">${sl}</div>
        <div class="target-cell-dist" style="${distCol}">${distMark >= 0 ? '+' : ''}${distMark.toFixed(2)}% from mark</div>
        <div class="target-cell-dist" style="color:var(--red);font-weight:600">${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)} USDT</div>
      </div>`);
  } else {
    // No SL set — make this loud, not subtle. Operator must see the gap.
    cards.push(`
      <div class="target-cell target-sl" style="border-color:rgba(255,179,0,.4);background:rgba(255,179,0,.06)">
        <div class="target-cell-label" style="color:var(--yellow)">SL</div>
        <div class="target-cell-price" style="color:var(--yellow);font-size:.85rem">SL not set</div>
        <div class="target-cell-dist" style="color:var(--muted)">—</div>
        <div class="target-cell-dist" style="color:var(--muted)">—</div>
      </div>`);
  }

  return `
    <div class="call-targets-panel">
      <div class="targets-grid">${cards.join('')}</div>
    </div>`;
}

// ── Linked call metadata panel ───────────────────────────────────────────────
// 2026-05-25: stripped down to just the call's metadata (setup score, R:R,
// archetype, entry timing) + the Mark-Call-Closed control. Previously this
// also showed the call's TP1/TP2/SL/avg-entry as cards, but those were
// computed for the call's recommended entry — not where the position actually
// filled — so they routinely showed values outside the trade's valid range.
// The position's REAL configured TPs/SL are rendered separately in
// renderPositionTpCards() above.
function renderCallTargetsPanel(call, pos) {
  const callKey = call.symbol + '_' + call.direction;
  return `
    <div class="call-targets-panel" style="padding-top:8px">
      ${call.status === 'closed' ? `
      <div style="font-size:.75rem;padding:6px 10px 6px 12px;margin-bottom:10px;
                  background:rgba(255,179,0,.1);border:1px solid rgba(255,179,0,.25);
                  border-radius:6px;color:var(--yellow);display:flex;align-items:center;gap:10px">
        <span>⟳ Previously linked — position may have reopened</span>
        <button class="btn btn-secondary btn-sm" style="flex-shrink:0"
                onclick="confirmMatch(${call.id},'${callKey}',${pos.id||'null'},'${pos.exchange||'bitget'}')">
          Re-activate
        </button>
      </div>` : ''}
      <h4>📡 Linked Call — ${call.trade_type || ''} · ${call.setup_score || '?'}/10 ${call.setup_label || ''} · R:R ${call.rr_ratio || '—'}</h4>
      ${call.entry_timing ? `
        <div style="font-size:.75rem;color:var(--muted);margin-top:4px"><strong style="color:var(--text)">Entry timing:</strong> ${call.entry_timing}</div>` : ''}
      <div style="margin-top:10px;display:flex;gap:8px">
        <button class="btn btn-secondary btn-sm" onclick="closeCall(${call.id});loadLiveTrades()">Mark Call Closed</button>
      </div>
    </div>`;
}

// ── Navigate to call analyzer with a symbol pre-filled ───────────────────────

function prefillCallAnalyzer(symbol, direction) {
  showPage('calls');
  const el = document.getElementById('call-text');
  if (el && !el.value.trim()) {
    const dir = direction || 'Long';
    el.value = `${dir.toUpperCase()} ${symbol} — paste the analyst's call text here`;
    el.focus();
    el.select();
  }
}

// ── Manual call linking ───────────────────────────────────────────────────────

function _normSym(s) { return (s || '').toUpperCase().replace(/[/\-_ ]/g, ''); }
function _normDir(s) { return (s || '').toLowerCase(); }

async function openLinkCallModal(symbol, direction, posId, exchange) {
  try {
    const res = await api('/api/calls/linkable');
    if (!res || !res.ok) { notify('Could not load saved calls', 'err'); return; }
    const calls = res.data || [];
    if (!calls.length) {
      notify('No saved calls found. Paste an analyst call in the Call Analyzer tab to create one.', 'err');
      return;
    }

    document.getElementById('link-call-modal')?.remove();

    const overlay = document.createElement('div');
    overlay.id = 'link-call-modal';
    overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.6);z-index:9999;display:flex;align-items:center;justify-content:center';

  const box = document.createElement('div');
  box.style.cssText = 'background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:24px;max-width:520px;width:90%;max-height:80vh;overflow-y:auto';

  const hdr = document.createElement('div');
  hdr.style.cssText = 'display:flex;justify-content:space-between;align-items:center;margin-bottom:16px';
  const title = document.createElement('strong');
  title.textContent = '🔗 Link Saved Call to ' + symbol + ' ' + direction;
  const closeBtn = document.createElement('button');
  closeBtn.textContent = '✕';
  closeBtn.style.cssText = 'background:none;border:none;color:var(--muted);font-size:1.2rem;cursor:pointer';
  closeBtn.onclick = () => overlay.remove();
  hdr.appendChild(title);
  hdr.appendChild(closeBtn);

  const hint = document.createElement('div');
  hint.style.cssText = 'font-size:.78rem;color:var(--muted);margin-bottom:12px';
  hint.textContent = 'Calls matching this symbol/direction are highlighted. Click any row to link it.';

  box.appendChild(hdr);

  // When there's no analyzed call for this symbol at all, show a prompt to create one.
  // This covers Telegram calls or any trade opened without running the call analyzer first.
  const hasSymCall = calls.some(c => _normSym(c.symbol) === _normSym(symbol));
  if (!hasSymCall) {
    const noCallBanner = document.createElement('div');
    noCallBanner.style.cssText = [
      'padding:10px 12px;margin-bottom:12px',
      'background:rgba(255,179,0,.1);border:1px solid rgba(255,179,0,.25)',
      'border-radius:6px;font-size:.8rem;color:var(--yellow)',
      'display:flex;align-items:center;gap:10px;flex-wrap:wrap',
    ].join(';');
    const noCallText = document.createElement('span');
    noCallText.textContent = 'No call analyzed for ' + symbol + ' yet (e.g. from Telegram).';
    const analyzeBtn = document.createElement('button');
    analyzeBtn.style.cssText = [
      'flex-shrink:0;padding:4px 10px',
      'border:1px solid rgba(255,179,0,.4)',
      'background:rgba(255,179,0,.15);color:var(--yellow)',
      'border-radius:4px;cursor:pointer;font-size:.75rem',
    ].join(';');
    analyzeBtn.textContent = '📝 Analyze First';
    analyzeBtn.onclick = () => {
      overlay.remove();
      prefillCallAnalyzer(symbol, direction);
    };
    noCallBanner.appendChild(noCallText);
    noCallBanner.appendChild(analyzeBtn);
    box.appendChild(noCallBanner);
  }

  box.appendChild(hint);

  calls.forEach(c => {
    const symMatch = _normSym(c.symbol) === _normSym(symbol);
    const dirMatch = _normDir(c.direction) === _normDir(direction);
    const row = document.createElement('div');
    row.style.cssText = [
      'display:flex;align-items:center;gap:10px;padding:8px 10px',
      'border:1px solid ' + (symMatch && dirMatch ? 'rgba(108,99,255,.4)' : 'var(--border)'),
      'border-radius:6px;margin-bottom:6px;cursor:pointer',
      'background:' + (symMatch && dirMatch ? 'rgba(108,99,255,.12)' : 'transparent'),
    ].join(';');
    row.onclick = () => confirmLinkCall(c.id, symbol, direction, posId, exchange);

    const sym = document.createElement('span');
    sym.style.cssText = 'font-weight:700;font-size:.85rem';
    sym.textContent = (c.symbol || '') + ' ' + (c.direction || '');

    const meta = document.createElement('span');
    meta.style.cssText = 'font-size:.75rem;color:var(--muted)';
    meta.textContent = (c.trade_type || '') + ' · ' + (c.setup_score || '?') + '/10';

    const date = document.createElement('span');
    date.style.cssText = 'font-size:.72rem;color:var(--muted);margin-left:auto';
    date.textContent = (c.created_at || '').slice(0, 10);

    const badge = document.createElement('span');
    badge.style.cssText = 'font-size:.7rem;padding:2px 7px;border-radius:10px;background:rgba(121,134,203,.1);color:var(--muted)';
    badge.textContent = c.status || '';

    row.appendChild(sym);
    row.appendChild(meta);
    row.appendChild(date);
    row.appendChild(badge);
    box.appendChild(row);
  });

    overlay.appendChild(box);
    document.body.appendChild(overlay);
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
  } catch(err) {
    notify('Error opening link modal: ' + err.message, 'err');
  }
}

async function confirmLinkCall(callId, symbol, direction, posId, exchange) {
  try {
    const res = await api('/api/calls/' + callId + '/confirm-match', 'POST', {
      position_id: posId || null,
      exchange: exchange || 'bitget',
    });
    document.getElementById('link-call-modal')?.remove();
    if (!res || !res.ok) { notify('Link failed: ' + ((res && res.error) || 'server error'), 'err'); return; }

    const savedRes = await api('/api/calls/saved');
    if (savedRes && savedRes.ok) {
      const call = savedRes.data.find(c => c.id === callId);
      if (call) liveCallMatches[symbol + '_' + direction] = call;
    }
    const exchF = (typeof _globalExchange !== 'undefined') ? _globalExchange : 'all';
    const disp  = exchF === 'all' ? livePositionsCache
      : livePositionsCache.filter(p => (p.exchange || 'bitget') === exchF);
    renderPositionCards(disp, liveWaitingLimits);
    notify('Call linked — targets panel updated', 'ok');
  } catch(err) {
    notify('Link error: ' + err.message, 'err');
  }
}
