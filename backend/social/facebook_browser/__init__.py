"""
social/facebook_browser — Playwright-based Facebook Group automation.

WHY THIS EXISTS:
  Meta deprecated the Facebook Groups API in April 2024. There is no official
  way to post to, scan, or comment in Facebook Groups via the Graph API.
  The only remaining path is a browser session authenticated as the page admin.

STRICT RULES:
  1. This agent NEVER posts autonomously. Every comment or group post requires
     owner approval via the WhatsApp console before execution.
  2. It reads groups and drafts proposals; the human approves before sending.
  3. Human-like delays (2-5s between actions) to avoid triggering bot detection.
  4. Sessions are persisted in /app/state/fb_browser_session/ (the worker_state
     volume) so login is not repeated on every cycle.
  5. Screenshots of failures are saved to /app/state/logs/fb_browser_failures/.

Public API:
  from social.facebook_browser import GroupAgent
  agent = GroupAgent()
  discoveries = await agent.scan_groups(approved_groups)
  await agent.submit_approved_comment(task_id, comment_text, target_url)

Author: AutoSpareFinder — 2026-08-06
"""
from social.facebook_browser.group_agent import GroupAgent

__all__ = ["GroupAgent"]
