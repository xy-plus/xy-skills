# xy-goal

用三个落盘文件延续跨会话的长目标：清单、用户原话、账本。

前提：Claude Code 的定时任务工具 CronCreate、CronList、CronDelete；本仓库的 xy-review。

## 用法

输入 `/xy-goal` 或明确要求「用 xy-goal」，再说目标；只在明确要求时调用。手动检查：`python3 ~/.claude/skills/xy-goal/check.py ~/.claude/xy-goal`。

规矩见 [`SKILL.md`](SKILL.md)。

## 测试

```bash
cd xy-goal && python3 -B -m unittest discover -s tests
```
