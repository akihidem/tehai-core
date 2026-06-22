# tehai （手配）

**English** | [日本語](README.ja.md)

[![CI](https://github.com/akihidem/tehai-core/actions/workflows/ci.yml/badge.svg)](https://github.com/akihidem/tehai-core/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Dependencies](https://img.shields.io/badge/deps-stdlib%20only-brightgreen.svg)](pyproject.toml)

> A **controlled, auditable delegation layer**. It turns a request into a DAG of
> small, contract-bound subtasks, routes each to the *cheapest capable* model,
> reviews by risk, judges, and logs — under hard budgets, bounded recursion, and
> least-privilege permissions.

`手配` means *arranging / dispatching the right hands for a job*. That is exactly
the job: not "spawn agents", but **decide who does what, at what model tier, with
what permissions, reviewed how — and prove it afterwards.**

> 📊 **Philosophy at a glance** — a one-page visual explainer of the design
> principles: [`docs/philosophy.html`](docs/philosophy.html) (rendered:
> [`docs/philosophy.png`](docs/philosophy.png)).
> 📖 **How to use** — a single-page HTML manual (commands, backends, multi-team,
> safety): [`docs/manual.html`](docs/manual.html).

This is **not** an unbounded self-replicating agent swarm. Every act of delegation
passes role templates, budgets, depth limits, permission boundaries, and stop
conditions.

Status: **MVP.** Deterministic by default — the whole pipeline runs offline with
**zero third-party dependencies** (Python ≥3.10 stdlib only). The **LLM backend is
wired end-to-end** (`--backend ollama|claude-cli`): the model *proposes*
decomposition, scoring, artifact generation, and per-lens review, while the
deterministic guards (contract validation, cycle/dup/delegation caps, permission
gate, Judge precedence) *bound* every step; any bad/garbage response falls back to
the heuristic/stub path. So `tehai run` will generate a real artifact, review the
artifact, and judge it — or stay fully offline with stubs. Verified live against a
local Ollama (gemma4). The orchestrator interface is a pure seam.

---

## Quickstart

```bash
cd tehai-core

# Plan a request into a contract-bound task DAG.
python3 -m tehai plan "ログイン画面に入力バリデーションを実装する" --save-log

# JSON (machine-readable RunPlan).
python3 -m tehai plan "決済APIに認証を追加し本番にデプロイ" --json

# LLM-backed decomposition (model proposes, guards bound it; falls back on bad output).
python3 -m tehai plan "ログイン画面の入力バリデーションを実装する" --backend ollama
python3 -m tehai plan "..." --backend ollama --ollama-host http://172.24.224.1:11434
# (deterministic default is --backend null)

# Execute the risk-based review plan + Judge over a request's tasks.
python3 -m tehai review "決済APIに認証を追加する" --backend ollama --limit 1
python3 -m tehai review "add pagination" --artifact ./patch.txt   # heuristic, offline

# Full loop: generate real artifacts -> review the artifacts -> Judge -> FSM.
python3 -m tehai run "メール検証関数を実装する" --backend ollama --limit 1 --out ./out
python3 -m tehai run "add pagination" --out ./out   # offline: deterministic stubs
# --sandbox: ACTUALLY compile/run the generated code (opt-in; grounds auto-check).
python3 -m tehai run "addと単体テストをPythonで実装" --backend ollama --sandbox --out ./out

# Inspect the registry / org templates.
python3 -m tehai agents
python3 -m tehai orgs

# Aggregate metrics + improvement *proposals* from a ledger.
python3 -m tehai evaluate runs/R-xxxxxxxx.jsonl

# Full end-to-end demo over three contrasting requests.
python3 examples/sample_run.py

# Tests (zero deps).
python3 -m unittest discover -s tests -t .
```

---

## 1. Architecture overview

```
request
  │
  ▼
┌──────────────┐   classify        ┌───────────────────┐
│ Orchestrator │ ────────────────▶ │ Organization      │  6 templates
│ (conductor)  │                   │ Template selector │  (PM/Arch/…)
└──────┬───────┘                   └───────────────────┘
       │ decompose
       ▼
┌──────────────┐  per-type heuristics + guarded 1-level recursion
│ TaskArchitect│ ───────────────────────────────────────────────▶  Task DAG
└──────┬───────┘                                                   (TaskContracts)
       │ for each contract:
       ├─▶ Agent Designer   (registry.select_for_task — never fabricates)
       ├─▶ Model Router     (weighted score + hazard escalation → tier)
       ├─▶ Permission model (child capabilities ⊆ parent)
       └─▶ Review Planner   (risk-based lens composition + judge/human gate)
       │
       ▼
   topological order ──▶ RunPlan ──▶ Execution Logger (JSONL) ──▶ Evaluation Store
                                                                    (metrics + proposals)
```

Components map 1:1 to modules:

| Spec component | Module | Core responsibility |
|---|---|---|
| Orchestrator | `orchestrator.py` | classify → decompose → assign → route → review-plan → order |
| Task Architect | `architect.py` | request → DAG of `TaskContract`s; guarded recursion; LLM or heuristic |
| Model backend | `backends.py` | seam: Null (default) / Echo / Ollama / claude-cli adapters |
| Task scoring | `scoring.py` | backend-driven 6-axis (re)scoring, heuristic fallback |
| Agent Designer | `registry.py` | `select_for_task` from the registry; record (never create) new-agent proposals |
| Model Router | `model_router.py` | weighted multi-factor scoring + hazard/ context/ failure escalation |
| Reviewer planning | `review_planner.py` | risk-based, lens-separated review composition |
| Reviewer execution | `reviewer.py` | runs each lens via backend → ReviewResult; deterministic fallback |
| Executor | `executor.py` | generate artifact → review artifact → Judge → FSM; permission gate; retries |
| Sandbox | `sandbox.py` | opt-in: really compile/run artifacts (isolated, limited); grounds auto-check |
| Judge | `judge.py` | compare review grounds → accept / revise / discard / rerun |
| Recursive guard | `decompose_guard.py` | depth/ delegation/ concurrency/ budget/ cycle/ dup/ progress stops |
| Permissions | `permissions.py` | least-privilege, approval gates, child ⊆ parent |
| Logger | `logger.py` | append-only JSONL ledger |
| Evaluation store | `evaluation.py` | aggregate metrics + *proposed* improvements |
| Schemas | `schemas/*.json` + `schema.py` | Task Contract / Agent Template / Log Record + a tiny validator |

```
tehai/
├── tehai/
│   ├── orchestrator.py     architect.py      registry.py
│   ├── model_router.py     decompose_guard.py permissions.py  scoring.py
│   ├── review_planner.py   reviewer.py       judge.py        evaluation.py
│   ├── executor.py         sandbox.py        logger.py       backends.py  models.py
│   ├── org_templates.py    schema.py         cli.py  __main__.py
│   ├── schemas/*.json                        (Task Contract / Agent / Log)
│   └── data/agents/registry.json             (seed Agent Registry)
├── examples/sample_run.py
├── tests/                  (216 tests, stdlib unittest)
├── README.md  ASSUMPTIONS.md  FUTURE.md  pyproject.toml
```

## 2. Data models (`models.py`)

The center of gravity is the **`TaskContract`**. Nothing executes against an
invalid contract — `validate()` rejects vague objectives ("全部よしなに…"),
missing expected output / acceptance criteria / escalation conditions, self-deps,
and out-of-range scores. A failing contract is returned to the parent for repair.

**Task lifecycle** (`TaskStatus` + `TASK_TRANSITIONS`):

```
pending → ready → running → reviewing → accepted → completed
              ↘ blocked        ↘ rejected → retrying → running
                               ↘ escalated ↗
              (any) → failed
```

**Artifact provenance** is first-class (`Provenance`): who made it, under which
contract, from which inputs, which reviews it passed, which Judge decided.

## 3. Agent Registry

Selection is **registry-first**. The Agent Designer picks a registered template
or, at most, adjusts its parameters. A genuinely novel need is **recorded as a
proposal** (`propose_new_template`) — never auto-instantiated. Each template is
defined by **responsibilities / allowed_actions / forbidden_actions /
output_schema / quality_bar / escalation_rules** — persona is incidental.

## 4. Organization templates

Six templates (Product Delivery, Research, Code Implementation, Incident
Response, Content Production, Security Review). Not a fixed hierarchy — the
orchestrator classifies the request and instantiates the matching template's
phase pipeline into concrete contracts. **Trivial/atomic requests** (typo, rename,
format…) collapse to a dynamic `single_deliverable` org (one task), so the offline
path stays request-sensitive instead of always emitting a full pipeline.

## 5. Task decomposition

Two interchangeable paths behind one interface:

- **Deterministic (default):** each org phase → one `TaskContract` with per-type
  artifact templates, heuristic 6-axis scores, dependencies forming a DAG. An
  oversized implementation task is split **one level deeper** — only if the
  `DecompositionGuard` permits.
- **LLM-backed (`--backend ollama|claude-cli`):** the model is given the org
  template's roles and the valid task-type enum and *proposes* the minimal
  subtask set (so a trivial request no longer instantiates a full pipeline). Each
  proposed contract is then run through the **same** `TaskContract.validate()`,
  the delegation cap, duplicate-objective and cycle checks; **any** failure falls
  back to the deterministic path. The model proposes — the guards decide.

*Decomposing is not progress*: a split is allowed only when children are smaller,
contractable, acyclic, non-duplicate, and move toward the parent's artifact.

## 6. Model routing

`model_selection_score = 0.25·complexity + 0.20·ambiguity + 0.20·risk +
0.15·context_size + 0.10·tool_risk + 0.10·domain_specialization` → tier by
threshold (≤35 small, ≤65 medium, else large). Then **hard escalations**: hazard
keywords in the objective (auth / payment / production / secret / external-send /
delete), inherently high-stakes types, `risk ≥ 70`, large context, or ≥2
consecutive failures. Default bias is the cheapest capable tier.

**Effort is a separate axis from tier.** Tier = *which* model; **effort** (low /
medium / high / xhigh / max) = *how hard it thinks*. The router sets a per-task
`recommended_effort` from the reasoning-hardness axes (complexity + ambiguity +
specialization), escalated by hazard / high-risk / repeated failure, and **floored
by the assigned agent's baseline** (each agent template has a `recommended_effort`,
defaulting from its tier: large→high, medium→medium, small→low). So a SecurityReviewer
reasons at ≥high effort, an AutoChecker at low. The CLI prints `… / effort=high`;
backends carry it via the seam (mapped to the API thinking budget where supported).

## 7. Permission model

Least privilege: an action not explicitly granted is **denied**. Dangerous /
outward-facing / destructive actions return `NEEDS_APPROVAL` and must pass an
Approval Gate (dry-run available). A child agent may never hold a capability its
parent lacks (`enforce_child_subset`).

## 8. Review planning

Risk-based, never uniform. Doc-formatting → a single automated check.
Implementation → tests + requirements + edge-cases. Auth/secret/payment/external
→ + security review + Judge + **human gate**. Release → independent review +
Judge + human gate. **Lenses are separated** — each reviewer sees exactly one
viewpoint (requirements *or* edge-cases *or* security *or* UX).

The planner (`review_planner.py`) decides *which* lenses; `reviewer.py` *executes*
them — each as a single-viewpoint LLM review via the backend (or a deterministic
structural reviewer under `NullBackend`), producing `ReviewResult`s the Judge then
aggregates. With no artifact yet, the lenses review the contract/plan itself. Run
it with `tehai review` / `Orchestrator.review_and_judge`.

## 9. Judge

Deterministic precedence over the *grounds* of the reviews:
`discard (critical/security) > rerun (transient) > revise (other fails) >
accept`. The reason and the basis (per-lens verdict/severity) are recorded.

## 9.5 Execution & safety (`executor.py`)

`Orchestrator.execute(plan)` walks the DAG in topological order. For each task the
assigned agent **generates the expected_output artifacts** — real content via the
backend, or a deterministic stub under `NullBackend` — and the *artifact itself*
(not the plan) is reviewed; the Judge's verdict drives the state machine:

```
generate → review(artifact) → judge ─ accept ───────────────→ completed
                                     ├ accept + human-gate ──→ escalated (awaiting approval)
                                     ├ revise / rerun ───────→ retry (tier escalates) up to guard cap → escalated
                                     └ discard ──────────────→ failed
```

Upstream artifacts are threaded into downstream tasks as context. Provenance is
recorded per artifact.

**Grounding (`--sandbox`, opt-in):** with the sandbox enabled, generated artifacts
are actually compiled/run (`python -m py_compile` + unittest/pytest, `node
--check`, `tsc --noEmit`) in a temp dir with a minimal env, POSIX resource limits,
and a process-group timeout kill. The real pass/fail **overrides the auto-check
lens**, so a genuine compile/test failure drives the Judge to REVISE. Verified
live: gemma4 generated `add.py` + a unittest, the sandbox ran it green, the Judge
accepted.

**Safety:** the Executor only ever produces artifact *text* and (with `--sandbox`)
runs it under isolation — it never performs a dangerous/side-effecting action. If a
contract's `required_tools` include a dangerous capability (deploy/push/delete/
external send), execution stops at the Approval Gate and the task is **escalated —
never auto-run**. The sandbox is **off by default** (executing model output is
risky). When enabled it auto-uses OS **namespace isolation** via `unshare` (user +
**network** + pid + ipc + uts, unprivileged) so generated code **cannot reach the
network** — the CLI prints `sandbox: on/unshare`. It also **mounts empty tmpfs over
`/home` and `/root`** so generated code can't read user secrets (SSH keys/dotfiles/
tokens) — verified by test. Use **`--sandbox-strict`** to *require* isolation
(refuse to run unconfined). FS hardening is a **deny-list, not a full jail**
(secrets outside /home,/root may remain readable — needs bwrap/nsjail/a container);
see ASSUMPTIONS #16 / FUTURE.md B.

## 10. Logging & evaluation

Phase 1: **record faithfully, change nothing.** Append-only JSONL ledger
(`schemas/log_record.schema.json`); `tehai run` logs real `elapsed_seconds`,
review score, rework, judge decision. The Evaluation Store aggregates success rate
by task-type / model / agent / strategy, rework, review scores, cost & time
estimate error, escalation & human-override rates — and emits **proposed**
improvements. **Phase 2 (complete):** `tehai calibrate <ledger>` turns the ledger
into a concrete *proposed* config diff (router thresholds from per-tier success,
observed per-tier cost/seconds) — **never auto-applied**. `--apply cfg.json` writes
an *adoptable* config; a human reviews it; `tehai plan/run --config cfg.json` then
adopts it. Adoption is an explicit human act loading narrow knobs (router
thresholds only) — never code self-modification. Phase 3 (bounded auto-tune) stays
out of scope by design. CI: a `Makefile` + `.github/workflows/ci.yml` gate every
push on the suite + a CLI smoke (spec §12).

## 11. CLI / API

`tehai plan|run|review|meta|teams|verify|agents|orgs|evaluate|calibrate` (`meta`/`teams`/
`verify` drive the Multi-Team layer — see the section near the end). Library API: `from tehai import
Orchestrator; o = Orchestrator.default(backend="ollama")`; `o.plan("…")` → `RunPlan`,
`o.execute(plan)` → `{task_id: ExecutionResult}`, `o.review_and_judge(contract)` →
`(plan, results, decision)`. `tehai plan` exits non-zero on a blocked task, `run`
on a failed task, `review` on a revise/discard/rerun — so CI can gate on any.

## 12. Tests

216 stdlib `unittest` tests — contract validation, weighted routing & every
escalation, all guard rejection reasons, permission subset, risk-based review,
judge precedence, registry/org integrity, the mini schema validator, and an
end-to-end orchestration check (≥3 contracts, all schema-valid, topo order,
agent+model+review assigned, sample log schema-valid). Run:
`python3 -m unittest discover -s tests -t .`

## 13. Future

See [FUTURE.md](FUTURE.md): LLM backend wiring (the seam already exists),
request-sensitive decomposition, AgentOps/CI gates, and the staged
self-improvement ladder (surface → propose → bounded auto-tune).

## Multi-Team AgentOps layer (`tehai/teams/`)

A controlled **multi-team** layer composes the single-team primitives above into an
AI development organization, without rebuilding any of them.

```
product goal
   ▼
Meta Orchestrator ── select team composition (Team Registry, 7 teams)
   ▼  Team Contracts (a DAG over teams)
Team Orchestrator (per team) ── Team Contract → tehai pipeline (architect/router/review/judge/execute)
   ▼  team result
Failure Router ── classify failure_type → route to the ROOT-CAUSE team (not a blind retry)
   ▼
Autonomous Loop Guard ── auto-reroute (low/med risk) OR stop (security/repeat/cost/prod → approval gate)
   ▼
Global Evaluation Store ── cross-team metrics + proposals (never auto-applied)
```

**Teams** (Team Registry, `data/teams/registry.json`): Product Planning, Architecture,
Implementation, Verification, Security, Integration, Documentation — each with a
mission, internal agents (reused from the Agent Registry), allowed/forbidden task
types, and an internal phase pipeline (turned into a tehai OrgTemplate).

**Failure routing** (`failure_router.py`): 11 failure types route by root cause —
`requirement_ambiguity`→Product Planning, `architecture_conflict`→Architecture,
`integration_conflict`→Integration (not Implementation), `security_risk`→Security
(+human gate), `cost_overrun`→Meta/shrink, `repeated_failure`/`permission_violation`
→human. A failure never blindly re-runs the same team.

**Autonomous Loop Guard** (`loop_guard.py`): auto-reroutes low/medium-risk failures,
but stops (with a classification: escalate / human-approval / shrink / clarify /
fail / defer) on a security risk, a 3× repeated failure type, a cost cap, a
prod/external action, low judge confidence, team conflict, or a scope change. On a
reroute the root-cause team **and its downstream subtree** re-run (cascading).

**Cross-team competition** (`competition.py`, spec §15): for a high-stakes design
(hazardous goal or `compete=True`), competing approaches (maintainability / speed /
security) are scored on 8 criteria weighted by the goal's risk profile — a
security-sensitive goal selects the security-first approach. Gated because it is
expensive.

```bash
python3 -m tehai meta "Todoアプリにタスク完了フラグ機能を実装する"   # full 6-team flow
python3 -m tehai meta "認証機能を実装する"                          # Security Team joins
python3 -m tehai teams                                             # list team templates
python3 -m tehai verify                                            # run scenarios -> verification_report.md
```

Library: `from tehai.teams import MetaOrchestrator; MetaOrchestrator.default().run(goal)`
returns a `MetaRunResult` (team contracts, execution order, loop history, clarification
reports, metrics). `injected_failures={team: (FailureType.X, n)}` deterministically
exercises failure routing — used by the verification harness.

**Verification:** `verification/run_verification.py` runs 9 scenarios (incl. deliberate
ambiguity / security / integration-conflict / repeated-failure / cost-overrun failures),
checks the routing + loop-guard behaviour (41 checks), and writes
[`verification_report.md`](verification_report.md) with an A/B/C structural comparison
(single large agent vs single multi-agent team vs multi-team). Schemas:
`schemas/team_contract.schema.json`, `schemas/team_registry.schema.json`.

## License

MIT.
