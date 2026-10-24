# runs-cleaner

Linux 上的通用临时目录清理程序，只用 Python 标准库，需要 Python 3.9 以上、`/proc` 和 `flock`。每个根目录的直接子项是一个清理单位：它自身和全部后代的 mtime、ctime 都早于 30 天前，且本用户没有进程在用，才整项删除。保留期固定为 30 天，没有天数参数。

## 命令行

```text
python3 /absolute/path/to/clean.py --mode list ROOT_OR_GLOB [ROOT_OR_GLOB ...]
python3 /absolute/path/to/clean.py --mode delete-listed --listed /absolute/path/to/list.jsonl ROOT_OR_GLOB [ROOT_OR_GLOB ...]
python3 /absolute/path/to/clean.py --mode delete ROOT_OR_GLOB [ROOT_OR_GLOB ...]
```

必须写明模式和至少一个根目录。`--listed` 只允许用于 `delete-listed`，且该模式必须提供它。通配要加引号，由脚本展开；根目录可以重复、嵌套。`list` 只向标准输出写候选清单，不改动目录，也不清空回收目录。`delete-listed` 只处理清单中的完整路径，删除时仍会重新检查；`delete` 处理所有符合条件的项。两种删除模式向标准输出写成功删除项的原路径。

清单和删除结果均为 JSON Lines：每行是一个 JSON 字符串，表示一个绝对路径。换行、回车、引号和非 ASCII 字符等均使用 JSON 转义，文件名中的换行不会拆成另一个批准项；非 UTF-8 文件名字节也能原样读回。清单须原样保存，不要去掉引号、改动转义或混入日志。读回时会在任何删除前验证整份清单，无效记录使本轮失败。旧版未经编码的裸路径清单不再接受，须重新用 `list` 生成。

警告和错误写标准错误。单项失败后继续处理其他项，退出码为 1；根目录不存在时告警后继续，单独出现这种警告不导致失败。参数错误退出码为 2，正常结束为 0。

## 安装与根目录配置

以下按 xy-skills 仓库位于 `$HOME/xy-skills` 操作，清理程序在其中的 `xy-runs/`。放在其他路径时，替换下面的仓库路径，并修改 service 中的 `%h/xy-skills/xy-runs/clean.py`。在工作树中试用也要指向工作树中的 `xy-runs/clean.py`。

先运行全部测试，测试数据均在临时目录中；工作树中用实际仓库路径替换下面的 `$HOME/xy-skills`：

```bash
PYTHONPATH="$HOME/xy-skills/xy-runs" python3 -B -m unittest discover -s "$HOME/xy-skills/xy-runs/tests" -v
```

编辑 `$HOME/.claude/settings.json`，保留其他配置，在 `env` 中设置唯一的 runs 根目录，使用绝对路径：

```json
{
  "env": {
    "RUNS_ROOT": "/data/user/xy/runs"
  }
}
```

service 每次运行都读取这个文件，不依赖登录 shell 的 `RUNS_ROOT`；没有设置、值为空或不是绝对路径，都在启动清理器前报错。根路径中的 `*`、`?`、`[` 按字面处理，例如 `run[1]` 不会选中旁边的 `run1`；只有追加的 runs 层级模式参与通配。它传入三层根：

| 根目录 | 直接子项，即清理单位 |
| --- | --- |
| `RUNS_ROOT` | 项目目录，或迁移期间的旧平铺项 |
| `RUNS_ROOT/-*` | 一件事的目录 |
| `RUNS_ROOT/-*/xy-goal-inbox` | 单个会话的 inbox |

三层要一起配置，最外层根决定共用的锁和回收目录。第二层只匹配以 `-` 开头的项目目录；旧平铺项不会被拆开删。项目内所有内容都过期时可以整项删除；空项目也须自身 30 天未变更才删除。

以下本机根目录直接写在 `systemd/runs-cleaner.service`，不在 Python 代码中：

- `/data/user/xy/agent-scratch`
- `/data/user/xy/evidence`
- `%h/.claude/xy-workflow`
- `%h/.codex-subagent*/sessions/*/*`，清理单位为按日的会话目录。

前三个退役位置完成迁移、清空后，从 service 中去掉。账号目录中的 `tasks/`、`reports/`、`logs/`、`brief-*.md` 由 runner 迁移，不另配根；deepseek 的配置目录、harness 的持久记忆不纳入。

复制两个用户单元；交付的 service 使用 `--mode list`：

```bash
install -D -m 644 "$HOME/xy-skills/xy-runs/systemd/runs-cleaner.service" "$HOME/.config/systemd/user/runs-cleaner.service"
install -D -m 644 "$HOME/xy-skills/xy-runs/systemd/runs-cleaner.timer" "$HOME/.config/systemd/user/runs-cleaner.timer"
systemctl --user daemon-reload
```

## 首删

清单生成和删除都使用已安装的同一个 service。先编辑它：

```bash
systemctl --user edit --full runs-cleaner.service
```

保持 `ExecStart` 的 `--mode list`，临时把标准输出改为清单文件，标准错误仍进 journal：

```ini
StandardOutput=truncate:/home/xy/runs-cleaner-first-delete.jsonl
StandardError=journal
```

把示例文件换成实际账号下的绝对路径，然后生成清单：

```bash
systemctl --user daemon-reload
systemctl --user start runs-cleaner.service
journalctl --user -u runs-cleaner.service
```

确认启动成功，并核对 journal 中的根目录警告。核对清单中没有在跑任务的目录和刚迁入的契约，将这份 JSON Lines 清单通知全部在办会话，让它们认领并把需长期保留的内容移到合适位置，等待 3 天。通知后保留同一份清单；`truncate:` 会在每次启动时覆盖文件。清单里的项后来有更新或有人使用，会在删除时保留。

3 天后，再编辑同一个 service：

```bash
systemctl --user edit --full runs-cleaner.service
```

先把 `StandardOutput` 改回 `journal`，避免首删启动时截断正在读的清单。再把 `ExecStart` 中的 `--mode list` 改为 `--mode delete-listed --listed "/home/xy/runs-cleaner-first-delete.jsonl"`，清单路径按实际账号填写绝对路径，其余参数保留。然后执行一次首删并查看结果：

```bash
systemctl --user daemon-reload
systemctl --user start runs-cleaner.service
journalctl --user -u runs-cleaner.service
```

确认首删结果后，再用 `systemctl --user edit --full runs-cleaner.service` 把整个 `--mode delete-listed --listed "清单路径"` 替换为 `--mode delete`，随后开启每天一次的定时清理：

```bash
systemctl --user daemon-reload
systemctl --user enable --now runs-cleaner.timer
systemctl --user list-timers runs-cleaner.timer
```

定时器使用 `OnCalendar=daily`，service 的标准输出、标准错误进入 journal。用户管理器需要持续运行；本机已开启 linger。

## 删除协议与限制

程序在最外层根目录本身取 `flock(LOCK_EX)`，在锁内重新核对根仍是展开时核验过的真实目录，并检查过期和在用，再以唯一名称改名移入该根下的 `.runs-cleaner-trash/`；放锁后删除移入的项。展开后被替换的根会跳过并报告，实际处理使用持有的目录句柄，不进入软链目标。两种删除模式每轮先清理上次残留的回收项。内层根不建回收目录，回收目录不作为候选；`list` 保持它原样。

在用检查覆盖本用户进程的工作目录、根目录、可执行文件、打开的文件与目录句柄、映射文件，以及命令行中的完整路径。命令行路径后须是 `/`、空白或结尾，路径前缀相同不算在用。需要与删除互斥的写入方必须共用最外层根锁；长时间写入时，在锁内打开事项目录句柄后放锁，持有到写完，子进程也要继承句柄。这一握手由写入方实现。

本用户中主动禁止查看的进程，只按可读的命令行判断；如果它隐藏了目录、文件句柄等引用，命令行又不含该项路径，30 天未变更的项仍可能被删。别的账号的进程（包括 root）不检查。程序以本人身份运行，不使用 sudo。

软链只判断链接自身的时间，只删除链接，不遍历目标；不能读取的后代使整项保留。ctime 会使刚迁入的旧文件保留 30 天，访问时间和只读访问不会延长保留期。需要长期保存的内容应放到仓库或已明确的长期位置。
