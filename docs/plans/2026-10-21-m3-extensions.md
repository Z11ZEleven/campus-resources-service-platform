# M3 超时升级、评价与站内通知实施计划

> 执行：按 Writing Plans 与 Verification Before Completion 技能逐项实现和验证。

**Goal:** 在现有真实报修闭环上补齐自动超时升级、一次性报修评价及真实站内通知。

**Architecture:** M3 保留五状态机和房间→工单锁顺序；升级与评价归 M3，通知采用 D 公共模块，由 M3 显式提供事件和收件人。同一数据库事务写业务及站内收件箱，失败整体回滚；扫描任务逐单事务，数据库幂等支持重启和多进程。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2、MySQL 8.4、Alembic；Vue 3、TypeScript、Element Plus。

## 已确认的本次规则

2026-10-21 用户在本次开发会话确认以下规则，作为本地实现依据，不声称已通过全组评审：

- 待派单、已派单、待验收分别超过 24 小时；维修中超过 48 小时。每阶段/返修轮次升级一次，通知全部有效物业管理员，工单状态不变。小时阈值由环境配置。边界定义为当前时间严格晚于截止时间。
- 阶段开始时间随实际状态转换更新，返修重新计时；重复请求、评价和升级不重置计时。
- 仅原报修人在验收完成后一次性提交 1～5 星、可选 500 字评语，提交后不可修改。相同内容重试返回原结果，内容不一致返回 409。
- 真实站内通知中心，提供本人通知、未读数量、已读、跳转与定时获取；不接入邮件或微信。
- 保留原完工请求格式和此前已记录限制；不增加撤销、转派、附件等功能；不创建 PR，不自行合并或推送。

## 数据与接口契约

追加迁移 `0003_m3_extensions`，父版本 `0002_m3_work_orders`：

1. `m3_work_orders` 添加 `stage_started_at DATETIME(3)`、`escalation_count INT UNSIGNED DEFAULT 0`。旧记录使用原 `updated_at` 回填阶段起点；启动扫描会补处理原已超时的工单。
2. `m3_work_order_ratings`：id、work_order_id（唯一 FK）、reporter_id（FK）、score（1～5）、comment（可空 500 字）、created_at。
3. `m3_work_order_escalations`：id、work_order_id（FK）、stage、completion_round、stage_started_at、due_at、escalated_at、reason（500 字）、triggered_by（可空用户 FK；NULL 表示自动系统任务）。唯一(work_order_id,stage,completion_round)，不冒用人工操作身份。
4. `d_notifications`：id、recipient_id（FK）、source_module、business_id、event_key、title、content、link、created_at、read_at；唯一(recipient_id,event_key)，支持本人未读和创建时间索引。站内收件箱提交即送达，不伪造外部投递状态。

M3 输出：WorkOrder 增加阶段时间和升级次数；详情增加 `rating`、`escalations`、`timeout`。

- `POST /api/v1/m3/work-orders/{id}/rating`，body `{score,comment}`，201；仅原报修人完成后操作。
- `GET /api/v1/m3/work-orders/timeout-policy`：`reported_hours/assigned_hours/repairing_hours/acceptance_hours/scan_interval_seconds/automatic_scan_enabled`。
- `POST /api/v1/m3/work-orders/scan-timeouts`：物业管理员手动补扫；返回 `{checked,escalated}`。
- `GET /api/v1/notifications?page=1&page_size=20&unread_only=false`：本人分页，含 `items/total/page/page_size/unread_count`。
- `GET /api/v1/notifications/unread-count`：`{unread_count}`。
- `POST /api/v1/notifications/{id}/read`：返回本人通知，重复已读幂等。
- `POST /api/v1/notifications/read-all`：返回 `{updated,unread_count}`，只处理本次开始前的通知。

RatingView：`id/work_order_id/reporter_id/score/comment/created_at`。
EscalationView：表字段对应 JSON，ID 字符串，triggered_by 可空。
TimeoutInfo：`due_at`（完成时 NULL）、`is_overdue`、`current_stage_escalated`。
NotificationView：表字段对应 JSON，ID 字符串。

通知事件：首次上报、派单、开工、每轮完工/验收通过/驳回、评价分别通知报修人、指定维修人和有效物业管理员的去重并集；超时仅通知物业管理员。重放不得重复通知。历史工单不回溯补发状态通知。

## Task 1：迁移和公共通知

- 新建 `backend/app/modules/notifications/{models,schemas,service,router}.py`，只处理 D 通用通知，不导入 M3 私有表。
- 追加迁移并注册模型，更新迁移精确断言，验证旧用户/工单保留、空库回放和单步回退。
- 通知 service `publish_notifications(session, *, recipients, source_module, business_id, event_key, title, content, link)`，只 flush，过滤无效收件人并使用稳定事件键去重。
- 编写真正 MySQL 通知越权、已读幂等、重复发布、失败回滚测试。

## Task 2：M3 业务和自动任务

- 增加 M3 模型/输出、评价接口和升级扫描；现有状态转换发布通知并刷新阶段起点。
- 新增 `backend/app/modules/m3/timeouts.py`：`run_timeout_scan(*, now=None, limit=200)`，返回 checked/escalated；候选无锁读取后，逐单按房间→工单锁定，复查状态、截止和幂等事件。按未处理候选分页，避免已升级记录占满批次饿死后续工单。
- `backend/app/modules/m3/worker.py`：`timeout_worker(stop)`，异步循环调用同步扫描；停止时等待本轮完成；失败留待下一轮补扫。
- 启动 lifespan 接入自动扫描；测试显式关闭自动 worker，避免污染并发与迁移测试。
- 真实数据库验证全部阶段边界、返修计时、重复扫描、多线程并发、扫描与正常流转竞态、评价权限/不可改/并发唯一性，以及通知失败整体回滚。

## Task 3：前端

- 工单列表显示升级次数；详情显示本阶段截止、升级记录、系统/人工触发及一次性评价表单和已提交结果。
- 新增通知中心，主导航未读提示；分页、仅未读、单条/全部已读及安全站内跳转；轮询不叠加，登出/切用户取消请求和清空旧状态。
- 使用原有视觉与组件；新增功能适配手机，所有用户内容按纯文本展示。

## Task 4：验证与本机启用

- `python -m pytest --require-mysql -q`：专用 `campus_resources_m3_test`，不对开发库清理。
- `python -m ruff check app scripts tests alembic`；`npm run build`；`git diff --check`。
- 至少对“评价仅报修人”或“超时幂等”保护执行一次破坏实现→测试失败→恢复→重新通过。
- 独立测试后端真实浏览器验证通知、标记已读、评价、超时升级和权限；保留截图与实际结果。
- 开发库执行版本化迁移，启用扫描配置并重启后端；保留现有用户密码与业务数据。
- 更新中英文标识一致的中文接入说明和需求追踪，记录限制；无 PR、推送或生产发布。
