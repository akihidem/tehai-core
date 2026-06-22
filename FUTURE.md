# FUTURE — extension plan

**English** | [日本語](FUTURE.ja.md)

**Status:** the original roadmap A–E is built (decompose → score → review →
generate → execute → review-the-artifact → judge → FSM → log → evaluate → propose →
adopt-by-config), grounded by an opt-in OS-isolated sandbox. What remains below is
either **blocked on tooling absent from this environment** (full FS jail),
**deliberately out of scope for safety** (Phase-3 auto-tune), **a human decision**
(publishing), or **polish with no new capability** (sqlite/dashboard, sibling
interop). Each is marked.

## A. Wire the model backend ✅ DONE (decomposition + scoring + review)
- **Decomposition** (`architect._llm_decompose`): the model proposes subtasks;
  the deterministic guards (contract validation, delegation cap, duplicate/cycle)
  bound them; any failure falls back to the heuristic template path.
- **Scoring** (`scoring.Scorer`): LLM path scores come from the decomposition call;
  the template path is re-scored in one batched backend call (`Scorer.rescore`),
  with heuristic fallback. Strategy label gains `+llmscore`.
- **Review** (`reviewer.Reviewer`): each planned lens is executed as a single-
  viewpoint LLM review → `ReviewResult` → the existing `Judge`. A deterministic
  per-lens reviewer runs under `NullBackend` / on failure, so the loop closes
  offline. Exposed as `tehai review` / `Orchestrator.review_and_judge`.
- All exposed via `--backend {null,echo,claude-cli,ollama}`. Verified live against
  local Ollama (gemma4): LLM reviewers produced per-lens fail/concerns and the
  Judge aggregated to REVISE.
- **Next within A**: feed real executed artifacts (Section B) into the reviewers
  instead of reviewing the contract/plan; let scoring inform a learned router.

## B. Real execution + state machine ✅ DONE (artifact generation)
- `executor.Executor` runs the assigned agent against a contract to *generate the
  expected_output artifacts* (backend, or a deterministic stub under NullBackend),
  hands the real artifact to the Reviewer + Judge, and drives the lifecycle:
  accept → completed, revise/rerun → retry with tier escalation up to the guard's
  cap, discard → failed, human-gated accept → escalated. Dependencies thread
  upstream artifacts into downstream tasks. `tehai run` / `Orchestrator.execute`.
- Permission gate enforced: a contract whose `required_tools` include a dangerous
  capability stops at the Approval Gate (escalated), never auto-run.
- Emits real review-based log rows (review_score, rework_count, test_pass_rate,
  judge_decision, escalated, human_override).
- **Sandbox** (`sandbox.Sandbox`, opt-in `--sandbox`): actually compiles/runs the
  generated artifacts (python py_compile + unittest/pytest, node --check, tsc
  --noEmit) in a temp dir with a minimal env, POSIX resource limits, and
  process-group timeout kill. The real pass/fail **overrides the AUTO_CHECK lens**,
  so a genuine compile/test failure drives the Judge to REVISE — grounding the
  loop in execution, not an LLM reading text.
- **OS isolation** (`Sandbox(isolation="auto")`, default): when `unshare` is usable
  unprivileged, runners execute inside user + **network** + pid + ipc + uts
  namespaces — generated code cannot reach the network (no exfiltration). Probed
  once, falls back to best-effort if unsupported. Verified live (network blocked,
  gemma4 code ran green under it).
- **Enforceable isolation**: `Sandbox(isolation="strict")` / `--sandbox-strict`
  refuses to run code if OS isolation is unavailable (no silent unconfined run).
- **FS read-hardening (deny-list)**: inside the mount namespace, empty tmpfs is
  mounted over `/home` and `/root`, so generated code can't read user secrets
  (SSH keys, dotfiles, tokens). Verified — a `$HOME` secret is unreadable in the
  sandbox. python's paths (/usr,/lib) untouched.
- Real **`elapsed_seconds`** and **`actual_cost`** logged per task — the Ollama
  backend reports token usage (`eval_count`), so cost-estimate error and
  `calibrate`'s observed per-tier cost now compute from real data.
- **Still TODO**: a **full allow-list rootfs jail** (deny-list misses secrets
  outside /home,/root, e.g. some /etc). On this host a read-only `/` remount can't
  carve a writable workdir (`/tmp` shares the `/` superblock), so the complete jail
  needs bwrap/nsjail/a container (absent) or a docker daemon (down here); add
  `isolation="docker"` where one exists. Also: more language runners, tie
  sandbox-enable to the agent's `run_test` capability, token cost from claude-cli
  (only Ollama reports usage today).

## C. Request-sensitive decomposition ✅ DONE
- Trivial/atomic requests collapse to a dynamic `single_deliverable` org (one task)
  with a request-derived task_type — "fix a typo" no longer instantiates a full
  pipeline. The LLM path is request-shaped by construction.
- **Minor remaining**: localize risk per subtask rather than per request
  (ASSUMPTIONS #6) — conservative as-is, low value.

## D. AgentOps / CI gates ✅ STARTED
A `Makefile` (test/demo/plan/run/clean) and `.github/workflows/ci.yml` now gate
every push/PR on the stdlib suite + a CLI smoke (plan/run/evaluate/calibrate) over
Python 3.10–3.12. `tehai run` exits non-zero on a failed task / `review` on a
revise-discard-rerun, so the pipeline gates on the Judge.
Remaining hook points to add as CI stages (each already has a natural home):
- automated tests / static analysis / type-check / lint / coverage → `AutoChecker`
  + `run_test` / `run_static_analysis` capabilities;
- security scan / dependency audit → `SecurityReviewer` / `DependencyAuditor`;
- prompt-eval / model-comparison / per-agent performance → Evaluation Store;
- final gates: **Judge decision → human approval** for outward-facing changes.
- `tehai plan` already exits non-zero on a blocked task, so it can gate a pipeline.

## E. Staged self-improvement ladder ✅ DONE (Phases 1–2)
- **Phase 1 ✅**: record faithfully; surface metrics; *propose* changes
  (`EvaluationStore.suggestions`), never auto-apply.
- **Phase 2 ✅**: `tehai calibrate <ledger>` proposes a concrete config diff
  (router thresholds, observed per-tier cost/seconds); `--apply cfg.json` writes an
  *adoptable* config; a human reviews it; `plan/run --config cfg.json` adopts it
  (`config.load_config` honours only narrow router knobs — never code, never
  security/permission logic).
- **Phase 3 (out of scope by design)**: bounded auto-tuning behind a confirm-budget /
  holdout split (cf. `self-improve-arch`). Deliberately NOT built — auto-mutating
  routing without a human in the loop is exactly the failure mode this project is
  designed to prevent. **Never** auto-edit security/permission/escalation logic.

## F. Persistence & observability
- Swap the JSONL ledger for a queryable store (sqlite, as `self-improve-arch`
  does) while keeping append-only JSONL as the source of truth.
- A small read-only dashboard over the metrics (success by task-type/model/agent,
  escalation & human-override rates, cost/time estimate error).

## G. Interop with siblings
- Optional adapter so `tehai` can hand a confirmed contract to `rinne`'s
  generate→L0→consensus→floor→gate engine for actual code production, and feed
  results back into the Evaluation Store.

## H. Multi-Team AgentOps layer (`tehai/teams/`) ✅ DONE (incl. §15 + cascade)
- **Cross-team competition (§15) ✅**: `competition.CrossTeamCompetition` solves a
  high-stakes design with competing approaches (maintainability/speed/security),
  scoring 8 criteria weighted by the goal's risk profile; a security-sensitive goal
  → the security-first approach wins. Auto-triggered on hazardous goals (or
  `meta.run(compete=True)`); recorded in `MetaRunResult.competitions`.
- **Cascading reroute ✅**: on a reroute the root-cause team **and its downstream
  subtree** re-run (topological), so dependents see the corrected output.
- **Real backend ✅ (verified live)**: `MetaOrchestrator.default(backend="ollama")`
  threads the live model through the whole team→tehai pipeline (proven on a
  doc-only goal). Large multi-team live runs are slow (many model calls/team) —
  reserve for spot checks.
- **Improvement loop**: the Global Evaluation Store surfaces proposals only (§20
  forbids auto-changing team composition/routing). Router-threshold knobs adopt via
  the existing `tehai calibrate --apply` / `--config`; team-level adoption stays a
  deliberate human step.
- **Still open**: genuine (non-injected) ambiguity detection by the Verification
  Team needs a live judge; deterministic runs inject failures for reproducibility.

## I. Vendor routing & backend bench — gama (蝦蟇) ✅ DONE (deterministic Conductor)
- **Backends ✅**: `claude-tui` (flat-subscription Claude Code TUI via `claude-cli-run.py`,
  not the metered `--print`), `codex` (`codex exec`), `gemini` (OpenAI-compatible,
  key-gated), remote-capable `ollama`. All stdlib, inert until used.
- **GamaBackend (Conductor) ✅**: deterministic table lookup on `task_type` (threaded
  through the seam), fallback to `default_backend`; built via `config.gama_from_config`,
  adopted with `--config` + `--backend gama`.
- **Bench as external anchor ✅**: `tehai bench` scores backends per task-class with
  deterministic checkers and proposes a `routing_table` (proposal only, human-ratified —
  same discipline as `calibrate`). Proven live on local `ollama` (gemma4, 5/5 classes).
- **EnsembleBackend (model-combination loop) ✅**: combines N sub-backends on the SAME
  task (`synthesize` / `majority` / `first`) — the mixture-of-agents counterpart to
  gama's routing. `--backend ensemble`, `ensemble_from_config`,
  `examples/ensemble.example.json`. Homogeneous self-ensemble (one model × temperature)
  or heterogeneous mix. Finding (adversarially verified, fair measurement): (1) identical copies are
  useless — 7B×5 == 7B×1 (easy 0.8, hard 0.5); (2) a HETEROGENEOUS light mix (7B+24B+32B)
  genuinely beats a single 7B (hard 0.5→0.83) by covering different blind spots; (3) but
  it does NOT reach the strong 122B (hard 1.0): a class where ALL small members fail
  identically (big mental arithmetic) can't be recovered by aggregation — the big
  model/tool is irreducible there. NB: an earlier run that *appeared* to show the ensemble
  beating the 122B was a measurement bug (reasoning-model code not extracted; tokens
  truncated) — the `_extract_code` fix + per-architecture max_tokens flipped the 122B's
  hard score 0.5→1.0. Cross-architecture comparison needs fair extraction + token budget.
- **ToolBackend (program-aided) + structured combination ✅ (verified)**: a small model that
  can't do arithmetic mentally solves it by WRITING Python we run (`ToolBackend`/PAL): 7B+tool
  went 0/3 → 3/3 on math, matching the 122B. Composing **gama routing × ensemble × tool** into a
  SOVEREIGN light system (qa→7B+tool, code→Coder, research→hetero) matched the 122B on the
  6-case hard suite (1.0 = 1.0) and held on a 12-case suite (combined **0.92** vs 122B 0.83 —
  but the 122B's 2 code losses may be token-truncation, so read as *competitive*, not a clean
  win). Lesson: **STRUCTURE — route each class to the right light mechanism — beats both copies
  (useless) and naive ensembling (0.83), and is competitive with a 122B, fully local.** The
  combined's lone miss (a mod-arithmetic "reasoning" task sent to the ensemble) was a routing
  gap (compute-able reasoning should route → tool), not a capability gap. Small N, single run.
- **Resolved (fair tokens + routing-fix attempt)**: at 8192 tokens the 122B recovers the
  c1 code loss (it WAS truncation) but still misses c3 (roman numerals) — a genuine miss
  where a 32B code-specialist beats the 122B reasoner. Final on the 12-case suite: the
  sovereign light stack and the 122B **TIE at 0.92, with COMPLEMENTARY misses** (light
  stack misses r4 day-of-week mod-arithmetic; 122B misses c3 roman). The attempted routing
  fix (a tool member inside the research ensemble) did NOT rescue r4 — the synthesize
  aggregator didn't adopt the tool's answer. Honest verdict: **structured light combination
  is genuinely COMPETITIVE/tied with a 122B (not strictly better — the earlier apparent edge
  was partly 122B truncation), fully local & sovereign.** Reproduce: `python3 -m
  experiments.moa_vs_strong <config>` (see `examples/moa_vs_strong.example.json`).
- **Sovereign remote floor ✅ (proven on a Mac Studio MLX)**: `ssh-openai` calls an
  OpenAI-compatible server (MLX `mlx_lm.server`, LM Studio, vLLM) over
  `ssh <host> curl localhost:<port>/v1/…` (prompt on stdin, no open port; 0.5–2 s/call).
  The `ollama` lane also has a `transport:"ssh"` (`ollama run`) for ollama hosts.
  `examples/gama_config.macstudio.example.json`.
- **Deferred — LLM Conductor**: a per-call LLM router (e.g. Claude decides each subtask's
  vendor) is intentionally NOT built — it trades transparency + cost for flexibility and
  reintroduces self-report dependence. Keep it behind an experiment flag if pursued.
- **Next**: real per-vendor `unit_cost` so the proposal optimizes score-per-dollar (not
  just score-then-latency); per-instance (not per-class) routing.
