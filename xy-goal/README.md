# xy-goal

用目录契约管理长目标：hook 记录原话，脚本维护原话与事项的唯一映射。输入 `/xy-goal` 或明确要求「用 xy-goal」才启用。执行规矩统一见 [SKILL.md](SKILL.md)。

## 安装与 hook 配置

前提：Linux、Python 3.10 及以上，Claude Code 的 `UserPromptSubmit`、`PostToolUse` hook 和 `CronCreate`、`CronList`、`CronDelete`。脚本使用目录文件锁及 Linux `/proc` 的父进程关系。

将本目录装到 `~/.claude/skills/xy-goal`。先按[仓库安装步骤](../README.md#安装)在全局 settings.json 的 `env` 里显式设置 `RUNS_ROOT`，重启会话让环境变量生效，再创建该根目录：

```bash
mkdir -p "${RUNS_ROOT:?请先在全局 settings.json 的 env 中设置 RUNS_ROOT}"
```

再把下面配置合并到 `~/.claude/settings.json` 的 `hooks`，保留已有配置：

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"$HOME/.claude/skills/xy-goal/goal.py\" --root \"$RUNS_ROOT\" hook-prompt || exit 1"
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
            "command": "python3 \"$HOME/.claude/skills/xy-goal/goal.py\" --root \"$RUNS_ROOT\" hook-answer"
          }
        ]
      }
    ]
  }
}
```

脚本文件打不开时 Python 解释器退出 2，而 `UserPromptSubmit` 退出 2 会拦下并丢弃这条用户消息、影响每个装了 hook 的会话，因此用 `|| exit 1` 把一切失败转成不拦消息的报错；`PostToolUse` 退出 2 只向模型反馈错误，配置保持原样（[官方 hooks 文档](https://code.claude.com/docs/en/hooks#exit-code-2-behavior-per-event)）。

`hook-prompt` 读取官方 hook JSON 的 `session_id`、`transcript_path`、`prompt`，逐字保存 `prompt`；`hook-answer` 将 `AskUserQuestion` 的 `tool_input`、`tool_response` 原值存成 JSON，不解析回答结构或改写问题。成功无输出，失败写 stderr、退出 1，Claude Code 仍会送达消息。编号、子代理排除及自动分类规则见 [契约与命令](SKILL.md#契约与命令)。

## 命令示例

在 Claude Code 的 Bash 工具中运行；所有命令必填 `--root`，agent 命令还需要环境变量 `CLAUDE_CODE_SESSION_ID`。根目录必须事先存在且为目录，脚本不会自动创建；不存在或不是目录就报错退出。

脚本路径显式赋值，`RUNS_ROOT` 使用全局 settings.json 传入的值：

```bash
goal_script="$HOME/.claude/skills/xy-goal/goal.py"
python3 "$goal_script" --root "$RUNS_ROOT" open release '交付新版本' --claude-pid "$PPID"
python3 "$goal_script" --root "$RUNS_ROOT" show
python3 "$goal_script" --root "$RUNS_ROOT" check
```

`open`、`adopt` 必填由该 Bash 展开的 `--claude-pid "$PPID"`；脚本核对它是调用进程的祖先，拒绝无关或已退出的进程号。它们输出的定时提示原文交给 `CronCreate(cron="*/30 * * * *", recurring=true, prompt=...)`。核对、过期重建、暂停提醒与自查步骤见 [定时自查](SKILL.md#定时自查)。

`show` 只输出目标、开放事项和未处理原话编号；`check` 通过退出 0，有问题逐条报告并退出 1。每项 300 字、全文 5000 字是 `show` 的软提示，不拒绝写入。原话按编号直接读 `quotes/N.md`。

```bash
# 原话编号换成 hook 已记录的实际编号，T 号由 add 输出。
python3 "$goal_script" --root "$RUNS_ROOT" add --title '发布' --criteria '测试通过并完成发布' --source 1 2 --state '在办' --pointer '无'
python3 "$goal_script" --root "$RUNS_ROOT" update T1 --field 状态 --value '等用户：确认发布日期'
python3 "$goal_script" --root "$RUNS_ROOT" update T1 --field 指针 --value '/absolute/path/to/report.md'
python3 "$goal_script" --root "$RUNS_ROOT" update T1 --field 判据 --value '连续两轮测试通过并完成发布' --source 3
python3 "$goal_script" --root "$RUNS_ROOT" update T1 --source 4 5
python3 "$goal_script" --root "$RUNS_ROOT" dismiss 6 --reason '问题已答，无后续要求'
python3 "$goal_script" --root "$RUNS_ROOT" accept T1 --verdict '两轮测试退出码均为 0，发布检查通过'
python3 "$goal_script" --root "$RUNS_ROOT" cancel T2 --quote 7
```

`update --field` 只认 `标题`、`判据`、`状态`、`指针`；改判据必带 `--source`，单独传 `--source` 只追加出处。状态只认 `在办`、`等用户：待定的事`，还没有指针就显式写 `无`。验收与取消移入 `done/`，T 号不复用。维护规矩见 [维护事项](SKILL.md#维护事项)。

新会话接管或同会话恢复，都用：

```bash
python3 "$goal_script" --root "$RUNS_ROOT" adopt /absolute/path/to/contract-directory --claude-pid "$PPID"
```

同会话恢复只刷新 PID。新会话的接管条件、恢复后操作见 [换会话接管](SKILL.md#换会话接管)。关闭前的整体复核、定时器删除及汇报顺序见 [验收与关闭](SKILL.md#验收与关闭)，最后运行：

```bash
python3 "$goal_script" --root "$RUNS_ROOT" close
```

## 文件与写入边界

```text
$RUNS_ROOT/<项目目录名>/<YYYYMMDD-HHMMSS>-<slug>/xy-goal/
  goal.md       目标、session_id、claude_pid
  quotes/1.md   原文，一条一文件，整数编号，无前导零
  tasks/T1.md   标题、判据、状态、指针
  done/T1.md    原事项加验收结论或取消依据
  links.md      唯一映射：1 T1，或 1 不必跟进 原因
```

写入只经脚本，读取直接用 Read/cat。字段用 JSON 字符串保存，按物理 LF 分行，U+2028 等 Unicode 字符能往返；事项字段中的分行字符在 `show` 中转义，保持四行。不必跟进原因必须一行，含 Unicode 分行符也会在写前拒绝。原话不转义、不增标注，CRLF、空行均保留。

验收或取消时，已解析的事项字段与结论统一编码写入 `done/`，保留字段值。

已迁入 `done/` 的旧账本验收行、取消行仍按原行读取，不补造判据、状态或验收字段；无出处的第二行是 `旧记录无出处`，标记须与 `links.md` 中的映射一致，正文里的原话编号也必须有对应映射。保留这种读取能力，现存契约才能继续 `show`、`check`、`close`。

未开契约时，`$RUNS_ROOT/<项目目录名>/xy-goal-inbox/<会话 id>/N.md` 只存原话。领取时重算自动分类，写进契约唯一的 `links.md`；匹配与复核规则见 [契约与命令](SKILL.md#契约与命令)。

项目目录名直接取记录文件的父目录名，不按当前工作目录换算。hook 定位 inbox 时取 `transcript_path` 的父目录名；`open` 建契约、新会话 `adopt` 领 inbox 时，在 `~/.claude/projects/*/<CLAUDE_CODE_SESSION_ID>.jsonl` 中必须恰好找到一份记录，否则报错且不建目录。已有契约的命令、同会话 `adopt` 不查记录。

契约只在 `$RUNS_ROOT/-*/*/xy-goal/goal.md` 中查找。`adopt` 只接受 `$RUNS_ROOT/<以 - 开头的项目目录>/<一件事>/xy-goal`，回收目录不参与查找或接管。事项目录可以共用：agent 的文件和 `sub-agent-runner/` 等工具目录放在 `xy-goal/` 外。

`close` 校验通过后只删 `xy-goal/goal.md`，其余文件保留；之后 hook 回到 inbox，同会话可以再开契约。事项目录和 inbox 的生命周期见 [契约生命周期](SKILL.md#契约生命周期)。

写命令先校验；本契约坏文件会指出路径，不诊断其他会话的数据。普通多文件写入 I/O 失败会回滚。`open` 要求本会话还没有契约；`adopt` 可接管或刷新自己的契约；其他 agent 命令要求恰好一份，本会话重复绑定会拒绝。

## 失败与输入领取

`open`、新会话 `adopt` 在主体提交前，将已校验的输入在原父目录内临时改名。提交前失败会放回输入，返回 1，可安全重试；同会话 `adopt` 只更新 PID。主体已提交后，临时输入删除失败只向 stderr 警告，照常返回 0 并输出定时提示；警告给出残留路径，已领取原话不会再次导入。

## 测试

仓库根目录运行：

```bash
python3 -B -m unittest discover -s xy-goal/tests
```

测试均用临时目录与隔离 HOME。根目录和 PID 走正式参数解析，覆盖项目记录定位、工具共用事项目录、回收目录隔离、关闭后的 inbox 与再开契约，以及真实 Bash → CLI 的 PID 往返、拒绝非祖先 PID、同会话恢复、其他会话坏元数据隔离、Unicode 字段、旧账本 `done/` 读取及真实只读权限下的提交与回滚。

合入前还需隔离会话（claude-deepseek，`--setting-sources project`，只加载临时测试目录配置）实测：中途插话的 `UserPromptSubmit`、`AskUserQuestion` 回答、开契约前消息领取、原生子代理 hook 的 `agent_id` 排除。单元测试不代替这一步。
