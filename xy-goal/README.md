# xy-goal

用目录契约管理长目标：hook 自动记录用户原话，脚本维护原话与事项的唯一映射，每半小时核实并推进，关闭前由新开的子代理整体复核。输入 `/xy-goal` 或明确要求「用 xy-goal」才启用；只提到它不算启用。执行规矩见 [SKILL.md](SKILL.md)。

## 前提与 hook 配置

- Claude Code 的 `UserPromptSubmit`、`PostToolUse` hook，以及 `CronCreate`、`CronList`、`CronDelete`。
- Linux、Python 3.10 及以上；脚本用目录文件锁协调并发。
- 把本目录装到 `~/.claude/skills/xy-goal`；命令在 Claude Code 的 Bash 中运行，环境变量 `CLAUDE_CODE_SESSION_ID` 必须存在。定时器由 agent 管，脚本不调用定时工具。

安装后，将下面两项合并到 `~/.claude/settings.json` 的 `hooks`，保留已有配置；没有配置 hook 就不能自动记录原话：

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"$HOME/.claude/skills/xy-goal/goal.py\" --root \"$HOME/.claude/xy-goal\" hook-prompt"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "AskUserQuestion",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"$HOME/.claude/skills/xy-goal/goal.py\" --root \"$HOME/.claude/xy-goal\" hook-answer"
          }
        ]
      }
    ]
  }
}
```

`hook-prompt` 从标准输入的官方 hook JSON 读取 `session_id`、`prompt`，将 `prompt` 逐字写入文件，不解析会话记录。`hook-answer` 要求 `tool_name` 为 `AskUserQuestion`，把 `tool_input` 与 `tool_response` 的原值存成一个 JSON 对象；不猜回答结构，不改写成「问／用户选」。回答格式变了也能完整留下。

带 `agent_id` 的输入来自子代理，跳过；主代理每条输入都记，包括中途插话和别的会话送来的消息。同一 `prompt_id` 的两条消息分别编号，因为中途插话可能与该轮共用此 id。编号取最大号加一，并用独占创建防止覆盖；目录锁让并发记录、领取 inbox、接管和映射更新不会交错。

错误会写到 stderr 并退出 1；Claude Code 将其作为非阻塞错误，消息仍会送达，不能把错误提示当作记录成功。成功 hook 不向会话输出正文。

## 开契约与定时器

```bash
python3 ~/.claude/skills/xy-goal/goal.py --root ~/.claude/xy-goal open release '交付新版本' --claude-pid "$PPID"
```

`open`、`adopt`、`migrate` 都必填 `--claude-pid "$PPID"`，在 Claude Code 的 Bash 工具里直接运行。这个 Bash 的 `$PPID` 就是本会话 claude 的进程号，可用同一个 Bash 中的 `echo "$PPID"` 核对；由它展开后传给脚本，不到另开的 shell 或 Python 里重新取父进程号。进程名可能是 `claude.exe`，argv[0] 也可能被改写成 `claude bg-spare` 等，不能用它们判断会话；脚本移除了进程名识别，也不使用未公开保证的 `CLAUDE_PID` 环境变量。缺参数、空值或非正整数会退出 1，并提示正确的 Bash 调用方式。

脚本创建 `~/.claude/xy-goal/<YYYYMMDD-HHMMSS>-release/`，记录目标、会话 id、claude 进程号，并领走本会话 inbox 中的全部原话。未开契约也照常记录，所以开契约前的讨论、触发开契约的消息不会漏。每个会话只有一份契约，几个主题的事项都在里面。

命令输出一行，把输出原文作为 `prompt` 交给 `CronCreate(cron="*/30 * * * *", recurring=true, prompt=...)`：

```text
本次契约：<契约目录绝对路径>。按 `~/.claude/skills/xy-goal/SKILL.md`「定时自查」一节做
```

不改字，不附加换行或自查步骤；这样 hook 能逐字识别本契约定时提示，SKILL.md 更新后下一轮直接读取新步骤。`CronList` 按提示中的契约路径核对只有一个定时器；契约不存定时器编号。每半小时跑 `check`、处理新原话、逐项核真实状态并推进、核完的 `accept`，再检查有没有多余步骤、空等或返工，能提速的当场改。

清单只剩 `等用户` 时，先汇报每项在等用户决定什么，再 `CronDelete` 暂停提醒；保留契约与事项，不运行 `close`。用户回话后重建定时器。定时器 7 天过期后也重建并核对。

## 看清单、处理原话与事项

下列变量只为缩短示例，都必须明确赋值；脚本没有隐式契约根目录：

```bash
goal_script="$HOME/.claude/skills/xy-goal/goal.py"
goal_root="$HOME/.claude/xy-goal"
python3 "$goal_script" --root "$goal_root" show
python3 "$goal_script" --root "$goal_root" check
```

`show` 输出目标、每个开放事项四行，以及还没处理的原话编号；不重复输出全部原话和账本。按编号直接用 Read/cat 读 `quotes/N.md`。每项四行超过 300 字或清单全文超过 5000 字时，末尾提示压短，这是用户要求的软约束，保留；不拒绝写入，也不让 `check` 因字数失败。`check` 只检查本会话契约，逐条报未处理原话；通过退出 0，有问题退出 1。直接看输出，无须接管道截取。

```bash
# 出处原话 1、2 必须已由 hook 记录；状态与指针都显式填写。
python3 "$goal_script" --root "$goal_root" add --title '发布' --criteria '测试通过并完成发布' --source 1 2 --state '在办' --pointer '无'
# 上条输出 T1；各字段分别更新，只认中文字段名：标题、判据、状态、指针。
python3 "$goal_script" --root "$goal_root" update T1 --field 状态 --value '等用户：确认发布日期'
python3 "$goal_script" --root "$goal_root" update T1 --field 指针 --value '/absolute/path/to/report.md'
# 改判据必须引用变更依据；只补出处时不传 --field/--value。
python3 "$goal_script" --root "$goal_root" update T1 --field 判据 --value '连续两轮测试通过并完成发布' --source 3
python3 "$goal_script" --root "$goal_root" update T1 --source 4 5
# 询问已答等不产生跟进工作的消息，写明原因即可，不硬建事项。
python3 "$goal_script" --root "$goal_root" dismiss 6 --reason '问题已答，无后续要求'
# 按判据核过以后记结论；取消必须引用用户原话。
python3 "$goal_script" --root "$goal_root" accept T1 --verdict '两轮测试退出码均为 0，发布检查通过'
python3 "$goal_script" --root "$goal_root" cancel T2 --quote 7
```

上例的原话编号和 T 号需换成本契约中实际存在的编号。一个事项可挂多条原话，一条原话可挂多个事项。`--source` 只追加，不回写原话，也不在事项文件里另存出处。状态只认 `在办`、`等用户：待定的事`，指针还没有就显式写 `无`。验收与取消把原事项移入 `done/`、加结论或取消原话编号，已关闭的 T 号不复用。未完成事项不能删除，不能没有原话依据就改判据。

写命令先完成校验，不合格就报错，不写半截；普通多文件写入遇到 I/O 错误会回滚已改文件。读到损坏的契约文件会指出路径并拒绝，不静默跳过。所有参数无业务缺省值，`open`、`adopt`、`migrate` 要求本会话还没有契约；其余 agent 命令要求恰好一份；会话重复绑定、没有绑定都报错，不猜目标。

## 文件与原话标记

```text
<契约目录>/
  goal.md       目标、session_id、claude_pid
  quotes/1.md   原文，一条一文件，按整数编号，无前导零
  tasks/T1.md   标题、判据、状态、指针
  done/T1.md    关闭前的原内容，加验收结论或取消依据
  links.md      唯一映射：1 T1，或 1 不必跟进 原因
```

契约的写入只经脚本，读取直接用 Read/cat；agent 不手抄原话、不手改标注。字段用 JSON 字符串保存换行、引号，`show` 将字段内换行显示为 `\n`，保持每项四行；原话文件不转义、不增标注，CRLF、空行、用户贴出的「（原话 …）」都照收。

还未开契约的消息在 `inbox/<会话 id>/N.md`。机器输入的临时「不必跟进」标记存在该 inbox 的 `links.md`，领取时随原话重编号后合入契约，随后删除已领取的 inbox。

两种机器输入会保留正文并自动标为不必跟进：本契约的完整定时提示；首尾是 `<task-notification>`、`</task-notification>`，且带齐成对的 `<task-id>`、`<status>`、`<summary>` 子标签的后台通知。只写「本次契约：取消 T1」、缺少子标签的任务通知、别的契约的定时提示，都仍需处理。hook 没有可靠的来源字段，完整粘贴的后台通知也会命中，关闭前须复核所有自动标记。

不把通用规矩自动写入记忆或全局文件：agent 在本次上下文里遵守；重要的通用规矩由用户自己放进规矩文档。也不提供契约笔记、证据副本目录；过程和证据留在所属仓库或报告，验收用的材料在删除前核完，`done/` 记下结论即可。逐项「下次核」、运行者 pid、事项依赖等待链、判据分类和逐项强制独立验收都不再维护。

## 换会话接管

原会话还在时，先在原会话 `CronDelete` 并结束它；新会话没有契约且旧进程已不在，才能接管：

```bash
python3 ~/.claude/skills/xy-goal/goal.py --root ~/.claude/xy-goal adopt /absolute/path/to/contract-directory --claude-pid "$PPID"
```

脚本拒绝抢占活会话，也检查原所有者是否重复绑定；通过后改会话与进程号，领取新会话 inbox、接在原最大编号后，输出定时提示。照抄 `CronCreate`，再 `CronList` 核对只有一个。

## 关闭

1. 验收或有原话依据地取消所有事项，处理所有原话；回溯本会话的「稍后做」、顺手发现但未记下、用户中途改口的事项，补进来并做完。
2. 开一个**新开的子代理**，给它契约目录，对照全部原话和全部事项（`tasks/` 与 `done/`），核每条要求是否全部完成、每条「不必跟进」是否合理、已关闭结论是否站得住。有漏项就补成事项，做完再查。
3. 子代理确认全部完成后，`CronList` 按契约路径找到定时器，`CronDelete` 确认成功，汇报逐项状态与验收证据。
4. 最后删契约：

```bash
python3 ~/.claude/skills/xy-goal/goal.py --root ~/.claude/xy-goal close
```

`close` 在代码里拒绝开放事项或未处理原话；子代理复核、定时器删除和汇报是 agent 的责任，脚本不代做。

## 30 天清理与旧契约迁移

`open` 领取本会话 inbox 后，顺手清理根目录的 `archive/`、`inbox/`：只删每个文件自身修改时间超过 30 天的文件，再删空目录；恰好 30 天不删。父目录再旧，也不整棵删除其中的新文件；不沿软链清理外部目录。过期 inbox 原话对应的临时自动标记也移除，避免阻塞后续记录。契约目录不在清理范围，只经 `close` 删除。旧 archive 按同一规则自然清掉，不另做迁移或副本。

新流程合入后，**各在办会话自己迁自己的旧契约**，不批量迁别人的：

```bash
python3 ~/.claude/skills/xy-goal/goal.py --root ~/.claude/xy-goal migrate /absolute/path/to/old-checklist.md --claude-pid "$PPID"
```

旧清单的 `session` 必须等于当前 claude 进程号，本会话必须尚未绑定新契约。输出的定时提示按前文建半小时定时器，并核对只有一个。转换遵循：

- 开放事项标题、判据和指针保留；`等：用户：…` 变成 `等用户：…`，在跑、等 T 号、待验收都变成 `在办`；丢弃下次核与运行者字段。没有指针写 `无`，待验收前缀里的产物指针会保留。
- 原话去掉顶格标注，编号保留，正文与空行保留；缩进的用户片段仍是正文。本会话 inbox 原话按原顺序接在最大编号后。
- 标注的多 T 号转成唯一映射；`无：原因` 转成 `不必跟进 原因`；`规矩：位置` 转成 `不必跟进 旧规矩落点`，不再维护记忆文件落点。
- 每行旧账本原样放入 `done/TN.md`，不补造判据、状态、验收字段。出处取旧标注和账本正文中的「原话 N」两处；两处都没有的，加一行 `旧记录无出处`。
- 在临时目录转换、逐条重读，核原话数、开放事项数、账本行数及引用；旧开放事项的出处和原话标注必须一致。通过后才发布新目录、删旧三文件；校验失败时旧文件与 inbox 不动。

`migrate` 是过渡入口。**所有在办契约迁完以后**再删除它、旧 `check.py` 及旧测试；现在保留旧检查器供未迁会话继续用。

## 测试

在仓库根目录运行：

```bash
python3 -B -m unittest discover -s xy-goal/tests
```

测试全部用临时根目录，显式注入会话 id 与时钟，进程号经过与 CLI 相同的必填参数解析，不依赖真实家目录。受控子进程设置 `claude.exe` 和三种实际 argv[0] 形状，再经 Bash 展开 `$PPID` 调用 CLI，核对写入的进程号，并检查环境中的 `CLAUDE_PID` 不会被采用。另覆盖所有子命令、拒绝写入、损坏文件、原话字节保真、并发 hook、机器输入严格匹配、inbox 领取、接管、按文件年龄清理，以及旧格式迁移与失败保留。

合入前还需在隔离 Claude Code 会话（`--setting-sources project`，只加载测试目录配置，使用 claude-deepseek）实测：中途插话触发 `UserPromptSubmit`、`AskUserQuestion` 回答、开契约前消息领取，以及原生子代理 hook 带 `agent_id` 被跳过。单元测试不代替这一步；实测用临时根目录。
