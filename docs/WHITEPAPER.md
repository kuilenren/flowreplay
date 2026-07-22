# Record once, replay forever: a governed, self-healing skill format for browser agents

> Design rationale for FlowReplay and the `web-flow` format. Living document — v0.1
> (milestone M0). Sections marked *(M1+)* describe where the design is headed.

## 1. Thesis

The reliable way to make an AI agent operate a website is not to have it reason about
the DOM on every run, and not to freeze a brittle script. It is to **record a human
doing the task once**, capture *multiple ways to find each element*, and replay that
demonstration **deterministically** — letting the recording **heal itself** when the
page shifts. The artifact should be a small, reviewable file that plugs into the
agent-skills ecosystem, not a service you have to host.

## 2. Why the two incumbents fall short

**Codegen (record → script).** Tools like Playwright's `codegen` emit an imperative
script with a *single* selector per action. The moment a class name, an auto-generated
id, or sibling order changes, the line breaks — and a human has to re-record or hand-
patch. There is no notion of "try another way to find this element."

**LLM-live agents (reason every run).** Browser-use, Stagehand, and similar drive the
page by asking a model what to do at each step. This is flexible but pays a tax every
single run: **tokens, latency, and nondeterminism**. The same task, run twice, can take
two different paths — which is exactly what you do *not* want for a scheduled report
export or a data pull that has to be auditable.

FlowReplay takes the determinism of a recording and adds the resilience the LLM
approach gets for free — without paying the per-run model tax.

## 3. Core idea

### 3.1 Record to *data*, not a script

A recorded flow is JSON embedded in Markdown (see [`SPEC.md`](../SPEC.md)). Because it
is data, it is: diffable in a pull request, parameterizable with `{{variables}}`,
transformable by tools, and replayable by any conforming player in any language. Because
it is Markdown with a `SKILL.md` name, it drops straight into the growing **agent
skills** convention.

### 3.2 The locator ladder

For every interaction, the recorder captures a *ladder* of ways to find the element:

```
css → role(+accessible name) → visible text → xpath → viewport coordinate → vision
```

On replay the player walks the ladder and uses the first locator that resolves. One
selector breaking no longer breaks the step — there are five more behind it, ending in
a visual coordinate and (optionally) VLM grounding.

### 3.3 Demote the volatile

Not all CSS is equal. `#el-id-3268-14` (Element-Plus), `:r0:` (React/Ant), a hashed
`…-a1b2c3d4` suffix, or a `div:nth-of-type(3) > span` chain are **regenerated or
position-fragile** — matching them on the next run is a coin flip. The recorder detects
these and orders them *below* the semantic role/text locators, so replay tries the
durable locator first instead of burning an actionability timeout on an id that can
never match again. (See `flowreplay/recorder.py`, `_css_is_volatile`.)

### 3.4 The file that learns *(M1)*

When a fallback locator wins, the player promotes it to the front of that step and
writes the flow back. The recording **converges on what works**: fragile recordings get
more robust every time they run, without a human touching them. Promotion never deletes
a locator — it only reorders — so nothing is lost.

## 4. Static robustness grading

You should not have to run a flow against a live site to learn it is fragile. The
`quality` grader scores each step by its *primary* locator: a stable css or a role/text
locator is durable; a step that resolves only to a viewport coordinate (or only to
volatile css) is fragile. A flow gets a grade — robust / moderate / fragile — and a
list of the risky steps, **before** its first live replay. `flowreplay lint` prints it.

## 5. From flow to *governed* skill *(M1+)*

A recording becomes an operational skill when an organization can trust it. The design
carries three governance ideas (present in the platform this format was extracted from,
and on the FlowReplay roadmap as optional layers):

- **Record → approve → replay.** A human records; the flow is reviewed like code; only
  then may an agent replay it. The `SKILL.md` diff *is* the review surface.
- **Success-rate tracking.** Each run appends a result; a flow accumulates a success
  rate, so a "recommended / needs-attention" signal is data, not a guess.
- **Side-effect classification.** Steps are classifiable as read/navigation vs.
  internal vs. external mutation (a *submit* / *pay* / *send* is external). A player can
  then refuse to re-fire an external side effect on a partial re-run unless explicitly
  told to. This is what makes replay safe to automate.

## 6. Resuming mid-flow: fork-replay *(future)*

The most fragile part of most flows is the login prefix. A checkpointing player can
snapshot authenticated state (cookies + storage, encrypted) after each successful step,
and later *resume from step N* with that state restored — replaying the useful tail
without re-running the brittle login every time. FlowReplay does not ship this yet; the
format leaves room for it (checkpoints are out-of-band, keyed by step).

## 7. Positioning

| | codegen | LLM-live agent | Selenium IDE | rrweb | **FlowReplay** |
|--|--|--|--|--|--|
| Output is data (not a script) | ✗ | n/a | partial | ✓ (for analytics) | ✓ |
| Deterministic replay | ✓ | ✗ | ✓ | n/a | ✓ |
| Multi-strategy self-healing | ✗ | ⚠ re-reasons | ✗ | n/a | ✓ |
| Parameterized | ✗ | prompt | limited | n/a | ✓ |
| LLM-optional | ✓ | ✗ | ✓ | ✓ | ✓ |
| Agent-skill-native (`SKILL.md`) | ✗ | ✗ | ✗ | ✗ | ✓ |

The gap FlowReplay fills: no existing open tool combines *record-to-data*,
*self-healing that persists into the file*, *`SKILL.md`-native packaging*, and
*LLM-optional determinism* in one artifact.

## 8. Roadmap

- **M0 (now):** format spec, reference recorder, round-trip, static grading, `lint`.
- **M1:** deterministic replay engine (a slim Playwright executor over ~15 actions),
  self-healing write-back, `{{variable}}` substitution.
- **M2:** `flowreplay replay` CLI, richer quality report, 1.0 on PyPI, golden-replay CI.
- **M3 (optional):** Agent-Skills alignment, an MCP `flow.replay` tool, a bring-your-own
  VLM visual-fallback plugin (off by default), and the governance layer of §5.

## 9. Non-goals

FlowReplay replays a *human demonstration*. Anti-bot-detection, fingerprint spoofing,
CAPTCHA solving, and proxy/geo cloaking are explicitly excluded — from the code and from
accepted contributions. See [`SPEC.md`](../SPEC.md) §9. Automate only what you are
authorized to automate, and respect each site's terms of service.

## 10. Provenance

The reference recorder and the SKILL.md distillation logic were extracted from the
browser subsystem of the boksclaw platform and released under Apache-2.0. The governance
and fork-replay ideas in §5–§6 describe that platform's production behavior and inform
the roadmap; they are not all present in this M0 release.
