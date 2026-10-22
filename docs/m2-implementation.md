# M2 两级审批：实现、运行与验收

本轮依据 [M2 需求基线](m2-requirements.md) 与 [M2 状态机、数据和接口设计](m2-design.md)，替代已撤回的教师/单级首次迭代方案。本次用户允许必要 D 扩展；公共变更仍需各相关模块评审，不预填团队共识或人工验收结论。

## 1. 业务规则

- 有效的 STUDENT、TEACHER、STAFF 均可用本人身份提交，不要求附加 USER 角色。申请人及身份来自登录态，不能在请求体冒用。
- M2 仅接收有效 WHOLE_ROOM 教室；SEAT 仍用于 M1。申请须符合院系可见范围。公共地点接口保持两类均可见，支持 booking_mode 显式筛选，M3 报修仍能选择整室教室；M1 座位写接口在服务端拒绝 WHOLE_ROOM。
- 提交至少提前两个北京时间自然日，整点、同日、1–4小时，位于08:00–22:00和实际开放时段内。M2 没有擅加7天上限。
- 一级审批根据教室归属使用 DEPT_ADMIN + DEPARTMENT 范围，或 CAMPUS_ADMIN + CAMPUS 范围；二级为 ROOM_ADMIN + 具体 ROOM 范围。仅有角色而没有范围不能审批，不允许自审。
- 每级截止为创建步骤后的下一自然日23:59:59（北京时间）；使用开始先到时以开始为界。任何写操作在边界重查，定时扫描默认每5秒补执行。
- 待一级、待二级可由本人撤回；批准、驳回、撤回、自动撤销等终态不重新审批或撤回。相同请求键重放只返回既有结果，不产生新的状态变化。
- 不收未确认的人数、联系方式、附件等字段。申请用途最多2000字，驳回意见必填且最多500字。

## 2. 状态、数据与并发

状态为 PENDING_FIRST_APPROVAL → PENDING_ROOM_APPROVAL → RESOURCE_CONFIRMING → APPROVED，任一级可进入 REJECTED；待审可 WITHDRAWN；截止可 AUTO_CANCELLED。

M2 有4张表：m2_applications、m2_approval_steps、m2_application_events、m2_resource_jobs。身份快照、步骤截止、操作历史、资源同步状态及 version 均持久化。技术无关草案中的 UUID/TIMESTAMPTZ 映射为当前统一契约的 BIGINT UNSIGNED / DATETIME(3) UTC / m2_表名前缀；JSON ID 为字符串。

本地变化先锁教室，再锁申请，并用旧状态/version 条件更新。创建同申请人/request_key 唯一；步骤 application_id/level 唯一，审批人/request_key 唯一。本人创建、一级/二级处理、撤回均使用 Idempotency-Key UUID 请求头。审批及撤回带 expected_version。相同键不同内容返回409。

## 3. 真正的网络资源确认与恢复

M2 不在数据库事务中等待网络，也不直接改 D 来源或其他模块私有表：

1. 二级同意在本地事务中写 RESOURCE_CONFIRMING、审批决定和持久化资源任务，提交并释放锁。
2. 使用独立服务密钥通过 HTTP 请求 D 应用。D 在教室锁下检查当前身份、角色/范围、教室分类、时段与真实来源，原子创建 M2 整室来源和幂等结果账本。
3. M2 仅在获得匹配的确定成功结果后写 APPROVED；确定业务拒绝写 REJECTED。成功后，同室重叠待审申请自动驳回，原因明确为“教室对应时段已被其他申请占用”。
4. 断连、超时、响应不匹配或本地确认提交失败，不伪装成批准/冲突。持久化任务使用同一来源、同一 payload 重试，租约过期可由重启后的 worker 继续确认。
5. 截止与网络竞态时，申请先 AUTO_CANCELLED，清理状态 CANCEL_PENDING；D 取消命令可先写墓碑阻止晚到创建，或只释放该申请的来源。网络不可达时明确保留待清理状态，恢复后继续确认，不假称释放完成，也不能把终态改回批准。

`app.resource_api:app` 为独立 D 应用，不导入或读取 M2 私有表。普通用户 JWT 无权调用资源命令；服务密钥仅在后端，不能放到浏览器或提交仓库。已有 M3 代码和0001–0003迁移保持原样。

## 4. 通知、审计和待评审配置

进入两级、临期、批准、驳回、撤回及超时事件写入真实 D 站内收件箱，与本地状态同事务提交。事件键稳定且不重复；通知失败时本地变更回滚。审批人必须有当前有效角色和范围，申请人不能被当作自己的审批收件人。通知链接严格限制为站内 M2/M3 详情。

M2 有独立业务历史，也调用 D 公共审计。系统动作使用 actor_type=SYSTEM、actor_id=null；不把自动超时伪装成人工操作。越权审批拒绝后另记必要安全审计，不记录密码、Token或完整敏感请求。

临期默认24小时为文档建议的可配置默认值，尚待团队评审；不是已经形成的团队共识。首版采用已有站内通知，不把它描述为邮件或微信投递。生产发布和全模块部署不在此次 M2 功能 PR 中。

## 5. 运行

先按后端 README 启动 MySQL、安装依赖并准备 .env。除了 MYSQL/JWT/SEED 密码，另生成独立 RESOURCE_SERVICE_KEY（至少32字符），在两个后端进程配置同一值：

```bash
cd backend
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m scripts.seed_m2
```

两个终端分别启动 D 与主应用：

```bash
# D 资源服务
.venv/bin/python -m uvicorn app.resource_api:app --host 127.0.0.1 --port 8001
# 主应用，M2_RESOURCE_BASE_URL=http://127.0.0.1:8001
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

在 frontend 运行 npm ci、npm run dev。端口变化使用已有 VITE_API_PROXY_TARGET 配置。停止各服务在相应终端按 Ctrl+C。主应用会停止 M2/M3 worker；已提交的资源任务保留，重启后继续。

| 环境项 | 默认 / 用途 |
|---|---|
| M2_WORKER_ENABLED | true，自动超时、提醒和资源恢复；测试中关闭自动并显式执行 |
| M2_SCAN_INTERVAL_SECONDS | 5，扫描间隔 |
| M2_REMINDER_HOURS | 24，临期提前量，待评审默认值 |
| M2_RESOURCE_BASE_URL | http://127.0.0.1:8001，D 应用地址 |
| M2_RESOURCE_TIMEOUT_SECONDS | 5，单次网络超时；失败保留待确认任务 |
| RESOURCE_SERVICE_KEY | 无可用默认值；缺失或不匹配时不能取得批准结果 |

## 6. 演示数据

密码统一来自本地 SEED_USER_PASSWORD，不在文档记录。seed_m2 幂等，不重置既有用户密码、不恢复已撤销角色/停用范围，也不重置既有教室模式。

| 账号 | 用途 |
|---|---|
| student、student_ee、teacher | 原有学生/教师，可提交本人申请 |
| staff | 工作人员申请；有效 repairer 等无 USER 角色账号也可申请校级教室 |
| dept_admin_cs、dept_admin_ee | 对应院系一级审批 |
| campus_admin | MAIN 校级教室一级审批 |
| m2_room_admin | 三间演示整室教室二级审批 |

新增 M2-CAMPUS、M2-CS、M2-EE 三间 WHOLE_ROOM，既有 B1 教室仍为 SEAT。使用 student 申请校级教室 → campus_admin 一级通过 → m2_room_admin 二级通过；一级完成时应仍显示待二级，不能直接显示批准。部门教室则由对应 dept_admin 处理一级。

## 7. HTTP 接口

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | /api/v1/m2/me、/rooms | 有效身份、审批范围、分页候选整室教室 |
| POST | /api/v1/m2/applications | 本人提交，body仅含room_id、purpose、start_at、end_at |
| GET | /api/v1/m2/applications、/applications/me | 授权范围/本人分页列表 |
| GET | /api/v1/m2/applications/{id}、/{id}/events | 详情、审批步骤和业务历史 |
| POST | /api/v1/m2/applications/{id}/withdraw | 本人撤回任一级待审 |
| GET | /api/v1/m2/approval-tasks | 按角色和实际范围过滤的待办，不含自审任务 |
| POST | /api/v1/m2/approval-tasks/{stepId}/approve、/reject | 按层级处理；二级自动发起网络资源确认 |
| POST | /api/v1/resource-commands/m2、/m2/cancel | D应用：服务鉴权、幂等创建/截止取消 |
| GET | /api/v1/resource-commands/m2/{source_id} | D应用：按业务来源查询确定结果 |

业务错误保持 detail.code、detail.message，M2 业务错误附 request_id；422 字段校验保持 FastAPI 格式。响应明确区分确认中、最终批准、业务拒绝、自动撤销和资源清理待确认。

## 8. 验收追踪

| 需求文档位置 | 实现 | 验证证据 |
|---|---|---|
| 需求§2、§11.1 | 身份来自认证；M2服务与D范围 | test_m2.py：三类身份、冒用、越权、角色/范围撤销 |
| 需求§3 | 整室分类、提前2自然日、1–4小时、整点/开放时间 | test_m2.py、test_m2_unit.py：类型、月末/年末/闰日、7天以外合法时段 |
| 需求§5、§11.2 | 两级步骤、角色/范围、自审拒绝、理由 | test_m2.py：两级状态、无直接批准、范围隔离、拒绝审计 |
| 需求§6–7 | 版本条件转换、仅待审撤回 | test_m2.py、test_m2_network.py：幂等、竞争与终态 |
| 需求§5.3、§11.3 | 自动扫描、截止/开始优先、持久化补扫 | test_m2.py、test_m2_network.py：重复扫描、提醒去重、超时竞态 |
| 需求§8–9、§11.4 | 独立D进程TCP、幂等命令、同室冲突和恢复 | test_m2_network.py：断连、丢响应、响应不匹配、晚到创建、补偿不误释放M3、真实并发 |
| 需求§10、设计§4.3–4.4 | 独立历史、真实站内收件箱、可靠资源任务 | test_m2.py、test_m2_migrations.py：投递、失败回滚、远端成功后本地失败恢复 |
| 公共迁移与回归 | 追加0004，不改旧迁移/M3业务代码 | test_migrations.py、test_m2_migrations.py、原有M3/D测试 |

测试只在显式 *_test 数据库运行。自动测试与浏览器验证不替代团队人工验收。

## 9. 数据升级和回退

0004_m2_two_stage 接在当前 main 的0003后。新D字段有兼容默认值，保留旧用户、SEAT教室、工单和通知。旧单级实验迁移 c229e10bbc03 / 0004_m2_applications 不属于当前 main 链，不能手改版本号硬套；本次不处理旧实验库数据转换，也不清理旧本地环境。

回退只允许没有 M2 业务、命令、范围、WHOLE_ROOM、新审批角色或系统审计数据的空扩展。若已有这些数据，迁移主动拒绝，避免把整室教室静默开放给M1或丢失授权/审计；须先备份并由负责人制定数据回退方案。测试验证空库往返、旧M3/D数据升级保留和带数据回退拒绝。

## 10. 本次验证记录（2026-10-22）

- 本地 Python3.12.15、MySQL8.4.11、Node22.23.2。完整后端测试200项通过，其中保留原有145项 M3/D/公共测试。
- 测试使用专用 campus_resources_m2_flow_test；网络场景启动独立 D 进程和真实 TCP 服务，故障代理只干扰传输，不替换 D 资源逻辑。
- 已验证断连、丢响应、错误响应身份、本地确认事务失败、晚到创建、超时补偿不误释放 M3、并发审批以及多来源状态。
- 关键验证：临时去掉 M2 房间锁，锁等待测试失败；恢复源码后通过。
- Ruff、前端类型检查与生产构建通过。既有 Starlette/httpx 弃用及包体大小提示保留。
- 浏览器使用 student、campus_admin、m2_room_admin 实际完成两级流程；一级仅进入待二级，最终批准前出现真实 D HTTP 请求，审批意见和系统确认历史可见。
- 演示使用单独数据库 campus_resources_m2_flow，没有升级、清空或转换旧本地单级实验库。人工验收与团队评审仍由项目成员完成。
