# runs-cleaner

Linux 临时目录清理器，仅用 Python 标准库，需 Python 3.9 以上、`/proc`、`flock`。根目录的直接子项为清理单位；仅当其自身和全部后代的 mtime、ctime 均早于 30 天前，且本用户无进程在用时，才整项删除。保留期固定 30 天，不提供天数参数。

## 命令行

```text
python3 /absolute/path/to/clean.py --mode list ROOT_OR_GLOB [ROOT_OR_GLOB ...]
python3 /absolute/path/to/clean.py --mode delete ROOT_OR_GLOB [ROOT_OR_GLOB ...]
```

必须写明模式和至少一个根目录。通配须加引号，由脚本展开；根可重复或嵌套。`list` 仅向标准输出列候选清单，不改目录或清空回收目录；`delete` 删除所有合格项，并向标准输出列出成功删除项的原路径。

清单和删除结果均为 JSON Lines，每行一个表示绝对路径的 JSON 字符串；换行、回车、引号、非 ASCII 字符等均用 JSON 转义。

警告和错误写标准错误。单项失败后继续，退出码 1；根目录不存在时告警并继续，单独此警告不导致失败。参数错误退出码 2，正常结束为 0。

## 安装与根目录配置

假设仓库在 `$HOME/xy-skills`、程序在 `xy-runs/`。其他路径须替换仓库路径并修改 service 的 `%h/xy-skills/xy-runs/clean.py`；工作树试用也指向其中的 `xy-runs/clean.py`。

按[仓库安装步骤](../README.md#安装)在 `$HOME/.claude/settings.json` 的 `env` 中设置 `RUNS_ROOT`。

service 每次从此文件读取 `RUNS_ROOT`，不依赖登录 shell 的 `RUNS_ROOT`；未设置、为空或非绝对路径时，启动清理器前报错。根路径的 `*`、`?`、`[` 按字面处理（`run[1]` 不会选中 `run1`）；只有追加的 runs 层级模式参与通配。传入三层根：

| 根目录 | 直接子项，即清理单位 |
| --- | --- |
| `RUNS_ROOT` | 项目目录，以及根下其他不以 `-` 开头的项 |
| `RUNS_ROOT/-*` | 一件事的目录 |
| `RUNS_ROOT/-*/xy-goal-inbox` | 单个会话的 inbox |

三层须一起配置；最外层根提供共用锁和回收目录。第二层只匹配以 `-` 开头的项目目录，不拆分删除旧平铺项。项目内容全过期可整项删除；空项目须自身满 30 天未变更。

本机根目录列在 `systemd/runs-cleaner.service`，不写入 Python：

- `%h/.codex-subagent*/sessions/*/*`，清理单位为按日的会话目录。

账号目录的 `tasks/`、`reports/`、`logs/`、`brief-*.md` 不另配根；各工具自己的配置目录和 harness 持久记忆不纳入。

复制两个用户单元：

```bash
install -D -m 644 "$HOME/xy-skills/xy-runs/systemd/runs-cleaner.service" "$HOME/.config/systemd/user/runs-cleaner.service"
install -D -m 644 "$HOME/xy-skills/xy-runs/systemd/runs-cleaner.timer" "$HOME/.config/systemd/user/runs-cleaner.timer"
systemctl --user daemon-reload
```

## 首次启用定时清理

首次启用前，用以下命令从与 service 相同的 `$HOME/.claude/settings.json` 读取并校验 `env.RUNS_ROOT`。它先对根路径做 `glob.escape`，再加 `/-*`、`/-*/xy-goal-inbox`；Codex 会话 glob 不变。手动运行后逐项检查清单：

```bash
RUNS_ROOT="$(python3 -c 'import json, os; from pathlib import Path; root = json.loads((Path.home() / ".claude/settings.json").read_text())["env"]["RUNS_ROOT"]; assert isinstance(root, str) and os.path.isabs(root), "env.RUNS_ROOT must be an absolute path"; print(root)')" &&
: "${RUNS_ROOT:?settings.json env.RUNS_ROOT must be an absolute path}" &&
RUNS_ROOT_GLOB="$(python3 -c 'import glob, sys; print(glob.escape(sys.argv[1]))' "$RUNS_ROOT")" &&
python3 "$HOME/xy-skills/xy-runs/clean.py" --mode list \
  "$HOME/.codex-subagent*/sessions/*/*" \
  "$RUNS_ROOT_GLOB" "$RUNS_ROOT_GLOB/-*" "$RUNS_ROOT_GLOB/-*/xy-goal-inbox"
```

确认清单范围和候选项后，在根目录建立预览门槛，再启用每日定时清理：

```bash
touch "$RUNS_ROOT/.runs-cleaner-previewed"
systemctl --user enable --now runs-cleaner.timer
systemctl --user list-timers runs-cleaner.timer
```

service 以 `--mode delete` 运行。每次启动先核验 `RUNS_ROOT` 为绝对路径，再确认根下 `.runs-cleaner-previewed` 存在；否则报错且不启动清理器。标记绑定当前根：改 `RUNS_ROOT` 后，须在新根重跑 `--mode list`、检查清单并重建标记；旧根标记无效。

定时器设为 `OnCalendar=daily`；service 的标准输出和标准错误进入 journal。用户管理器须持续运行；本机已开启 linger。

## 删除协议与限制

程序在最外层根本身取 `flock(LOCK_EX)`。锁内重核根仍是展开时核验的真实目录及候选的过期/在用状态，再以唯一名移入该根 `.runs-cleaner-trash/`；解锁后删除。根在展开后被替换则跳过并报告；操作用持有的目录句柄，不跟随软链。`delete` 每轮先清上次残留的回收项；内层根不建回收目录，回收目录不作候选，`list` 保持其原样。

在用检查读取本用户进程的工作目录、根目录、可执行文件、打开的文件和目录句柄、映射文件及命令行完整路径。命令行路径后须为 `/`、空白或结尾；同前缀路径不算在用。与删除互斥的写入方须共用最外层根锁；长时间写入时，先在锁内打开事项目录句柄，再放锁并持有至写完，子进程也须继承句柄。握手由写入方实现。

本用户中主动禁止查看的进程只按可读命令行判断；若它隐藏目录、文件句柄等引用且命令行不含该项路径，30 天未变更的项仍可能被删。其他账号的进程（包括 root）不检查；程序以本人身份运行，不用 sudo。

软链只按链接自身时间判断，只删除链接、不遍历目标；不能读取的后代使整项保留。ctime 使刚迁入的旧文件保留 30 天；访问时间和只读访问不延长保留期。长期内容放仓库或已明确的长期位置。
