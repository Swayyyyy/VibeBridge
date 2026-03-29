<div align="center">
  <img src="./frontend-src/public/logo-256.png" alt="VibeBridge logo" width="120" />
  <h1>VibeBridge</h1>
  <p>
    One web console for managing Claude Code and Codex sessions, terminals, and projects across multiple machines.
  </p>
</div>

<div align="right"><i><b>English</b> · <a href="./README.zh-CN.md">中文</a></i></div>

---

<p align="center">
  <img src="./docs/screenshots/vibebridge-overview.jpg" alt="VibeBridge managing Claude Code and Codex sessions from multiple nodes in one UI" width="100%" />
</p>

## Overview

`VibeBridge` is a multi-node web console for connecting to and managing `Claude Code` and `Codex` running on different machines from a single interface.

## Use Cases

VibeBridge is useful when your `Claude Code` or `Codex` sessions are spread across a local machine, one or more remote dev boxes, or multiple servers. It gives you one browser entry point for switching nodes, opening projects, and following conversations without bouncing between SSH sessions, terminals, and browser tabs.

## Key Features

- One web UI for Claude Code and Codex across multiple machines
- Main / Node split, so the browser talks only to Main while each Node executes locally
- Multi-user registration, approval, role-based access, and node ownership
- Answer-first chat UI with intermediate activity folded into a process panel
- Better session restoration, including Codex compact history
- Supports both direct Node -> Main WebSocket and HTTP registration + Main callback

## Multi-User and Node Ownership

VibeBridge supports multi-user registration, approvals, and role-based node ownership. Each approved user gets a dedicated `node_register_token`, and any Node that registers with that token is attached to that user automatically.

- The first registered account becomes the `creator`.
- Later registrations default to `pending` and must be approved by the `creator` or an `admin` before they can sign in.
- `creator` can approve users, change `admin / user / pending` roles, and rotate other users' node tokens.
- `admin` can approve pending users, but cannot change roles.
- `user` can access only their own nodes.

## Session UI and History Restore

VibeBridge keeps the main message flow answer-first instead of dumping every low-level event inline.

- Each assistant turn highlights the final reply and keeps intermediate tool calls, thinking, and compact activity in a collapsible process panel.
- Turns without meaningful intermediate activity stay visually clean instead of showing empty process containers.
- Reopening a session restores the conversation state more faithfully, including Codex compact history.
- Codex sessions handle long outputs more reliably, which makes extended runs feel closer to the Codex app experience.

<a id="quick-start"></a>

## Quick Start

If you are deploying through `Claude Code` or `Codex`, the recommended path is to let it use the repository skills instead of typing every step manually:

- Main: `skills/vibebridge-main-install`
- Node: `skills/vibebridge-node-install`

These skills first confirm whether the target is the current machine or an SSH-connected remote machine, then ask for the required config and carry out the install.

### 1. Install backend dependencies

```bash
cd /path/to/VibeBridge
pip install -r requirements.txt
```

Use any environment manager you like; a dedicated virtualenv or conda environment is recommended.

### 2. Create runtime config files

```bash
cd /path/to/VibeBridge
cp configs/main.toml.example configs/main.toml
cp configs/node.toml.example configs/node.toml
```

For same-machine local development, use different `database.path` values in the two files so Main and Node do not share the same SQLite database. Update `node.main_server_url` or `node.main_register_url` if the Node should connect to a different Main host.

### 3. Start the Main Server

```bash
cd /path/to/VibeBridge
python main_server.py
```

### 4. Start one Node Server

```bash
cd /path/to/VibeBridge
python app.py
```

To add more nodes, repeat the Node setup on other machines with each machine's own `configs/node.toml`.

### 5. Open the UI

```text
http://127.0.0.1:4457/
```

If the database is empty, the first registered account becomes the `creator`. Later accounts default to `pending` and must be approved by the `creator` or an `admin` before they can sign in.

## Architecture

```text
Browser
  -> Main Server (main_server.py)
       - serves dist/
       - auth + JWT
       - node registry and routing
       - browser WebSocket and shell relay
  -> Node Server(s) (app.py)
       - connect to Main
       - run Claude Code / Codex locally
       - expose local project, filesystem, shell, and Git APIs
```

The browser should connect only to Main. Nodes are execution workers, not separate browser entry points. This keeps auth, routing, and browser access centralized while execution stays local to each machine.

## Connection Modes

### Mode 1: Node initiates a direct WebSocket connection

Set `node.main_server_url` in `configs/node.toml`.

### Mode 2: Node registers over HTTP and Main connects back

Set `node.main_register_url` in `configs/node.toml`.

If both are set, the current implementation prefers direct WebSocket mode. Use `node.advertise_host` and `node.advertise_port` when Main needs a different callback address than the Node sees locally.

## Common Config Keys

| File or Key | Used By | Description |
| --- | --- | --- |
| `configs/main.toml` | Main | Main runtime config file; the example keeps only the minimal required keys |
| `configs/node.toml` | Node | Node runtime config file; the example shows both direct WS and HTTP registration modes |
| `server.host` / `server.port` | Main / Node | Listen address and port |
| `database.path` | Main / Node | SQLite database path; Main and Node should usually use different files |
| `node.main_server_url` | Node | Direct WebSocket target |
| `node.main_register_url` | Node | HTTP registration target for Main callback mode |
| `node.register_token` | Node | User-scoped node registration token; determines node ownership |
| `node.id` / `node.name` | Node | Stable node identifier and display name; can be left blank to derive from the hostname |
| `node.labels` / `node.capabilities` | Node | Labels and declared capabilities |
| `node.advertise_host` / `node.advertise_port` | Node | Override the callback address Main should use in HTTP registration mode |
| `auth.platform_mode` | Node | Optional node-local bypass mode; normally not needed in a Main + Node deployment |
| `filesystem.*` | Node | File browser guardrails |
| `terminal.default_shell` | Node | Default shell for the built-in terminal |
| `providers.claude.*` / `providers.codex.*` | Node | Provider-specific timeouts, context, and limits |

The project still keeps compatibility-oriented advanced Main options such as `main.node_addresses` and `main.node_register_tokens`, but the recommended multi-user setup usually does not need them.

<a id="acknowledgements"></a>

## Acknowledgements

- [claudecodeui](https://github.com/siteboon/claudecodeui)
- [happy](https://github.com/slopus/happy)
