# ttt — Terminal Time Tracker

A local, private time tracker for macOS that lives in your terminal.
A tiny background daemon samples the frontmost app every 5 seconds; `ttt` shows
where your time went and lets you sort it into containers (clients, projects)
with the arrow keys or by just asking the built-in AI chat.

```
‹ today · Mon Nov 16 ›
sites                                   apps
  ▸ 25m  client-a.com                     45m  Terminal
    16m  meet.google.com                  39m  Figma
finder                                  containers
     9m  …o/projects/client-a/          5h26m  other
     3m  ~/Downloads/invoices/          1h10m  work

ai: work today: 1h10m.
› move everything in other from slack to work█
ttt · today · Mon Nov 16: active total 5h26m · [daily] weekly monthly
↑↓←→ select · enter actions · tab period · esc back · ctrl-c quit
```

- **Local only.** Raw samples go to SQLite in `~/.usage/usage.db`. Nothing leaves your
  machine unless you use the AI chat (see Privacy).
- **Retroactive rules.** Samples are stored raw; containers are computed when you read.
  Add a rule today and last month is re-sorted too. No migrations of your data, ever.
- **Python stdlib only.** No dependencies.

## Requirements

- macOS (uses `lsappinfo`, `ioreg`, AppleScript)
- Python 3.12+
- Browsers with URL support: Dia, Chrome, Chromium, Arc, Brave, Safari.
  Dia also records the active **profile**.

## Install

```sh
git clone https://github.com/delsallorenzo/ttt.git ~/ttt
ln -s ~/ttt/ttt ~/.local/bin/ttt     # or anywhere on your PATH
ttt tracker on                       # starts the background sampler (LaunchAgent)
ttt doctor                           # checks tracker, permissions, errors
```

On first run macOS asks for **Automation** permission (System Events, Finder,
your browser). Grant it, or URLs, folders and window titles stay empty.

Optional:

```sh
ttt settings      # AI provider (Anthropic, OpenAI, OpenRouter), model, API key
ttt menubar on    # today's total in the menu bar (Swift helper: swiftc menu.swift -o menubar)
```

## Using the app

Run `ttt` with no arguments.

| key | does |
|---|---|
| `↑ ↓ ← →` | move the selection; on the date row `← →` move in time |
| `enter` on a site / app / folder | move it to a container (existing or new), with a preview and `s/N` confirm |
| `enter` on a container | rename it |
| type + `enter` | talk to the AI chat, e.g. *"what's in other?"*, *"move 1 and 3 to client-a"*, *"undo"* |
| `/command` + `enter` | run a command inside the app, e.g. `/unmapped`, `/doctor` |
| `tab` | daily → weekly → monthly |
| `esc` | close menu / clear input / back to today |
| `ctrl-c` or `/quit` | quit |

Every change is confirmed first and backed up; `ttt containers undo` restores the previous file.

## Containers and rules

Containers live in `containers.yaml` next to the code (git-ignored, see
`containers.example.yaml`). Each rule is one line:

| type | matches | example |
|---|---|---|
| `domain` | browser host contains | `client-a.com` |
| `path` | Finder folder path contains (subfolders included) | `/Work/client-a/` |
| `app` | app name contains | `Figma` |
| `title_contains` | window title contains | `client-a` |
| `profile` | Dia profile, exact name; wins over every other rule | `Client A` |

Matching is case-insensitive. When several rules match, a profile rule wins, then
the longest value. Unmatched time goes to `other`.

Only Finder reports a folder path. For other apps, a `title_contains` rule on the
project name usually catches the documents you have open.

## Commands

```
ttt                     the app
ttt daily|weekly|monthly [--container NAME]
ttt unmapped [--days N] where 'other' time goes
ttt containers [add NAME --domain D --path P --app A --title T --profile P | rm NAME | undo]
ttt settings [set KEY VALUE]
ttt tail | doctor | prune [--days N]
ttt tracker on|off | menubar on|off
ttt help
```

## Privacy

- The tracker stores, every 5 s: app name, bundle id, browser host (not the full URL),
  Finder folder, window title, Dia profile, idle flag.
- The AI chat is opt-in (`ttt settings`). It sends the model aggregated totals per
  site / app / folder and your container rules — never window titles or raw samples.
- The API key is stored in `~/.usage/ttt.conf` (mode 600) or read from `TTT_API_KEY`.

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install pytest
.venv/bin/python -m pytest tests/
```

`import_chronoid.py` is a one-off importer for a Chronoid backup, kept for reference.

## License

MIT
