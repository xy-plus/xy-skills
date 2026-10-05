# xy-goal

xy-goal 用一份落盘契约延续跨会话的长目标。定时自查真实进度，并把能推进的事项派出去。子代理按用户原话核验产物，通过后才关闭目标。

## 安装

```bash
git clone https://github.com/xy-plus/xy-goal-skill.git ~/.claude/skills/xy-goal
```

需要提供 `CronCreate`、`CronList`、`CronDelete` 的 Claude Code 环境。

## 用法

输入 `/xy-goal` 或明确要求「用 xy-goal」，再说目标；只在明确要求时调用。

手动检查契约：

```bash
python3 ~/.claude/skills/xy-goal/check.py ~/.claude/xy-goal
```

契约、定时自查、验收与关闭规则见 [`SKILL.md`](SKILL.md)。
