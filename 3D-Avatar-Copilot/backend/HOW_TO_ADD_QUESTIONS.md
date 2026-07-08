# How to add or change questions & answers (no coding needed)

All questions and answers live in one file: **`backend/scenes.json`**.
Open it in Notepad or VS Code, edit, save — then just **refresh the browser**.
No server restart, nothing else to touch.

## Turn a question on or off

Every block has `"enabled": true` or `"enabled": false`. Flip it. That's it.

## Add a new question & answer

Copy an existing block (from `{` to `},`), paste it before the closing `]`,
and change these fields:

```json
{
  "id": 7,
  "enabled": true,
  "topic": "Portfolio risk",
  "question": "How is his portfolio positioned?",
  "answer": "John's portfolio is balanced, with sixty percent in equities...",
  "keywords": ["portfolio", "positioned", "risk"],
  "card": {
    "icon": "book",
    "title": "Portfolio Position",
    "keyValues": [ { "label": "Risk level", "value": "Balanced" } ],
    "bullets": [ "Equity 60%", "Bonds 30%", "Cash 10%" ]
  }
}
```

What each field means:

| Field | What it does |
|---|---|
| `id` | Any unique number |
| `enabled` | `true` = part of the demo, `false` = skipped |
| `topic` | Short internal name (shown only in the ?debug=1 scene list) |
| `question` | **The advisor's question** — shown as a speech bubble in autoplay |
| `answer` | **Exactly what the avatar says out loud** |
| `keywords` | Lowercase words — if the advisor's spoken question contains one, this answer plays |
| `searching` | Optional: `true` shows a "Searching knowledge base…" moment before the answer |
| `card` | Optional: the panel shown on the right (delete the whole `"card"` part for no panel) |

Card options — use any mix: `keyValues` (label/value rows), `bullets` (list),
`chips` (green tags), `note` (italic paragraph). `icon` can be:
`calendar`, `user`, `book`, `scale`, `mail`. `badge` puts a small tag next to
the title (`"badgeTone": "amber"` makes it yellow).

## Rules that prevent mistakes

- Keep every text inside `"double quotes"`.
- Every block ends with `},` **except the last one** before `]` — no comma there.
- If something is wrong, the demo start screen will tell you the line number to check.
