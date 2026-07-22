# The `web-flow` format — Specification v0.1

Status: **Draft / alpha.** This document defines the on-disk format that FlowReplay
records and (from milestone M1) replays. The format is deliberately small and
language-agnostic: any recorder or player in any language can produce and consume it.

The key words MUST, SHOULD, and MAY are used as in RFC 2119.

---

## 1. Overview

A **flow** is a recorded sequence of browser interactions — a human demonstration —
stored as a single Markdown file conventionally named `*.SKILL.md`. The file has
three parts:

1. **YAML front-matter** — identity and the parameter contract.
2. **A human-readable step list** — reviewable prose, one line per step.
3. **A fenced `web-flow` block** — the machine-readable, lossless representation used
   for exact replay.

The prose is for humans (and pull-request review); the fenced block is authoritative
for replay. A file MAY omit the fenced block (see §8, *foreign import*), in which case
a player derives steps best-effort from the prose.

## 2. File structure

````markdown
---
name: Search Hacker News
description: web-action on news.ycombinator.com
format: web-flow/0.1
domain: news.ycombinator.com
variables:
  - {name: query, label: Search term, required: true}
---

# Search Hacker News

Optional free-text description.

## Parameters
- `{{query}}` — Search term · required

## Steps
1. Open https://news.ycombinator.com
2. Click «Search»
3. Fill «Search stories…» with {{query}}
4. Press Enter

## flow (machine-readable, for replay import)
```web-flow
{ "name": "...", "start_url": "...", "variables": [...], "steps": [...] }
```
````

## 3. Front-matter fields

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `name` | string | yes | Human name of the flow. |
| `description` | string | no | One line; what the flow does. |
| `format` | string | SHOULD | Format id + version, e.g. `web-flow/0.1`. |
| `domain` | string | no | Primary host the flow operates on. |
| `capability` | string | no | Coarse label: `navigate`, `form-submit`, `extract`, `web-action`. |
| `variables` | array | no | The parameter contract (§6). Mirrors `variables` in the block. |

## 4. The `web-flow` block

A single fenced code block with info-string `web-flow` containing one JSON object:

```jsonc
{
  "name": "string",
  "description": "string | null",
  "start_url": "string | null",     // where replay begins
  "variables": [ /* Variable */ ],
  "steps": [ /* Step */ ]
}
```

A conforming reader MUST parse the first ` ```web-flow ` block. A reader SHOULD also
accept a ` ```boks-flow ` block as a compatibility alias (§8).

### 4.1 Variable

```jsonc
{
  "name": "query",          // referenced in templates as {{query}}
  "label": "Search term",   // human label (optional)
  "required": true,          // optional, default false
  "default": "…",           // optional; used when no value is supplied
  "secret": false            // optional; if true, MUST NOT carry a default,
                             // MUST be supplied at replay, MUST be redacted in logs
}
```

### 4.2 Step

```jsonc
{
  "action_type": "click",            // see §5
  "description": "Click Search",     // human label
  "locators": [ /* Locator */ ],     // ordered fallback ladder (§5.2)
  "value_template": "{{query}}",     // for fill/select/upload; supports {{vars}}
  "url_template": "https://…",       // for navigate; supports {{vars}}
  "options": { },                    // action-specific (§5.1)
  "viewport": { "width": 1280, "height": 800 }  // record-time viewport, for
                                                 // scaling coordinate locators
}
```

Absent fields MAY be `null` or omitted; a serializer MAY normalize them to the full
key set (FlowReplay does), and that normalization MUST NOT change meaning.

## 5. Actions

### 5.1 Core action types

A conforming player SHOULD support the core set. The reference recorder emits the
**Recorded** actions; the others are producible by hand or by other tools.

| `action_type` | Recorded | Uses | Notes |
|---------------|:--:|------|-------|
| `navigate` | ✓ | `url_template`, `options.wait_until` | Emitted on every URL change. |
| `click` | ✓ | `locators` | |
| `fill` | ✓ | `locators`, `value_template` | Text inputs / textareas. |
| `select` | ✓ | `locators`, `value_template` | `<select>` option. |
| `hover` | ✓ | `locators` | Reveals menus/popovers. |
| `press_keys` | ✓ | `options.keys` | e.g. `["Control","Enter"]`. |
| `scroll` | ✓ | `locators?`, `options.dx/dy` | |
| `drag` | ✓ | `options.from_x/from_y/to_x/to_y` | Pointer drag. |
| `upload` | ✓ | `locators`, `options.file_*` | File input; path supplied at replay. |
| `download` | ✓ | `locators?`, `options.url/save_as` | Genuine download trigger. |
| `handle_dialog` | ✓ | `options.action/dialog_type/...` | alert/confirm/prompt. |
| `back` / `forward` / `reload` | ✓ | — | History / navigation. |
| `coordinate_click` | | `options.x/y` | Pure visual click. |
| `wait` | | `options` | Wait for a condition. |
| `scroll_to_text` | | `options.text` | |
| `new_tab` / `switch_tab` / `close_tab` | | `options` | Tab control. |
| `extract_text` / `extract_tables` / `extract_structured` | | `locators?`, `options` | *Reserved for M2.* |
| `monitor_start` / `monitor_poll` / `monitor_stop` | | `options` | *Reserved.* |
| `search` / `screenshot` | | `options` | *Reserved.* |

Unknown `action_type` values MUST be treated as a hard error by a player (not
silently skipped), unless the caller opts into lenient mode.

### 5.2 Locators — the fallback ladder

A step's `locators` is an **ordered** list. On replay a player MUST try them in order
and use the first that resolves to exactly one actionable element.

| `kind` | Fields | Resolves to |
|--------|--------|-------------|
| `css` | `value` | CSS selector. |
| `role` | `value` (ARIA role), `name` (accessible name) | Role + name. |
| `text` | `value` | Visible text. |
| `xpath` | `value` | XPath. |
| `coordinate` | `x`, `y` | Absolute viewport point (scale by `viewport`). |
| `vision` | `value`/`name` (target description) | Visual grounding via a VLM. *Optional; requires an explicitly configured model; off by default.* |

Any locator MAY additionally carry frame metadata for same-origin iframes:
`frame_selector`, `frame`, `frame_url`, and `frame_path` (an array from the top
document down to the target frame).

**Ordering rule (normative).** A recorder SHOULD place the most durable locator
first. A CSS selector whose specificity comes from a value regenerated on every
component mount — framework auto-ids (Element-Plus `#el-id-<n>-<n>`, React/Ant
`:r0:`), hashed suffixes, or positional `nth-of-type`/`nth-child` chains — is
**volatile** and MUST be ordered *below* any `role`/`text` locator for the same step.
This is why the ladder exists: the durable semantic locator is tried before a brittle
id burns an actionability timeout.

### 5.3 Self-healing (normative for players that persist)

When a locator other than the first one in the ladder succeeds, a player MAY
**promote** it to the front of that step's `locators` and write the updated flow back
to disk. Over repeated runs the file converges on the locators that actually work —
the recording gets more robust the more it is replayed. Promotion MUST preserve every
original locator (as lower-priority fallbacks); it MUST NOT delete locators.

## 6. Variables and templating

`value_template` and `url_template` MAY contain `{{name}}` placeholders. Before a step
runs, a player MUST substitute each `{{name}}` with the supplied value for that
variable, or the variable's `default` if none is supplied. A `required` variable with
no value and no default MUST cause replay to fail before that step executes.

**Secrets.** A variable with `"secret": true` MUST NOT have a `default`, MUST be
supplied at replay time, and MUST be redacted from any logs, run records, or error
evidence. Recorders MUST NOT write captured secret values into the flow file.

## 7. Coordinates and viewport

Coordinate locators are absolute points in the record-time viewport, whose size is in
the step's `viewport`. A player replaying at a different viewport SHOULD scale
coordinates proportionally. Coordinates are a last resort — a flow that relies on them
(no durable css/role/text) will be graded *fragile* (see the reference `quality`
grader).

## 8. Compatibility

- **`boks-flow` alias.** Files exported by the boksclaw platform use a ` ```boks-flow `
  fence. A conforming reader SHOULD accept it identically to `web-flow`, so those files
  import losslessly.
- **Foreign SKILL.md.** A file with no `web-flow`/`boks-flow` block MAY still be
  imported best-effort: a reader parses the numbered prose steps into `navigate` /
  `click` / `fill` steps carrying `text` locators. Such a flow is marked
  `imported_foreign` and is expected to lean on the `vision` fallback when text
  locators miss.

## 9. Non-goals (normative exclusion)

The `web-flow` format describes a **human demonstration** of a web task. It is out of
scope, and a conforming implementation MUST NOT bundle, the following:

- anti-bot-detection or browser-fingerprint spoofing,
- CAPTCHA detection or solving, or integration with CAPTCHA-solving services,
- proxy-egress / geolocation cloaking, or any evasion of a site's terms of service.

Automate only what you are authorized to automate.

## 10. Versioning

The `format` field carries `web-flow/MAJOR.MINOR`. A MINOR bump is backward-compatible
(new optional fields, new `action_type`s). A MAJOR bump MAY change existing semantics
and MUST be accompanied by migration notes. Readers MUST ignore unknown front-matter
fields and unknown keys inside a Step/Locator object.

## 11. Conformance

- A **conforming recorder** produces a file per §2–§7, ordering locators per §5.2, and
  never writes secret values (§6).
- A **conforming player** substitutes variables (§6), tries locators in ladder order
  (§5.2), errors on unknown `action_type` (§5.1), and honors the non-goals (§9).
- Neither is required to implement the reserved actions in §5.1.
