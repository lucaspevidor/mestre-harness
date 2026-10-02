# Finding contract

Use stable IDs: `H-` for hardening, `B-` for bloat, and `R-` for code review. Bloat and review findings are reported, never applied by the agent that found them. Hardening is the exception: the hardener applies in-scope fixes to the canonical plan with targeted edits and records each finding in the plan's dispositions table, in compact form.

For every finding include:

- **ID and severity:** blocker, important, or suggestion; state confidence separately
- **Exact location:** plan version + step/section ID, or path + revision/side + line range or symbol
- **Problem:** a concrete failure, avoidable cost, or missing requirement
- **Why it matters:** scenario and consequence, tied to the requested outcome
- **Evidence:** source path/example, relevant requirement, or check result; label inference
- **Minimum change:** the smallest correction or simpler alternative
- **Tradeoff:** behavior, compatibility, clarity, cost, or verification you gain/lose
- **Scope:** in-scope fix, material decision, or out-of-scope observation
- **Verification:** how to establish the correction or resolve uncertainty

For bloat, always include location, why, simpler alternative, and tradeoff even when the recommendation is to remove something completely. Do not call security, error handling, or tests "bloat" merely because they add code. Justify proportionality from concrete requirements and risk.

Every finding gets a disposition: incorporated, applicable pending approval, rejected, deferred, or needs decision, with a reason and resulting plan version. The hardener records its own; the orchestrator decides bloat and review dispositions and can overrule a hardening edit. No-finding reports must state inspected scope and limitations rather than inventing suggestions.

Soft budgets: critics aim for 800 words and the reviewer for 1,000 words. Group duplicates, put important issues first, and never drop a critical finding to meet the budget. Return an explicit partial status if a scoped continuation is needed.
