---
name: xy-workflow
description: 讨论问题并可能改代码时的标准工作流程；只要改代码就走全套。
---

## 工作流程

**只要是改代码就走全套，不分大小。** 提到的 skill 除 `xy-` 开头和 `sync-think` 外都来自 superpowers，缺了就提醒用户安装；引用的 skill 和本流程冲突时以本流程为准，派子代理时把它该守的边界写进任务说明。

本流程要求用 `xy-goal`：会话里还没开就先开，几个主题的项都登记在本会话这一份契约里。用户原话只由契约照抄、编号，spec 和审查都引用契约里的原话，不再抄一份。spec、plan、审查报告都放 `~/.claude/xy-workflow/<仓库名>-<主题>/`，不入库；这个目录和契约的路径告诉每个子代理。每个产物在契约里登记一项：spec、plan、代码；审查不单独成项，审查通过就是 spec、plan 两项的验收；代码项的验收是终审通过、已快进合入并推送、运行目录已删，后三件用命令输出核。`xy-iron-law` 和 `xy-repo-rules` 自己已经加载；子代理不继承，派任何子代理都把这两份 SKILL.md 交给它并说明按它们做。

自己只编排，尽量不写代码；spec 和 plan 自己写。子代理只做交给它的事，不合入、不再派子代理。要用户定的事在第 1 步按 `sync-think` 问清，之后尽量不停下来等用户。所有审查都按 `xy-review` 派和处理，核实再改、有理由就顶回去的态度按 `receiving-code-review`；审查是闸，不通过，下一步不开始。

1. 用 `brainstorming` 讨论并写 spec，每条要求注明来自哪条原话。
2. 审 spec。
3. 用 `writing-plans` 写 plan，不问执行方式，plan 开头也不写执行方式那一行，怎么执行由第 5 步定。最后一个任务是把 spec、plan 里要留的信息和实现中的经验写进注释和 README、runbook、TODO 这类文档文件，spec 和 plan 删掉后不丢信息。
4. 审 plan。
5. 用 `using-git-worktrees` 开工作树：`git worktree add`，开在被改的那个子仓的 `.worktrees/<分支>`。派一个工作子代理按 plan 逐任务实现，带 `test-driven-development`；任务说明写明工作树的绝对路径，所有命令用绝对路径或 `git -C`，卡住就停下报告。plan 里互不依赖的任务多时，按 `dispatching-parallel-agents` 各开工作树、各派一个，各审各合。
6. 把工作树变基到主分支最新提交，再终审：按 `requesting-code-review` 派，审查者按它的模板和 `xy-review` 的模板各出一份报告、各写一个文件，闸以 `xy-review` 的结论为准。
7. 按 `verification-before-completion` 核过证据，再按 `finishing-a-development-branch` 走「本地合并」，不出菜单、不等用户，合完推主分支。主检出是大家共用的，不在那里跑测试，所以合并只许快进：`git merge --ff-only`，主分支指向的就是工作树里审过测过的那个提交；快进不了就回第 6 步。删工作树会连带删掉里面被忽略的产物，先确认没有进程或配置还在用它，要留的先挪走。
8. 删掉 `~/.claude/xy-workflow/<仓库名>-<主题>/`。
