# xy-review

对抗审查的协议在 [`SKILL.md`](SKILL.md)；`check.py` 查报告格式、分组路径和全部前序记录。

## 用法

每个对象从 `group1/round1/` 起，每组最多 `round1`、`round2`、`round3`。

每一轮目录里放一份以 `submitted` 开头的文件，写明这一轮审的那一版怎么取得：已有不可变版本（如提交号加路径、提交范围）就写引用，没有才存原文。

```bash
python3 ~/.claude/skills/xy-review/check.py <对象目录>/group1/round1/review-main.md
python3 ~/.claude/skills/xy-review/check.py <对象目录>/group1/round1/review-razor.md
```

判因报告的检查命令：

```bash
python3 ~/.claude/skills/xy-review/check.py <对象目录>/group1/diagnosis.md
```

退出 0 检查合格，1 不合格并逐条打印缺什么、该由谁补，2 用法错误。

## 测试

```bash
cd xy-review && python3 -B -m unittest test_check
```
