---
name: vibebridge-main-install
description: Install, deploy, or reconfigure a VibeBridge Main server. Use when setting up configs/main.toml, asking the user for required main deployment settings, creating a dedicated Python virtualenv, deciding whether to rebuild the frontend, choosing nohup/systemd/launchd/manual process management, and validating the Main health endpoint plus browser entrypoint. 适用于安装或部署 VibeBridge Main 服务，并在改配置前向用户确认必要参数。
---

# VibeBridge Main Install

Use this skill when the task is to install, deploy, or reconfigure the VibeBridge Main server.

## Ask first

Before editing `configs/main.toml`, rebuilding the frontend, or starting a long-running process, ask the user for the required deployment values instead of guessing:

- whether the install target is the current machine or a machine reached through SSH
- if the target is remote, the SSH user, host, port, and whether the SSH session is already open
- repo path on the target machine
- whether this must be a fresh isolated environment, and the desired venv path
- whether this is a new Main instance or a change to an existing one
- bind `host` and `port`
- whether the browser will access Main directly or through a reverse proxy
- if exposed publicly, the public URL or domain and which port/gateway is already open
- database path for Main
- whether to reuse the committed `dist/` bundle or rebuild the frontend
- preferred run style: `nohup`, `systemd`, `launchd`, or manual
- whether a fixed `jwt_secret` is required or auto-generated per installation is acceptable
- whether compatibility options such as `main.node_addresses` or `main.node_register_tokens` are intentionally needed

If any required values are missing, stop and ask. Do not silently invent public URLs, firewall assumptions, database locations, or process managers.

## Defaults and preferences

- Prefer a fresh role-specific venv such as `.venv-vibebridge-main`.
- For production-like runs, use `python -m uvicorn main_server:app ...`.
- Do not use `python main_server.py` for long-running deployment because the repo entrypoint enables `reload=True`.
- Reuse the committed `dist/` bundle unless the user explicitly wants a fresh frontend build or local frontend changes must be included.
- Keep Main and Node on different SQLite files.
- `jwt_secret` can be omitted. Main will auto-generate and persist one for the installation.
- The first registered account becomes `creator`; later accounts default to `pending` and need approval.
- Browser traffic should go to Main only. Nodes are workers and should not be exposed as user-facing web entrypoints.
- Do not assume local paths apply to a remote machine. If deployment is over SSH, treat the repo path, venv path, logs, and service files as remote paths.

## Bundled script

Use the bundled installer when it fits:

- `scripts/install_main.sh`
  - creates the venv
  - installs Python dependencies
  - optionally rebuilds the frontend
  - writes `configs/main.toml`
  - leaves process supervision to the caller

Do not overwrite an existing `configs/main.toml` unless the user explicitly asks for that. The script supports `--force-config` for intentional replacement.

Example with the committed frontend bundle:

```bash
bash skills/vibebridge-main-install/scripts/install_main.sh \
  --repo /root/VibeBridge \
  --venv /root/VibeBridge/.venv-vibebridge-main \
  --host 0.0.0.0 \
  --port 8000 \
  --db-path database/auth.db
```

Example with a fresh frontend build:

```bash
bash skills/vibebridge-main-install/scripts/install_main.sh \
  --repo /root/VibeBridge \
  --venv /root/VibeBridge/.venv-vibebridge-main \
  --host 0.0.0.0 \
  --port 8000 \
  --db-path database/auth.db \
  --build-frontend
```

## Start patterns

Use the run style the user asked for. If they do not care, choose the platform-appropriate default:

- Linux server: prefer `systemd`
- macOS workstation: prefer `nohup` unless the user explicitly wants `launchd`

Typical `nohup` command:

```bash
mkdir -p logs
nohup env PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin \
  ./.venv-vibebridge-main/bin/python -m uvicorn main_server:app --host 0.0.0.0 --port 4457 \
  >> logs/main.log 2>&1 < /dev/null &
```

Typical `systemd` shape:

```ini
[Unit]
Description=VibeBridge Main
After=network.target

[Service]
WorkingDirectory=/path/to/VibeBridge
ExecStart=/path/to/VibeBridge/.venv-vibebridge-main/bin/python -m uvicorn main_server:app --host 0.0.0.0 --port 4457
Restart=always
Environment=PATH=/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=multi-user.target
```

## Validation

After installation or config changes:

1. Check Main health:

   ```bash
   curl -sf http://127.0.0.1:<port>/health
   ```

2. Confirm the `dist/` assets are present if the browser UI should be served by Main.
3. If exposed publicly, verify the external URL and port actually reach Main.
4. Confirm the first account flow works for a new database, or existing accounts remain intact for an existing database.

## Notes

- In the recommended setup, `configs/main.toml` only needs `[server]` and `[database]`.
- `main.node_addresses` and `main.node_register_tokens` still exist as advanced compatibility options, but most multi-user setups do not need them.
- If the user asks to keep an old instance untouched, create a separate venv, config, database path, and service name instead of modifying the existing deployment in place.
