# 待处理

## 拆分事项对账逻辑和过长测试文件

- 问题：`read_session_transcript`、`withdrawn_quote_dismissals`、`relink`、`change` 复杂度较高，`change` 语句过多；`test_track_and_verify.py` 超过 1000 行。
- 暂不处理的代价：会话解析、撤回对账和事项更新继续集中在复杂逻辑中，单体测试文件也增加导航与维护成本。
- 完成条件：拆开会话读取、撤回匹配及事项更新阶段，并按行为把测试拆到清晰文件；保留命令输出、文件写入、错误处理和全部现有行为覆盖。
