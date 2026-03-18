<div align="center">
  <img src="./frontend-src/public/logo-256.png" alt="VibeBridge logo" width="120" />
  <h1>VibeBridge</h1>
  <p>
    Manage Claude Code and Codex sessions on multiple machines from one browser UI.
  </p>
</div>

<div align="right"><i><b>English</b> · <a href="./README.zh-CN.md">中文</a></i></div>

---

<p align="center">
  <img src="./docs/screenshots/vibebridge-overview.jpg" alt="VibeBridge managing Claude Code and Codex sessions from multiple nodes in one UI" width="100%" />
</p>

## Overview

`VibeBridge` is a browser control plane for running `Claude Code` and `Codex` across one or more machines.

- `main_server.py` is the only browser-facing entry.
- `app.py` runs on each node and executes local filesystem, shell, Git, and provider work.
- A session is created by choosing `node + path + provider`, so execution stays on the machine that owns the workspace.

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

The browser should talk only to Main. Nodes are execution workers, not separate browser entry points.

## Connection Modes

### 1. Direct WebSocket

Set `node.main_server_url` in `configs/node.toml`.

### 2. HTTP registration + Main callback

Set `node.main_register_url` in `configs/node.toml`.

If both are set, the current implementation prefers direct WebSocket mode. Use `node.advertise_host` and `node.advertise_port` when Main needs a different callback address than the Node sees locally.

<a id="quick-start"></a>

## Quick Start

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

If the database is empty, the first visit will go through the registration flow.

## Config Reference

| File or Key | Used By | Description |
| --- | --- | --- |
| `configs/main.toml` | Main | Main runtime config |
| `configs/node.toml` | Node | Node runtime config |
| `server.host` / `server.port` | Main / Node | Listen address and port |
| `database.path` | Main / Node | SQLite database path |
| `auth.jwt_secret` | Main | JWT secret; generated and persisted if omitted |
| `main.node_register_tokens` | Main | Allowed node registration tokens |
| `main.node_addresses` | Main | Nodes that Main should connect to proactively |
| `node.main_server_url` | Node | Direct WebSocket target |
| `node.main_register_url` | Node | HTTP registration target for Main callback mode |
| `node.id` / `node.name` | Node | Stable node identifier and display name |
| `node.register_token` | Node | Node registration token |
| `node.labels` / `node.capabilities` | Node | Labels and declared capabilities |
| `node.advertise_host` / `node.advertise_port` | Node | Override the address Main should use to reach this Node |
| `filesystem.*` | Node | File browser guardrails |
| `terminal.default_shell` | Node | Default shell for the built-in terminal |
| `providers.claude.*` / `providers.codex.*` | Node | Provider-specific timeouts and limits |

## Repository Layout

```text
VibeBridge/
├── app.py
├── main_server.py
├── config.py
├── configs/
├── database/
├── main/
├── middleware/
├── providers/
├── routes/
├── ws/
├── frontend-src/
└── dist/
```

Good starting points:

- `main_server.py`
- `app.py`
- `node_connector.py`
- `main/browser_gateway.py`
- `providers/claude_sdk.py`
- `providers/codex_mcp.py`

## Providers

### Claude

- Implementation: `providers/claude_sdk.py`
- Uses the real Python SDK path
- Dependency: `claude-agent-sdk`

### Codex

- Implementation: `providers/codex_mcp.py`
- Uses `codex mcp-server` first
- Falls back to `codex exec --json` if MCP bootstrap fails
- Requires the `codex` CLI on the node machine

## Verification

Main:

```bash
curl -sf http://<main-host>:4457/health
```

Node:

```bash
curl -sf http://<node-host>:4456/health
```

## Notes

- `dist/` is already included in the repository. Rebuild the frontend only when you actually change frontend code.
- `Dockerfile` currently describes the Node role only.

<a id="acknowledgements"></a>

## Acknowledgements

- [claudecodeui](https://github.com/siteboon/claudecodeui)
- [happy](https://github.com/slopus/happy)
