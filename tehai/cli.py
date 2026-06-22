"""tehai CLI.

    tehai plan "<request>" [--json] [--save-log] [--run-id ID] [--runs-dir DIR]
    tehai agents
    tehai orgs
    tehai evaluate <ledger.jsonl>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .backends import get_backend
from .config import ensemble_from_config, gama_from_config, load_config
from .evaluation import EvaluationStore
from .logger import ExecutionLogger
from .model_router import ModelRouter
from .models import ModelTier, RunPlan, TaskStatus
from .orchestrator import Orchestrator
from .org_templates import CATALOG
from .registry import AgentRegistry
from .sandbox import Sandbox


BACKEND_CHOICES = ["null", "echo", "claude-cli", "claude-tui", "codex", "gemini", "ollama", "ssh-openai"]
BACKEND_CHOICES_GAMA = BACKEND_CHOICES + ["gama", "ensemble"]


def _backend_arg(args: argparse.Namespace):
    """Backend to hand to Orchestrator.default: a GamaBackend (vendor router) for
    'gama' or an EnsembleBackend (model-combination loop) for 'ensemble', both built
    from --config; else the backend name string."""
    b = getattr(args, "backend", None)
    cfg = getattr(args, "config", None)
    if b == "gama":
        return gama_from_config(cfg)
    if b == "ensemble":
        return ensemble_from_config(cfg)
    return args.backend


def _render_plan(plan: RunPlan, router: ModelRouter, orch: Orchestrator) -> str:
    lines: list[str] = []
    a = lines.append
    a(f"run_id        : {plan.run_id}")
    a(f"request       : {plan.request}")
    a(f"org template  : {plan.org_template_id}  (top task_type: {plan.task_type.value})")
    a(f"strategy      : {plan.decomposition_strategy}")
    a(f"tasks         : {len(plan.contracts)}   delegations under guard")
    a("")
    a("Execution order (topological):")
    by_id = {c.task_id: c for c in plan.contracts}
    for i, tid in enumerate(plan.execution_order, 1):
        c = by_id[tid]
        rp = plan.review_plans[tid]
        routing = router.route(c)
        depth_mark = "  " * c.depth
        a(f"{i:>2}. {depth_mark}[{c.task_id}] {c.task_type.value}  ->  "
          f"{c.recommended_model.value.upper()} / effort={c.recommended_effort.value}")
        a(f"      {depth_mark}objective : {c.objective}")
        a(f"      {depth_mark}agent     : {c.assigned_agent_template}")
        a(f"      {depth_mark}status    : {c.status.value}")
        if c.dependencies:
            a(f"      {depth_mark}depends   : {', '.join(c.dependencies)}")
        a(f"      {depth_mark}outputs   : {', '.join(c.expected_output)}")
        a(f"      {depth_mark}route why : {routing.reasons[-1]}")
        lenses = ", ".join(s.lens.value for s in rp.steps) or "(none)"
        gate = []
        if rp.require_judge:
            gate.append("judge")
        if rp.require_human_gate:
            gate.append("HUMAN-GATE")
        gate_s = (" + " + ", ".join(gate)) if gate else ""
        a(f"      {depth_mark}review    : {lenses}{gate_s}")
    a("")
    if plan.assumptions:
        a("Assumptions:")
        for s in plan.assumptions:
            a(f"  - {s}")
    if orch.registry.proposals:
        a("")
        a("New-agent proposals (recorded, NOT created):")
        for p in orch.registry.proposals:
            a(f"  - {p}")
    return "\n".join(lines)


def cmd_plan(args: argparse.Namespace) -> int:
    backend_kwargs = {}
    if args.backend == "ollama" and args.ollama_host:
        backend_kwargs["host"] = args.ollama_host
    orch = Orchestrator.default(_backend_arg(args), config=args.config, **backend_kwargs)
    plan = orch.plan(args.request, run_id=args.run_id)
    if orch.architect.last_error:
        sys.stderr.write(f"[tehai] LLM fell back to template: {orch.architect.last_error}\n")

    if args.save_log:
        runs_dir = Path(args.runs_dir)
        logger = ExecutionLogger(runs_dir / f"{plan.run_id}.jsonl")
        orch.emit_sample_log(plan, logger)
        sys.stderr.write(f"[tehai] sample log -> {logger.path}\n")

    if args.json:
        print(plan.to_json())
    else:
        print(_render_plan(plan, orch.router, orch))
    # Non-zero exit if any task is blocked, so CI can gate on it.
    blocked = [c.task_id for c in plan.contracts if c.status == TaskStatus.BLOCKED]
    return 1 if blocked else 0


def cmd_run(args: argparse.Namespace) -> int:
    backend_kwargs = {}
    if args.backend == "ollama" and args.ollama_host:
        backend_kwargs["host"] = args.ollama_host
    sandbox_arg = Sandbox(isolation="strict") if args.sandbox_strict else args.sandbox
    orch = Orchestrator.default(_backend_arg(args), sandbox=sandbox_arg, config=args.config, **backend_kwargs)
    plan = orch.plan(args.request, run_id=args.run_id)
    logger = ExecutionLogger(Path(args.runs_dir) / f"{plan.run_id}.jsonl") if args.save_log else None
    results = orch.execute(plan, limit=args.limit, logger=logger)

    out_dir = Path(args.out) if args.out else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    sbx = "off"
    if (args.sandbox or args.sandbox_strict) and orch.executor.sandbox is not None:
        sbx = f"on/{orch.executor.sandbox.resolve_isolation()[0]}"
    print(f"run_id: {plan.run_id}   strategy: {plan.decomposition_strategy}   "
          f"backend: {args.backend}   sandbox: {sbx}   "
          f"executed {len(results)}/{len(plan.contracts)} task(s)")
    mark = {TaskStatus.COMPLETED: "✓", TaskStatus.FAILED: "✗", TaskStatus.ESCALATED: "⚠"}
    exit_code = 0
    for tid in plan.execution_order:
        r = results.get(tid)
        if r is None:
            continue
        c = next(c for c in plan.contracts if c.task_id == tid)
        judge = r.decision.decision.value if r.decision else "-"
        print(f"\n{mark.get(r.status, '·')} [{tid}] {c.task_type.value} -> {r.status.value}  "
              f"(judge={judge}, attempts={r.attempts})")
        for line in r.trace:
            if "sandbox" in line:
                print(f"    ▷ {line}")
        if r.approval_required:
            print(f"    ⛔ approval gate required for action: {r.approval_required}")
        for name, content in r.artifacts.items():
            print(f"    artifact: {name} ({len(content)} chars)")
            if out_dir:
                safe = name.replace("/", "_").replace(" ", "_")
                (out_dir / f"{tid}__{safe}").write_text(content, encoding="utf-8")
        if r.status == TaskStatus.FAILED:
            exit_code = 1
    if out_dir:
        print(f"\nartifacts written under {out_dir}/")
    if logger:
        sys.stderr.write(f"[tehai] execution log -> {logger.path}\n")
    return exit_code


def cmd_review(args: argparse.Namespace) -> int:
    backend_kwargs = {}
    if args.backend == "ollama" and args.ollama_host:
        backend_kwargs["host"] = args.ollama_host
    orch = Orchestrator.default(_backend_arg(args), **backend_kwargs)
    plan = orch.plan(args.request, run_id=args.run_id)
    artifact = Path(args.artifact).read_text(encoding="utf-8") if args.artifact else None

    by_id = {c.task_id: c for c in plan.contracts}
    targets = plan.execution_order[: args.limit]
    print(f"run_id: {plan.run_id}   strategy: {plan.decomposition_strategy}   "
          f"backend: {args.backend}   reviewing {len(targets)}/{len(plan.contracts)} task(s)")
    exit_code = 0
    for i, tid in enumerate(targets):
        c = by_id[tid]
        _, results, decision = orch.review_and_judge(c, artifact if i == 0 else None)
        print(f"\n[{c.task_id}] {c.task_type.value} — {c.objective[:70]}")
        for r in results:
            print(f"   - {r.lens.value:<12} {r.verdict:<8} sev={r.severity:<8} {r.rationale[:78]}")
            for f in r.findings[:3]:
                print(f"       · {f[:88]}")
        print(f"   => JUDGE: {decision.decision.value.upper()}   ({decision.reason})")
        if decision.decision.value in ("revise", "discard", "rerun"):
            exit_code = 1
    if orch.reviewer.last_error:
        sys.stderr.write(f"[tehai] some reviews fell back to heuristic: {orch.reviewer.last_error}\n")
    return exit_code


def cmd_meta(args: argparse.Namespace) -> int:
    from .teams import AutonomyLevel, MetaOrchestrator
    m = MetaOrchestrator.default(args.backend, sandbox=args.sandbox)
    res = m.run(args.goal, autonomy=AutonomyLevel(args.autonomy))
    if args.json:
        print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
        return 0 if res.final_status == "completed" else 1

    print(f"run_id : {res.run_id}")
    print(f"goal   : {res.goal}")
    print(f"teams  : {' -> '.join(res.team_composition)}")
    print(f"final  : {res.final_status}   (human_intervention={res.human_intervention_required})")
    print("\nTeam Contracts (execution order):")
    by_id = {c.team_task_id: c for c in res.team_contracts}
    for ttid in res.execution_order:
        c = by_id[ttid]
        tr = res.team_results.get(ttid)
        ntasks = len(tr.plan.contracts) if (tr and tr.plan) else 0
        st = tr.status.value if tr else "-"
        print(f"  [{c.team_task_id}] {c.assigned_team}  deps={c.dependencies}  -> {st} ({ntasks} tasks, loop={c.loop_count})")
        print(f"      objective: {c.objective}")
        print(f"      outputs  : {c.expected_outputs}")
    if res.loop_history:
        print("\nFailure routing / loop:")
        for h in res.loop_history:
            print(f"  #{h['iteration']} {h['team']}: {h['failure']['failure_type']} "
                  f"-> {h['failure']['recommended_route']}  | guard: {h['guard']['reason']}")
    print("\nMetrics:", json.dumps(res.metrics, ensure_ascii=False))
    print("Assumptions:")
    for a in res.assumptions:
        print(f"  - {a}")
    return 0 if res.final_status == "completed" else 1


def cmd_verify(args: argparse.Namespace) -> int:
    import importlib.util
    script = Path(__file__).resolve().parent.parent / "verification" / "run_verification.py"
    if not script.exists():
        sys.stderr.write("verification harness not found (run from a source checkout)\n")
        return 2
    spec = importlib.util.spec_from_file_location("tehai_verification", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.main()


def cmd_teams(args: argparse.Namespace) -> int:
    from .teams import TeamRegistry
    for t in TeamRegistry.load().all():
        print(f"{t.team_id:<22} {t.team_name}")
        print(f"  agents : {t.internal_agents}")
        print(f"  allowed: {t.allowed_task_types}   forbidden: {t.forbidden_task_types}")
        print(f"  phases : {' -> '.join(p['key'] for p in t.phases)}")
    return 0


def cmd_agents(args: argparse.Namespace) -> int:
    reg = AgentRegistry.load()
    for t in sorted(reg.all(), key=lambda x: x.agent_template_id):
        allowed = ",".join(t.allowed_actions)
        print(f"{t.agent_template_id:<22} tier={t.recommended_model_tier.value:<6} "
              f"types={t.handles_task_types}")
        print(f"  allowed: {allowed}")
    return 0


def cmd_orgs(args: argparse.Namespace) -> int:
    for tid, org in CATALOG.items():
        print(f"{tid:<20} {org.name}")
        print(f"  phases: {' -> '.join(p.key for p in org.phases)}")
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    logger = ExecutionLogger(args.ledger)
    records = logger.read()
    store = EvaluationStore()
    metrics = store.compute(records)
    suggestions = store.suggestions(metrics)
    print(json.dumps({
        "metrics": metrics.to_dict(),
        "suggestions": suggestions,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_calibrate(args: argparse.Namespace) -> int:
    records = ExecutionLogger(args.ledger).read()
    cal = EvaluationStore().calibrate(records)
    out = json.dumps(cal.to_dict(), ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
        sys.stderr.write(f"[tehai] calibration proposal -> {args.out} (review before adopting)\n")
    if args.apply:
        adopt = {"router_small_max": cal.proposed["router_small_max"],
                 "router_medium_max": cal.proposed["router_medium_max"]}
        Path(args.apply).write_text(json.dumps(adopt, ensure_ascii=False, indent=2), encoding="utf-8")
        sys.stderr.write(f"[tehai] adoptable config -> {args.apply} "
                         f"(review, then pass it via --config to plan/run)\n")
    print(out)
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    from .benchmark import propose_routing_table, run_bench

    cfg = load_config(args.config)
    names = [n.strip() for n in args.backends.split(",") if n.strip()]
    backends: dict = {}
    unavailable: list[str] = []
    for n in names:
        try:
            if n == "ensemble":          # the model-combination loop, as one backend
                be = ensemble_from_config(args.config)
            elif n == "gama":            # the vendor router, as one backend
                be = gama_from_config(args.config)
            else:
                be = get_backend(n, **cfg["backends"].get(n, {}))
        except Exception as e:  # unknown name / bad kwargs — skip, don't abort the sweep
            sys.stderr.write(f"[tehai] skip backend {n!r}: {e}\n")
            continue
        backends[n] = be
        if not getattr(be, "available", False):
            unavailable.append(n)
    if not backends:
        sys.stderr.write("[tehai] no usable backends to benchmark\n")
        return 2
    if unavailable:
        sys.stderr.write(f"[tehai] WARNING: unavailable backends will score 0: {unavailable}\n")
    sys.stderr.write("[tehai] NOTE: code cases EXECUTE model-generated Python in-process "
                     "(opt-in, like --sandbox). Only run on trusted backends.\n")

    logger = ExecutionLogger(args.out) if args.out else None
    records = run_bench(
        backends, tier=ModelTier(args.tier), repeats=args.repeats,
        limit_per_class=args.limit_per_class, unit_cost=cfg.get("unit_cost") or None,
        logger=logger, run_id=args.run_id or "bench",
    )
    proposal = propose_routing_table(records)
    print(json.dumps(proposal, ensure_ascii=False, indent=2))
    if args.out:
        sys.stderr.write(f"[tehai] bench ledger -> {args.out}\n")
    if args.propose:
        Path(args.propose).write_text(
            json.dumps({"routing_table": proposal["routing_table"]}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        sys.stderr.write(f"[tehai] routing_table proposal -> {args.propose} "
                         f"(review, merge into a --config file, then run --backend gama)\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tehai", description="手配 — controlled delegation layer")
    p.add_argument("--version", action="version", version=f"tehai {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    pp = sub.add_parser("plan", help="plan a request into a contract-bound task DAG")
    pp.add_argument("request", help="the user request, in quotes")
    pp.add_argument("--json", action="store_true", help="emit the RunPlan as JSON")
    pp.add_argument("--save-log", action="store_true", help="write a sample execution log")
    pp.add_argument("--run-id", default=None, help="override the derived run id")
    pp.add_argument("--runs-dir", default="runs", help="directory for sample logs")
    pp.add_argument("--backend", default="null", choices=BACKEND_CHOICES_GAMA,
                    help="model backend for LLM-backed decomposition (default: null = deterministic). "
                         "'gama' = config-driven multi-vendor router")
    pp.add_argument("--ollama-host", default=None, help="override Ollama host URL (WSL2: http://172.24.224.1:11434)")
    pp.add_argument("--config", default=None, help="adopt calibration knobs from a JSON config (router thresholds)")
    pp.set_defaults(func=cmd_plan)

    prun = sub.add_parser("run", help="plan, then generate artifacts + review + judge each task")
    prun.add_argument("request", help="the user request, in quotes")
    prun.add_argument("--backend", default="null", choices=BACKEND_CHOICES_GAMA,
                      help="model backend for generation+review (default: null = stub+heuristic). "
                           "'gama' = config-driven multi-vendor router")
    prun.add_argument("--ollama-host", default=None, help="override Ollama host URL")
    prun.add_argument("--limit", type=int, default=3, help="max tasks to execute (bounds backend calls)")
    prun.add_argument("--sandbox", action="store_true",
                      help="ACTUALLY compile/run generated code in an isolated sandbox "
                           "(opt-in; grounds the auto-check lens). Risky: executes model output.")
    prun.add_argument("--sandbox-strict", action="store_true",
                      help="like --sandbox but REQUIRE OS isolation; refuse to run code unconfined")
    prun.add_argument("--out", default=None, help="directory to write generated artifacts")
    prun.add_argument("--save-log", action="store_true", help="write the execution ledger")
    prun.add_argument("--runs-dir", default="runs", help="directory for the ledger")
    prun.add_argument("--run-id", default=None, help="override the derived run id")
    prun.add_argument("--config", default=None, help="adopt calibration knobs from a JSON config (router thresholds)")
    prun.set_defaults(func=cmd_run)

    pr = sub.add_parser("review", help="plan a request then run reviews + judge over its tasks")
    pr.add_argument("request", help="the user request, in quotes")
    pr.add_argument("--backend", default="null", choices=BACKEND_CHOICES_GAMA,
                    help="model backend for the reviewers (default: null = heuristic)")
    pr.add_argument("--ollama-host", default=None, help="override Ollama host URL")
    pr.add_argument("--artifact", default=None, help="file to review against the first task")
    pr.add_argument("--limit", type=int, default=3, help="max tasks to review (bounds LLM calls)")
    pr.add_argument("--run-id", default=None, help="override the derived run id")
    pr.add_argument("--config", default=None, help="config (router thresholds + multi routing_table)")
    pr.set_defaults(func=cmd_review)

    pm = sub.add_parser("meta", help="run the Multi-Team AgentOps loop on a product goal")
    pm.add_argument("goal", help="the product goal, in quotes")
    pm.add_argument("--backend", default="null", choices=BACKEND_CHOICES,
                    help="model backend for the underlying tehai pipeline")
    pm.add_argument("--sandbox", action="store_true", help="run generated code in the sandbox")
    pm.add_argument("--autonomy", default="supervised",
                    choices=["manual", "supervised", "autonomous_low_risk",
                             "autonomous_with_budget", "fully_blocked_for_high_risk"])
    pm.add_argument("--json", action="store_true", help="emit the MetaRunResult as JSON")
    pm.set_defaults(func=cmd_meta)

    pt = sub.add_parser("teams", help="list registered team templates")
    pt.set_defaults(func=cmd_teams)

    pv = sub.add_parser("verify", help="run the Multi-Team verification scenarios + write verification_report.md")
    pv.set_defaults(func=cmd_verify)

    pa = sub.add_parser("agents", help="list registered agent templates")
    pa.set_defaults(func=cmd_agents)

    po = sub.add_parser("orgs", help="list organization templates")
    po.set_defaults(func=cmd_orgs)

    pe = sub.add_parser("evaluate", help="aggregate metrics + improvement proposals from a ledger")
    pe.add_argument("ledger", help="path to a .jsonl execution ledger")
    pe.set_defaults(func=cmd_evaluate)

    pcal = sub.add_parser("calibrate",
                          help="propose concrete config tuning from a ledger (proposal only, never applied)")
    pcal.add_argument("ledger", help="path to a .jsonl execution ledger")
    pcal.add_argument("--out", default=None, help="write the full proposal JSON for human review")
    pcal.add_argument("--apply", default=None, help="write an adoptable config (router thresholds) to a file; use via --config")
    pcal.set_defaults(func=cmd_calibrate)

    pb = sub.add_parser("bench",
                        help="benchmark backends per task-class (deterministic) and propose a routing_table")
    pb.add_argument("--backends", default="echo",
                    help="comma-separated backend names, e.g. claude-tui,codex,ollama. "
                         "Use 'echo' for a free deterministic smoke test.")
    pb.add_argument("--tier", default="large", choices=["small", "medium", "large"],
                    help="model tier each backend uses for the bench (default: large)")
    pb.add_argument("--repeats", type=int, default=1, help="repeats per case")
    pb.add_argument("--limit-per-class", type=int, default=None,
                    help="cap cases per task-class (keep small to respect rate limits)")
    pb.add_argument("--out", default=None, help="write a LogRecord JSONL bench ledger")
    pb.add_argument("--propose", default=None, help="write the proposed routing_table JSON for review")
    pb.add_argument("--run-id", default=None, help="override the bench run id")
    pb.add_argument("--config", default=None,
                    help="config providing per-backend kwargs (host/model_by_tier) + unit_cost")
    pb.set_defaults(func=cmd_bench)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
