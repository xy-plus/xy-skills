# xy-review

对抗审查的协议在 [`SKILL.md`](SKILL.md)；`check.py` 查一份报告的格式。

## 用法

```bash
python3 ~/.claude/skills/xy-review/check.py <报告路径>
```

退出 0 格式合格，1 不合格并逐条打印问题，2 用法错误。
格式合格且在 `group<G>/round3/` 下结论为「不通过」时，会提醒先派判因子代理写 `group<G>/diagnosis.md`、按结论处理后再开下一组，仍退出 0。

## 测试

```bash
cd xy-review && python3 -B -m unittest test_check
```
