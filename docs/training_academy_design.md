# Training Academy — Public Hosting Design

**Status:** DRAFT — awaiting your review/approval
**Date:** 2026-05-28
**Author:** Claude (planning artifact, will move to public repo on kickoff)

---

## 1. Goals & non-goals

### Goals
- Host the standalone Training Module on a public-facing VPS with a custom domain.
- Real multi-user authentication — each user has their own progress.
- Cover server costs through **voluntary donations** (Ko-fi), not paid subscriptions.
- Stay as anonymous as possible — pseudonymous public face, no legal entity.
- Trading repo stays private and remains the single source of truth for lesson content.
- Free for users; donations optional, no paywall.

### Explicit non-goals
- No Stripe / paid tiers / subscriptions.
- No forums, comments, user-generated content.
- No mobile app.
- No multi-language at launch (Indonesian PDF stays offline-only).
- No federation, no SSO with third parties beyond magic-link email.
- No analytics that track individual users (privacy first).

---

## 2. Constraints driving the design

| Constraint | Implication |
|---|---|
| ~$60/yr VPS budget, donations likely net <$50/yr | Architecture must be free-to-operate at any scale we'd realistically hit |
| Anonymous-as-possible | No personal-name domain registration, pseudonym on Cloudflare/Hostinger if KYC allows, separate email |
| No legal entity | Donations not subscriptions; light-touch privacy policy; user accepts terms before signup |
| Unknown scale (5 to maybe 500) | No scaling cliffs in auth, DB, hosting until well into thousands of users |
| Trading repo stays private | Public repo gets a CLEAN copy of training module — no leak of trading code/keys/data |

---

## 3. High-level architecture

```
                   ┌─────────────┐
   User browser ──►│ Cloudflare  │──► DNS + TLS + DDoS shield (free plan)
                   └──────┬──────┘
                          │ HTTPS
                          ▼
                   ┌─────────────────────────────────────────────┐
                   │     VPS — Hostinger KVM1 (Ubuntu 24.04)     │
                   │                                             │
                   │   nginx :443                                │
                   │     │ (TLS termination, rate limit, headers)│
                   │     ▼                                       │
                   │   gunicorn :5050 (3 workers, 127.0.0.1)     │
                   │     │                                       │
                   │     ▼                                       │
                   │   Flask app (training-academy)              │
                   │     ├─ users.db (SQLite WAL)                │
                   │     ├─ content/ (rsynced from trading repo) │
                   │     └─ static/ (css, js, charts)            │
                   │                                             │
                   │   Brevo SMTP (outbound only)                │
                   │     ↳ magic-link emails                     │
                   └─────────────────────────────────────────────┘
                          ▲
                          │ rsync over SSH (publish script)
                          │
                   ┌──────┴──────────────────┐
                   │  Your Mac               │
                   │  Trading-Journal repo   │
                   │  training/content/*     │ ← lessons authored here, like today
                   └─────────────────────────┘
```

---

## 4. Repository strategy

### Two repos, clean separation

**Trading-Journal** (existing, stays PRIVATE, lives on your Mac/Pi)
- Source of truth for lesson content (`training/content/lessons/*.json`, `quizzes/*.yaml`)
- Authoring tools — you continue editing lessons here, same workflow as today
- Trading code stays in here forever — never goes to public VPS

**Training-Academy** (NEW, separate public repo)
- Suggested location: Codeberg or fresh GitHub account under a pseudonym
- Can be public-source (transparent, builds trust) or private — your choice
- Contains:
  - `app/` — Flask app code (forked from `training/` with auth bolted on)
  - `auth/` — magic-link routes, session handling, login templates
  - `migrations/` — DB schema migrations (idempotent)
  - `deploy/` — nginx config, systemd units, certbot setup, bash scripts
  - `legal/` — privacy policy, terms, imprint templates
  - **No content** — lessons folder is gitignored and provided at deploy time

### Content sync workflow

```
Trading-Journal/training/content/lessons/*.json  (you author here)
                          │
                          │ ./scripts/publish_training.sh
                          ▼
                  /tmp/training-release-2026-05-28.tgz
                          │
                          │ scp + ssh
                          ▼
VPS:/home/training/app/content_staging/
                          │ atomic swap
                          ▼
VPS:/home/training/app/content/  ← gunicorn picks up on graceful reload
```

`publish_training.sh` does:
1. Run `pytest training/tests/` — refuse to publish if anything fails
2. Tarball `training/content/` with timestamp + git SHA in filename
3. rsync to VPS staging directory
4. SSH to VPS, atomic swap (rename old → backup, new → live)
5. `systemctl reload training-academy` (gunicorn graceful reload, no downtime)
6. Curl `/health` endpoint to confirm app still responds

Idempotent and reversible — failed releases auto-roll-back by keeping the previous content dir.

---

## 5. Authentication — magic link via email

### Why magic-link not password

| Factor | Magic-link | Password |
|---|---|---|
| User UX | type email → click link → done | type email + password, remember password |
| Forgot-password flow | not needed (always email-based) | needed, classic security exploit target |
| Breach if DB stolen | tokens expire in 10 min, useless | password hashes brute-forceable offline |
| Code complexity | ~150 LOC | ~400 LOC (signup, login, reset, change) |
| Vendor lock-in | none (your own SMTP) | none |
| Scaling cliff | none (Brevo free covers ~150 daily logins) | none |

Magic-link is strictly better for this use case.

### Flow

```
1. User visits /login → form with email field only
2. POST /login {email}
   ├─ generate 32-byte cryptographically random token T
   ├─ hash(T) stored in login_tokens table:
   │    {token_hash, user_id_or_email, expires_at = now+10min, used_at = null}
   ├─ email sent via Brevo SMTP:
   │    "Click here to sign in: https://site/auth/verify?t=<T>"
   └─ user sees: "Check your email — link is good for 10 minutes."
3. User clicks → GET /auth/verify?t=<T>
   ├─ hash(T) lookup
   ├─ verify expires_at > now AND used_at IS NULL
   ├─ mark used_at = now (single use)
   ├─ if user doesn't exist yet: CREATE users row (email-only signup)
   ├─ set session cookie (secure, httponly, samesite=lax, 30-day lifetime)
   └─ redirect to /training (dashboard)
4. Subsequent requests: session cookie validates via Flask-Login
```

### Implementation details

- **Token generation**: `secrets.token_urlsafe(32)` — 256 bits of entropy, URL-safe
- **Token storage**: SHA-256 hash stored, raw never persisted (defense against DB leak)
- **TTL**: 10 minutes (balance: user-friendly vs replay-attack window)
- **Single-use**: `used_at` column set on first verify, second-use rejected
- **Rate limit**: max 5 login emails per IP per 15 min (nginx + Flask-Limiter)
- **Email content**: plaintext, no tracking pixels, no HTML — keeps deliverability high
- **Sender**: `Crypto Trading Academy <noreply@your-domain>` (verified domain with Brevo)

### Session management

- `Flask-Login` with signed cookies
- 30-day session lifetime, sliding expiration
- Logout button clears server-side session record + cookie
- Sessions stored in DB (table `sessions`) so logout is true server-side invalidation

### Admin role

- One user (yours) has `is_admin = 1` in users table
- Admin can:
  - View users list + login count
  - Trigger `POST /api/reset-progress` (single-user wipe of own data — admin can wipe anyone)
  - View aggregate stats (no per-user tracking)
- Regular users: zero admin powers

---

## 6. Database schema

### New tables

```sql
-- New: users
CREATE TABLE users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  email         TEXT NOT NULL UNIQUE,
  created_at    TEXT NOT NULL,         -- ISO8601 UTC
  last_login_at TEXT,
  login_count   INTEGER DEFAULT 0,
  is_admin      INTEGER DEFAULT 0,
  is_active     INTEGER DEFAULT 1,
  deleted_at    TEXT                    -- soft-delete for GDPR
);
CREATE UNIQUE INDEX idx_users_email ON users(email);

-- New: login_tokens (magic-link state)
CREATE TABLE login_tokens (
  token_hash TEXT PRIMARY KEY,
  email      TEXT NOT NULL,             -- so token works even before user record exists
  user_id    INTEGER,                   -- set after first login if user already existed
  ip_address TEXT,                      -- audit
  user_agent TEXT,                      -- audit
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  used_at    TEXT,
  FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE INDEX idx_login_tokens_expires ON login_tokens(expires_at);

-- New: sessions (server-side invalidation support)
CREATE TABLE sessions (
  session_id TEXT PRIMARY KEY,
  user_id    INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  ip_address TEXT,
  user_agent TEXT,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX idx_sessions_user ON sessions(user_id);
CREATE INDEX idx_sessions_expires ON sessions(expires_at);
```

### Existing tables — add `user_id` column

```sql
ALTER TABLE lesson_progress ADD COLUMN user_id INTEGER REFERENCES users(id);
ALTER TABLE quiz_attempts   ADD COLUMN user_id INTEGER REFERENCES users(id);
ALTER TABLE widget_attempts ADD COLUMN user_id INTEGER REFERENCES users(id);
ALTER TABLE review_queue    ADD COLUMN user_id INTEGER REFERENCES users(id);

-- For each, NEW indexes:
CREATE INDEX idx_lp_user ON lesson_progress(user_id, lesson_id);
CREATE INDEX idx_qa_user ON quiz_attempts(user_id, lesson_id);
-- etc.
```

### Migration strategy

- Fresh DB on the VPS — no migration of existing data needed (your Pi's progress doesn't transfer)
- `init_db()` runs all CREATE TABLE statements + creates first admin user from `ADMIN_EMAIL` env var
- All future DB changes via numbered migration files: `migrations/01_add_X.sql`

---

## 7. Donations

### Ko-fi integration

- Sign up at ko-fi.com under pseudonym (Ko-fi KYC is lighter than Stripe)
- Get an iframe embed code
- Drop into `templates/base.html` footer
- Optionally: a banner shown after passing 5 lessons (warm lead conversion)

### Privacy

- No donor tracking in our DB
- No "thanks to X" page (donors anonymous unless they opt in via Ko-fi's own donor page)
- Ko-fi handles all KYC for payouts — we never see donor card numbers or PII

### Realistic income estimate

| Users active | Donor conversion | Avg donation | Annual income |
|---|---|---|---|
| 5 | 0-5% | $5 | $0-1 |
| 50 | 1-3% | $5 | $2-7 |
| 500 | 1-3% | $5-10 | $25-150 |

VPS cost is $60/yr. Plan for breakeven at ~500 users; treat anything below as a hobby expense.

---

## 8. VPS setup — Hostinger KVM1 hardening

### OS + base config

- Ubuntu Server 24.04 LTS (boring, well-supported, big security team)
- Set hostname (e.g. `academy-1`), timezone UTC
- `apt update && apt full-upgrade -y`
- Enable `unattended-upgrades` for security patches only
- Persistent journald, log rotation enabled

### User accounts

| User | Purpose | Sudo? | SSH access? |
|---|---|---|---|
| `root` | Initial setup only | yes | DISABLED after `deploy` user works |
| `deploy` | Admin ops, deployments | yes | yes, key-only |
| `training` | Runs Flask app | no | no — switch to from `deploy` via sudo |

### SSH hardening (`/etc/ssh/sshd_config`)

```
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
AllowUsers deploy
Port 22                    # security-by-obscurity not worth the inconvenience
MaxAuthTries 3
ClientAliveInterval 300
LoginGraceTime 30
```

- SSH keys: ed25519, your existing key copied to `~deploy/.ssh/authorized_keys`
- **Fail2ban** monitors auth.log, bans IPs after 3 failed attempts (10-min ban, escalating)

### Firewall (`ufw`)

```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp       # SSH
ufw allow 80/tcp       # HTTP (Let's Encrypt + 301 to HTTPS)
ufw allow 443/tcp      # HTTPS
ufw enable
```

Nothing else. ICMP allowed (default).

### Process hardening

- `training` user runs gunicorn — no sudo, no shell login (`usermod -s /usr/sbin/nologin training`)
- gunicorn binds to `127.0.0.1:5050` — NOT `0.0.0.0`, never reachable from internet
- nginx is the only thing on public ports
- App config + secrets in `/etc/training-academy/env` — readable by `training` user only (chmod 600)

### TLS

- Let's Encrypt via certbot
- Auto-renew via systemd timer (already provided by certbot package)
- nginx `ssl_protocols TLSv1.2 TLSv1.3` only — no SSLv3, TLS 1.0/1.1
- HSTS header: `Strict-Transport-Security: max-age=31536000; includeSubDomains; preload`

### nginx security headers

```nginx
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "SAMEORIGIN" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
add_header Content-Security-Policy "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' https://ko-fi.com; frame-src https://ko-fi.com;" always;
add_header Permissions-Policy "geolocation=(), microphone=(), camera=()" always;
```

### Rate limiting

```nginx
limit_req_zone $binary_remote_addr zone=login:10m rate=5r/m;
limit_req_zone $binary_remote_addr zone=general:10m rate=60r/m;

location /login        { limit_req zone=login burst=2 nodelay; ... }
location /api          { limit_req zone=general burst=10 nodelay; ... }
location /              { limit_req zone=general burst=20 nodelay; ... }
```

### Cloudflare in front (optional but recommended)

- Free plan: DNS, TLS, basic DDoS shield, bot blocking
- Reveal-IP protection: Cloudflare proxies all traffic, your VPS IP is never directly exposed
- "Always Use HTTPS" + "Automatic HTTPS Rewrites" on
- Set Cloudflare → VPS to "Full (strict)" so end-to-end encrypted
- `set_real_ip_from` in nginx so logs show real client IPs not Cloudflare's

### Backup strategy

- Nightly: `sqlite3 .backup` of `training.db` → `/var/backups/training/db_YYYYMMDD.db`
- Rolling 7-day local retention
- Daily rsync of backup dir to your Mac (or to a free Backblaze B2 bucket — $0/mo at this volume)
- Test restore monthly: spin up local copy of the DB, confirm it boots

### Monitoring

- `/health` endpoint returns `{"ok": true, "uptime_s": N}` — public, no auth
- UptimeRobot free tier pings every 5 min — emails you if it goes red
- `journalctl -u training-academy -f` shows live logs
- Disk + RAM alerting via simple cron + email (no Datadog complexity needed)

---

## 9. Legal layer (you handle, I draft templates)

You'll need three documents on the site, accessible from footer:

1. **Privacy Policy** — what data you collect (email, IP, progress), how long you keep it (until account deletion + 30 days for backups), who it's shared with (Brevo for email, Cloudflare for DDoS, Ko-fi for donations — that's it), GDPR rights (access, deletion, portability)
2. **Terms of Service** — service is free, no warranty, no liability, account can be deleted anytime by user or owner for abuse, donations are voluntary and non-refundable
3. **Imprint** — in CH/EU, even non-commercial sites need contact info. You can use a PO box or a "Contact: hello@your-domain" with no real name if not selling

**I'll provide templates. You should run them past a CH lawyer for a one-off ~€100-200 review before going live.** That's the only legal spend.

GDPR-required features in the app:
- `/account/delete` button — hard-deletes user record + all progress + sessions
- `/account/export` button — exports your own data as JSON
- Both accessible only when logged in

---

## 10. Project structure (new public repo)

```
training-academy/
├── README.md                          ← public-facing description
├── LICENSE                            ← AGPL or MIT, your choice
├── .gitignore                         ← content/, *.db, .env, *.pyc
├── requirements.txt                   ← flask, gunicorn, flask-login,
│                                        flask-limiter, itsdangerous,
│                                        pyyaml, requests (for Brevo)
├── app/
│   ├── __init__.py
│   ├── __main__.py                    ← python -m app entry point
│   ├── config.py                      ← env-driven config
│   ├── db.py                          ← extended from training/db.py
│   ├── routes/
│   │   ├── public.py                  ← landing, /login, /health
│   │   ├── auth.py                    ← magic-link verify, logout
│   │   ├── lessons.py                 ← (forked from training/routes.py)
│   │   ├── account.py                 ← /account/delete, /account/export
│   │   └── admin.py                   ← admin-only routes
│   ├── auth/
│   │   ├── magic_link.py              ← token generation/verification
│   │   ├── session.py                 ← Flask-Login wrapper
│   │   └── email.py                   ← Brevo SMTP send
│   ├── templates/
│   │   ├── base.html                  ← extends training base, adds login/logout, ko-fi
│   │   ├── login.html
│   │   ├── login_sent.html
│   │   ├── account.html
│   │   └── legal/                     ← privacy.html, terms.html, imprint.html
│   ├── static/
│   │   ├── css/training.css           ← extended from training module
│   │   └── js/auth.js                 ← login form handling
│   └── content/                       ← GITIGNORED — populated by publish script
├── migrations/
│   ├── 001_initial_schema.sql
│   ├── 002_add_users.sql
│   ├── 003_add_user_id_to_progress.sql
│   └── ...
├── deploy/
│   ├── nginx/
│   │   └── academy.conf
│   ├── systemd/
│   │   ├── training-academy.service   ← gunicorn service
│   │   └── training-academy-backup.timer
│   ├── certbot/
│   │   └── setup.sh
│   └── bootstrap.sh                   ← run on fresh VPS, sets up everything
├── scripts/
│   ├── publish_content.sh             ← deploy-side counterpart (runs on VPS)
│   └── backup_db.sh
└── docs/
    ├── DEPLOYMENT.md                  ← step-by-step VPS setup
    ├── SECURITY.md                    ← hardening checklist + responses to common incidents
    └── DEVELOPMENT.md                 ← run locally for development
```

---

## 11. Rollout phases (with rough hours)

| Phase | Description | Est. hours |
|---|---|---|
| **0** | This design doc + your approval | done after you approve |
| **1** | Local dev — fork training module, add auth, multi-user DB schema, magic-link flow working with local SMTP (mailpit). Tests cover happy path + 5 attack scenarios. | 6-8h |
| **2** | VPS provision — Hostinger order, base hardening (Layer 1-2 from §8), domain DNS, Cloudflare proxy. End state: nginx serving "hello world" on https://your-domain. | 2-3h |
| **3** | Deploy app to staging subdomain — `staging.your-domain` runs the full app, you can sign up + log in + take quizzes. Brevo SMTP wired up. | 3-4h |
| **4** | Legal layer — privacy policy + ToS + imprint drafts. You review with lawyer (~€100-200, your call). | 1h coding + your lawyer time |
| **5** | Production cutover — flip domain root to production app, staging stays as canary. UptimeRobot live. Backups verified by restoring once. | 2h |
| **6** | Trading repo publish script + first content sync. End state: you can edit a lesson in trading repo, run one command, and see it live in 30 seconds. | 2h |

**Total Claude-side work: ~15-20 hours**. Spread over multiple sessions, billed only when you say go. You'd spend additional time on: Hostinger order, Cloudflare account, Ko-fi setup, lawyer review, your own Brevo signup — maybe 2-3 hours of your time.

---

## 12. Open questions for you

These don't block the design but I need answers before/during implementation:

1. **Brand name + domain.** Need a working name so I can put it in configs. e.g. `cryptotradingacademy.com`, `tradingschool.io`, `cryptolessons.app`. Cheap TLDs: `.com` (~$10/yr), `.app` ($15), `.dev` ($12). Cloudflare Registrar sells at cost — no markup.
2. **Public-repo location.** Codeberg (privacy-friendly, EU-hosted), GitHub under pseudonym, or stay closed-source on a private GitLab? I lean Codeberg if you want maximum independence, GitHub if you want maximum visibility.
3. **Pseudonym + contact email.** Need one for: domain whois, Cloudflare, Hostinger, Brevo, Ko-fi. Suggest a brand-aligned address like `hello@your-domain` (set up after domain registration).
4. **Public vs private source code?** AGPL/MIT open source builds trust + lets future you fork it cleanly; private gives you full control over who runs copies. Either fine.
5. **Free-trial vs login-gated?** Two flavors:
   - **Walled garden**: must sign up (free) before reading anything. Forces account creation = better donor conversion later.
   - **Open reading + gated quizzes**: lessons readable anonymously; only quizzes/progress need login. More inviting for first-time visitors.
   I lean **walled garden** for the operational simplicity, but happy with either.
6. **Ko-fi vs Buy Me a Coffee?** Both fine. Ko-fi has slightly better widget options, BMC has slightly cleaner branding. Both KYC-light. Pick one.
7. **Email-from address.** `noreply@your-domain` standard; or something friendlier like `hello@your-domain`. Latter gets more replies (some users hit Reply on magic-link emails).

---

## 13. Risks I see + mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| User base never grows past 5 | high | low | Treat VPS as hobby expense; no impact on architecture |
| Magic-link emails hit spam | medium | medium | Use Brevo (good reputation), SPF/DKIM/DMARC properly configured, plaintext-only emails |
| VPS compromised via OS vulnerability | low | medium | Hardening + unattended-upgrades + fail2ban + Cloudflare in front |
| Someone discovers public training site and DOSes it | low | low | Cloudflare DDoS + nginx rate limits + free tier limits |
| Lesson content gets scraped by competitor | medium | low | It's CC-BY-licensable educational content; scraping is the price of being open |
| User signs up, you delete account, they sue | very low | low | ToS allows account termination at owner's discretion + no money exchanged |
| Donation income < server cost | high | low | You said you're OK absorbing $60/yr |
| You get bored / move on, site becomes orphan | medium | low | DB backups go to your Mac; you can shutter the VPS anytime |
| Someone uses site to abuse Brevo (mass-signup attack) | medium | medium | Rate-limit signup per IP; flag accounts with no activity after 7 days; Brevo will throttle automatically |

---

## 14. What I need from you to start

Once you approve this design (or send edits):

1. **Answer the 7 open questions in §12** (or say "you decide" on any).
2. **Order the Hostinger KVM1 VPS.**
3. **Decide on a domain name and register it** (Cloudflare Registrar is fine).
4. **Create accounts** at Cloudflare (DNS+proxy), Brevo (SMTP), Ko-fi (donations) — all free tiers.
5. **Set up SSH key access** for me — create a `deploy` sudo user, add my public key. Or paste the VPS root credentials and I'll do user setup myself.

Then I start Phase 1 (local dev) and we go from there.

---

## 15. Approval

Reply with:
- ✅ "Approved as-is, here are answers to §12" — I start Phase 1
- 🔄 "Approved with these changes: ..." — I revise, then start
- 📝 "Let's discuss section X" — we talk it through
- ❌ "Different direction: ..." — we redesign

This document will be moved to the new public training-academy repo as `docs/DESIGN.md` once the project starts, with subsequent decisions appended as ADRs (Architecture Decision Records).
