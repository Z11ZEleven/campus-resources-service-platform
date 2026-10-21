# M3 超时升级、评价与站内通知

本说明对应 2026-10-21 的本地实现，基于 [实施计划](plans/2026-10-21-m3-extensions.md)、[M3 后端](m3-backend.md)和[前端](m3-frontend.md)。用户在本次开发会话确认了下列业务规则；这不代表全组已完成评审。

新增 D 站内通知表、公共服务和 HTTP 接口属于本次用户确认的**本地实现提案，待团队评审**。其影响范围包括公共模型注册、迁移、应用启动、导航与通知中心；未据此指定新的公共层负责人或改变共享归属决策。

## 1. 已实现的规则

### 超时升级

| 当前状态 | 默认时限 | 起点 |
|---|---|---|
| `REPORTED` 待派单 | 24 小时 | 首次上报 |
| `ASSIGNED` 已派单 | 24 小时 | 实际派单 |
| `REPAIRING` 维修中 | 48 小时 | 首次开工或本次验收驳回 |
| `WAIT_ACCEPTANCE` 待验收 | 24 小时 | 本轮提交完工 |
| `COMPLETED` 已完成 | 不再计时 | 无截止时间 |

按连续经过的小时计算，所有时间存 UTC `DATETIME(3)`，页面显示 Asia/Shanghai。只有服务器当前时间**严格晚于** `due_at` 才超时；正好等于截止时间不升级。

每次真实状态转换更新 `stage_started_at`；驳回返修重新计时。重复开工、已通过验收的合法重放、评价和升级均不重置起点。修改阈值会重新影响尚未结束阶段的截止时间，已有升级记录保留当时的起点、截止与原因。

同一 `(work_order_id, stage, completion_round)` 最多升级一次。升级增加累计次数、保存记录并通知全部当前有效的 `PROPERTY_ADMIN`，**保持工单状态和维修来源不变**。无有效物业管理员时保留升级记录，不伪造收件人或外部送达。

默认自动扫描每 60 秒执行一轮；应用启动会开始扫描已超时的工单。自动任务的 `triggered_by` 为 `null`，物业管理员手动补扫保存本人 ID。扫描先读取至多 200 条尚未处理的候选，随后逐单按房间锁 → 工单锁复查当前状态、阶段时间和去重记录。每单独立提交；单条失败回滚并写服务器错误日志，留待下轮重试，已成功的其他工单不回滚。返回的 `checked/escalated` 是本轮候选检查与成功升级数，不是所有未结工单总数。

### 一次性评价

- 仅原报修人可在 `COMPLETED` 后提交；管理员与指定维修人员可查看已获授权工单的评价，不能代评。
- 评分为整数 1～5，评语选填、最多 500 字，首尾空白清除；空评语归一化为 `null`。
- 每单最多一条。相同内容重试返回原结果；内容不同返回 409，不能修改既有评价。
- 工单锁和唯一约束保证并发提交只保留一条；评价、操作历史和通知在同一事务提交。
- 前端成功后显示已确认评价并刷新完整处理历史。网络或服务器错误时先获取最新详情核对，不自动重发；核对失败时保留输入并要求手动重新核对。

### 站内通知

首次上报、派单、开工、每轮完工、验收通过/驳回、评价，通知原报修人、当前指定维修人员、全部有效物业管理员的去重并集；上报时尚未派单，不添加虚构维修人员。超时升级仅通知有效物业管理员。停用用户或停用身份不接收新通知。历史工单不追溯补发既往状态通知。

通知收件箱记录与对应业务在同一数据库事务提交，提交即站内送达；失败整体回滚。稳定事件键与 `UNIQUE(recipient_id, event_key)` 防止重放生成重复消息，重复发布不改变原创建时间或已读状态。事件键冲突且内容不同会返回业务冲突。

通知查询及已读操作只作用于当前登录用户；单条已读重复执行保留首次已读时间。全部已读先以非锁定查询快照当时可见的本人未读 ID，再分批更新该集合；快照后才提交送达的新通知保持未读，不以时间戳或最大 ID 冒充收件箱快照。

这里的通知为真实站内收件箱，**没有邮件、短信、微信或系统桌面推送**，也没有外部通道投递、重试或回执服务。

## 2. 数据结构与迁移

追加迁移 `0003_m3_extensions`，父版本 `0002_m3_work_orders`，不重建原用户、公共资源或工单表：

| 对象 | 新增内容与约束 |
|---|---|
| `m3_work_orders` | `stage_started_at DATETIME(3)` 非空、`escalation_count INT UNSIGNED DEFAULT 0`；阶段/起点查询索引 |
| `m3_work_order_ratings` | `id/work_order_id/reporter_id/score/comment/created_at`；工单唯一外键，评分 1～5 检查 |
| `m3_work_order_escalations` | `id/work_order_id/stage/completion_round/stage_started_at/due_at/escalated_at/reason/triggered_by`；阶段与轮次检查，工单/阶段/轮次唯一 |
| `d_notifications` | `id/recipient_id/source_module/business_id/event_key/title/content/link/created_at/read_at`；收件人/事件键唯一，本人未读查询索引 |

旧工单的阶段起点用原 `updated_at` 回填，适用于原版本只在状态实际变化时更新该字段的记录；升级不追溯重建不存在的旧阶段升级历史。ID 在 JSON 中仍序列化为字符串，可空字段返回 `null`。

在 `backend` 目录、使用已有本地配置执行：

```powershell
python -m alembic upgrade head
python -m alembic current
```

代码期望的最新版本为 `0003_m3_extensions (head)`。执行应用前先迁移；自动 worker 不会代替建表。本机开发库已按下文记录升级，其他环境需各自执行并核验，不能由本机结果推定。

回退到 `0002_m3_work_orders` 会删除评价、升级、全部 D 通知及工单新增字段，基础工单与原公共表保留；回退会丢失这些扩展数据，不能作为无损撤销操作。

## 3. HTTP 接口

均需 Bearer 登录令牌，继续使用公共错误结构。M3 原七个接口保留，新增：

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/m3/work-orders/{id}/rating` | 请求 `{ "score": 5, "comment": "维修及时" }`；201，`RatingView` |
| GET | `/api/v1/m3/work-orders/timeout-policy` | 任意当前有效登录用户读取当前时限配置 |
| POST | `/api/v1/m3/work-orders/scan-timeouts` | 仅 `m3:assign`（物业管理员），无请求体；`{checked,escalated}` |
| GET | `/api/v1/notifications` | 本人分页；`page=1`、`page_size=20`（最大 100）、`unread_only=false` |
| GET | `/api/v1/notifications/unread-count` | `{unread_count}` |
| POST | `/api/v1/notifications/{id}/read` | 无请求体；返回本人该条 `NotificationView`，不存在或不属于本人返回 404 |
| POST | `/api/v1/notifications/read-all` | 无请求体；`{updated,unread_count}`，其中 updated 为本次实际改为已读数 |

`RatingView`：`id/work_order_id/reporter_id/score/comment/created_at`。

`EscalationView`：上表全部升级字段，`triggered_by` 可空。

`NotificationView`：上表全部通知字段，`source_module` 当前允许 `D/M1/M2/M3`；当前 M3 发布的安全链接为 `/m3/work-orders/{id}`，事件键带 `M3:` 命名空间。分页返回 `{items,total,page,page_size,unread_count}`，未读数为当前账号全部未读量，独立于分页与未读筛选。

工单列表/写响应增加 `stage_started_at/escalation_count`；详情还增加 `rating`（无则 `null`）、`escalations`（无则空数组）、`timeout`：

```json
{
  "due_at": "2026-10-22T01:00:00Z",
  "is_overdue": false,
  "current_stage_escalated": false
}
```

完成后 `due_at=null`，其余两个字段为 `false`。详情中的超时判定是获取响应时的服务器结果，不是浏览器自行推进的倒计时。

评价未完成、修改已有评价、事件键内容冲突返回 409；代评返回 403，无权查看的工单或通知返回 404；评分/评语非法返回 422。扫描的单单错误记录在后端日志，返回统计不包含错误详情和敏感 SQL 参数。

## 4. 配置与任务运行

根目录或 `backend/.env` 使用下列变量；示例没有真实凭据：

```dotenv
M3_REPORTED_TIMEOUT_HOURS=24
M3_ASSIGNED_TIMEOUT_HOURS=24
M3_REPAIRING_TIMEOUT_HOURS=48
M3_ACCEPTANCE_TIMEOUT_HOURS=24
M3_TIMEOUT_SCAN_INTERVAL_SECONDS=60
M3_TIMEOUT_SCAN_ENABLED=true
```

小时范围 1～8760，扫描间隔 5～3600 秒。修改后需重启后端。自动 worker 在 FastAPI lifespan 内用线程运行同步数据库扫描，避免阻塞事件循环；正常关闭时等待当前轮结束。多个应用实例可能各自扫描，数据库行锁与唯一约束保证同阶段不重复升级。自动关闭不禁止有权管理员手动补扫。测试应显式设置 `M3_TIMEOUT_SCAN_ENABLED=false`，避免 worker 与测试清理互相干扰。

## 5. 前端使用

- `/m3/work-orders`：显示累计升级次数与配置时限；物业管理员可“补扫超时工单”。
- `/m3/work-orders/:id`：显示阶段起点、截止、超时/已升级标记、升级历史、系统或人工触发人，以及评价表单或已提交结果。
- `/notifications`：当前账号通知，全部/未读筛选、分页、单条/全部已读和跳转工单；导航显示未读徽标。

全局每 30 秒查询未读数量，请求不叠加；列表收到新消息提示后可刷新。请求票据含用户会话与版本，拒绝旧列表覆盖新轮询；登出/切用户取消请求并清空旧状态。通知链接仅允许本站 M3 工单路径。页面不把用户内容渲染为 HTML，手机布局与桌面使用同一后端。

## 6. 本地验证记录（2026-10-21）

下列为本次开发会话的真实本地验证，不代表远端 CI、全组评审或生产验收：

- Python 3.12 / MySQL 8.4，专用 `campus_resources_m3_test`：后端完整测试 **145 passed**，Ruff 通过。覆盖阶段严格边界、返修重计时、重复/并发扫描、状态转换竞态、评价权限/并发唯一性/不可改、通知去重/回滚/越权、延迟提交不被全部已读吞掉，以及迁移往返。
- 暂时移除“仅原报修人可评价”保护后，对应测试失败；恢复源码原始字节后通过。通知 store 的旧列表覆盖新轮询脚本先暴露失败、修复后通过。
- 独立测试后端 8002 与 Vite 5174 真实浏览器验证：五星评价、不可修改、处理历史自动刷新，通知单条/全部已读、未读筛选及跳转工单；30 秒轮询显示新通知提示，刷新保留仅未读筛选。
- 人工补扫将维修中超时工单升级一次，工单仍为 `REPAIRING`、维修来源仍 `ACTIVE`；测试后端 worker 启动后自动升级待派单工单一次，`triggered_by=null`，仅通知有效物业管理员。
- 桌面 1280 和手机 390 宽度通知中心无页面横向溢出；手机 390 宽度评价/处理历史面板同样无横向溢出。截图与运行证据保存在本机 `outputs/m3-extensions-20261021`，不作为仓库内可移植测试资源。
- 最终修复后 `npm run build`（含 `vue-tsc`）通过；Element Plus 整体导入仍产生大包体积提示。日常后端 8001、前端 5173 已就绪；独立测试端口 8002/5174 已停止。
- 本机开发库 `campus_resources` 已升级至 `0003_m3_extensions`，原九张表每个原字段的内容哈希完全一致，保留原用户密码和业务数据；行数分别为用户 6、身份 6、角色 6、房间 2、座位 6、汇总 144、工单 0、日志 0、来源 0。`alembic check` 通过，后端 8001 已重启，启用每 60 秒自动扫描。

复跑命令与测试库隔离要求见 [后端指南](../backend/README.md)。勿将测试清理指向日常 `campus_resources`。

## 7. 剩余范围

原设计的提交完工请求仍只有 `completion_note`：迟到旧请求在驳回返修后可能被当作下一轮完工，前端不自动重发只能减少触发，不能消除此限制；详见 [M3 后端第 8 节](m3-backend.md#8-已记录的原设计限制)。

附件、细粒度管理范围、撤销、转派、完成后重开、完整独立业务事件流、M1/M2 业务网络集成和跨进程可靠补偿仍未实现。站内消息和同库事务不等同于跨模块公开网络调用、外部通知可靠投递或生产自动部署；新增 D 契约仍需团队评审。
