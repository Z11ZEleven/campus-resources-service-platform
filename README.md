# campus-resources-service-platform

校园资源服务平台：座位预约、教室审批与物业报修。

本项目归属 2026 年秋季软件工程与过程伦理课程项目第九组。

## 当前状态

项目启动阶段。本次初始化提供协作规则、需求草案和目录说明，尚无可运行的业务代码、自动测试或部署流水线。未完成内容在文档中保留为待办，不作为已交付功能。

## 业务模块

| 模块 | 范围 |
| --- | --- |
| M1 | 自习座位搜索、预约、签到、提醒、违约记录、历史查询与复用 |
| M2 | 整教室申请、权限门槛、多级审批与超时撤销 |
| M3 | 故障上报、派单、维修、超时升级、验收与评价 |
| D | 身份与权限、空间与座位主数据、通知、操作审计 |

公共层子域负责人及模块分工尚待团队确认，见 [团队分工](docs/team.md)。

## 技术方向

- 团队倾向后端使用 Python。
- 候选方案：Vue 3 + TypeScript；FastAPI + SQLAlchemy + Alembic；PostgreSQL；Docker Compose。
- 上述框架、版本及部署拓扑尚待团队评审，见 [架构草案](docs/architecture.md) 和 [技术选型记录](docs/decisions/0001-technology-stack.md)。

## 目录导航

| 路径 | 内容 |
| --- | --- |
| frontend/ | 前端目录说明，待建立实际工程 |
| backend/ | 后端目录说明，待建立实际工程 |
| deploy/ | 环境与部署规划，待加入可执行配置 |
| docs/requirements.md | 需求草案、追踪表及待定规则 |
| docs/team.md | 分工与公共层责任表 |
| docs/first-review.md | 第一次讨论课准备清单 |
| docs/risks.md | 风险与伦理问题记录 |
| docs/decisions/ | 架构及公共层归属决策记录 |
| CONTRIBUTING.md | 人员协作与 PR 流程 |
| AGENTS.md | AI 编程代理规则 |
| .github/ | Issue 和 PR 模板 |

## 新成员开始协作

1. 接受仓库协作邀请，使用个人 GitHub 账号，不共用账号。
2. 克隆仓库：

   ```bash
   git clone https://github.com/Z11ZEleven/campus-resources-service-platform.git
   cd campus-resources-service-platform
   ```

3. 阅读 CONTRIBUTING.md、AGENTS.md、需求和分工文档。
4. 认领 Issue，从最新 main 创建短期任务分支。
5. 在 PR 中说明实现、验证结果和已知问题，请另一位成员评审。

工程初始化完成后，必须在这里补齐实际环境版本、依赖安装、配置、启动、迁移、种子数据和测试命令。目前没有这些命令，不能按本 README 启动业务系统。

## 第一轮工程目标

先通过团队评审确认规则与技术方案，再建立“前端调用后端并显示数据库中的教室列表”的最小链路，随后完成登录、座位查询和预约。同步建立自动构建、真实行为测试与测试环境部署。

## 发布与安全

- main 只通过人工评审后的 PR 更新；发布版本用 tag 关联提交。
- 分支保护、成员邀请和部署权限需要在 GitHub 设置中另行配置，本文档不代表已启用。
- 不提交真实密码、Token、私钥、学生信息、生产数据库或真实 AI 对话中的敏感内容。
- 若需更改仓库可见性，由仓库所有者确认课程和团队要求后操作。
