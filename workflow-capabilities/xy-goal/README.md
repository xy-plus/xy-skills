# xy-goal

长目标契约：自动记录原话、对账事项、验收进度并支持跨会话接续。执行规则见 [SKILL.md](SKILL.md)。

## 安装与 hook 配置

前提：Linux、Python 3.10 及以上；Claude Code 提供 `UserPromptSubmit`、`PostToolUse` hook 和 `CronCreate`、`CronList`、`CronDelete`。脚本使用目录文件锁及 Linux `/proc` 父进程关系。

按[仓库安装步骤](../../README.md#安装)配置并创建 `RUNS_ROOT` 后，将 `<xy-goal 目录>` 替换为已安装 xy-goal 的绝对路径，再把下面配置合并到 `~/.claude/settings.json` 的 `hooks`，保留现有配置：

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
    ]
  }
}
```

脚本文件打不开时 Python 退出 2；`UserPromptSubmit` 退出 2 会拦截并丢弃消息，影响所有装有 hook 的会话，所以用 `|| exit 1` 把一切失败转成不拦消息的报错。`PostToolUse` 退出 2 只向模型反馈错误，配置保持原样（[官方 hooks 文档](https://code.claude.com/docs/en/hooks#exit-code-2-behavior-per-event)）。

`hook-prompt` 从官方 hook JSON 读取 `session_id`、`transcript_path`、`prompt`，逐字保存 `prompt`；`hook-answer` 将 `AskUserQuestion` 的 `tool_input`、`tool_response` 原值存成 JSON，不解析回答结构或改写问题。

关闭契约时按 [SKILL.md](SKILL.md) 的流程提交独立复核报告；报告里的复核者会话 id 须与当前持有这份契约的会话不同。
