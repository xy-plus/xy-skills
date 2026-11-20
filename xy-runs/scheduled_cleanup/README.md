# runs-cleaner

Linux 临时目录清理器，仅用 Python 标准库；需 Python 3.9 以上、`/proc`、`flock`。根目录的直接子项是清理单位，保留期固定 30 天且不可配置；完整到期判据见 [xy-runs 规则](../SKILL.md#什么时候删)。

## 命令行

```text
python3 /absolute/path/to/clean.py --mode list ROOT_OR_GLOB [ROOT_OR_GLOB ...]
python3 /absolute/path/to/clean.py --mode delete ROOT_OR_GLOB [ROOT_OR_GLOB ...]
python3 /absolute/path/to/clean.py configured --mode list --additional-root ROOT_OR_GLOB
python3 /absolute/path/to/clean.py delete-obsolete --target ABSOLUTE_TARGET
```

必须写明模式和至少一个根目录；通配符须加引号，由脚本展开，根可重复或嵌套。`list` 仅向标准输出列候选，不改目录或清空回收目录；`delete` 删除所有合格项，并输出成功删除项的原路径。

预览和删除结果都是 JSON Lines，每行一个绝对路径 JSON 字符串；换行、回车、引号及非 ASCII 字符均转义。

警告和错误写标准错误。单项失败后继续，退出码 1；根不存在时告警，单独此警告不导致失败。参数错误退出码 2，正常为 0。

## 安装与根目录配置

假设仓库在 `$HOME/xy-skills`、程序在 `xy-runs/scheduled_cleanup/`。路径不同则替换仓库路径，并修改 service 的 `%h/xy-skills/xy-runs/scheduled_cleanup/clean.py`；工作树试用也指向其中的 `xy-runs/scheduled_cleanup/clean.py`。

按[仓库安装步骤](../../README.md#安装)在 `$HOME/.claude/settings.json` 的 `env` 中设置 `RUNS_ROOT`。

service 每次从此文件读取 `RUNS_ROOT`，不依赖登录 shell；未设置、为空或非绝对路径时，启动前报错。根路径中的 `*`、`?`、`[` 按字面处理（`run[1]` 不会选中 `run1`）；仅追加的 runs 层级模式参与通配。传入三层根：

| 根目录 | 直接子项，即清理单位 |
| --- | --- |
| `RUNS_ROOT` | 项目目录，以及根下其他不以 `-` 开头的项 |
| `RUNS_ROOT/-*` | 一件事的目录 |
| `RUNS_ROOT/-*/xy-goal-inbox` | 单个会话的 inbox |

三层须一起配置；最外层提供共用锁和回收目录。第二层只匹配以 `-` 开头的项目目录，不拆旧平铺项。项目内容全过期可整项删除；空项目也须自身满 30 天未变更。

本机根目录列在 `systemd/runs-cleaner.service`，不写入 Python：

- `%h/.codex-subagent*/sessions/*/*`，清理单位为按日的会话目录。

账号目录的 `tasks/`、`reports/`、`logs/`、`brief-*.md` 不另配根；各工具配置目录与 harness 持久记忆不纳入。

复制两个用户单元：

```bash
install -D -m 644 "$HOME/xy-skills/xy-runs/scheduled_cleanup/systemd/runs-cleaner.service" "$HOME/.config/systemd/user/runs-cleaner.service"
install -D -m 644 "$HOME/xy-skills/xy-runs/scheduled_cleanup/systemd/runs-cleaner.timer" "$HOME/.config/systemd/user/runs-cleaner.timer"
systemctl --user daemon-reload
```

## 首次启用定时清理

首次启用前先检查 `settings.json` 里的 `env.RUNS_ROOT`。首次预览和 systemd 使用同一个 `configured` 入口；它每次读取设置、校验根路径，并只让追加的层级模式参与匹配。运行后逐项检查清单：

```bash
python3 "$HOME/xy-skills/xy-runs/scheduled_cleanup/clean.py" configured --mode list \
  --additional-root "$HOME/.codex-subagent*/sessions/*/*"
```

确认清单范围和候选项后，在设置中的 RUNS_ROOT 下建立预览门槛，再启用每日定时器。将 `<RUNS_ROOT>` 替换为 `settings.json` 中 `env.RUNS_ROOT` 的绝对路径：

```bash
touch "<RUNS_ROOT>/.runs-cleaner-previewed"
systemctl --user enable --now runs-cleaner.timer
systemctl --user list-timers runs-cleaner.timer
```

service 调用同一个 `configured --mode delete` 入口。入口要求 RUNS_ROOT 下的 `.runs-cleaner-previewed` 是普通文件；否则报错并停止。标记只对当前根有效；换 RUNS_ROOT 后，须在新根重跑 `configured --mode list`、检查清单并重建标记；旧根标记无效。

定时器 `OnCalendar=daily`；service 标准输出/错误写入 journal。用户管理器须持续运行；本机已开启 linger。

## 删除协议与限制

程序在最外层根上取 `flock(LOCK_EX)`。锁内复核根仍是展开时的真实目录、清理项仍符合 mtime 和 ctime 的期限，再以唯一名移入该根 `.runs-cleaner-trash/` 并删除。根在展开后被替换则报告；操作依持有的目录句柄，不跟随软链。`delete` 每轮先清上次残留；内层根不建回收目录，回收目录不是候选，`list` 不改其内容。

`delete-obsolete` 只检查路径：目标须为字面绝对路径，位于 `RUNS_ROOT` 下某项目的一件事目录或其内容中；不接受运行根、项目目录、inbox 或经过软链的上级。目标本身是软链时，真实目标须在运行根内，删除只解除链接。它与定期清理共用最外层根锁和回收目录。

写入方须共用最外层根锁，并在整个写入期间持锁，避免清理与写入交错。

软链只按链接自身时间判断，只删链接、不遍历目标；任一后代不可读时整项保留。ctime 让刚迁入的旧文件重新获得 30 天保留期；访问时间和只读访问不延长。长期内容放仓库或已注明的长期位置。
