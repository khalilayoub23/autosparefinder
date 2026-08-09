"""
integrations/facebook_browser/ — AI-decision → Playwright execution bridge.

Architecture:
  The AI (NOA via social/tools.py) decides WHAT should happen.
  Playwright (via this package) executes HOW it happens.

IMPORTANT SAFETY INVARIANT:
  APPROVAL_REQUIRED = True is hardcoded in group_agent.py.
  The browser layer NEVER acts autonomously — every submit/publish
  action requires explicit owner approval stored in group_targets.status='approved'.

Components:
  session.py    — Persistent Playwright session (cookie reuse, health-check, screenshots)
  group_agent.py — Group scanning, comment drafting, and approved execution
  task_queue.py  — Serialized task queue for browser actions (prevents concurrent sessions)

Re-exports from social/facebook_browser/ (the implementation lives there):
"""

from social.facebook_browser import GroupAgent
from social.facebook_browser.session import FacebookSession

__all__ = ["GroupAgent", "FacebookSession"]
