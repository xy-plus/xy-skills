# 夹具审查答案

预期发现放在夹具仓之外，不会复制给审查者。

- `unused_checksum` 没有调用方，可连同已放弃的格式代码一起删除。
- `normalize_label` 和 `clean_label` 做的是同一种规范化。
- `get_timeout` 只是把参数转交给 `timeout_for_mode`。
- `parse_external_result` 中的特殊分支仍在替上游缺陷兜底；应以这是下游补丁、应改上游修根因的理由找出，不能只当成死代码。
- 重试计数的注释重复描述了下一行的递增。
- 两个标签测试断言的是同一行为。
- `--legacy-export` 选项由夹具 README 记载的部署主机 cron 调用；删除前要问用户。
