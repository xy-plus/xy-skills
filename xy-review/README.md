# xy-review

对抗审查的协议在 [`SKILL.md`](SKILL.md)；`check.py` 查一份报告的格式，`audit.py` 对账事项目录的分组、轮次和判因记录。

## 用法

```bash
python3 ~/.claude/skills/xy-review/check.py <报告路径>
python3 ~/.claude/skills/xy-review/audit.py <目录路径>
```

## 测试

```bash
cd xy-review && python3 -B -m unittest
```
