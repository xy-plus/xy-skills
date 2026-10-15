# xy-review

对抗审查的协议在 [`SKILL.md`](SKILL.md)；`check.py` 查报告格式、分组路径和全部前序记录。

## 用法

```bash
python3 ~/.claude/skills/xy-review/check.py <对象目录>/group1/round1/review-main.md
python3 ~/.claude/skills/xy-review/check.py <对象目录>/group1/round1/review-razor.md
```

每个对象从 `group1/round1/` 起，每组最多 `round1`、`round2`、`round3`。主审、剃刀报告各一份，文件名不限，按内容认角色；写报告前发现目标文件已存在就停下回报作者，不覆盖。当前轮可先核一份报告，另一角色未到不报错，同角色重复会报错。

每一轮目录里放一份以 `submitted` 开头的文件，写明这一轮审的那一版怎么取得：已有不可变版本（如提交号加路径、提交范围）就写引用，没有才存原文。审查者审这份版本，判因者用各轮版本核对旧意见；脚本要求当前轮及之前每一轮都有它。

有「不通过」时，作者在该轮写 `handling.md`，记录每条意见的处理和对象修改；改完在同组下一轮再审。脚本检查从 `group1/round1` 起的全部前序记录：两角色报告须齐全、格式合格，失败轮须有处理记录，组内进入下一轮须上一轮有「不通过」。

第 3 轮仍有「不通过」，作者先派独立判因子代理写该组的 `diagnosis.md`，按结论处理后才开下一组。判因报告用协议里的模板，只有「判因」「结论」两节；结论是一种或几种走法（拆分、重想、问用户、顶回），用「、」隔开且不重复。检查命令：

```bash
python3 ~/.claude/skills/xy-review/check.py <对象目录>/group1/diagnosis.md
```

核判因报告时，所在组须三轮齐全、每轮有 `submitted` 送审版本、第 3 轮有「不通过」，失败轮须有 `handling.md`；核下一组时，各前组还须有格式合格的判因报告。出现 `round4` 会立即报错，提示判因报告的位置和下一组。

退出 0 检查合格，1 不合格并逐条打印缺什么、该由谁补，2 用法错误。审查者写完和作者收到后都运行检查。

## 测试

```bash
cd xy-review && python3 -B -m unittest test_check
```
