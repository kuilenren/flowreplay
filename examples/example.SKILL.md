---
name: Search Hacker News
description: web-action on news.ycombinator.com
format: web-flow/0.1
domain: news.ycombinator.com
capability: form-submit
variables:
- name: query
  label: Search term
  required: true
---

# Search Hacker News

Search Hacker News for a term and open the search results. `{{query}}` is filled
in at replay time — record once, replay for any term.

## Parameters

- `{{query}}` — Search term · required

## Steps

1. Open https://news.ycombinator.com
2. Click «Search» (the footer search link)
3. Fill «Search stories…» with {{query}}
4. Press Enter

## flow (machine-readable, for replay import)

```web-flow
{
  "name": "Search Hacker News",
  "description": "web-action on news.ycombinator.com",
  "start_url": "https://news.ycombinator.com",
  "variables": [
    { "name": "query", "label": "Search term", "required": true }
  ],
  "steps": [
    {
      "action_type": "navigate",
      "description": "Open https://news.ycombinator.com",
      "locators": [],
      "value_template": null,
      "url_template": "https://news.ycombinator.com",
      "options": { "wait_until": "domcontentloaded" },
      "viewport": { "width": 1280, "height": 800 }
    },
    {
      "action_type": "click",
      "description": "Click Search",
      "locators": [
        { "kind": "role", "value": "link", "name": "Search" },
        { "kind": "text", "value": "Search" },
        { "kind": "coordinate", "x": 360, "y": 620 }
      ],
      "value_template": null,
      "url_template": null,
      "options": {},
      "viewport": { "width": 1280, "height": 800 }
    },
    {
      "action_type": "fill",
      "description": "Fill Search stories",
      "locators": [
        { "kind": "css", "value": "input[name=\"q\"]" },
        { "kind": "role", "value": "textbox", "name": "Search stories…" },
        { "kind": "coordinate", "x": 480, "y": 60 }
      ],
      "value_template": "{{query}}",
      "url_template": null,
      "options": { "field_type": "text" },
      "viewport": { "width": 1280, "height": 800 }
    },
    {
      "action_type": "press_keys",
      "description": "Press Enter",
      "locators": [],
      "value_template": null,
      "url_template": null,
      "options": { "keys": ["Enter"] },
      "viewport": { "width": 1280, "height": 800 }
    }
  ]
}
```
