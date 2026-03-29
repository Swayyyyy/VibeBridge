<div align="center">
  <img src="./frontend-src/public/logo-256.png" alt="VibeBridge logo" width="120" />
  <h1>VibeBridge</h1>
  <p>
    用一个浏览器界面，统一管理多台机器上的 Claude Code 与 Codex 会话、终端与项目。
  </p>
</div>

<div align="right"><i><a href="./README.md">English</a> · <b>中文</b></i></div>

---

<p align="center">
  <img src="./docs/screenshots/vibebridge-overview.jpg" alt="VibeBridge 在一个界面中统一管理多个节点上的 Claude Code 与 Codex 会话" width="100%" />
</p>

## 概览

`VibeBridge` 是一个面向多节点场景的 Web 控制台，用一个界面统一接入和管理运行在不同机器上的 `Claude Code` 与 `Codex`。

## 适用场景

VibeBridge 适合把 `Claude Code` / `Codex` 跑在本地机、远程开发机或多台服务器上的场景。它把分散的会话、节点和项目统一收进一个浏览器入口，减少在多个终端、多个窗口和多台机器之间来回切换的成本。

## 核心特性

- 用一个 Web 界面统一管理多台机器上的 Claude Code 和 Codex 会话
- Main / Node 分离，浏览器只连接 Main，节点负责本地执行
- 支持多用户注册、审批、角色权限和节点归属管理
- 主消息流保持答案优先，中间过程可折叠查看
- 更完整地恢复历史状态，包括 Codex 的 compact 历史
- 支持 Node 直连 Main，或先注册再由 Main 主动回连

## 多用户与节点归属

VibeBridge 支持多用户注册、审批和基于角色的节点归属管理。每个已批准用户都有独立的 `node_register_token`，Node 使用该 token 注册后会自动归属到对应用户。

- 第一个注册的账户会自动成为 `creator`（创建者）。
- 后续注册账户默认是 `pending`（待批准），需要由 `creator` 或 `admin` 批准后才能登录。
- `creator` 可以批准用户、调整 `admin / user / pending` 角色，并重置其他用户的节点 token。
- `admin` 可以批准待审核用户，但不能修改角色。
- `user` 只能访问自己名下的节点。

## 会话展示与历史恢复

VibeBridge 不会把底层事件原样摊在主消息流里，而是尽量让界面保持“答案优先”。

- 每一轮对话都会突出最后一条正式回复，中间的工具调用、思考和 compact 过程会收进可折叠的过程区。
- 没有明显中间过程的轮次不会出现空的过程容器，整体阅读会更干净。
- 重新打开会话时，历史状态能更完整地恢复，包括 Codex 的 compact 历史。
- Codex 会话对长输出的处理更稳定，整体使用感受会更接近 Codex app。

<a id="quick-start"></a>

## 快速开始

如果你是通过 `Claude Code` 或 `Codex` 来部署，推荐直接让它调用仓库里的 skill 自动操作，而不是手动一步步敲命令：

- Main: `skills/vibebridge-main-install`
- Node: `skills/vibebridge-node-install`

这两套 skill 会先确认部署目标是当前机器还是 SSH 连接后的远端机器，再继续询问必要配置并执行安装。

### 1. 安装后端依赖

```bash
cd /path/to/VibeBridge
pip install -r requirements.txt
```

环境管理方式不限，推荐使用独立 virtualenv 或 conda 环境。

### 2. 创建运行配置文件

```bash
cd /path/to/VibeBridge
cp configs/main.toml.example configs/main.toml
cp configs/node.toml.example configs/node.toml
```

如果 Main 和 Node 在同一台机器上联调，建议把两个文件里的 `database.path` 改成不同值，避免共用同一个 SQLite 文件。若 Node 需要连接其他 Main，也在这里修改 `node.main_server_url` 或 `node.main_register_url`。

### 3. 启动 Main Server

```bash
cd /path/to/VibeBridge
python main_server.py
```

### 4. 启动一个 Node Server

```bash
cd /path/to/VibeBridge
python app.py
```

如果要接入更多节点，只需要在其他机器上重复 Node 侧配置，并准备各自的 `configs/node.toml`。

### 5. 打开界面

```text
http://127.0.0.1:4457/
```

如果数据库为空，第一次访问进入注册流程后，首个注册用户会成为 `creator`。后续注册用户默认进入 `pending` 状态，需要由 `creator` 或 `admin` 批准后才能登录。

## 架构

```text
Browser
  -> Main Server (main_server.py)
       - serve dist/
       - auth + JWT
       - node registry and routing
       - browser WebSocket and shell relay
  -> Node Server(s) (app.py)
       - connect to Main
       - run Claude Code / Codex locally
       - expose local project, filesystem, shell, and Git APIs
```

浏览器只连接 Main，Node 负责本地执行，不应作为独立页面入口。这种设计更适合多节点接入、统一认证和集中管理。

## 连接方式

### 模式 1：Node 主动连接 Main WebSocket

在 `configs/node.toml` 中设置 `node.main_server_url`。

### 模式 2：Node 先 HTTP 注册，再由 Main 主动回连

在 `configs/node.toml` 中设置 `node.main_register_url`。

如果两个配置同时存在，当前实现会优先使用直连 WebSocket。若 Main 回连 Node 需要使用不同地址，可设置 `node.advertise_host` 和 `node.advertise_port`。

## 常用配置说明

| 文件或键 | 作用角色 | 说明 |
| --- | --- | --- |
| `configs/main.toml` | Main | Main 运行配置文件；示例只保留最小必需项 |
| `configs/node.toml` | Node | Node 运行配置文件；示例同时展示直连和 HTTP 注册两种模式 |
| `server.host` / `server.port` | Main / Node | 监听地址和端口 |
| `database.path` | Main / Node | SQLite 数据库路径；Main 和 Node 建议分开 |
| `node.main_server_url` | Node | 直连 Main 的 WebSocket 地址 |
| `node.main_register_url` | Node | 供 Main 回连模式使用的 HTTP 注册地址 |
| `node.register_token` | Node | 用户级节点注册 token；决定节点归属 |
| `node.id` / `node.name` | Node | 节点稳定标识和显示名称；留空时可按主机名自动派生 |
| `node.labels` / `node.capabilities` | Node | 节点标签和能力声明 |
| `node.advertise_host` / `node.advertise_port` | Node | HTTP 注册模式下供 Main 回连使用的地址覆盖 |
| `auth.platform_mode` | Node | 可选的节点本地免登录模式；常规 Main + Node 部署通常不需要改 |
| `filesystem.*` | Node | 文件浏览限制 |
| `terminal.default_shell` | Node | 内置终端默认 shell |
| `providers.claude.*` / `providers.codex.*` | Node | Provider 相关超时、上下文和限制 |

项目仍保留 `main.node_addresses`、`main.node_register_tokens` 等兼容性高级选项，但在当前推荐的多用户部署里通常不需要配置。

<a id="acknowledgements"></a>

## 致谢

- [claudecodeui](https://github.com/siteboon/claudecodeui)
- [happy](https://github.com/slopus/happy)
