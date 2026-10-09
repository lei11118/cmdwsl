# cmdwsl — Hermes Service Runner

> 不用再手敲命令启动服务：把 **WSL2 里的服务**（如 `hermes-web-ui start`）和 **CMD 下的服务**整理成一张清单，点一下批量执行，失败自动重试。

Windows GUI 小工具（Python + Tkinter，单文件），为「开机 / 日常要跑的几条命令」提供一个可视化管理面板。

## 特性

- 📋 **表格管理**：增删命令、上下排序、双击单元格编辑，表格顺序即执行顺序
- 🖥 **两种运行环境**
  - `WSL` — 通过 `wsl bash -il -c` 执行（`-il` 会加载 `.bashrc` / `.profile`，环境与你手敲完全一致）
  - `CMD` — 通过 `cmd /c` 执行（自动 `chcp 65001` 切换 UTF-8，避免中文乱码）
- 🔁 **两种命令类型**
  - `oneshot` — 跑完即结束：退出码 0 算成功，否则按间隔重试
  - `service` — 常驻服务：输出行匹配「成功信号」正则后视为启动成功，进程转入后台继续运行；若进程在信号出现前退出，则自动重试
- ⏱ **重试机制**：可配置重试间隔；最大重试次数 `0` = 无限重试
- 💾 **配置即文件**：所有行保存为同目录 `hermes_commands.json`，可直接拷贝 / 版本管理
- 🛑 **一键停止**：终止本次执行启动的所有子进程

## 使用

1. 环境：Windows 10 / 11 + Python 3.10+
2. 启动：

   ```bat
   python hermes_runner.py
   ```

3. 在表格里维护命令：
   - **勾选 = 启用**（点「运行全部」执行所有勾选项）
   - **取消勾选 = 选中运行**（点「运行选中」执行被取消勾选的项）
   - 双击任意单元格编辑；`成功信号` 支持正则，如 `Application startup complete|Listening on`
4. 点「保存配置」写回 `hermes_commands.json`。

## 配置格式（hermes_commands.json）

```json
[
  {
    "command": "hermes-web-ui start",
    "env": "WSL",
    "enabled": true,
    "cmd_type": "service",
    "ready_signal": "",
    "retry_delay": 3,
    "max_retries": 0
  }
]
```

| 字段 | 说明 |
|---|---|
| `command` | 原样在 WSL(bash) 或 CMD 中执行的命令 |
| `env` | `WSL` 或 `CMD` |
| `enabled` | 是否参与「运行全部」 |
| `cmd_type` | `service`（常驻服务）或 `oneshot`（跑完即止） |
| `ready_signal` | 正则；输出中匹配到该模式即视为启动成功（可留空） |
| `retry_delay` | 失败重试间隔（秒） |
| `max_retries` | 最大重试次数；`0` = 无限重试 |

## 打包成独立 exe（可选）

```bat
pip install pyinstaller
pyinstaller hermes_runner.spec
```

产物：`dist\hermes_runner.exe`（单文件）。

## 行为说明

- 命令按表格顺序**串行执行**；某条命令重试耗尽后仍失败时，**跳过后续命令**（避免在失败的基础上继续启动），日志区有明确提示。
- 日志区实时输出子进程 stdout / stderr（自动识别系统编码，兼容中文输出）。
- 仅支持 Windows（依赖 `wsl.exe` / `cmd.exe`）。
