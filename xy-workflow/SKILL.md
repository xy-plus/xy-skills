---
name: xy-workflow
description: 讨论问题并可能改代码时的标准工作流程；只要改代码就走全套。
---

## 工作流程

确保 superpower 系列的 skill 全部存在，如果不存在，提醒用户安装。本章节提到的 skill 全部来自 superpower。

讨论问题并后续可能需要写代码时，按照以下流程工作。**只要是改代码就走全套，不分大小**

步骤的先后只表示依赖，不依赖的同时做。所有审查都是对抗审查：用 `adversarial-review` skill 的模板派，三条铁律和仓库规范逐条回复「查了」和结论，交回后用它的脚本检查；查出的旧代码冗余另开以删为目的的任务（功能不变、代码变少）。

1. 自己是编排者，尽量不要写代码，而是安排子代理工作。
2. 先用 `brainstorming` skill 思考和讨论问题，写 spec 文档。
3. 开子代理审查 spec 是否符合三条铁律和仓库规范，审查期间，使用 `writing-plans` skill 写计划文档。
4. 等待子代理对 spec 的审查结束，更新计划文档。
5. 开一个子代理审查计划文档是否符合三条铁律和仓库规范。
6. 审查期间，使用 `subagent-driven-development` skill、`using-git-worktrees` skill（用 git worktree add，开在被改的那个子仓的 .worktrees/<分支>） 和 `dispatching-parallel-agents` skill，开一个工作子代理写代码（不被审查阻塞），工作子代理需要加载 `test-driven-development` skill 和 `executing-plans` skill。
7. 审查结束后，把审查结果同步给工作子代理，要求工作子代理更新计划文档。
8. 工作子代理完成工作结束后，使用 `requesting-code-review` skill、 `receiving-code-review` skill、`verification-before-completion` skill 和 `finishing-a-development-branch` skill，审查内容需要包括三条铁律和仓库规范。

   合入走 `finishing-a-development-branch` 的「本地合并」，合并命令用 `git merge --ff-only`：合入结果就是工作树里跑过闸的那个提交，不在主检出重跑闸（主检出是大家共用的）；快进不了就回工作树变基、重跑闸。合完推主分支，分支和 `.worktrees/` 下的工作树由它删。删树会连带删掉树里被忽略的产物和 `.venv`：合入前先确认没有进程、没有在跑的配置用着这棵树，要留的产物先挪走。
9. 先开一个子代理，核对两件事：① spec、plan、code、doc 四者是否全部对齐（doc 指 README、runbook、TODO 这类文档文件；code 指代码与注释）；② spec 和 plan 里的每一项是否真的做完了。两件都确认之后，再把它们结合自己的工作经验内化到注释文档和文件文档里。确保事情全部做完并且信息内化完后，删除 spec 文档和计划文档。
