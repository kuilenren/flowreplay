"""FlowReplay command-line interface.

    flowreplay record <url> [-o out.SKILL.md]   # record a flow in a real browser
    flowreplay lint <file.SKILL.md>              # summary + robustness grade
    flowreplay export <file.SKILL.md> [--json]   # round-trip the machine block
    flowreplay replay <file.SKILL.md> [--var k=v] # deterministic replay + self-heal
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import __version__
from .quality import assess_flow_quality
from .skillmd import distill_flow, flow_to_skill_md, parse_skill_md


def _print_quality(steps: list[Any]) -> None:
    q = assess_flow_quality(steps)
    icon = {"robust": "🟢", "moderate": "🟡", "fragile": "🔴"}[q["grade"]]
    print(f"  {icon} robustness: {q['grade']} (score {q['score']}, "
          f"{q['robust']}/{q['graded_steps']} steps on durable locators)")
    for fs in q["fragile_steps"]:
        print(f"     - step {fs['index']} [{fs['grade']}] {fs['action_type']}: {fs['description']}")


async def _run_record(args: argparse.Namespace) -> dict[str, Any]:
    import asyncio

    from .recorder import record_flow

    stop = asyncio.Event()
    task = asyncio.create_task(record_flow(
        args.url,
        headless=args.headless,
        stop_event=stop,
        name=args.name,
        browser_channel=args.channel,
    ))
    loop = asyncio.get_running_loop()
    print("● Recording. Perform your actions in the browser, then press Enter here "
          "to stop and save.", flush=True)
    try:
        await loop.run_in_executor(None, sys.stdin.readline)
    except KeyboardInterrupt:  # pragma: no cover
        pass
    stop.set()
    return await task


def _cmd_record(args: argparse.Namespace) -> int:
    import asyncio

    try:
        flow = asyncio.run(_run_record(args))
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    steps = flow.get("steps") or []
    if not steps:
        print("No actions captured — nothing to save.", file=sys.stderr)
        return 1
    md = flow_to_skill_md(flow, steps)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(md)
        print(f"✔ saved {len(steps)} steps → {args.output}")
    else:
        sys.stdout.write(md)
    _print_quality(steps)
    return 0


def _cmd_lint(args: argparse.Namespace) -> int:
    with open(args.file, encoding="utf-8") as fh:
        flow = parse_skill_md(fh.read())
    steps = flow.get("steps") or []
    d = distill_flow(flow, steps)
    print(f"{d.get('name') or flow.get('name')}  —  {d['capability']} on {d.get('domain') or 'web'}")
    print(f"  {d['step_count']} steps"
          + (f", params: {', '.join(d['params'])}" if d["params"] else "")
          + (f"  [imported from a foreign SKILL.md]" if flow.get("imported_foreign") else ""))
    _print_quality(steps)
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    with open(args.file, encoding="utf-8") as fh:
        flow = parse_skill_md(fh.read())
    if args.json:
        print(json.dumps(flow, ensure_ascii=False, indent=2))
    else:
        print(flow_to_skill_md(flow, flow.get("steps") or []))
    return 0


def _parse_vars(pairs: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in pairs or []:
        k, _, v = p.partition("=")
        out[k.strip()] = v
    return out


def _cmd_replay(args: argparse.Namespace) -> int:
    import asyncio

    from .player import ReplayError, replay_flow

    with open(args.file, encoding="utf-8") as fh:
        flow = parse_skill_md(fh.read())

    def _on_step(e: dict[str, Any]) -> None:
        mark = "✓" if e["success"] else "✗"
        extra = ""
        if e.get("healed"):
            extra = f"  (self-healed → locator #{e['locator_index']})"
        if not e["success"]:
            extra = f"  ERROR: {e.get('error')}"
        print(f"  {mark} step {e['seq']} {e['action']}{extra}")

    try:
        result = asyncio.run(replay_flow(
            flow,
            _parse_vars(args.var),
            headless=not args.headed,
            heal=not args.no_heal,
            source_path=(args.file if not args.no_heal else None),
            channel=args.channel,
            on_step=_on_step,
        ))
    except ReplayError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if result["success"]:
        tail = f" · self-healed, wrote back {args.file}" if result["healed"] and not args.no_heal else ""
        print(f"✅ replay succeeded{tail}")
        return 0
    print(f"❌ replay failed at step {result['failed_step']}", file=sys.stderr)
    return 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="flowreplay", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"flowreplay {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    rec = sub.add_parser("record", help="record a flow in a real browser")
    rec.add_argument("url", help="URL to open")
    rec.add_argument("-o", "--output", help="write the SKILL.md here (default: stdout)")
    rec.add_argument("--name", help="flow name")
    rec.add_argument("--headless", action="store_true", help="run the browser headless")
    rec.add_argument("--channel", help="Playwright browser channel, e.g. chrome")
    rec.set_defaults(func=_cmd_record)

    lint = sub.add_parser("lint", help="summarize a SKILL.md and grade robustness")
    lint.add_argument("file")
    lint.set_defaults(func=_cmd_lint)

    exp = sub.add_parser("export", help="round-trip the machine-readable flow block")
    exp.add_argument("file")
    exp.add_argument("--json", action="store_true", help="emit the flow as JSON")
    exp.set_defaults(func=_cmd_export)

    rep = sub.add_parser("replay", help="deterministically replay a flow in a real browser")
    rep.add_argument("file")
    rep.add_argument("--var", action="append", metavar="NAME=VALUE",
                     help="supply a variable value (repeatable)")
    rep.add_argument("--headed", action="store_true", help="show the browser (default: headless)")
    rep.add_argument("--channel", help="drive a system browser channel, e.g. chrome")
    rep.add_argument("--no-heal", action="store_true",
                     help="do not promote/persist winning locators")
    rep.set_defaults(func=_cmd_replay)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
