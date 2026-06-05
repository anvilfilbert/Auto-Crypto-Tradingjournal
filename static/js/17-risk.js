/* 17-risk.js — Risk Analytics dashboard tab
   Uses the app's existing CSS classes: .kpi-card, .kpi-label, .tbl,
   .table-card, .badge, .risk-badge, .pos/.neg/.neu
*/

function loadRiskDashboard() {
    loadVarPanel();
    loadCorrelationPanel();
    loadAttributionPanel();
    loadKellyPanel();
    loadAlphaDecayPanel();
    loadBlindspotsPanel();
    loadSelfReviewPanel();
}

/* ── Shared helpers ─────────────────────────────────────────────────────── */

function _riskInsight(text) {
    const box = document.createElement('div');
    box.style.cssText = 'font-size:.8rem;color:var(--muted);background:var(--bg3);border-left:3px solid var(--accent);border-radius:0 6px 6px 0;padding:8px 12px;margin-bottom:14px;line-height:1.5';
    box.textContent = text;
    return box;
}

function _alertBanner(text, color) {
    const b = document.createElement('div');
    b.style.cssText = `background:${color}18;border:1px solid ${color}44;border-radius:8px;padding:10px 14px;margin-bottom:12px;font-size:.82rem;color:${color};display:flex;align-items:center;gap:8px`;
    const icon = document.createElement('span');
    icon.textContent = color === 'var(--red)' || color === '#ef5350' ? '⚠' : 'ℹ';
    const msg = document.createElement('span');
    msg.textContent = text;
    b.appendChild(icon);
    b.appendChild(msg);
    return b;
}

function _kpiGrid(items, cols) {
    const grid = document.createElement('div');
    grid.style.cssText = `display:grid;grid-template-columns:repeat(${cols},1fr);gap:12px;margin-bottom:8px`;
    items.forEach(([label, value, sub, cls]) => {
        const card = document.createElement('div');
        card.className = 'kpi-card';
        const lbl = document.createElement('div');
        lbl.className = 'kpi-label';
        lbl.textContent = label;
        const val = document.createElement('div');
        val.style.cssText = 'font-size:1.3rem;font-weight:700;margin:4px 0 2px';
        val.textContent = value;
        if (cls) val.style.color = cls;
        card.appendChild(lbl);
        card.appendChild(val);
        if (sub) {
            const s = document.createElement('div');
            s.className = 'kpi-sub';
            s.textContent = sub;
            card.appendChild(s);
        }
        grid.appendChild(card);
    });
    return grid;
}

function _emptyState(msg) {
    const el = document.createElement('div');
    el.style.cssText = 'text-align:center;padding:28px 16px;color:var(--muted);font-size:.85rem';
    const icon = document.createElement('div');
    icon.style.cssText = 'font-size:1.8rem;margin-bottom:8px;opacity:.4';
    icon.textContent = '📊';
    const txt = document.createElement('div');
    txt.textContent = msg;
    el.appendChild(icon);
    el.appendChild(txt);
    return el;
}

/* ── 1. Value at Risk ───────────────────────────────────────────────────── */
async function loadVarPanel() {
    const el = document.getElementById('risk-var');
    if (!el) return;
    while (el.firstChild) el.removeChild(el.firstChild);
    try {
        const d = await fetch('/api/risk/var').then(r => r.json());
        if (!d.ok || !d.data || !d.data.available) {
            el.appendChild(_emptyState('Open at least one position to calculate VaR.'));
            return;
        }
        const v = d.data;

        el.appendChild(_riskInsight(
            'VaR (Value at Risk) estimates the worst likely 1-day loss based on 90 days of historical price moves. ' +
            '95% VaR means: on 95 out of 100 days, your loss should not exceed this number.'
        ));

        // Risk level badge
        const pct = v.var_95_pct;
        const level = pct > 10 ? ['CRITICAL', 'var(--red)'] : pct > 6 ? ['HIGH', 'var(--red)'] : pct > 3 ? ['MEDIUM', 'var(--yellow)'] : ['LOW', 'var(--accent3)'];
        const badge = document.createElement('div');
        badge.style.cssText = `display:inline-flex;align-items:center;gap:6px;padding:4px 12px;border-radius:20px;font-size:.75rem;font-weight:700;text-transform:uppercase;background:${level[1]}22;color:${level[1]};margin-bottom:12px`;
        badge.textContent = 'Risk Level: ' + level[0];
        el.appendChild(badge);

        el.appendChild(_kpiGrid([
            ['95% VaR — 1 Day',   '$' + v.var_95_usd.toLocaleString('en-US', {maximumFractionDigits: 0}), pct + '% of notional', 'var(--red)'],
            ['99% VaR — 1 Day',   '$' + v.var_99_usd.toLocaleString('en-US', {maximumFractionDigits: 0}), 'Worst 1% of days',   'var(--red)'],
            ['Total Notional',    '$' + v.total_notional.toLocaleString('en-US', {maximumFractionDigits: 0}), 'Open position size',  null],
            ['Data Sample',       v.sample_days + ' days', '90-day simulation window', null],
        ], 4));

    } catch(e) { el.appendChild(_emptyState('VaR calculation failed.')); }
}

/* ── 2. Correlation Matrix ──────────────────────────────────────────────── */
async function loadCorrelationPanel() {
    const el = document.getElementById('risk-correlation');
    if (!el) return;
    while (el.firstChild) el.removeChild(el.firstChild);
    try {
        const d = await fetch('/api/risk/correlation').then(r => r.json());
        if (!d.ok || !d.data || !d.data.available) {
            el.appendChild(_emptyState((d.data && d.data.reason) || 'Open 2 or more positions to see correlation.'));
            return;
        }
        const c = d.data;

        el.appendChild(_riskInsight(
            'Correlation measures how much two positions move together (−1 to +1). ' +
            'Values above 0.70 in the same direction mean you are doubling up on one bet — not truly diversified. ' +
            'Lower is safer.'
        ));

        if (c.high_risk_pairs && c.high_risk_pairs.length > 0) {
            el.appendChild(_alertBanner(
                'Highly correlated same-direction pairs: ' +
                c.high_risk_pairs.map(p => p.symbol_a + ' / ' + p.symbol_b + ' (r=' + p.correlation.toFixed(2) + ')').join('  ·  '),
                '#ef5350'
            ));
        }

        const wrap = document.createElement('div');
        wrap.className = 'table-card';
        const tbl = document.createElement('table');
        tbl.className = 'tbl';
        tbl.style.width = '100%';

        const thead = tbl.createTHead();
        const hr = thead.insertRow();
        ['Pair', 'Correlation', 'Strength', 'Same Direction?'].forEach(h => {
            const th = document.createElement('th');
            th.textContent = h;
            hr.appendChild(th);
        });

        const tbody = tbl.createTBody();
        (c.matrix || []).slice(0, 10).forEach(row => {
            const tr = tbody.insertRow();
            const corr = row.correlation;
            const abs = Math.abs(corr);
            const [label, color] = abs > 0.80 ? ['Very High', 'var(--red)']
                                 : abs > 0.60 ? ['High',      'var(--yellow)']
                                 : abs > 0.40 ? ['Medium',    'var(--muted)']
                                 :              ['Low',       'var(--accent3)'];

            // Pair cell
            const tdPair = tr.insertCell();
            tdPair.style.fontWeight = '600';
            tdPair.textContent = row.symbol_a + ' / ' + row.symbol_b;

            // Correlation bar cell
            const tdCorr = tr.insertCell();
            const barWrap = document.createElement('div');
            barWrap.style.cssText = 'display:flex;align-items:center;gap:8px';
            const bar = document.createElement('div');
            bar.style.cssText = `width:${Math.round(abs * 60)}px;height:6px;border-radius:3px;background:${color};min-width:2px`;
            const num = document.createElement('span');
            num.style.color = color;
            num.style.fontWeight = '700';
            num.style.fontSize = '.85rem';
            num.textContent = corr.toFixed(3);
            barWrap.appendChild(bar);
            barWrap.appendChild(num);
            tdCorr.appendChild(barWrap);

            // Strength badge
            const tdStr = tr.insertCell();
            const badge = document.createElement('span');
            badge.style.cssText = `background:${color}22;color:${color};padding:2px 8px;border-radius:12px;font-size:.72rem;font-weight:700;text-transform:uppercase`;
            badge.textContent = label;
            tdStr.appendChild(badge);

            // Direction cell
            const tdDir = tr.insertCell();
            tdDir.style.color = 'var(--muted)';
            tdDir.style.fontSize = '.8rem';
            tdDir.textContent = '—';  // populated below if we have direction data
        });

        wrap.appendChild(tbl);
        el.appendChild(wrap);

        const note = document.createElement('div');
        note.style.cssText = 'font-size:.75rem;color:var(--muted);margin-top:8px';
        note.textContent = 'Based on ' + c.lookback_days + ' days of daily returns  ·  ' + c.sample_days + ' days of aligned data';
        el.appendChild(note);

    } catch(e) { el.appendChild(_emptyState('Correlation calculation failed.')); }
}

/* ── 3. P&L Attribution ─────────────────────────────────────────────────── */
async function loadAttributionPanel() {
    const el = document.getElementById('risk-attribution');
    if (!el) return;
    while (el.firstChild) el.removeChild(el.firstChild);
    try {
        const d = await fetch('/api/risk/attribution?days=90').then(r => r.json());
        if (!d.ok || !d.data || !d.data.available) {
            el.appendChild(_emptyState('Not enough attributed trade history. Needs BTC price data via internet.'));
            return;
        }
        const a = d.data;

        el.appendChild(_riskInsight(
            'Alpha is P&L that comes from your trading skill — setups you found, timing, exits. ' +
            'Beta is P&L that came purely from BTC going up or down (you would have made it just holding). ' +
            'A good trader has positive alpha. Negative alpha means the market did the work.'
        ));

        // Chain label — attribution is now per-chain (default auto_ai, since manual
        // chain has corrupt size_usdt in some legacy rows that produced absurd numbers).
        const chainLabel = (a.chain || 'auto_ai').toUpperCase() + ' chain · last ' + (a.lookback_days || 90) + 'd';
        const skipNote = (a.skipped_bad_size && a.skipped_bad_size > 0)
            ? ' · ' + a.skipped_bad_size + ' excluded (bad size_usdt)' : '';

        el.appendChild(_kpiGrid([
            ['Total P&L', (a.total_pnl >= 0 ? '+' : '') + '$' + a.total_pnl.toFixed(2), chainLabel + skipNote, a.total_pnl >= 0 ? 'var(--accent3)' : 'var(--red)'],
            ['Alpha — vs BTC buy-and-hold', (a.alpha_pnl >= 0 ? '+' : '') + '$' + a.alpha_pnl.toFixed(2), (a.alpha_pnl >= 0 ? 'outperforming' : 'underperforming') + ' passive BTC', a.alpha_pnl >= 0 ? 'var(--accent3)' : 'var(--red)'],
            ['Beta — BTC market move', (a.beta_pnl >= 0 ? '+' : '') + '$' + a.beta_pnl.toFixed(2), 'What buy-and-hold would have earned', a.beta_pnl >= 0 ? 'var(--accent2)' : 'var(--muted)'],
        ], 3));

        // Visual split bar
        const totalAbs = Math.abs(a.alpha_pnl) + Math.abs(a.beta_pnl);
        if (totalAbs > 0) {
            const alphaPct = Math.round(Math.abs(a.alpha_pnl) / totalAbs * 100);
            const splitWrap = document.createElement('div');
            splitWrap.style.cssText = 'margin:12px 0 4px';
            const splitLabel = document.createElement('div');
            splitLabel.style.cssText = 'font-size:.72rem;color:var(--muted);margin-bottom:4px;text-transform:uppercase;letter-spacing:.05em';
            splitLabel.textContent = 'Alpha / Beta Split';
            const splitBar = document.createElement('div');
            splitBar.style.cssText = 'display:flex;height:8px;border-radius:4px;overflow:hidden;background:var(--bg3)';
            const alphaBar = document.createElement('div');
            alphaBar.style.cssText = `width:${alphaPct}%;background:var(--accent3);transition:width .5s`;
            const betaBar = document.createElement('div');
            betaBar.style.cssText = `width:${100 - alphaPct}%;background:var(--accent2)`;
            splitBar.appendChild(alphaBar);
            splitBar.appendChild(betaBar);
            const splitLegend = document.createElement('div');
            splitLegend.style.cssText = 'display:flex;gap:16px;margin-top:6px;font-size:.72rem;color:var(--muted)';
            const leg1 = document.createElement('span');
            leg1.textContent = '● Alpha (Skill) ' + alphaPct + '%';
            leg1.style.color = 'var(--accent3)';
            const leg2 = document.createElement('span');
            leg2.textContent = '● Beta (BTC) ' + (100 - alphaPct) + '%';
            leg2.style.color = 'var(--accent2)';
            splitLegend.appendChild(leg1);
            splitLegend.appendChild(leg2);
            splitWrap.appendChild(splitLabel);
            splitWrap.appendChild(splitBar);
            splitWrap.appendChild(splitLegend);
            el.appendChild(splitWrap);
        }

        const note = document.createElement('div');
        note.style.cssText = 'font-size:.75rem;color:var(--muted);margin-top:8px';
        note.textContent = a.sample_size + ' trades analyzed  ·  ' + a.attributed + ' attributed to BTC moves';
        el.appendChild(note);

    } catch(e) { el.appendChild(_emptyState('Attribution calculation failed.')); }
}

/* ── 4. Kelly Criterion ─────────────────────────────────────────────────── */
async function loadKellyPanel() {
    const el = document.getElementById('risk-kelly');
    if (!el) return;
    while (el.firstChild) el.removeChild(el.firstChild);
    try {
        const d = await fetch('/api/risk/kelly').then(r => r.json());
        if (!d.ok || !d.data || !d.data.available) {
            el.appendChild(_emptyState((d.data && d.data.reason) || 'Need at least 5 trades with setup scores to calculate Kelly.'));
            return;
        }

        el.appendChild(_riskInsight(
            'Kelly Criterion calculates the mathematically optimal fraction of capital to risk per trade, ' +
            'based on your historical win rate and average win/loss size. ' +
            'Half-Kelly is used here for safety. Hard cap: 20% of capital per trade.'
        ));

        // Show as cards per score bucket — cleaner than a 7-column table
        const buckets = d.data.buckets || [];
        const grid = document.createElement('div');
        grid.style.cssText = 'display:grid;grid-template-columns:repeat(' + Math.min(buckets.length, 3) + ',1fr);gap:12px;margin-bottom:12px';

        buckets.forEach(b => {
            const card = document.createElement('div');
            card.className = 'kpi-card';
            card.style.cssText = card.style.cssText + ';border-top:3px solid var(--accent)';

            const scoreLabel = document.createElement('div');
            scoreLabel.style.cssText = 'font-size:.7rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px';
            scoreLabel.textContent = 'Score ' + b.score_range;

            const rec = document.createElement('div');
            rec.style.cssText = 'font-size:2rem;font-weight:800;color:var(--accent3);margin:2px 0 4px';
            rec.textContent = b.recommended_size_pct + '%';

            const recLabel = document.createElement('div');
            recLabel.style.cssText = 'font-size:.72rem;color:var(--muted);margin-bottom:10px';
            recLabel.textContent = 'Recommended capital per trade';

            const divider = document.createElement('div');
            divider.style.cssText = 'border-top:1px solid var(--border);margin:8px 0';

            // Color the headline 0% red when Kelly is clamped (negative edge),
            // not green — green at 0% reads as "fine, don't bet much" but a
            // clamped value means "your edge is broken at this R:R".
            const isClamped = (b.kelly_raw_pct != null && b.kelly_raw_pct <= 0);
            if (isClamped) {
                rec.style.color = 'var(--danger,#e53935)';
            }

            const rows = [
                ['Win Rate',  b.win_rate + '%'],
                ['Trades',    b.trade_count],
                ['Avg Win',   '+$' + b.avg_win_usd.toFixed(1)],
                ['Avg Loss',  '-$' + b.avg_loss_usd.toFixed(1)],
            ];
            if (b.reward_ratio != null) {
                rows.push(['R:R',         b.reward_ratio.toFixed(2)]);
            }
            if (b.win_rate_breakeven != null) {
                rows.push(['Breakeven WR', b.win_rate_breakeven + '%']);
            }
            if (b.kelly_raw_pct != null && b.kelly_raw_pct !== b.kelly_full_pct) {
                // Show the unclamped value when it disagrees with the displayed one
                rows.push(['Kelly (raw)',  b.kelly_raw_pct + '%']);
            }

            const stats = document.createElement('div');
            stats.style.cssText = 'display:grid;grid-template-columns:1fr 1fr;gap:4px 8px;font-size:.75rem';
            rows.forEach(([k, v]) => {
                const lbl = document.createElement('span');
                lbl.style.color = 'var(--muted)';
                lbl.textContent = k;
                const val = document.createElement('span');
                val.style.fontWeight = '600';
                val.textContent = v;
                stats.appendChild(lbl);
                stats.appendChild(val);
            });

            card.appendChild(scoreLabel);
            card.appendChild(rec);
            card.appendChild(recLabel);
            card.appendChild(divider);
            card.appendChild(stats);

            // When the API surfaces a reason (e.g. negative edge), show it as
            // a warning footer so a 0% headline isn't mistaken for "no data".
            if (b.reason) {
                const warn = document.createElement('div');
                warn.style.cssText = 'margin-top:10px;padding:8px 10px;background:rgba(229,57,53,0.08);border-left:3px solid var(--danger,#e53935);border-radius:4px;font-size:.72rem;line-height:1.4;color:var(--text-2,#aeb6cc)';
                warn.textContent = '⚠ ' + b.reason;
                card.appendChild(warn);
            }

            grid.appendChild(card);
        });

        el.appendChild(grid);

        const note = document.createElement('div');
        note.style.cssText = 'font-size:.75rem;color:var(--muted);margin-top:4px';
        note.textContent = 'Half-Kelly applied for safety  ·  Hard cap 20% of capital per position';
        el.appendChild(note);

    } catch(e) { el.appendChild(_emptyState('Kelly calculation failed.')); }
}

/* ── 5. Alpha Decay ─────────────────────────────────────────────────────── */
async function loadAlphaDecayPanel() {
    const el = document.getElementById('risk-alpha-decay');
    if (!el) return;
    while (el.firstChild) el.removeChild(el.firstChild);
    // 2026-05-26: also try to hide the section header above the tile when
    // there's no data. Otherwise an empty card under a heading looks broken.
    const section = el.closest('.risk-section') || el.parentElement;
    try {
        const d = await fetch('/api/risk/alpha-decay').then(r => r.json());
        if (!d.ok || !d.data || !d.data.available) {
            // Show inline empty-state until enough auto_ai trades accrue.
            // Auto_ai now populates execution_lag_minutes (2026-05-26) so
            // this tile activates after ~5 closed auto_ai trades.
            el.appendChild(_emptyState(
                (d.data && d.data.reason) ||
                'Execution-lag data accumulating. Activates after 5 closed auto_ai trades with timing data.'
            ));
            if (section) section.style.display = '';
            return;
        }
        if (section) section.style.display = '';
        const a = d.data;

        el.appendChild(_riskInsight(
            'Alpha decay measures whether your trading edge weakens the longer you wait to enter after a signal. ' +
            'A negative correlation means P&L drops as entry delay grows — enter sooner. ' +
            'A correlation near zero means timing doesn\'t matter much.'
        ));

        // Correlation KPI
        const corrColor = a.edge_decays ? 'var(--red)' : a.correlation < 0 ? 'var(--yellow)' : 'var(--accent3)';
        const corrLabel = a.edge_decays ? 'Edge is decaying — enter faster' : a.correlation < 0 ? 'Mild decay detected' : 'No decay — timing flexible';
        el.appendChild(_kpiGrid([
            ['Correlation (lag → P&L)', a.correlation !== null ? a.correlation.toFixed(3) : '—',
             corrLabel, corrColor],
            ['Sample Size', a.sample_size + ' trades', 'With execution lag data', null],
        ], 2));

        if (a.edge_decays) {
            el.appendChild(_alertBanner('Edge decay confirmed (r=' + (a.correlation || 0).toFixed(3) + '). Entering faster after a signal would likely improve your P&L.', '#ef5350'));
        }

        // Lag bucket table with gradient color — earlier = greener
        if (a.lag_buckets && a.lag_buckets.length > 0) {
            const wrap = document.createElement('div');
            wrap.className = 'table-card';
            wrap.style.marginTop = '12px';

            const header = document.createElement('div');
            header.className = 'table-header';
            const title = document.createElement('span');
            title.style.cssText = 'font-weight:600;font-size:.85rem';
            title.textContent = 'P&L by Entry Lag';
            header.appendChild(title);
            wrap.appendChild(header);

            const tbl = document.createElement('table');
            tbl.className = 'tbl';
            tbl.style.width = '100%';
            const thead = tbl.createTHead();
            const hr = thead.insertRow();
            ['Entry Delay', 'Trades', 'Avg P&L', 'Win Rate', 'Signal'].forEach(h => {
                const th = document.createElement('th');
                th.textContent = h;
                hr.appendChild(th);
            });

            const tbody = tbl.createTBody();
            // Find best bucket for relative comparison
            const maxPnl = Math.max(...a.lag_buckets.map(b => b.avg_pnl));

            a.lag_buckets.forEach((b, i) => {
                const tr = tbody.insertRow();
                const isDecayed = b.avg_pnl < maxPnl * 0.6;

                const tdLag = tr.insertCell();
                tdLag.style.fontWeight = '600';
                tdLag.textContent = b.lag_range;

                const tdCount = tr.insertCell();
                tdCount.style.color = 'var(--muted)';
                tdCount.textContent = b.trade_count;

                const tdPnl = tr.insertCell();
                tdPnl.style.fontWeight = '700';
                tdPnl.style.color = b.avg_pnl >= 0 ? 'var(--accent3)' : 'var(--red)';
                tdPnl.textContent = (b.avg_pnl >= 0 ? '+' : '') + '$' + b.avg_pnl.toFixed(2);

                const tdWr = tr.insertCell();
                tdWr.style.color = b.win_rate >= 50 ? 'var(--accent3)' : 'var(--muted)';
                tdWr.textContent = b.win_rate + '%';

                const tdSig = tr.insertCell();
                const sig = document.createElement('span');
                const sigColor = i === 0 ? 'var(--accent3)' : isDecayed ? 'var(--red)' : 'var(--muted)';
                sig.style.cssText = `background:${sigColor}22;color:${sigColor};padding:2px 8px;border-radius:12px;font-size:.7rem;font-weight:700`;
                sig.textContent = i === 0 ? 'BEST' : isDecayed ? 'DECAYED' : 'OK';
                tdSig.appendChild(sig);
            });

            wrap.appendChild(tbl);
            el.appendChild(wrap);
        }

    } catch(e) { el.appendChild(_emptyState('Alpha decay calculation failed.')); }
}


/* ── 6. AI Blindspots ───────────────────────────────────────────────────── */
async function loadBlindspotsPanel() {
    const el = document.getElementById('risk-blindspots');
    if (!el) return;
    try {
        while (el.firstChild) el.removeChild(el.firstChild);
        const d = await fetch('/api/blindspots').then(r => r.json());
        if (!d.ok || !d.data || !d.data.available) {
            el.appendChild(_emptyState('Need more closed trades with AI analyses to surface blindspots.'));
            return;
        }
        el.appendChild(_riskInsight(
            'Phrases the AI uses confidently in its reasoning that DON\'T predict winners. ' +
            'Lift = win rate minus baseline. Strongly negative lift = recurring false-confidence pattern.'
        ));
        // Phrase table
        const phrases = d.data.phrases || [];
        if (phrases.length) {
            const tbl = document.createElement('table');
            tbl.className = 'tbl';
            const thead = tbl.createTHead();
            const hr = thead.insertRow();
            ['Phrase','Times used','Actual WR','Lift vs baseline'].forEach(h => {
                const th = document.createElement('th'); th.textContent = h; hr.appendChild(th);
            });
            const tbody = tbl.createTBody();
            phrases.forEach(p => {
                const tr = tbody.insertRow();
                tr.insertCell().textContent = p.phrase;
                tr.insertCell().textContent = p.n;
                const wrCell = tr.insertCell();
                wrCell.textContent = p.win_rate + '%';
                const liftCell = tr.insertCell();
                liftCell.textContent = (p.lift >= 0 ? '+' : '') + p.lift + 'pp';
                liftCell.style.color = p.lift < -5 ? 'var(--red)' : (p.lift > 5 ? 'var(--accent3)' : 'var(--muted)');
                liftCell.style.fontWeight = '600';
            });
            const wrap = document.createElement('div');
            wrap.style.cssText = 'margin-bottom:14px';
            const title = document.createElement('div');
            title.style.cssText = 'font-size:.78rem;color:var(--muted);margin-bottom:6px';
            title.textContent = 'TOP PHRASES BY LIFT';
            wrap.appendChild(title);
            wrap.appendChild(tbl);
            el.appendChild(wrap);
        }
        // Feature calibration: show only the most-spread groups
        const feats = d.data.features || {};
        const groupKeys = Object.keys(feats).filter(k => (feats[k] || []).length >= 2);
        if (groupKeys.length) {
            const featTitle = document.createElement('div');
            featTitle.style.cssText = 'font-size:.78rem;color:var(--muted);margin:14px 0 6px';
            featTitle.textContent = 'FEATURE CALIBRATION — empirical WR by feature value';
            el.appendChild(featTitle);
            const ftbl = document.createElement('table');
            ftbl.className = 'tbl';
            const fhead = ftbl.createTHead();
            const fhr = fhead.insertRow();
            ['Feature','Best value','n','Worst value','n','Spread'].forEach(h => {
                const th = document.createElement('th'); th.textContent = h; fhr.appendChild(th);
            });
            const ftbody = ftbl.createTBody();
            const ranked = groupKeys.map(k => {
                const arr = feats[k];
                const wrs = arr.map(e => e.win_rate);
                return { key: k, entries: arr, spread: Math.max(...wrs) - Math.min(...wrs) };
            }).sort((a, b) => b.spread - a.spread).slice(0, 8);
            ranked.forEach(g => {
                const top = g.entries[g.entries.length - 1];
                const bot = g.entries[0];
                const tr = ftbody.insertRow();
                tr.insertCell().textContent = g.key;
                const tv = tr.insertCell();
                tv.textContent = top.value + ' (' + top.win_rate + '%)';
                tv.style.color = 'var(--accent3)';
                tr.insertCell().textContent = top.n;
                const bv = tr.insertCell();
                bv.textContent = bot.value + ' (' + bot.win_rate + '%)';
                bv.style.color = 'var(--red)';
                tr.insertCell().textContent = bot.n;
                const sp = tr.insertCell();
                sp.textContent = g.spread.toFixed(1) + 'pp';
                sp.style.fontWeight = '600';
            });
            el.appendChild(ftbl);
        }
    } catch (e) {
        el.appendChild(_emptyState('Failed to load blindspots: ' + e.message));
    }
}

/* ── 7. AI Self-Review wishlist ─────────────────────────────────────────── */
async function loadSelfReviewPanel() {
    const el = document.getElementById('risk-self-review');
    if (!el) return;
    try {
        while (el.firstChild) el.removeChild(el.firstChild);
        const d = await fetch('/api/self-review/wishlist').then(r => r.json());
        if (!d.ok) {
            el.appendChild(_emptyState('Wishlist API failed.'));
            return;
        }
        const wl = (d.data && d.data.wishlist) || [];
        el.appendChild(_riskInsight(
            'After each significant alpha-leak trade, the AI is asked "what one signal would ' +
            'have flipped your call?". Signals that recur ≥2× across reviews show up here. ' +
            'Use them to decide which indicators to add to the confluence stack.'
        ));
        if (!wl.length) {
            const msg = document.createElement('div');
            msg.style.cssText = 'color:var(--muted);font-size:.85rem';
            msg.textContent = 'Wishlist is empty — either no recurring patterns yet (each unique suggestion needs 2+ mentions) or self-review hasn\'t run yet. Click "Run now" above to process the next 5 alpha-leak trades.';
            el.appendChild(msg);
            return;
        }
        const tbl = document.createElement('table');
        tbl.className = 'tbl';
        const thead = tbl.createTHead();
        const hr = thead.insertRow();
        ['Missing signal','Mentions','Common TFs','Sample threshold'].forEach(h => {
            const th = document.createElement('th'); th.textContent = h; hr.appendChild(th);
        });
        const tbody = tbl.createTBody();
        wl.forEach(w => {
            const tr = tbody.insertRow();
            const sigCell = tr.insertCell();
            sigCell.textContent = w.sig;
            sigCell.style.fontWeight = '600';
            sigCell.style.textTransform = 'capitalize';
            tr.insertCell().textContent = w.n + 'x';
            tr.insertCell().textContent = w.tfs || '?';
            tr.insertCell().textContent = (w.thresholds || '').split(',')[0];
        });
        el.appendChild(tbl);
    } catch (e) {
        el.appendChild(_emptyState('Failed to load wishlist: ' + e.message));
    }
}

async function runSelfReviewNow() {
    const btn = document.getElementById('btn-self-review-run');
    const el  = document.getElementById('risk-self-review');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Running...'; }
    try {
        const d = await fetch('/api/self-review/run?limit=5', { method: 'POST' }).then(r => r.json());
        if (!d.ok) throw new Error(d.error || 'unknown error');
        const n = (d.data && d.data.reviewed) || 0;
        if (btn) { btn.textContent = `✓ Reviewed ${n}`; }
        // Refresh the wishlist panel
        await loadSelfReviewPanel();
        setTimeout(() => { if (btn) { btn.disabled = false; btn.textContent = '⚡ Run now'; } }, 3000);
    } catch (e) {
        if (btn) { btn.disabled = false; btn.textContent = '⚡ Run now'; }
        if (el) { const err = document.createElement('div');
            err.style.cssText = 'color:var(--red);margin-top:8px;font-size:.8rem';
            err.textContent = 'Run failed: ' + e.message;
            el.appendChild(err);
        }
    }
}
