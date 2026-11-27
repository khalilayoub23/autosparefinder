INVALID run - kept only for transparency. The replay harness's recording wrapper `_gate(reply, regen)` did not accept the
`allow_self` keyword added in 48cf3df, so every console turn raised before reaching the LLM. Harness fixed (`**kw`) and the
round was rerun: see ../replay_round5/.
