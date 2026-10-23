---
name: xy-goal
description: 仅在用户或文档明确要求使用 xy-goal 时调用；只提到它不算要求。
---

# xy-goal

xy-goal 在 goal 的基础上做简单优化，只管五件事：用户原话自动逐字记录，事项与原话对得上，契约能换会话接管，在办的事每半小时核实并推进，关闭前由新开的子代理整体复核。它是通用的长目标工具。

前提与 hook 配置见 [README.md](README.md)。用户消息和选择题回答由 hook 记录、编号，agent 不抄。原话是权威；判据是它的索引，不能悄悄放宽要求。

## 契约与命令

一份契约一个目录 `~/.claude/xy-goal/<YYYYMMDD-HHMMSS>-<slug>/`：

| 文件 | 内容 |
|---|---|
| `goal.md` | 目标、会话 id、claude 进程号 |
| `quotes/1.md` … | 一条原话一个文件，编号无前导零，写完不改 |
| `tasks/T1.md` … | 开放事项：标题、判据、状态、指针 |
| `done/T1.md` … | 原事项加验收结论或取消依据；迁入的旧账本只保留原行 |
| `links.md` | 全契约唯一映射：`1 T1` 或 `1 不必跟进 原因`，一行一条 |

一条原话可挂多个事项，一个事项可挂多条原话；出处与去向都从 `links.md` 算。每个开放事项必须有出处，每条原话要么挂到事项，要么标明不必跟进。通用规矩由 agent 在自己的上下文里遵守，不为落点自动写记忆或改全局规矩。

写入只经脚本，不手改契约；读取直接用 Read 或 cat。脚本根目录必须显式提供：

```bash
python3 ~/.claude/skills/xy-goal/goal.py --root ~/.claude/xy-goal open <slug> '<目标>' --claude-pid "$PPID"
python3 ~/.claude/skills/xy-goal/goal.py --root ~/.claude/xy-goal show
python3 ~/.claude/skills/xy-goal/goal.py --root ~/.claude/xy-goal check
```

`open`、`adopt`、`migrate` 必须在 Claude Code 的 Bash 工具里运行，显式传 `--claude-pid "$PPID"`。该 Bash 的 `$PPID` 就是本会话 claude 进程号，可在同一个 Bash 里用 `echo "$PPID"` 核对；让它直接展开。脚本核对传入的正整数 PID 是本次调用进程的祖先，否则拒绝并说明调用方式，不能抄别的会话的 PID 来迁移或接管。

脚本从 `CLAUDE_CODE_SESSION_ID` 定位本会话的契约。一个会话一份契约，几个主题的事项都放里面。`open`、`migrate` 要求本会话还没有契约；`adopt` 可新会话接管，也可同会话恢复刷新 PID（见「换会话接管」）；其余 agent 命令要求恰好一份。本会话重复绑定会报错，不猜。标题等字段的值用 JSON 字符串保存，以保留换行和引号，按物理 LF 分行；原话正文不经过字段编码。

尚未开契约时，hook 把消息原文暂记在 `inbox/<会话 id>/`，不存分类副本；`open`、新会话 `adopt` 领走全部原话，按原顺序接着编号，领取时统一计算自动分类。触发开契约的那条消息也在其中。子代理输入带 `agent_id`，两个 hook 都跳过。主代理的输入每条都记，同一 `prompt_id` 也不去重：中途插话可能与该轮共用这个 id。

与本契约定时提示逐字一致的输入，以及首尾为 `<task-notification>`、`</task-notification>` 且带齐 `<task-id>`、`<status>`、`<summary>` 成对子标签的完整后台通知，原文保留并自动标为不必跟进。不能只凭「本次契约」或一个标签就排除要求；来源字段不足以辨认机器输入，用户完整粘贴通知也会命中，所以关闭前必须复核自动标记。其他会话发来的消息照记，由 agent 处理。hook 错误写到 stderr、退出 1；消息照常送达，看到提示后要处理记录失败，不能把它当作已记下。

## 维护事项

所有参数都显式给出；状态只认 `在办` 或 `等用户：<待定的事>`，还没有指针就写 `无`。脚本分配 T 号，不复用已关闭编号。

```bash
python3 ~/.claude/skills/xy-goal/goal.py --root ~/.claude/xy-goal add --title '<标题>' --criteria '<可核实的完成条件>' --source <N> <M> --state '在办' --pointer '无'
python3 ~/.claude/skills/xy-goal/goal.py --root ~/.claude/xy-goal update <T> --field 标题 --value '<新标题>'
python3 ~/.claude/skills/xy-goal/goal.py --root ~/.claude/xy-goal update <T> --field 判据 --value '<新判据>' --source <N>
python3 ~/.claude/skills/xy-goal/goal.py --root ~/.claude/xy-goal update <T> --source <N> <M>
python3 ~/.claude/skills/xy-goal/goal.py --root ~/.claude/xy-goal dismiss <N> --reason '<不必跟进的原因>'
python3 ~/.claude/skills/xy-goal/goal.py --root ~/.claude/xy-goal accept <T> --verdict '<按判据核过的结论，如命令和退出码、审查结论>'
python3 ~/.claude/skills/xy-goal/goal.py --root ~/.claude/xy-goal cancel <T> --quote <N>
```

`update --field` 只认 `标题`、`判据`、`状态`、`指针`。改判据必须同时带原话；`--source` 只追加出处。出处和取消依据必须引用已存在的原话。取消必须有用户原话依据，未完成事项不得删除；判据按最新原话改，不能自行降要求。不带可跟踪要求的询问，答过后用 `dismiss` 标明一行原因，不必硬建事项；原因含 Unicode 分行符也会写前拒绝。

`show` 只列目标、每个开放事项四行（标题、判据、出处、状态与指针）、未处理原话编号，不重复输出全部原话和已关闭事项。每项四行超过 300 字、清单全文超过 5000 字会在末尾提示压短，这是用户要求的，保留；它是软约束，不拒绝写入。`check` 只检查本契约，逐条报未处理原话；通过退出 0，有问题退出 1，直接看输出，不接管道截取。

## 定时自查

`open`、`adopt`、`migrate` 都会输出下面这一行定时提示，照抄进 `CronCreate(cron="*/30 * * * *", recurring=true, prompt=<输出原文>)`，不要加换行或改字。`CronList` 按其中的契约绝对路径核对只有一个本契约定时器，不把编号存进契约：

```text
本次契约：<契约目录绝对路径>。按 `~/.claude/skills/xy-goal/SKILL.md`「定时自查」一节做
```

提示只指向本节，不复制步骤，规则更新后下一轮就生效。每半小时依次做：

1. 跑 `check`，处理还没处理的原话：挂到事项或标明不必跟进。
2. 逐项核真实状态：看文件、跑命令，不靠记忆；对照出处与判据，能推进的当场推进，按判据核完的 `accept`。
3. 回看自己的步骤：哪步多余、哪里空等、哪里返工；多余的砍掉，能提速的当场改。

不维护逐项「下次核」、推进者 pid 或事项间等待链。需要说明阻塞时写进判据或指针，定时自查直接核真实状态。

只剩 `等用户` 时，先汇报还剩哪几项、各在等用户什么，再 `CronDelete` 暂停提醒。契约和事项原样保留，这是暂停，不是关闭，不走整体完成复核；用户回话后处理新原话，再按同一提示重建定时器。定时器 7 天过期后也重建并用 `CronList` 核对。

## 换会话接管

```bash
python3 ~/.claude/skills/xy-goal/goal.py --root ~/.claude/xy-goal adopt <契约目录绝对路径> --claude-pid "$PPID"
```

新会话必须还没有契约，原会话的 claude 进程必须已不在；还在就先在原会话 `CronDelete` 并结束它，不能抢接。脚本改会话 id 与进程号、领走新会话 inbox，输出定时提示；照抄重建，再 `CronList` 核对只有一个。

同一会话恢复（`--resume` 沿用 session id，PID 变化）也先对自己的契约运行上述 `adopt`。这时只刷新 PID，不要求原 PID 退出，不改原话、事项和映射，防止已恢复的活会话被当成无主接管；再按输出核对或重建本契约定时器。

## 验收与关闭

事项按自己的判据核，结论写进 `accept --verdict`。验收要的证据在删除之前核完，`done/` 留结论；过程与证据放在所属仓库或报告里，不另建契约笔记、证据目录或副本。

关闭顺序只有一条：

1. 没有开放事项、没有未处理原话后，先回溯本会话里说过稍后做、顺手发现未记录、用户中途改口的事项，补进来并做完。
2. 开一个新开的子代理，交给它契约目录，让它对照**全部原话与全部事项**（`tasks/`、`done/`）：每条要求是否都已做完，所有「不必跟进」是否真的不带要求，已关闭事项的结论是否站得住。
3. 它指出未完成的，补成事项、做完再查；它确认全部完成后，`CronList` 找出本契约定时器，`CronDelete` 并确认成功，汇报逐项状态与验收证据。
4. 最后运行 `python3 ~/.claude/skills/xy-goal/goal.py --root ~/.claude/xy-goal close`，删除本契约目录。

`close` 在代码里拒绝有开放事项或未处理原话的契约；整体复核、定时器删除和汇报由 agent 执行，脚本不调用定时工具。

## 清理与迁移

`open` 顺手清理 `archive/`、`inbox/` 中修改时间超过 30 天的文件，再删空目录。按文件各自的时间算，不因父目录超期就删其中的新文件，不沿软链走出清理范围。契约目录不在清理范围，只经 `close` 删除。旧 `archive/` 按同一规则自然清掉，不另做归档迁移。

主体已提交后的清理错误只警告，仍返回 0、输出定时提示；返回 1 的操作可重试，不会重复领取。残留路径与提交边界见 README 的[失败与清理](README.md#失败与清理)。

新流程合入后，各在办会话自己运行：

```bash
python3 ~/.claude/skills/xy-goal/goal.py --root ~/.claude/xy-goal migrate <自己的旧清单绝对路径> --claude-pid "$PPID"
```

旧清单的 `session` 必须等于经祖先核对的本次 claude 进程号。已知旧头的「笔记：…」行丢弃，不读取或复制笔记。脚本转换旧三文件、领取本会话 inbox，校验原话数、开放事项数、账本行数和出处；校验或提交前失败保留输入，可重试，已提交后的清理失败只警告。照输出按本节重建半小时定时器。所有在办契约迁完后，再删 `migrate`、旧 `check.py` 及其测试，不保留长期兼容入口。转换细节与测试命令见 README。
