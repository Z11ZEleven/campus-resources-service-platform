# campus-resources-service-platform

校园资源服务平台：座位预约、教室审批与物业报修。

本项目归属 2026 年秋季软件工程与过程伦理课程项目第九组。

## 当前状态

第一次功能迭代：已加入公共层 D 的 MySQL 后端、迁移与种子数据、认证与资源 API、模块接入函数、最小 Vue 示例和 CI 检查配置；M3 已提供工单主表与上报、查询、派单、维修、完工和验收后端。M1 T01–T05 已提供基础模型、统一会话与范围授权、空间维护与开放规则；M1 预约/签到等业务、M2 业务、自动部署和生产发布尚未实现。M3 已有前端，后续扩展继续按模块计划推进。

开始运行请看 [后端启动说明](backend/README.md)、[公共前端](frontend/README.md) 和 [模块接入指南](docs/public-layer-integration.md)。

M3 的接口、权限、状态与测试见 [M3 后端接入说明](docs/m3-backend.md)。

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
- 首次迭代实施方案：Vue 3 + TypeScript；FastAPI + SQLAlchemy + Alembic；**MySQL 8.4**；Docker Compose。
- 上述框架、版本及部署拓扑尚待团队评审，见 [架构草案](docs/architecture.md) 和 [技术选型记录](docs/decisions/0001-technology-stack.md)。

## 目录导航

| 路径 | 内容 |
| --- | --- |
| frontend/ | 公共登录、布局与资源查询 Vue 示例 |
| backend/ | 公共层 FastAPI、MySQL 迁移、初始化和测试 |
| deploy/ | 本地 MySQL Compose；自动部署待实现 |
| docs/public-layer-integration.md | 各模块调用公共层的接口与事务约定 |
| docs/m1/contracts.md | M1 T01–T05 基础接口、数据及兼容契约 |
| docs/m1/traceability.md | M1 需求—任务—实现—测试对应 |
| docs/m1/verification.md | 本轮实际验收结果与未决事项 |
| docs/database-contract.md | D、M1、M2、M3 统一数据库字段与变更流程 |
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

环境、启动、迁移、种子及测试命令见 backend/README.md；前端命令见 frontend/README.md。历史完整设计中的 PostgreSQL 内容不作为首次迭代实施规范。

## 第一轮工程目标

先通过团队评审确认规则与技术方案，再建立“前端调用后端并显示数据库中的教室列表”的最小链路，随后完成登录、座位查询和预约。同步建立自动构建、真实行为测试与测试环境部署。

## 发布与安全

- main 只通过人工评审后的 PR 更新；发布版本用 tag 关联提交。
- 分支保护、成员邀请和部署权限需要在 GitHub 设置中另行配置，本文档不代表已启用。
- 不提交真实密码、Token、私钥、学生信息、生产数据库或真实 AI 对话中的敏感内容。
- 若需更改仓库可见性，由仓库所有者确认课程和团队要求后操作。

## 补充规则

### M1：个人预约自习座位
- 预约时段限制在当天8:00-22:00；

### M2：整间教室预约
- 限制一部分教室只供团体预约（暂定，需要根据老师指导改进）

### M3：物业报修工单

### D：公共层
