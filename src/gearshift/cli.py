"""gearshift CLI.

    gearshift route "Find every caller of parse_config()"
    gearshift route --json --ceiling opus "Plan the Postgres migration"
    gearshift session "Refactor the billing module"
    gearshift ladder sonnet medium
    gearshift cost --model opus --input 120000 --output 8000 --cache-read 900000
    gearshift report --days 7
    gearshift bench examples/bench_tasks.jsonl --simulate
    gearshift eval tests/cases_holdout4.jsonl
    gearshift doctor
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from typing import List, Optional

from . import __version__
from .policy import EFFORTS, TIERS, load_policy


def _read_task(args: argparse.Namespace) -> str:
    text = " ".join(args.task).strip()
    if text == "-" or (not text and not sys.stdin.isatty()):
        text = sys.stdin.read().strip()
    if not text:
        raise SystemExit("gearshift: give the task as an argument, or pipe it on stdin")
    return text


def _router_for(args: argparse.Namespace):
    from .router import Router
    kind = getattr(args, "judge", None)
    if not kind or kind == "off":
        return Router()
    from .judge import judge_from_config
    judge = judge_from_config(kind)
    if judge is None:
        raise SystemExit("gearshift: judge %r is not available (api needs GEARSHIFT_API_KEY or ANTHROPIC_API_KEY, "
                         "claude needs the claude CLI on PATH)" % kind)
    return Router(judge=judge, judge_mode="always" if getattr(args, "judge_always", False) else "unsure")


def cmd_route(args: argparse.Namespace) -> int:
    d = _router_for(args).route(
        _read_task(args), description=args.description, agent_type=args.agent_type,
        platform=args.platform, floor=args.floor, ceiling=args.ceiling,
        available=args.available.split(",") if args.available else None,
        prefer=args.prefer, task_class=args.task_class,
    )
    if args.json:
        print(json.dumps(d.to_dict(), indent=2))
    elif args.quiet:
        print("%s %s" % (d.tier if args.platform == "claude-code" else d.model, d.effort))
    else:
        print(d.explain())
    return 0


def cmd_session(args: argparse.Namespace) -> int:
    from .router import Router
    adv = Router().advise_session(_read_task(args), current_tier=args.current)
    if args.json:
        print(json.dumps(adv, indent=2))
        return 0
    print("start the session on %s at effort %s" % (adv["tier"], adv["effort"]))
    print("  reads as    %s work (confidence %.2f)" % (adv["task_class"], adv["confidence"]))
    print("  then        " + adv["note"])
    if adv["mismatch"] == "too_small":
        print("  mismatch    %s is small for this; switch before you start, not midway" % args.current)
    return 0


def cmd_ladder(args: argparse.Namespace) -> int:
    from .router import Router
    rungs = Router().ladder(args.tier, args.effort, ceiling=args.ceiling)
    print("%s@%s" % (args.tier, args.effort) + "".join("  ->  %s@%s" % r for r in rungs))
    return 0


def cmd_cost(args: argparse.Namespace) -> int:
    from .pricing import cost_usd, resolve_model
    key = resolve_model(args.model)
    if key is None:
        print("gearshift: no price on file for %r" % args.model, file=sys.stderr)
        return 1
    usage = {"input_tokens": args.input, "output_tokens": args.output,
             "cache_read_input_tokens": args.cache_read,
             "cache_creation": {"ephemeral_5m_input_tokens": args.cache_write_5m,
                                "ephemeral_1h_input_tokens": args.cache_write_1h}}
    c = cost_usd(usage, key, batch=args.batch)
    print("%s  $%.6f%s" % (key, c, "  (batch)" if args.batch else ""))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from .config import state_dir
    from .ledger import build_report, format_report
    rep = build_report(state_dir(), days=None if args.all else args.days, session=args.session)
    print(json.dumps(rep, indent=2) if args.json else format_report(rep))
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from .evalset import evaluate
    r = evaluate(args.path, _router_for(args))
    if args.json:
        print(json.dumps(r, indent=2))
        return 0
    print("cases %d   tier accuracy %.1f%%   under-routed %d   over-routed %d" % (
        r["cases"], 100 * r["tier_accuracy"], r["under_routed"], r["over_routed"]))
    print("confident on %d of %d (the hook acts only on these): %d under-routed, %d over-routed" % (
        r["confident"], r["cases"], r["confident_under_routed"], r["confident_over_routed"]))
    for m in r["misses"]:
        print("  %s want %-22s got %-24s %s" % ("TIER " if m["tier_miss"] else "     ",
                                              m["want"], m["got"], m["task"]))
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    from .bench import format_table, run_bench
    try:
        result = run_bench(args.tasks, configs=args.configs.split(","), simulate=args.simulate,
                           limit=args.limit, max_workers=args.workers)
    except RuntimeError as exc:
        print("gearshift: %s" % exc, file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2) if args.json else format_table(result))
    return 0


def cmd_policy(args: argparse.Namespace) -> int:
    pol = load_policy()
    if args.json:
        print(json.dumps(pol.data, indent=2))
        return 0
    print("policy %s (as of %s)\n" % (pol.version, pol.data.get("as_of", "?")))
    print("%-9s %-7s %-7s %s" % ("class", "tier", "effort", "what"))
    for name, c in pol.classes.items():
        print("%-9s %-7s %-7s %s" % (name, c["tier"], c["effort"], c["what"]))
    print("\nladder  " + "  ->  ".join("%s@%s" % (t, e) for t, e in pol.ladder))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from .config import load_config, state_dir
    from .router import Router
    ok = True

    def line(good: bool, text: str) -> None:
        nonlocal ok
        ok = ok and good
        print("  [%s] %s" % ("ok" if good else "!!", text))

    print("gearshift %s" % __version__)
    line(sys.version_info >= (3, 9), "python %s" % sys.version.split()[0])
    try:
        pol = load_policy()
        line(True, "policy %s loads (%d classes, %d signals)" % (pol.version, len(pol.classes), len(pol.signals)))
    except Exception as exc:
        line(False, "policy failed to load: %s" % exc)
        return 1
    d = Router(pol).route("Find every caller of parse_config() and list the file paths")
    line(d.tier == "haiku", "self-test route: lookup -> %s@%s" % (d.tier, d.effort))
    sd = state_dir()
    try:
        sd.mkdir(parents=True, exist_ok=True)
        probe = sd / ".probe"
        probe.write_text("x")
        probe.unlink()
        line(True, "state directory writable: %s" % sd)
    except OSError as exc:
        line(False, "state directory not writable: %s (%s)" % (sd, exc))
    cfg = load_config(os.getcwd())
    jk = cfg["judge"]
    if jk == "auto":
        has_key = os.environ.get("GEARSHIFT_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
        jk = "auto (api key found: on)" if has_key else "auto (no api key: off)"
    print("  judge: %s" % jk)
    print("  config: mode=%s ceiling=%s set_effort=%s announce=%s advise=%s%s" % (
        cfg["mode"], cfg["ceiling"], cfg["set_effort"], cfg["announce"], cfg["advise"],
        " project_cap=%s" % cfg["project_cap"] if cfg.get("project_cap") else ""))
    claude = shutil.which("claude")
    if claude:
        try:
            out = subprocess.run([claude, "--version"], capture_output=True, text=True, timeout=15).stdout.strip()
            from .claude_code import EFFORT_PARAM_MIN_VERSION, _parse_version
            v = _parse_version(out)
            line(v is not None and v >= (2, 1, 251), "Claude Code %s (model-switch hooks need 2.1.251+)" % out)
            if v is not None and v < EFFORT_PARAM_MIN_VERSION:
                print("  note: per-call effort needs 2.1.292+; on this version gearshift sets the model only")
        except (OSError, subprocess.SubprocessError):
            print("  note: could not run `claude --version`")
    else:
        print("  note: `claude` not on PATH (fine for API-only use)")
    for var in ("CLAUDE_CODE_EFFORT_LEVEL", "CLAUDE_CODE_SUBAGENT_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL_FORCE"):
        if os.environ.get(var):
            print("  note: %s is set; Claude Code gives it precedence over per-subagent %s"
                  % (var, "effort" if "EFFORT" in var else "model routing in some cases"))
    print("all good" if ok else "problems found")
    return 0 if ok else 1


def cmd_hook(args: argparse.Namespace) -> int:
    from .claude_code import main as hook_main
    return hook_main([args.event])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gearshift", description="Model and effort routing for Claude.")
    p.add_argument("--version", action="version", version="gearshift " + __version__)
    sub = p.add_subparsers(dest="cmd")

    r = sub.add_parser("route", help="pick a model and effort for a task, with reasons")
    r.add_argument("task", nargs="*", help="the task text, or - for stdin")
    r.add_argument("--description", help="short summary of the task (counts 1.5x)")
    r.add_argument("--agent-type", help="Claude Code subagent type, e.g. Explore")
    r.add_argument("--platform", choices=["api", "claude-code"], default="api")
    r.add_argument("--class", dest="task_class", help="skip classification: " + ", ".join(
        ["lookup", "extract", "edit", "write", "review", "build", "design", "research", "frontier", "chat"]))
    r.add_argument("--floor", choices=TIERS)
    r.add_argument("--ceiling", choices=TIERS)
    r.add_argument("--available", help="comma-separated tiers this account can use")
    r.add_argument("--prefer", choices=["cost", "balanced", "quality"], default="balanced")
    r.add_argument("--json", action="store_true")
    r.add_argument("-q", "--quiet", action="store_true", help="print only: <model> <effort>")
    r.add_argument("--judge", choices=["off", "api", "claude"], help="ask a model when the keyword rules are unsure")
    r.add_argument("--judge-always", action="store_true", help="ask the judge for every task")
    r.set_defaults(fn=cmd_route)

    s = sub.add_parser("session", help="starting model and effort for a whole session")
    s.add_argument("task", nargs="*")
    s.add_argument("--current", choices=TIERS, help="tier the session is on now")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_session)

    la = sub.add_parser("ladder", help="where to go next when a result falls short")
    la.add_argument("tier", choices=TIERS)
    la.add_argument("effort", choices=EFFORTS)
    la.add_argument("--ceiling", choices=TIERS)
    la.set_defaults(fn=cmd_ladder)

    c = sub.add_parser("cost", help="list-price cost of one request")
    c.add_argument("--model", required=True)
    c.add_argument("--input", type=int, default=0)
    c.add_argument("--output", type=int, default=0)
    c.add_argument("--cache-read", type=int, default=0)
    c.add_argument("--cache-write-5m", type=int, default=0)
    c.add_argument("--cache-write-1h", type=int, default=0)
    c.add_argument("--batch", action="store_true")
    c.set_defaults(fn=cmd_cost)

    rp = sub.add_parser("report", help="what Claude Code subagents were routed where, and the cost")
    rp.add_argument("--days", type=float, default=7)
    rp.add_argument("--all", action="store_true")
    rp.add_argument("--session")
    rp.add_argument("--json", action="store_true")
    rp.set_defaults(fn=cmd_report)

    e = sub.add_parser("eval", help="score the router against a labelled JSONL file")
    e.add_argument("path")
    e.add_argument("--json", action="store_true")
    e.add_argument("--judge", choices=["off", "api", "claude"], help="score with the judge switched on (real model calls)")
    e.add_argument("--judge-always", action="store_true")
    e.set_defaults(fn=cmd_eval)

    b = sub.add_parser("bench", help="compare configurations on your own tasks: cost per finished task")
    b.add_argument("tasks", help="JSONL of tasks with checks (see docs/BENCH.md)")
    b.add_argument("--configs", default="haiku@medium,sonnet@medium,opus@medium,router,cascade,cheap-first:opus")
    b.add_argument("--simulate", action="store_true", help="use the built-in toy model instead of the API")
    b.add_argument("--limit", type=int)
    b.add_argument("--workers", type=int, default=4)
    b.add_argument("--json", action="store_true")
    b.set_defaults(fn=cmd_bench)

    po = sub.add_parser("policy", help="print the routing table in effect")
    po.add_argument("--json", action="store_true")
    po.set_defaults(fn=cmd_policy)

    dr = sub.add_parser("doctor", help="check the install")
    dr.set_defaults(fn=cmd_doctor)

    h = sub.add_parser("hook", help="Claude Code hook entry point (reads the hook JSON on stdin)")
    h.add_argument("event")
    h.set_defaults(fn=cmd_hook)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "fn", None):
        parser.print_help()
        return 0
    try:
        return int(args.fn(args) or 0)
    except BrokenPipeError:      # e.g. `gearshift policy | head`
        try:
            sys.stdout.close()
        except OSError:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
