# NOA Social Publishing — Setup Guide

NOA drafts a post each day → it lands in the **`social_posts` approval queue**
(`status='pending_approval'`) → the owner approves (Telegram button or the admin API) →
`social/registry.py` publishes it to the platform(s) via that platform's publisher.

**Nothing posts publicly without approval.** A platform with no credentials returns
`not_configured` and NOA hands back copy-paste-ready text instead of failing.

Fill the keys in `.env` (already scaffolded, empty) and `docker restart autospare_backend`.
The keys are also wired into `docker-compose.yml` so a recreate passes them through.

| Platform | Keys | Media | Status |
|---|---|---|---|
| **Telegram** | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID` | text/img | ✅ **LIVE** |
| **Discord** | `DISCORD_WEBHOOK_URL` | text/img | ✅ **LIVE** (server "AutoSpareFinder", #general) |
| **Facebook** | `FACEBOOK_PAGE_ID`, `FACEBOOK_PAGE_TOKEN` | text/img | ✅ **LIVE** (2026-07-19) |
| Instagram | `INSTAGRAM_USER_ID`, `INSTAGRAM_ACCESS_TOKEN` | **image required** | ⏸️ needs IG Pro acct linked to the Page |
| X / Twitter | `X_API_KEY/SECRET`, `X_ACCESS_TOKEN/SECRET` | text (img later) | ⏸️ paid tier for write access |
| TikTok | `TIKTOK_CLIENT_KEY/SECRET` | **video required** | ⏸️ sandbox; app review + video needed |
| Reddit | `REDDIT_CLIENT_ID/SECRET`, `REDDIT_USERNAME/PASSWORD`, `REDDIT_SUBREDDIT` | text | ⏸️ blocked: reCAPTCHA + Google-SSO (no password) |

> Security: every value is a secret — keep it ONLY in `.env` (gitignored). Never commit,
> log, or paste a token into chat. Rotate if exposed.

---

## 1. Discord (start here — 2 minutes, no app)

1. Open your Discord **server** → **Server Settings → Integrations → Webhooks**.
2. **New Webhook** → pick the channel → **Copy Webhook URL**.
3. `.env`: `DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/<id>/<token>`
4. `docker restart autospare_backend`. Done — Discord is live.

## 2. Facebook Page + Instagram (one Meta app) — ✅ Facebook DONE 2026-07-19

**Live setup (do not recreate):**
- App: **AutoSpareFinder Social** — App ID `2954756941573291`, owned by the
  **autosparefinder.co.il** business portfolio.
- Use cases: *Manage everything on your Page* + *Manage messaging & content on Instagram*.
- Login configuration: **"NOA Publisher"** — Config ID `4450562271892203`
  (system-user token, **never expires**, assets = Pages + Instagram, all 6 permissions).
- Page: **autosparefinder** — `FACEBOOK_PAGE_ID=1170174359502072`.

> **The old app `873729112353484` cannot do this** — Meta offers it no Pages/Instagram
> use cases ("Invalid Scopes" on every publishing permission). That's why a new app exists.

### Re-issuing the token (when needed)
The classic `scope=` OAuth dialog does NOT work for this app — it's **Facebook Login for
Business**, which honours only a **`config_id`**. Use:

```
https://www.facebook.com/v21.0/dialog/oauth?client_id=2954756941573291
  &config_id=4450562271892203
  &redirect_uri=https://developers.facebook.com/tools/explorer/callback
  &response_type=token
```
Approve → select the Page (+ Instagram) → it redirects to the Graph API Explorer with the
token. Then:
- `GET /me/permissions` → confirm the 6 scopes are `granted`
- `GET /me/accounts?fields=id,name,access_token,instagram_business_account`
  → `FACEBOOK_PAGE_ID` + `FACEBOOK_PAGE_TOKEN` (and `INSTAGRAM_USER_ID` if linked)
- put them in `.env` → `docker compose up -d --no-deps backend` (a plain `restart` will
  NOT pick up new env vars).

### Instagram — one prerequisite still open
`instagram_business_account` on the Page returns **None**, i.e. no IG account is linked.
Instagram publishing stays `not_configured` until:
1. The Instagram account is a **Professional (Business or Creator)** account, and
2. It is **linked to the autosparefinder Page** (Page Settings → Linked accounts, or Meta
   Business Suite), and added to the **autosparefinder.co.il** portfolio.

Then re-issue the token above; `INSTAGRAM_USER_ID` will appear and IG goes live.
IG posts REQUIRE an image — NOA supplies a clean part thumbnail
(`https://autosparefinder.co.il/api/v1/thumbnails/...`), which Meta can fetch.

> Development mode is fine for posting to Pages/IG **you** admin. Public posting on behalf
> of other users later needs **App Review + Business Verification**.

## 3. X / Twitter

1. https://developer.x.com/ → Project + App (write access to `/2/tweets` is a paid tier).
2. App → **Keys and tokens**: API Key/Secret + an Access Token/Secret with **Read and Write**.
3. `.env`: `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET` → restart.

## 4. Reddit

1. https://www.reddit.com/prefs/apps → **create app** → type **script**
   (redirect uri `http://localhost:8080`).
2. Note the client id (under the app name) + secret.
3. `.env`: `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USERNAME`,
   `REDDIT_PASSWORD`, `REDDIT_SUBREDDIT` (target sub, no `r/`) → restart.

## 5. TikTok

Client keys are set but `TIKTOK_SANDBOX=true` (posts land in the sandbox, not the public
feed). TikTok needs a **video** and app review for public posting — organic NOA text posts
stay draft on TikTok until a clip pipeline + production approval exist.

---

## Verifying

- `GET /api/v1/admin/social/posts?status=pending_approval` — NOA's queued drafts.
- Approve via the Telegram button, or `POST /api/v1/admin/social/publish/{post_id}`
  (after `status='approved'`). The response's `platform_results` shows per-platform
  outcome; `not_configured` means the token isn't set yet.
- `python3 /app/devtests/social_publishers_test.py` — checks the registry wiring and that
  unconfigured platforms fail closed (no crash, clear `not_configured`).

_Last updated: 2026-07-19_
