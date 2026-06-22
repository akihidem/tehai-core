# ASSUMPTIONS

Reasonable assumptions made while building the MVP, per the spec's instruction to
proceed without blocking and record them here. Each is cheap to revisit.

## Scope & environment
1. **Placement**: new standalone repo `~/Projects/tehai` (user-confirmed). It is a
   distinct *delegation / AI-org planning* layer, complementary to the sibling
   projects `recurse` (hypothesis loop), `rinne` (recursive code engine), and
   `self-improve-arch` (service RSI). None of those implement a generic Task DAG +
   rich Task Contract + Agent Registry + multi-factor Model Router + org templates,
   which is what this provides.
2. **Backend realness (Q2 was left unanswered)**: started from the recommended
   **"deterministic + LLM seam"**; the LLM seam is now **wired for decomposition**
   (`--backend ollama|claude-cli`). Default remains `NullBackend` (deterministic,
   offline, unit-testable). The LLM *proposes*; the deterministic guards *bound*;
   bad output *falls back*. `EchoBackend` is a test double.
   - **Real Ollama environment** (this machine, 2026-06-19): reachable at
     **`http://localhost:11434`** (NOT `172.24.224.1` — `~/Projects/context.md` is
     dated), models are the **gemma4** family (`gemma4:latest`, `gemma4:e2b`); the
     older `gemma2`/`codellama`/`llama3` are gone. `OllamaBackend` defaults updated
     accordingly; override with `--ollama-host`.
   - `claude-cli` / `ollama` adapters mirror `recurse/recurse/llm.py`; tier→model
     maps are editable.
3. **Language / deps**: Python ≥3.10, **standard library only** (matches
   `recurse` / `self-improve-arch` and the house style). Tests use stdlib
   `unittest` so `python3 -m unittest discover` works with zero installs.
   `python3` is the interpreter on this machine (no bare `python`).

## Decomposition & scoring
4. **Deterministic decomposition is heuristic.** The Task Architect instantiates
   the chosen org template's phase pipeline; it does **not** "understand" the
   request the way an LLM would — phase *types* come from the template. Two things
   make it request-sensitive anyway: (a) the **LLM backend** proposes a request-
   shaped task set (`--backend ollama` produced 4 tasks vs the template's 7); and
   (b) **trivial/atomic requests collapse to a `single_deliverable` org** (one task),
   so "fix a typo" no longer instantiates a full pipeline. Non-trivial offline
   requests still use the fixed org pipelines (acceptable — the control structure,
   not phase minimality, is the point of the MVP).
5. **Score baselines** (`architect.py` `_COMPLEXITY/_DOMAIN_SPEC/_TOOL_RISK/_STEPS/
   _CTX_TOKENS`) are hand-tuned constants, not learned. They are deliberately
   conservative (bias toward escalation on risk). The Evaluation Store is the
   intended mechanism for recalibrating them over time (gated).
6. **Risk is computed at request granularity**: every subtask of a hazardous
   request inherits elevated risk (conservative). Acceptable for safety; a future
   version can localize risk per subtask.
7. **`context_size_score`** is derived from `estimated_context_tokens` (≈ tokens/600,
   clamped) — a proxy, not a measured context window.

## Routing & review
8. **Router thresholds**: weighted score ≤35 → small, ≤65 → medium, else large.
   Tunable in `ModelRouter(small_max, medium_max)`.
9. **Hazard detection reads the OBJECTIVE only**, not the `constraints`. Standard
   safety prohibitions like "秘密情報をログに出力しない" contain hazard tokens (秘密)
   and would otherwise force every task to LARGE + human gate. Bare `email`/`メール`/
   `送信` are excluded (validating an email field is benign); only outward *sending*
   (`メール送信`, `外部送信`, `webhook`, `slack通知`) is hazardous.
10. **Tier→model mapping** in the live adapters (haiku/sonnet/opus,
    gemma2/llama3.1) is a placeholder; set real model ids when wiring a backend.

## Org templates & agents
11. The **6 org templates** and their phase pipelines are fixed seeds. Org
    selection is keyword-based (substring match, priority-ordered, `product_delivery`
    as fallback). An ambiguous request may pick a broader template (e.g. an "auth"
    feature classifies as `security_review`); this errs toward more review, by design.
12. **Agent Registry is one bundled `data/agents/registry.json`** array (the loader
    globs `*.json`, so per-template files also work). `allowed`/`forbidden` action
    sets were authored to be internally coherent and least-privilege; only
    `ReleaseManager` may `git_push`/`production_deploy` (both gated).

## State & logging
13. **`run_id` is derived from a SHA-1 of the request** (content-addressed, no
    wall-clock) so plans are reproducible. Pass `--run-id` to override.
14. **Sample logs are plan-time *estimates***, not results of a real execution
    (there is no execution in the MVP). `estimated_cost` uses a crude per-tier
    constant × steps. They exist to seed and exercise the Evaluation Store.
15. **Self-improvement is Phase 1 only**: record + surface + *propose*. No
    automatic rewriting of routing/decomposition logic. Phases 2–3 are designed but
    not implemented (see FUTURE.md).
16. **Executor + sandbox.** By default `tehai run` generates artifact *text* and
    reviews the text; it never performs a dangerous/side-effecting action (those
    stop at the Approval Gate → escalated). With **opt-in `--sandbox`** it actually
    compiles/runs the generated code (py_compile + unittest/pytest, node --check,
    tsc --noEmit) and the real pass/fail overrides the auto-check lens.
    - **OS isolation (default `isolation="auto"`)**: when `unshare` works
      unprivileged (user namespaces enabled — true on this WSL2 host), runners
      execute in user + **network** + pid + ipc + uts namespaces, so generated code
      **cannot reach the network** (the main exfiltration risk). Probed once; falls
      back to best-effort if unsupported. The CLI prints `sandbox: on/unshare`.
    - **Best-effort layer (always)**: temp dir, minimal env (no inherited secrets,
      no PYTHONPATH), `shell=False`, POSIX rlimits (CPU/AS/FSIZE), new session +
      **SIGKILL** process-group reap on timeout (SIGTERM is ignored by a runner that
      is PID 1 in its namespace), filename sanitization (no `..`/`/`).
    - **FS read-hardening (deny-list)**: inside the mount namespace, empty tmpfs is
      mounted over `/home` and `/root`, so generated code can't read user secrets
      (SSH keys, dotfiles, tokens) — verified by test. python's paths (/usr,/lib)
      untouched; best-effort (a failed mount is skipped, network isolation still holds).
    - **STILL partial**: this is a deny-list, not a rootfs jail — secrets outside
      /home,/root (e.g. some /etc files) remain readable. A full allow-list jail
      needs bwrap/nsjail/a container (absent) or a docker daemon (down here), and a
      read-only `/` remount can't carve a writable workdir on this host (`/tmp` is on
      the `/` superblock). The sandbox stays OFF by default and must be enabled.
    - pytest is detected under the *same stripped env* as execution (it lived only
      on the venv `PYTHONPATH`); when absent, test files run directly via the
      interpreter, which only runs `unittest.main()`-style tests (pytest-only files
      without a main guard would be a false pass — a known limitation).

## Process
18. The user chose "design-docs-first" in an earlier turn, then switched to
    "new repo ~/Projects/tehai" — taken as approval to build the code MVP (not docs
    only). Acceptance criteria were frozen before building and verified after.

## Multi-Team AgentOps layer (`tehai/teams/`)
19. **Built ON the single-team primitives, not beside them.** The Meta/Team layer
    reuses the existing Task Architect, Agent Registry, Model Router, Reviewer,
    Judge, Executor, Evaluation Store and the 6 org templates verbatim (the spec
    re-specifies them identically). A team is decomposed by wrapping its internal
    phase pipeline as a tehai `OrgTemplate` and calling the existing `Orchestrator`
    (one new optional `org=` hook on `Orchestrator.plan`).
20. **Deterministic, reproducible failure injection.** The deterministic engine
    can't "detect" an ambiguous requirement from a normal goal, so the verification
    harness injects failures via `injected_failures={team: (FailureType, resolve_after)}`
    to exercise the Failure Router + Loop Guard reproducibly. `resolve_after=1` models
    "root cause fixed on reroute → recover"; a large value models "persists → guard
    stops". Genuine contract-level ambiguity (vague objective) is also detected by
    `TeamContract.validate`.
21. **A/B baselines are STRUCTURAL, not empirical.** verification_report.md compares
    Single Large Agent (A) / Single Multi-Agent Team (B) / Multi-Team (C) by
    capability (decomposition, risk-based review, cross-team failure routing,
    approval gate). C's numbers are real runs; B's task count is a real tehai run;
    A is modeled. It is NOT an LLM-quality benchmark (that needs a wired backend).
22. **Reroute cascades.** On a failure the engine re-runs the root-cause team AND
    its transitive downstream subtree (topological), so dependents see the
    corrected output. Bounded by the iteration cap + the loop guard (re-running
    already-passing teams does not add failures). Team-level capability bounding is
    enforced via `forbidden_task_types` plus the underlying tehai agent permission
    model (child ⊆ parent).
23. **Cross-team competition (§15)** is deterministic and gated: each priority
    boosts the criteria it optimizes and the Judge weights criteria by the goal's
    risk profile, so a security-sensitive goal selects the security-first approach.
    It is NOT an LLM design bake-off (that needs a backend); it models the
    decision structure and is auto-triggered only on hazardous goals / `compete=True`.
