"""
integrations/ — Thin façade packages over the social/* implementation modules.

Provides the structured namespace the architecture requires without duplicating
the actual implementation. All real logic lives in:

  social/meta_client.py         → integrations/meta/auth_manager.py (re-exported)
  social/meta_client.py         → integrations/meta/graph_client.py  (re-exported)
  social/meta_client.py         → integrations/meta/rate_limiter.py  (re-exported)
  social/facebook_pages.py      → integrations/meta/facebook_pages.py (re-exported)
  social/facebook_browser/      → integrations/facebook_browser/     (re-exported)

New in this package:
  integrations/meta/instagram.py       — Instagram Graph API adapter
  integrations/meta/webhook_handler.py — Meta webhook signature verification
"""
