---
name: vibebridge-node-install
description: Install, deploy, or reconfigure a VibeBridge Node server. Use when setting up configs/node.toml, asking the user for required node deployment settings, creating a dedicated Python virtualenv, choosing direct WebSocket or HTTP registration mode, starting the node with nohup/systemd/launchd, and validating /health plus Main registration. 适用于安装或部署 VibeBridge Node 节点，并在改配置前向用户确认必要参数。
---

# VibeBridge Node Install

Use this skill when the task is to install, deploy, or reconfigure a VibeBridge Node.

## Ask first

Before editing `configs/node.toml` or starting a long-running process, ask the user for the required deployment values instead of guessing:

- whether the install target is the current machine or a machine reached through SSH
- if the target is remote, the SSH user, host, port, and whether the SSH session is already open
- repo path on the target machine
- whether a fresh isolated environment is required, and the desired venv path
- connection mode:
  - `main_server_url` for direct `Node -> Main` WebSocket
  - `main_register_url` for HTTP registration followed by Main callback
- the actual Main URL for the chosen mode
- `register_token`
- node listen `host` and `port`
- whether Main can reach the callback address if HTTP registration mode is chosen
- optional node identity overrides: `id`, `name`, `labels`, `capabilities`
- preferred run style: `nohup`, `systemd`, `launchd`, or manual
- whether Codex must work on this node; if yes, confirm the `codex` CLI path or install method

If any of the required values are missing, stop and ask. Do not silently invent network addresses, tokens, or service managers.

## Defaults and preferences

- Prefer a fresh role-specific venv such as `.venv-vibebridge-node`.
- For long-running deployment, use `python -m uvicorn app:app ...`.
- Do not use `python app.py` for production-like runs because the repo entrypoint enables `reload=True`.
- If the node is not publicly reachable, prefer direct `main_server_url` mode so the node initiates the outbound connection.
- In HTTP registration mode, Main still needs a reachable callback address. Use `advertise_host` and `advertise_port` when the local listen address is not the right callback address.
- The browser should connect only to Main, not to the Node.
- Keep Main and Node on different SQLite files.
- Do not assume local paths apply to a remote machine. If deployment is over SSH, treat the repo path, venv path, logs, and service files as remote paths.

## Bundled script

Use the bundled installer when it fits:

- `scripts/install_node.sh`
  - creates the venv
  - installs Python dependencies
  - writes `configs/node.toml`
  - leaves process supervision to the caller

Do not overwrite an existing `configs/node.toml` unless the user explicitly asks for that. The script supports `--force-config` for intentional replacement.

Example for direct WebSocket mode:

```bash
bash skills/vibebridge-node-install/scripts/install_node.sh \
  --repo /path/to/VibeBridge \
  --venv /path/to/VibeBridge/.venv-vibebridge-node \
  --mode ws \
  --main-server-url ws://main.example.com:8000/ws/node \
  --register-token <user-node-register-token> \
  --host 127.0.0.1 \
  --port 4456
```

Example for HTTP registration mode:

```bash
bash skills/vibebridge-node-install/scripts/install_node.sh \
  --repo /path/to/VibeBridge \
  --venv /path/to/VibeBridge/.venv-vibebridge-node \
  --mode http \
  --main-register-url http://main.example.com:8000/api/nodes/register \
  --register-token <user-node-register-token> \
  --host 0.0.0.0 \
  --port 4456 \
  --advertise-host node.example.com \
  --advertise-port 4456
```

## Start patterns

Use the run style the user asked for. If they do not care, use the platform default that matches prior local conventions:

- Linux server: `systemd` for Main, `nohup` or `systemd` for Node
- macOS workstation: `nohup` unless the user explicitly asks for `launchd`

Typical `nohup` command:

```bash
mkdir -p logs
nohup env PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin \
  ./.venv-vibebridge-node/bin/python -m uvicorn app:app --host 127.0.0.1 --port 4456 \
  >> logs/node.log 2>&1 < /dev/null &
```

## Validation

After installation or config changes:

1. Check local health:

   ```bash
   curl -sf http://127.0.0.1:<port>/health
   ```

2. Confirm the process environment can find `codex` if Codex support is expected:

   ```bash
   command -v codex
   ```

3. Confirm the Main side shows the node as registered.
4. If the node should be private, verify that no public inbound access is required in direct WebSocket mode.

## Notes

- `main_server_url` and `main_register_url` are alternatives. Only one should be meaningfully configured.
- If HTTP registration is selected for a private node that Main cannot call back, stop and realign with the user instead of forcing a broken setup.
- Respect existing deployments. Avoid replacing user-managed services, plists, or unit files unless asked.
