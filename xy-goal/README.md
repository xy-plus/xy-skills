# xy-goal

用目录契约管理长目标：hook 记录原话，脚本维护原话与事项的唯一映射。输入 `/xy-goal` 或明确要求「用 xy-goal」才启用。执行规矩统一见 [SKILL.md](SKILL.md)。

## 安装与 hook 配置

前提：Linux、Python 3.10 及以上，Claude Code 的 `UserPromptSubmit`、`PostToolUse`、`Stop` hook（`Stop` 输入需带 `session_crons`）和 `CronCreate`、`CronList`、`CronDelete`。脚本使用目录文件锁及 Linux `/proc` 的父进程关系。

按[仓库安装步骤](../README.md#安装)设好并创建 `RUNS_ROOT` 后，把 `<xy-goal 目录>` 换成自己安装 xy-goal 的绝对路径，再把下面配置合并到 `~/.claude/settings.json` 的 `hooks`，保留已有配置：

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"<xy-goal 目录>/goal.py\" --root \"$RUNS_ROOT\" hook-prompt || exit 1"
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
            "command": "python3 \"<xy-goal 目录>/goal.py\" --root \"$RUNS_ROOT\" hook-answer"
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"<xy-goal 目录>/goal.py\" --root \"$RUNS_ROOT\" hook-stop || exit 1",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

脚本文件打不开时 Python 解释器退出 2，而 `UserPromptSubmit` 退出 2 会拦下并丢弃这条用户消息、影响每个装了 hook 的会话，因此用 `|| exit 1` 把一切失败转成不拦消息的报错。`Stop` 退出 2 会让 agent 继续运行，但 agent 无法修复启动失败的 hook，因此 Stop 命令也用 `|| exit 1`；启动失败时照常结束这一轮，下一轮再核对。`PostToolUse` 退出 2 只向模型反馈错误，配置保持原样（[官方 hooks 文档](https://code.claude.com/docs/en/hooks#exit-code-2-behavior-per-event)）。

Stop hook 显式设 `timeout` 为 10 秒：它要等与其他命令共用的根目录锁，不能用平台 600 秒的默认超时拖住会话结束。若 10 秒超时，Claude Code 会取消这次 hook 并丢掉输出；这一轮不核对，下一次正常结束时再核对。

`hook-prompt` 读取官方 hook JSON 的 `session_id`、`transcript_path`、`prompt`，逐字保存 `prompt`；`hook-answer` 将 `AskUserQuestion` 的 `tool_input`、`tool_response` 原值存成 JSON，不解析回答结构或改写问题。

`hook-stop` 读取 Stop 输入中的 `session_id`、`stop_hook_active` 和 `session_crons`，校验每个定时器的 `id`、`schedule`、`prompt`、`recurring`，按契约只读核对定时器。只有需要新建或删除时才输出一行 Stop hook JSON，其余成功时无输出，不写文件。`stop_hook_active` 为真时跳过核对，避免自己的提示续跑后重复追问。

hook 不能调用 `CronCreate`、`CronDelete`，挂和删仍由 agent 执行。以 API 错误结束的轮次走 `StopFailure`，不会触发 `Stop`，因此那一轮不核对；例如定时器 7 天到期后的最后一次触发若以 API 错误结束，要等下一次正常结束时才会要求重挂。收件套接字虽然能发消息，但消息可在 agent 正做事时到达并打断当前轮次，还需要常驻发送进程；`asyncRewake` 会把 hook 反馈包装成待处理原话通知，而现有分类规则不能自动标掉它。两者都会增加状态或改变原话处理规则，所以定时器状态由 Stop hook 核对、agent 照做。

全局 settings.json 可能已有 ntfy 手机通知、claude-switch 备份和 chinese-output-reminder 等 Stop hook。`hook-stop` 与 chinese-output-reminder 一样用 `additionalContext` 让 agent 续跑。续跑会再触发 Stop；ntfy-notify 以最后一条 `stop_hook_summary` 为本轮边界，续跑部分没有用户输入标记，仍由它自己的分类器决定是否推送。`hook-stop` 只在定时器状态不符时续跑，频率很低，接受这次分类判定，不改 ntfy-notify。

## 合入前的隔离会话实测

合入前还需隔离会话（`--setting-sources project`，只加载临时测试目录配置）实测：中途插话的 `UserPromptSubmit`、`AskUserQuestion` 回答、开契约前消息领取、原生子代理 hook 的 `agent_id` 排除，以及 Stop hook 读取 `session_crons` 并核对定时器。单元测试不代替这一步。
