# FlowReplay

**Record a browser flow once — keep it as a portable, self-healing `SKILL.md` that agents replay deterministically, with no tokens burned per step.**

> 录一次浏览器操作 → 存成便携、自愈的 `SKILL.md`；智能体确定性回放，逐步零 token。

FlowReplay captures a human demonstration in a real browser and compiles it into a
small, human-readable **flow file**: Markdown you can review in a pull request, with
a machine-readable block for exact replay. Every recorded action carries a **ladder
of locators** (CSS → ARIA role → text → XPath → coordinate), so replay survives the
DOM shifting under it — and the *winning* locator is promoted and written back, so
the file gets more robust the more it runs.

> ⚠️ **Status: `v0.1` / alpha.** Ships the **format spec**, the **reference
> recorder**, lossless **SKILL.md round-trip**, static **robustness grading**, and
> the deterministic **replay engine** with **self-healing write-back** (M0 + M1).
> Next up is M2 (see [Roadmap](#roadmap)).

---

## Why not just use codegen / an LLM agent?

|  | Playwright codegen | LLM browser agent (browser-use, etc.) | **FlowReplay** |
|--|--|--|--|
| Output | imperative **script** | none (drives live each run) | **data** — a portable `SKILL.md` |
| Per-run cost | free | **tokens every step** | **free** (LLM optional, off by default) |
| Determinism | yes | no | **yes** |
| Survives DOM change | ❌ single selector | ⚠️ re-reasons (slow/§) | ✅ **locator ladder + self-heal** |
| Parameterized | ❌ | prompt-shaped | ✅ `{{variables}}` |
| Reviewable in a PR | hard | n/a | ✅ Markdown |
| Plugs into agent skills | ❌ | ❌ | ✅ `SKILL.md`-native |

FlowReplay's wedge: **record → replay as data, not a script**; **multi-strategy
self-healing** that persists back into the file; **`SKILL.md` / agent-skills-native**
packaging; and **LLM-optional** (the core has zero LLM dependency).

## Install

```bash
pip install flowreplay          # core: parse / distill / lint / round-trip
pip install "flowreplay[record]"  # + Playwright, to record in a real browser
python -m playwright install chromium
```

## Quickstart

```bash
# Record: opens a browser, you drive it, Ctrl-C to stop and save.
flowreplay record https://example.com -o example.SKILL.md

# Inspect what was captured, with a robustness grade.
flowreplay lint example.SKILL.md

# Round-trip check (parse the machine-readable block back to a flow).
flowreplay export example.SKILL.md --json

# Replay deterministically — supply variables; the file self-heals as it runs.
flowreplay replay example.SKILL.md --var query="hello world"
```

A recorded flow is just a file — commit it, diff it, review it:

```markdown
---
name: Search example.com
description: web-action on example.com
format: web-flow/0.1
domain: example.com
variables:
  - {name: query, label: Search term, required: true}
---

# Search example.com

## Steps
1. Open https://example.com
2. Fill «Search» with {{query}}
3. Click «Search»

## flow (machine-readable, for replay import)
```web-flow
{ "name": "...", "start_url": "...", "steps": [ ... ] }
```
```

See [`examples/example.SKILL.md`](examples/example.SKILL.md) for a full file and
[`SPEC.md`](SPEC.md) for the format.

## How it works

```
record(url)  ──►  inject recorder.js  ──►  you click/type/scroll  ──►
    compile events → steps (each with a locator ladder)  ──►  write  xxx.SKILL.md

replay(xxx.SKILL.md, vars)   ──►  for each step, try locators in order
    (css → role → text → xpath → coordinate) → on success, promote + write back
```

- **`flowreplay/recorder.py`** — the injected capture script (`RECORDER_JS`) plus
  `compile_events_to_steps()`, which demotes framework-volatile CSS (Element-Plus
  `#el-id-…`, React/Ant `:r0:`, hashed suffixes, `nth-of-type` chains) *below* the
  semantic role/text locators.
- **`flowreplay/skillmd.py`** — `flow_to_skill_md()` / `parse_skill_md()` (lossless
  round-trip) and `distill_flow()` (deterministic summary; LLM-optional).
- **`flowreplay/quality.py`** — static robustness grading, no execution needed.

## Roadmap

- **M0 ✓:** format spec, reference recorder, SKILL.md round-trip, robustness lint.
- **M1 ✓:** deterministic replay engine (slim Playwright executor, ~15 actions),
  self-healing locator write-back, `{{variable}}` substitution, `flowreplay replay`.
- **M2 (in progress):** ✓ extract actions (text / tables / structured), ✓ richer
  quality report + `lint --json`, ✓ golden-replay CI job. Remaining: monitor actions,
  `1.0` on PyPI.
- **M3 (optional):** Agent-Skills alignment, an MCP `flow.replay` tool, an optional
  bring-your-own-VLM visual fallback (off by default), governance layer.

## Non-goals

FlowReplay replays a **human demonstration**. It does **not** include — and will not
accept contributions of — anti-bot-detection, fingerprint spoofing, CAPTCHA solving,
or proxy/geo cloaking. Automate only sites you are authorized to automate, and
respect their terms of service. See [`SPEC.md`](SPEC.md) §Non-goals.

## Compatibility

The reference parser reads the canonical ` ```web-flow ` block and also accepts a
` ```boks-flow ` block, so files exported by the boksclaw platform import losslessly.

## License

[Apache-2.0](LICENSE). Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).
