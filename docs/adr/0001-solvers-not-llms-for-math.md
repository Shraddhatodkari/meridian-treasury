# ADR-0001: Solvers and models do the math, LLMs explain it

**Status:** Accepted

**Context.** Treasury decisions are numeric and constrained: buffers, capital controls, covenants and prepayment
rules. LLMs are unreliable at arithmetic and constraint satisfaction.

**Decision.**
- Payment timing comes from a quantile gradient-boosting model with conformal calibration.
- Cash positions come from a Monte Carlo forecast.
- Where cash should sit comes from a linear programme solved by HiGHS, with policy constraints coded in.
- Agents interpret these outputs, and a numeric grounding check rejects any dollar figure that the tools did not produce.

**Consequences.** Recommendations are reproducible and explainable to auditors. Offline reasoners give a
deterministic baseline.
