---
name: xy-goal
description: 仅在用户或文档明确要求使用 xy-goal 时调用；只提到它不算要求。
---

# xy-goal

xy-goal 只管五件事：用户原话自动逐字记录，事项与原话对得上，契约能换会话接管，在办的事每半小时核实并推进，关闭前由新开的子代理整体复核。它是通用的长目标工具。

前提与 hook 配置见 [README.md](README.md)。用户消息和选择题回答由 hook 记录、编号，agent 不抄。原话是权威；判据是它的索引，不能悄悄放宽要求。

## 契约与命令

契约文件放在一件事的工具私有子目录 `$RUNS_ROOT/<项目目录名>/<YYYYMMDD-HHMMSS>-<slug>/xy-goal/`：

| 文件 | 内容 |
|---|---|
| `goal.md` | 目标、会话 id、claude 进程号 |
| `quotes/1.md` … | 一条原话一个文件，编号无前导零，写完不改 |
| `tasks/T1.md` … | 开放事项：标题、判据、状态、指针 |
| `done/T1.md` … | 原事项字段加验收结论或取消依据 |
| `links.md` | 全契约唯一映射：`1 T1` 或 `1 不必跟进 原因`，一行一条 |

一条原话可挂多个事项，一个事项可挂多条原话；出处与去向都从 `links.md` 算。每个开放事项都必须有出处。原话挂上事项后，「不必跟进」作废。

写入只经脚本，不手改契约；读取直接用 Read 或 cat。`--root` 必须显式提供，且指向事先创建的目录（首次安装步骤见 README）。根目录不存在或不是目录就报错退出，脚本不会自动创建。

下文 `<本 skill 目录>` 是这份 SKILL.md 所在的目录：用 Skill 工具加载时就是 Claude Code 给出的 Base directory，经 CLAUDE.md 引入时就是引入路径所在的目录。

```bash
python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" open <slug> '<目标>' --claude-pid "$PPID"
python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" show
python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" check
```

`open`、`adopt` 必须在 Claude Code 的 Bash 工具里运行，显式传 `--claude-pid "$PPID"`。该 Bash 的 `$PPID` 就是本会话 claude 进程号，可在同一个 Bash 里用 `echo "$PPID"` 核对；让它直接展开。脚本核对传入的正整数 PID 是本次调用进程的祖先，否则拒绝并说明调用方式，不能抄别的会话的 PID 来接管。

`open`、`adopt` 成功后输出契约绝对路径，用它登记事项指针并告诉每个子代理契约目录和路径；`show` 不输出契约路径。

脚本只在 `$RUNS_ROOT/-*/*/xy-goal/goal.md` 中按会话 id 查契约，回收目录不参与查找。项目目录名取会话记录的父目录名：hook 定位 inbox 时用输入的 `transcript_path`；`open` 建契约和新会话 `adopt` 领 inbox 时，用 `CLAUDE_CODE_SESSION_ID` 在 `~/.claude/projects/*/<会话号>.jsonl` 中查找。必须恰好找到一份，否则报错且不建目录。已有契约的命令及同会话 `adopt` 不查会话记录。

每个会话只能有一份契约。`open` 要求本会话还没有契约；`adopt` 可由新会话接管，也可在同会话恢复时刷新 PID（见「换会话接管」）；其余 agent 命令要求恰好一份。重复绑定就报错，不猜。标题等字段用 JSON 字符串保存，保留换行和引号，并按物理 LF 分行；原话正文不编码。

尚未开契约时，hook 把消息原文暂记在 `$RUNS_ROOT/<项目目录名>/xy-goal-inbox/<会话 id>/`，不存分类副本；`open`、新会话 `adopt` 领走全部原话，按原顺序接着编号，领取时统一计算自动分类。触发开契约的那条消息也在其中。子代理输入带 `agent_id`，两个 hook 都跳过。主代理的输入每条都记，同一 `prompt_id` 也不去重：中途插话可能与该轮共用这个 id。

与本契约定时提示逐字一致的输入，以及首尾为 `<task-notification>`、`</task-notification>` 且带齐 `<task-id>`、`<status>`、`<summary>` 成对子标签的完整后台通知，原文保留并自动标为不必跟进。不能只凭「本次契约」或一个标签就排除要求；来源字段不足以辨认机器输入，用户完整粘贴通知也会命中，所以关闭前必须复核自动标记。其他会话发来的消息照记，由 agent 处理。

`hook-prompt`、`hook-answer` 成功时 stdout、stderr 都为空；运行起来后的失败输出一行 JSON、退出 0，错误全文经 `hookSpecificOutput.additionalContext` 进入模型上下文，stderr 为空。`hook-prompt` 的事件名是 `UserPromptSubmit`，`hook-answer` 是 `PostToolUse`。提示以「请优先修复本会话契约或 hook 问题，再做别的。」开头，随后按最终文件状态说明记录结果，末尾附原始错误：

- 「这次输入没有记入契约。」：没有完整原话，也没有残缺文件。
- 「这次输入已记为原话 N（<路径>）。」：原话已完整写入，后续校验或分类失败；按编号处理待办原话。
- 「这次输入的原话文件可能残缺（<路径>），先删掉它。」：写入失败且残缺文件删不掉，先清理提示中的文件。

`hook-stop` 挂在 Claude Code 的 `Stop` 事件上。它读取 `session_id`、`stop_hook_active`、`session_crons`，共用根目录锁，只读契约和定时器状态，不写文件。`stop_hook_active` 必须是布尔值；为真时直接退出，不核对定时器。它自己的提示会让 agent 续跑并再次触发 `Stop`；跳过这次可避免 agent 已照做后又被追问，也避免定时工具不可用时不断把 agent 拉回来。`session_crons` 必须是包含 `id`、`schedule`、`prompt`、`recurring` 的数组。

`hook-stop` 只有在需要新建或删除定时器时才输出一行 JSON，事件名为 `Stop`，让 agent 执行其中的调用；没有需要做的事时无输出。失败也输出一行 JSON、退出 0、stderr 为空，提示以「请优先修复本会话契约或 hook 问题，再做别的。这一轮结束时没有核对定时器。原始错误：」开头。hook 本身不能调用 `CronCreate` 或 `CronDelete`，实际挂和删仍由 agent 完成；若一轮以 API 错误结束，会走 `StopFailure` 而不触发 `Stop`，这一轮不核对，需等下一次正常结束的轮次。

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
python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" reopen <T>
```

`update` 至少要改一个字段或追加出处；改判据必须同时带原话；`--source` 只追加出处。出处和取消依据必须引用已存在的原话。取消必须有用户原话依据，未完成事项不得删除；判据按最新原话改，不能自行降要求。不带可跟踪要求的询问，答过后用 `dismiss` 标明一行原因，不必硬建事项；已挂事项的原话不能 `dismiss`，命令会报出事项并拒绝；原因含 Unicode 分行符也会写前拒绝。已关闭事项的结论站不住就用 `reopen` 重开：只收已关闭事项，四个原字段回到开放清单，旧结论丢掉，出处不变，之后照常推进。

`relink` 只用于当前校验失败、links.md 与 quotes/ 都读得出的契约；健康契约挂出处用 `update`。不带参数只清理坏行；事项与 `--source` 必须同时给，事项必须是编号合法的开放事项，原话编号须先查看 quotes/、全部存在。它逐条打印丢掉的行与原因，保留合格映射并添加给定出处，只写 links.md。写后整体校验，通过时打印「修复完成：<路径>」并退出 0；仍有错误时向 stderr 报告、退出 1，已写修复保留，可接着对下一个事项用 `relink`。

`show` 只列目标、每个开放事项四行（标题、判据、出处、状态与指针）、未处理原话编号，不重复输出全部原话和已关闭事项。每项四行超过 300 字、清单全文超过 5000 字会在末尾提示压短，这是用户要求的，保留；它是软约束，不拒绝写入。`check` 只检查本契约，逐条报未处理原话；通过退出 0，有问题退出 1，直接看输出，不接管道截取。

## 定时自查

定时器每半小时发下面这一行提示，不要加换行或改字：

```text
本次契约：<契约目录绝对路径>。按 xy-goal 的「定时自查」一节做
```

已有定时器在 7 天内仍可能发旧提示；旧文字与新提示不匹配，不会自动标为不必跟进，需手动用 `dismiss` 标掉。

`hook-stop` 在每轮正常结束时读取 Stop 输入的 `session_crons`，按当前契约决定定时器该有几个、保留哪个、删除哪个，并给出要照做的 `CronCreate`/`CronDelete` 调用。提示不出来就不动定时器。契约读不通时按需要定时器处理，让提示触发定时自查来暴露问题；暂停要求契约读得通、至少有一个开放事项、所有开放事项都在等用户且没有未处理原话。没有契约时会要求删掉本会话残留的 xy-goal 定时器。`stop_hook_active` 为真时跳过本轮核对，避免自己的提示续跑后反复追问。

定时器只能由 agent 调 `CronCreate`、`CronDelete` 来挂和删，hook 无法执行这两个工具。我们没有用收件套接字发定时消息：消息可能在 agent 正做事时到达并打断当前轮次，还需要常驻发送进程；发送进程失效时会悄悄停止提醒。`asyncRewake` 会把 Stop hook 反馈包装成后台任务通知，hook 会把它记成待处理原话且现有规则不会自动标掉；改分类规则才能消除这点，超出 xy-goal 原话记录边界。

提示只指向本节，不复制步骤，规则更新后下一轮就生效。每半小时依次做：

1. 跑 `check`，处理还没处理的原话：挂到事项或标明不必跟进。
2. 逐项核真实状态：看文件、跑命令，不凭印象；对照出处与判据，能推进的当场推进，按判据核完的 `accept`。
3. 回看自己的步骤：哪步多余、哪里空等、哪里返工；多余的砍掉，能提速的当场改。

需要说明阻塞时写进判据或指针，定时自查直接核真实状态。

只剩 `等用户` 且没有未处理原话时，先汇报还剩哪几项、各在等用户什么，再照 `hook-stop` 给出的调用删掉定时器。契约和事项原样保留，这是暂停，不是关闭，不走整体完成复核。用户回话后处理新原话，是否需要定时器由 `hook-stop` 按当时状态决定。定时器 7 天过期后也由 `hook-stop` 在本轮正常结束时判断是否要重建；若最后一次触发因 API 错误结束，这一轮不会核对，等下一次正常结束时再处理。

## 换会话接管

```bash
python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" adopt <契约目录绝对路径> --claude-pid "$PPID"
```

`adopt` 只接受 `$RUNS_ROOT/<以 - 开头的项目目录>/<一件事>/xy-goal` 这一层的契约路径。新会话必须还没有契约，原会话的 claude 进程必须已不在；还在就先结束原会话，不能抢接。原会话仍持有契约时它自己的 `hook-stop` 会要求保留定时器，先删会被要求重挂。脚本改会话 id 与进程号、领走新会话 inbox，输出契约绝对路径。

同一会话恢复（`--resume` 沿用 session id，PID 变化）也先对自己的契约运行上述 `adopt`。这时只刷新 PID，不要求原 PID 退出，不改原话、事项和映射，防止已恢复的活会话被当成无主接管。Claude Code 会恢复未过期的定时器；是否还需要新建或删除，由 `hook-stop` 在本轮正常结束时核对。

原会话被新会话接管后若又恢复，Claude Code 也会恢复它尚未过期的旧定时器。此时旧会话已没有契约，它第一次正常结束时 `hook-stop` 会要求删除这些定时器；若删除前先到点，漏出的旧提示会进入 inbox，之后若该会话被领进新契约，按「已有定时器在 7 天内仍可能发旧提示」的规则用 `dismiss` 标掉。

## 验收与关闭

事项按自己的判据核，结论写进 `accept --verdict`。验收要的证据在删除之前核完，`done/` 留结论；过程与证据放在所属仓库或报告里，不另建契约笔记、证据目录或副本。

关闭顺序只有一条：

1. 没有开放事项、没有未处理原话后，先回溯本会话里说过稍后做、顺手发现未记录、用户中途改口的事项，补进来并做完。
2. 开一个新开的子代理，交给它契约目录，让它先审**全部原话**本身：对不对、合不合理、前后有没有矛盾、有没有更好的解决办法；再对照全部原话与全部事项（`tasks/`、`done/`）查：每条要求是否都已做完，所有「不必跟进」是否真的不带要求，已关闭事项的结论是否站得住。
3. 它指出的原话问题，站得住的补成事项，状态写 `等用户：<要定的事和建议>`；站不住的连同理由写进汇报。它指出未完成的，补成事项、做完再查；只有确认全部完成后才关闭。
4. 确认全部完成后先运行 `python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" close`，只删除本契约的 `xy-goal/goal.md`，解除会话绑定；其余文件留在原处，由定期清理统一处理。
5. 再用 `CronList` 找出提示含本契约绝对路径的定时器，逐个 `CronDelete` 并确认成功，最后汇报逐项状态与验收证据。这里仍手动删定时器，是为了让汇报成为本轮最后一条回复：若留给 `hook-stop`，它只能在汇报写完、轮次结束后才要求删除，会让 agent 续跑，汇报就不再是最后回复；漏删的情况由 hook 兜底。

## 契约生命周期

契约所在的事项目录按 `xy-runs` 的规则定期清理。在办契约每半小时的定时提示会写进原话，不会过期；暂停提醒的契约、会话没走 `close` 就消失的契约，30 天没动静也会被删，不加特例。隔一个多月回来时，可能需要重新开契约、重新说要求。

`close` 后 hook 回到项目下的 `xy-goal-inbox`，同会话可以再开契约。`xy-goal-inbox` 整体及其中各会话的 inbox 也按统一规则清理。

`open`、新会话 `adopt` 主体已提交后，临时输入删除失败只警告，仍返回 0、输出契约绝对路径；它们返回 1 时可重试，不会重复领取。这个路径用于登记事项指针并交给子代理；`show` 不输出契约路径。
