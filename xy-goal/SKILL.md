---
description: 仅在用户或文档明确要求使用 xy-goal 时调用；只提到它不算要求。
---

# xy-goal

xy-goal 只管五件事：自动逐字记录用户原话；每项挂用户原话，作者发起的事项可另附证据；契约可换会话接管；每半小时核实并推进在办事项；关闭前由新开的子代理整体复核。它是通用的长目标工具。

前提与 hook 配置见 [README.md](README.md)。hook 自动记录并编号用户消息和选择题回答，agent 不抄。原话为准，判据只作索引，不能悄悄放宽。

## 契约与命令

契约文件位于一件事的工具私有子目录 `$RUNS_ROOT/<项目目录名>/<YYYYMMDD-HHMMSS>-<slug>/xy-goal/`：

| 文件 | 内容 |
|---|---|
| `goal.md` | 目标、会话 id、claude 进程号 |
| `quotes/1.md` … | 一条原话一个文件，编号无前导零，写完不改 |
| `tasks/T1.md` … | 开放事项：标题、判据、状态、指针；作者发起的事项另有证据 |
| `done/T1.md` … | 原事项字段加验收结论、取消原话或取消原因 |
| `links.md` | 全契约唯一映射：`1 T1` 或 `1 不必跟进 原因`，一行一条 |

原话与事项多对多关联，唯一映射存于 `links.md`。每个开放事项至少挂一条用户原话；作者从原话延伸或调整出的事项也要挂相关原话，并可说明推导、不做的后果、收益和代价。已挂事项的原话不能标「不必跟进」。

契约只经脚本写入，不手改；可用 Read 或 cat 读取。`--root` 必须显式提供并指向预先创建的目录（首次安装见 README）；根不存在或不是目录时报错退出，脚本不创建。

`<本 skill 目录>` 指本文件所在目录：Skill 工具加载时是 Claude Code 给出的 Base directory；经 CLAUDE.md 引入时是引入路径所在目录。

```bash
python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" open <slug> '<目标>' --claude-pid "$PPID"
python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" show
python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" check
```

`open`、`adopt` 必须在 Claude Code 的 Bash 工具里运行并显式传 `--claude-pid "$PPID"`。Bash 的 `$PPID` 就是本会话 claude 进程号；可在同一 Bash 用 `echo "$PPID"` 核对并直接展开。脚本核实该正整数 PID 是本次调用进程的祖先，否则拒绝并说明用法；不能抄其他会话的 PID 接管。

只在 `$RUNS_ROOT/-*/*/xy-goal/goal.md` 中按会话 id 查契约，不查回收目录。项目名取会话记录的父目录：hook 用输入的 `transcript_path` 定位 inbox；`open` 和新会话 `adopt` 用 `CLAUDE_CODE_SESSION_ID` 在 `~/.claude/projects/*/<会话号>.jsonl` 查找。必须恰好一份，否则报错且不建目录；已有契约的命令和同会话 `adopt` 不查记录。

每个会话仅一份契约：`open` 仅限本会话还没有契约；`adopt` 可供新会话接管，或同会话恢复时刷新 PID（见「换会话接管」）；其他 agent 命令须恰好一份。重复绑定报错，不猜。标题等字段用 JSON 字符串保存，保留换行和引号，按物理 LF 分行；原话正文不编码。

未开契约时，hook 将消息原文暂存于 `$RUNS_ROOT/<项目目录名>/xy-goal-inbox/<会话 id>/`，不存分类副本；`open`、新会话 `adopt` 领取全部原话，按原序续编并统一计算自动分类，触发 `open` 的消息也在其中。两个 hook 跳过带 `agent_id` 的子代理输入；主代理每条都记，同一 `prompt_id` 不去重（中途插话可能共用 id）。

与本契约定时提示逐字一致的输入，以及首尾为 `<task-notification>`、`</task-notification>` 且带齐 `<task-id>`、`<status>`、`<summary>` 成对子标签的完整后台通知，原文保留并自动标为不必跟进。不能只凭「本次契约」或一个标签排除要求；来源字段不足以辨认机器输入，用户完整粘贴也会命中，所以关闭前复核自动标记。其他会话消息照记，由 agent 处理。

hook 成功时 stdout、stderr 均为空；运行后失败输出一行 JSON、退出 0，经 `hookSpecificOutput.additionalContext` 将完整错误送入模型，stderr 仍为空。`hook-prompt` 事件名为 `UserPromptSubmit`，`hook-answer` 为 `PostToolUse`。提示以「请优先修复本会话契约或 hook 问题，再做别的。」开头，按最终文件状态说明记录结果，末尾附原始错误：

- 「这次输入没有记入契约。」：没有完整原话，也没有残缺文件。
- 「这次输入已记为原话 N（<路径>）。」：原话已完整写入，但后续校验或分类失败；按编号处理待办原话。
- 「这次输入的原话文件可能残缺（<路径>），先删掉它。」：写入失败且残缺文件删不掉；先清理提示中的文件。

本会话契约损坏（包括 tasks/、done/ 缺失或不可读）时，hook 仍逐字记录原话；新编号跳过 quotes/ 和 links.md 行首出现的编号，避免悬空映射认领新输入。原话完整写入后不再删除；损坏期间不分类，修复后按未处理原话处理，机器输入可用 `dismiss` 标记。输入不合法、根或项目目录有问题、重复归属、quotes/ 或 links.md 不可读、原话未写完时不记录。

## 维护事项

所有参数显式提供；状态只允许 `在办` 或 `等用户：<待定的事>`，无指针写 `无`。脚本分配 T 号，已关闭编号不复用。

```bash
python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" add --title '<标题>' --criteria '<可核实的完成条件>' --source <N> <M> --source-kind '原话直提' --state '在办' --pointer '无'
python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" add --title '<标题>' --criteria '<可核实的完成条件>' --source <N> --source-kind '作者发起' --evidence '<原话如何延伸；不做的后果；做了的收益；代价>' --state '在办' --pointer '无'
python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" update <T> --title '<新标题>'
python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" update <T> --criteria '<新判据>' --source <N>
python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" update <T> --state '等用户：<待定的事>'
python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" update <T> --pointer '<新指针>'
python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" update <T> --source <N> <M>
python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" relink [<T> --source <N> <M>]
python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" dismiss <N> --reason '<不必跟进的原因>'
python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" accept <T> --verdict '<按判据核过的结论，如命令和退出码、审查结论>'
python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" cancel <T> --quote <N>
python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" cancel <T> --reason '<取消原因>'
python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" reopen <T>
```

`add` 必须给 `--source`、`--source-kind`；后者只能是「原话直提」或「作者发起」，无缺省值。原话直提不写 `--evidence`；作者发起须给非空证据，说明原话推导、不做的后果、收益和代价。证据存事项的 `evidence` 字段，不写入 `links.md`；`show` 的出处行同时显示原话与「作者发起：<证据>」。`update --source` 只追加原话，证据不能替代原话。

`update` 至少改一个字段或追加出处；改判据必须同时带原话，`--source` 只追加出处。原话出处和 `cancel --quote` 均须引用已有原话。有作者发起证据的事项可用 `cancel --reason` 写取消原因，即使也挂了原话；无证据则必须用 `cancel --quote`。未完成事项不得删除；判据按最新原话改，不能自行降要求。不带可跟踪要求的询问答过后，用 `dismiss` 标一行原因，不必建事项；已挂事项的原话不能 `dismiss`，命令会拒绝并报出事项；原因含 Unicode 分行符时写前拒绝。已关闭结论站不住时用 `reopen` 重开：仅限已关闭事项，字段回到开放清单，旧结论删除、出处不变，之后照常推进。

`relink` 仅用于当前校验失败且 links.md、quotes/ 都可读的契约；健康契约用 `update` 挂出处。不带参数只清理坏行；事项与 `--source` 必须同时给，事项须为编号合法的开放事项，原话编号须先查 quotes/ 且全部存在。逐条打印丢弃行及原因，保留合格映射并添加给定出处，只写 links.md。写后整体校验：通过时打印「修复完成：<路径>」并退出 0；仍有错误时向 stderr 报告、退出 1，已写修复保留，可继续对下一个事项用 `relink`。

`show` 只列目标、开放事项（每项四行：标题、判据、出处、状态和指针）及未处理原话编号，不列原话全文或已关闭事项。单项四行超 300 字或全文超 5000 字时在末尾提示压短；提示是软约束，不阻止写入。`check` 只查本契约并逐条报未处理原话；通过退出 0，有问题退出 1，直接读输出，不用管道截取。

## 定时自查

`open`、`adopt` 均输出下面的定时提示；原样复制到 `CronCreate(cron="*/30 * * * *", recurring=true, prompt=<输出原文>)`，不加换行或改字。`CronList` 按契约绝对路径确认仅有一个定时器，编号不存入契约：

```text
本次契约：<契约目录绝对路径>。按 xy-goal 的「定时自查」一节做
```

旧定时器 7 天内仍可能发旧提示；因文字与新提示不匹配，不会自动标为不必跟进，须手动用 `dismiss` 标记。

提示只指向本节，不复制步骤；规则更新后下轮生效。每半小时依次：

1. 跑 `check`，处理未处理原话：挂事项或标明不必跟进。
2. 逐项核实真实状态（看文件、跑命令，不凭印象），对照出处和判据；能推进就当场推进，核完按判据 `accept`。
3. 回看自己的步骤：找多余、空等、返工；砍掉多余，能提速就当场改。

阻塞时写入判据或指针；定时自查核实真实状态。

只剩 `等用户` 时，先汇报剩余事项及各自等待内容，再用 `CronDelete` 暂停提醒。契约和事项原样保留，不关闭、不做整体完成复核；用户回话后处理新原话，再按同一提示重建定时器。定时器过期 7 天后也重建并用 `CronList` 核对。

## 换会话接管

```bash
python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" adopt <契约目录绝对路径> --claude-pid "$PPID"
```

`adopt` 只接受 `$RUNS_ROOT/<以 - 开头的项目目录>/<一件事>/xy-goal` 这一层的契约路径。新会话须无契约，原会话 claude 进程须已退出；若仍在，先在原会话用 `CronDelete` 并结束它，不能抢接。脚本更新会话 id 和进程号、领取新会话 inbox 并输出定时提示；照抄重建，再用 `CronList` 确认只有一个。

同一会话恢复（`--resume` 沿用 session id、PID 变化）也先对自己的契约运行上述 `adopt`。这时只刷新 PID，不要求原 PID 退出，不改原话、事项或映射，避免把恢复的活会话当作无主接管；再按输出核对或重建定时器。

## 验收与关闭

按事项判据验收，结论写入 `accept --verdict`。所需证据在删除前核完，`done/` 留结论；过程和证据放所属仓库或报告，不另建契约笔记、证据目录或副本。

关闭顺序只有一条：

1. 没有开放事项和未处理原话后，回溯本会话提过稍后做、顺手发现未记录、用户中途改口的事项，补进并做完。
2. 新开子代理并交契约目录，让它对照全部原话和事项（`tasks/`、`done/`）检查：要求是否做完、「不必跟进」是否确实不带要求、已关闭结论是否站得住；带证据的事项是否其实是用户原话要求、却错记为作者发起。
3. 有未完成事项就补入、做完再查；确认全部完成后，用 `CronList` 找出本契约定时器，`CronDelete` 并确认成功，汇报逐项状态和验收证据。
4. 最后运行 `python3 <本 skill 目录>/goal.py --root "$RUNS_ROOT" close`，只删除本契约的 `xy-goal/goal.md` 并解除会话绑定；其他文件留原处，由定期清理处理。

## 契约生命周期

事项目录按 `xy-runs` 规则定期清理。在办契约的半小时提示写入原话且不过期；暂停提醒或会话未执行 `close` 就消失的契约，30 天无活动后也删除，不加特例。一个多月后回来，可能要重开契约、重说要求。

`close` 后 hook 回到项目的 `xy-goal-inbox`，同会话可重开契约；整个 `xy-goal-inbox` 及各会话 inbox 都按统一规则清理。

`open` 或新会话 `adopt` 主体提交后，临时输入删除失败只警告，仍退出 0 并输出定时提示；退出 1 可重试，不会重复领取。
