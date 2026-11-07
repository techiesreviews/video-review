# Agents

Kitchen rulebook: `scripts/agents/kit rulebook` (stations, who decides, builder and reviewer pins).
Agent commands: `scripts/agents/kit --help`; `scripts/agents/kit <command> --help` for one.

## Messages to Lex
<!-- messages-to-lex:start v1 -->
- Only what matters to him. Start with the question, or what he must do (test, do or answer).
- Add a recommendation when it fits.
- Caveman style, short bullets, plain words (he is not technical). He asks if he wants more context.
- Leave out internal process (review rounds, waivers, SHAs, file paths) unless he must act on it.
- Example: "Keep the old export button or remove it?" / "Both do the same." / "Recommend: remove it."
<!-- messages-to-lex:end -->

Review loop: `scripts/agents/kit rulebook`, section "Review loop" (3 fix rounds, 5 only for risk; stop when only nits are left; one reviewer; a findings ledger in the PR).
