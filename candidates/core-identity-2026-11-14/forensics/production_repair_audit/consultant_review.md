# Consultant review (configured advisor) — status PARTIAL
Limitation: the consultant sees the transcript only; it cannot query production. It is not an independent production verification.

Findings and resolution:
1. Core finding (93/93 NOT_APPLIED, identical updated_at) accepted as strongest evidence. 
2. price_history check was dead code -> run: 0 repair-source rows, 0 rows on the 93 offers after manifest time (prod); same 0 in sandbox. RESOLVED.
3. Artifact search by filename only -> newer-file search over state/repo/backend: nothing relevant (only harvester state, /tmp harvest logs). RESOLVED.
4. Sandbox DB read: the 93 offers are not present there. RESOLVED.
5. Side-effect audit was inference -> replaced by counts (3,858 moves, 36 UNDO, 1,070 NOT_READY, all classes). RESOLVED.
6. Read time, canary label, expected-current guard statement, escalation options -> added. RESOLVED.
Open: no independent production-side confirmation is possible by the consultant.
