# 待处理

## 拆分决策页校验、服务参数与长测试

- 问题：`validate_decisions` 分支和语句过多，`run_ask` 参数过多；单测 `test_ask_mounts_receives_one_valid_answer_and_unmounts`、`test_term_unmounts_its_site_and_removes_service_without_answer` 以及浏览器测试 `test_decisions_persist_per_call_and_submit_from_a_phone_view` 都承担过多步骤。
- 暂不处理的代价：校验规则、服务生命周期和端到端故障定位仍集中在较长的函数与测试中，后续改动更难局部验证。
- 完成条件：按校验阶段、服务依赖和测试行为拆成小函数；覆盖字段校验、挂载与卸载、提交持久化及手机视图的断言完整保留。
