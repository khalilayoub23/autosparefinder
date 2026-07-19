# NOA Social Publishing — Setup Guide

NOA drafts a post each day → it lands in the **`social_posts` approval queue**
(`status='pending_approval'`) → the owner approves (Telegram button or the admin API) →
`social/registry.py` publishes it to the platform(s) via that platform's publisher.

**Nothing posts publicly without approval.** A platform with no credentials returns
`not_configured` and NOA hands back copy-paste-ready text instead of failing.

Fill the keys in `.env` (already scaffolded, empty) and `docker restart autospare_backend`.
The keys are also wired into `docker-compose.yml` so a recreate passes them through.

| Platform | Keys | Media | Effort |
|---|---|---|---|
| Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID` | text/img | ✅ already live |
| **Discord** | `DISCORD_WEBHOOK_URL` | text/img | ⭐ easiest — no app/OAuth |
| Facebook | `FACEBOOK_PAGE_ID`, `FACEBOOK_PAGE_TOKEN` | text/img | Meta app (you have one) |
| Instagram | `INSTAGRAM_USER_ID`, `INSTAGRAM_ACCESS_TOKEN` | **image required** | Meta app + IG Business |
| X / Twitter | `X_API_KEY/SECRET`, `X_ACCESS_TOKEN/SECRET` | text (img later) | paid tier for write |
| TikTok | `TIKTOK_CLIENT_KEY/SECRET` | **video required** | app review for public |
| Reddit | `REDDIT_CLIENT_ID/SECRET`, `REDDIT_USERNAME/PASSWORD`, `REDDIT_SUBREDDIT` | text | script app |

> Security: every value is a secret — keep it ONLY in `.env` (gitignored). Never commit,
> log, or paste a token into chat. Rotate if exposed.

---

## 1. Discord (start here — 2 minutes, no app)

1. Open your Discord **server** → **Server Settings → Integrations → Webhooks**.
2. **New Webhook** → pick the channel → **Copy Webhook URL**.
3. `.env`: `DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/<id>/<token>`
4. `docker restart autospare_backend`. Done — Discord is live.

## 2. Facebook Page + Instagram (one Meta app)

You already have the app **autosparefinder** (App ID `873729112353484`).

1. **Graph API Explorer** → https://developers.facebook.com/tools/explorer/
2. Meta App = **autosparefinder**. Add permissions:
   `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`,
   `instagram_basic`, `instagram_content_publish`, `business_management`.
3. **Generate Access Token** → approve → select your **Page** and **Instagram** account.
4. Get the long-lived **Page token + IDs** (the backend helper does this for you from the
   user token — or by hand):
   - `GET /me/accounts` → your Page's `id` (→ `FACEBOOK_PAGE_ID`) and `access_token`
     (→ `FACEBOOK_PAGE_TOKEN`, exchange for long-lived).
   - `GET /{page_id}?fields=instagram_business_account` → `INSTAGRAM_USER_ID`.
   - `INSTAGRAM_ACCESS_TOKEN` = the Page token (reused).
5. `.env` those four, `docker restart autospare_backend`.

> Instagram needs a public image — NOA supplies a part thumbnail
> (`https://autosparefinder.co.il/api/v1/thumbnails/...`), which Meta can fetch.
> Publishing while the app is in **Development mode** works for Pages/IG accounts you
> admin; going fully public later needs Meta **App Review + Business Verification**.

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
