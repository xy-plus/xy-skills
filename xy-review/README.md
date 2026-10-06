# xy-review

对抗审查的协议在 [`SKILL.md`](SKILL.md)；`check.py` 查一份报告的格式。

## 用法

```bash
python3 ~/.claude/skills/xy-review/check.py <报告路径>
```

退出 0 格式合格，1 不合格并逐条打印问题，2 用法错误。

## 测试

```bash
cd xy-review && python3 -B -m unittest test_check
```
