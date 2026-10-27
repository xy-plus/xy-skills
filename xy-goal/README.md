# xy-goal

用目录契约管理长目标：hook 记录原话，脚本维护原话与事项的唯一映射。输入 `/xy-goal` 或明确要求「用 xy-goal」才启用。执行规矩统一见 [SKILL.md](SKILL.md)。

## 安装与 hook 配置

前提：Linux、Python 3.10 及以上，Claude Code 的 `UserPromptSubmit`、`PostToolUse` hook 和 `CronCreate`、`CronList`、`CronDelete`。脚本使用目录文件锁及 Linux `/proc` 的父进程关系。

按[仓库安装步骤](../README.md#安装)设好并创建 `RUNS_ROOT` 后，把下面配置合并到 `~/.claude/settings.json` 的 `hooks`，保留已有配置：

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

`hook-prompt` 读取官方 hook JSON 的 `session_id`、`transcript_path`、`prompt`，逐字保存 `prompt`；`hook-answer` 将 `AskUserQuestion` 的 `tool_input`、`tool_response` 原值存成 JSON，不解析回答结构或改写问题。

## 测试

仓库根目录运行：

```bash
python3 -B -m unittest discover -s xy-goal/tests
```

合入前还需隔离会话（`--setting-sources project`，只加载临时测试目录配置）实测：中途插话的 `UserPromptSubmit`、`AskUserQuestion` 回答、开契约前消息领取、原生子代理 hook 的 `agent_id` 排除。单元测试不代替这一步。
