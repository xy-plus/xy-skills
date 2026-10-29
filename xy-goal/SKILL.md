---
name: xy-goal
description: 仅在用户或文档明确要求使用 xy-goal 时调用；只提到它不算要求。
---

# xy-goal

xy-goal 只管五件事：用户原话自动逐字记录，事项与原话对得上，契约能换会话接管，在办的事每半小时核实并推进，关闭前由新开的子代理整体复核。它是通用的长目标工具。

前提与 hook 配置见 [README.md](README.md)。用户消息和选择题回答由 hook 记录、编号，agent 不抄。原话是权威；判据是它的索引，不能悄悄放宽要求。

## 清扫

每份契约自带 T1 清扫。第一次改文件前，记下要改的每个仓库或工作树的 `git rev-parse HEAD` 和 `git status`。收尾时列出本会话改过的文件：以自己做过的 Edit、Write、commit 为准，基线只用来缩小范围，拿不准就问用户。改过：按 xy-handoff 交出去一个清扫任务，回复「要」；它按 xy-review 清扫审，它自己的改动不再派清扫；收到验收通过再 accept。没改过：直接 accept。

## 契约与命令

契约文件放在一件事的工具私有子目录 `$RUNS_ROOT/<项目目录名>/<YYYYMMDD-HHMMSS>-<slug>/xy-goal/`：

| 文件 | 内容 |
|---|---|
| `goal.md` | 目标、会话 id、claude 进程号 |
| `quotes/1.md` … | 一条原话一个文件，编号无前导零，写完不改 |
| `tasks/T1.md` … | 开放事项：标题、判据、状态、指针 |
| `done/T1.md` … | 原事项字段加验收结论或取消依据 |
| `links.md` | 全契约唯一映射：`1 T1` 或 `1 不必跟进 原因`，一行一条 |

一条原话可挂多个事项，一个事项可挂多条原话；出处与去向都从 `links.md` 算。每个开放事项必须有出处，T1 是契约创建时原子种下的固定「清扫」事项，是唯一例外；每条原话要么挂到事项，要么标明不必跟进。`open` 成功后先 `show` 看一遍开放事项，T1 的判据是「按 xy-goal 的「清扫」一节做」。

写入只经脚本，不手改契约；读取直接用 Read 或 cat。`--root` 必须显式提供，且指向事先创建的目录（首次安装步骤见 README）。根目录不存在或不是目录就报错退出，脚本不会自动创建。

下文 `<本 skill 目录>` 是这份 SKILL.md 所在的目录：用 Skill 工具加载时就是 Claude Code 给出的 Base directory，经 CLAUDE.md 引入时就是引入路径所在的目录。

```bash
python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" open <slug> '<目标>' --claude-pid "$PPID"
python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" show
python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" check
```

`open`、`adopt` 必须在 Claude Code 的 Bash 工具里运行，显式传 `--claude-pid "$PPID"`。该 Bash 的 `$PPID` 就是本会话 claude 进程号，可在同一个 Bash 里用 `echo "$PPID"` 核对；让它直接展开。脚本核对传入的正整数 PID 是本次调用进程的祖先，否则拒绝并说明调用方式，不能抄别的会话的 PID 来接管。

脚本只在 `$RUNS_ROOT/-*/*/xy-goal/goal.md` 中按会话 id 找契约，回收目录不参与查找。项目目录名直接取会话记录的父目录名：hook 定位 inbox 时用输入的 `transcript_path`；`open` 建契约、新会话 `adopt` 领取 inbox 时，用 `CLAUDE_CODE_SESSION_ID` 在 `~/.claude/projects/*/<会话号>.jsonl` 中找，必须恰好一份，否则报错、不建目录。已有契约的命令及同会话 `adopt` 不再查会话记录。一个会话一份契约。`open` 要求本会话还没有契约；`adopt` 可新会话接管，也可同会话恢复刷新 PID（见「换会话接管」）；其余 agent 命令要求恰好一份。本会话重复绑定会报错，不猜。标题等字段的值用 JSON 字符串保存，以保留换行和引号，按物理 LF 分行；原话正文不经过字段编码。

尚未开契约时，hook 把消息原文暂记在 `$RUNS_ROOT/<项目目录名>/xy-goal-inbox/<会话 id>/`，不存分类副本；`open`、新会话 `adopt` 领走全部原话，按原顺序接着编号，领取时统一计算自动分类。触发开契约的那条消息也在其中。子代理输入带 `agent_id`，两个 hook 都跳过。主代理的输入每条都记，同一 `prompt_id` 也不去重：中途插话可能与该轮共用这个 id。

与本契约定时提示逐字一致的输入，以及首尾为 `<task-notification>`、`</task-notification>` 且带齐 `<task-id>`、`<status>`、`<summary>` 成对子标签的完整后台通知，原文保留并自动标为不必跟进。不能只凭「本次契约」或一个标签就排除要求；来源字段不足以辨认机器输入，用户完整粘贴通知也会命中，所以关闭前必须复核自动标记。其他会话发来的消息照记，由 agent 处理。

hook 成功时 stdout、stderr 都为空；运行起来后的失败输出一行 JSON、退出 0，错误全文经 `hookSpecificOutput.additionalContext` 进入模型上下文，stderr 为空。`hook-prompt` 的事件名是 `UserPromptSubmit`，`hook-answer` 是 `PostToolUse`。提示以「请优先修复本会话契约或 hook 问题，再做别的。」开头，随后按最终文件状态说明记录结果，末尾附原始错误：

- 「这次输入没有记入契约。」：没有完整原话，也没有残缺文件。
- 「这次输入已记为原话 N（<路径>）。」：原话已完整写入，后续校验或分类失败；按编号处理待办原话。
- 「这次输入的原话文件可能残缺（<路径>），先删掉它。」：写入失败且残缺文件删不掉，先清理提示中的文件。

本会话契约坏了，包括 tasks/、done/ 目录缺失或读不出，hook 仍逐字记录原话；新编号跳过 quotes/ 和 links.md 行首所有已出现的号，避免悬空映射认领新输入。完整原话写入后不再删除；坏契约期间不自动分类，修复后按未处理原话处理，机器输入可用 `dismiss` 标掉。输入不合法、根或项目目录有问题、重复归属、quotes/ 或 links.md 读不出、原话写不完时不记录。

## 维护事项

所有参数都显式给出；状态只认 `在办` 或 `等用户：<待定的事>`，还没有指针就写 `无`。脚本分配 T 号，不复用已关闭编号。

```bash
python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" add --title '<标题>' --criteria '<可核实的完成条件>' --source <N> <M> --state '在办' --pointer '无'
python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" update <T> --title '<新标题>'
python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" update <T> --criteria '<新判据>' --source <N>
python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" update <T> --state '等用户：<待定的事>'
python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" update <T> --pointer '<新指针>'
python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" update <T> --source <N> <M>
python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" relink [<T> --source <N> <M>]
python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" dismiss <N> --reason '<不必跟进的原因>'
python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" accept <T> --verdict '<按判据核过的结论，如命令和退出码、审查结论>'
python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" cancel <T> --quote <N>
```

`update` 至少要改一个字段或追加出处；改判据必须同时带原话；`--source` 只追加出处。出处和取消依据必须引用已存在的原话。取消必须有用户原话依据，未完成事项不得删除；判据按最新原话改，不能自行降要求。不带可跟踪要求的询问，答过后用 `dismiss` 标明一行原因，不必硬建事项；原因含 Unicode 分行符也会写前拒绝。

`relink` 只用于当前校验失败、links.md 与 quotes/ 都读得出的契约；健康契约挂出处用 `update`。不带参数只清理坏行；事项与 `--source` 必须同时给，事项必须是编号合法的开放事项，原话编号须先查看 quotes/、全部存在。它逐条打印丢掉的行与原因，保留合格映射并添加给定出处，只写 links.md。写后整体校验，通过时打印「修复完成：<路径>」并退出 0；仍有错误时向 stderr 报告、退出 1，已写修复保留，可接着对下一个事项用 `relink`。

`show` 只列目标、每个开放事项四行（标题、判据、出处、状态与指针）、未处理原话编号，不重复输出全部原话和已关闭事项。每项四行超过 300 字、清单全文超过 5000 字会在末尾提示压短，这是用户要求的，保留；它是软约束，不拒绝写入。`check` 只检查本契约，逐条报未处理原话；通过退出 0，有问题退出 1，直接看输出，不接管道截取。

## 定时自查

`open`、`adopt` 都会输出下面这一行定时提示，照抄进 `CronCreate(cron="*/30 * * * *", recurring=true, prompt=<输出原文>)`，不要加换行或改字。`CronList` 按其中的契约绝对路径核对只有一个本契约定时器，不把编号存进契约：

```text
本次契约：<契约目录绝对路径>。按 xy-goal 的「定时自查」一节做
```

已有定时器在 7 天内仍可能发旧提示；旧文字与新提示不匹配，不会自动标为不必跟进，需手动用 `dismiss` 标掉。

提示只指向本节，不复制步骤，规则更新后下一轮就生效。每半小时依次做：

1. 跑 `check`，处理还没处理的原话：挂到事项或标明不必跟进。
2. 逐项核真实状态：看文件、跑命令，不凭印象；对照出处与判据，能推进的当场推进，按判据核完的 `accept`。
3. 回看自己的步骤：哪步多余、哪里空等、哪里返工；多余的砍掉，能提速的当场改。

需要说明阻塞时写进判据或指针，定时自查直接核真实状态。

只剩 `等用户` 时，先汇报还剩哪几项、各在等用户什么，再 `CronDelete` 暂停提醒。契约和事项原样保留，这是暂停，不是关闭，不走整体完成复核；用户回话后处理新原话，再按同一提示重建定时器。定时器 7 天过期后也重建并用 `CronList` 核对。

## 换会话接管

```bash
python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" adopt <契约目录绝对路径> --claude-pid "$PPID"
```

`adopt` 只接受 `$RUNS_ROOT/<以 - 开头的项目目录>/<一件事>/xy-goal` 这一层的契约路径。新会话必须还没有契约，原会话的 claude 进程必须已不在；还在就先在原会话 `CronDelete` 并结束它，不能抢接。脚本改会话 id 与进程号、领走新会话 inbox，输出定时提示；照抄重建，再 `CronList` 核对只有一个。

同一会话恢复（`--resume` 沿用 session id，PID 变化）也先对自己的契约运行上述 `adopt`。这时只刷新 PID，不要求原 PID 退出，不改原话、事项和映射，防止已恢复的活会话被当成无主接管；再按输出核对或重建本契约定时器。

## 验收与关闭

事项按自己的判据核，结论写进 `accept --verdict`。验收要的证据在删除之前核完，`done/` 留结论；过程与证据放在所属仓库或报告里，不另建契约笔记、证据目录或副本。

关闭顺序只有一条：

1. 没有开放事项、没有未处理原话后，先回溯本会话里说过稍后做、顺手发现未记录、用户中途改口的事项，补进来并做完。
2. 开一个新开的子代理，交给它契约目录，让它对照**全部原话与全部事项**（`tasks/`、`done/`）：每条要求是否都已做完，所有「不必跟进」是否真的不带要求，已关闭事项的结论是否站得住。
3. 它指出未完成的，补成事项、做完再查；它确认全部完成后，`CronList` 找出本契约定时器，`CronDelete` 并确认成功，汇报逐项状态与验收证据。
4. 最后运行 `python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" close`，只删除本契约的 `xy-goal/goal.md`，解除会话绑定；其余文件留在原处，由定期清理统一处理。

## 契约生命周期

契约所在的事项目录按 `xy-runs` 的规则定期清理。在办契约每半小时的定时提示会写进原话，不会过期；暂停提醒的契约、会话没走 `close` 就消失的契约，30 天没动静也会被删，不加特例。隔一个多月回来时，可能需要重新开契约、重新说要求。

`close` 后 hook 回到项目下的 `xy-goal-inbox`，同会话可以再开契约。`xy-goal-inbox` 整体及其中各会话的 inbox 也按统一规则清理。

`open`、新会话 `adopt` 主体已提交后，临时输入删除失败只警告，仍返回 0、输出定时提示；它们返回 1 时可重试，不会重复领取。
