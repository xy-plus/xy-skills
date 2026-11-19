# 待处理

## 拆分共享目标逻辑和长测试

- 问题：`goal.execute`、`Contract.__init__` 分支过多，`publish_contract` 参数过多；测试支持函数 `transcript_record` 有 10 个参数；`test_waiting_hook_and_open_refuse_deleted_or_replaced_root_without_writes` 语句过多。
- 暂不处理的代价：命令分发、契约解析和会话记录夹具变复杂时，生产逻辑和长测试都难以局部理解。
- 完成条件：按命令分发、契约阶段解析和发布参数拆分；用具名夹具表达会话记录；将长测试拆成可读场景，现有命令行为和所有断言保持不变。
