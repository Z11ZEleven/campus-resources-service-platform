# M1 需求追踪

日期：2026-10-22；T06 更新：2026-10-31；T07–T10 更新：2026-11-11；范围与契约见 [基础契约](contracts.md)。原父需求保留，未实现部分不从基线删除。

| 需求/任务 | 本轮交付与实现 | 验证入口 | 仍未完成 |
| --- | --- | --- | --- |
| T01；全部 M1 需求的追踪基础 | 技术 ADR、公共层 ADR、责任登记、PLAN_M1、contracts、本文 | 文档链接检查、实现与测试映射核对 | 人员责任到人、组内评审和人工验收 |
| T02；公共运行基础 | core/http、main、config、现有 Db、Vue/Vite、锁文件和 CI | test_http_foundation、完整 pytest、Ruff、npm build | 共享测试自动部署、生产审批 T17 |
| M1-001-B / T03 | D auth sessions/permissions/dependencies、0004、前端会话恢复/身份选择 | test_auth、test_auth_foundation、test_unit、浏览器检查 | 团队评审及人工验收 |
| M1-006-A / T04 | D resources opening/management/router/models、0005、ResourceManagementView | test_opening、test_resource_management、test_resources、浏览器检查 | 已有预约免责取消/中断 T13 |
| M1-001-A/B / T08 提案 | M1 搜索、时段校验及 D 资源可用性判断 | test_m1_search、test_admin_restrictions、test_admin_migration_recovery（真实 MySQL）、test_resource_management | 组内评审及人工验收待完成 |
| M1-002-A/B/C / T05 数据 | Reservation、Repository、来源/占用、0006 | test_m1_models、test_allocations、test_migrations | T09 已接入创建、本人取消与幂等 HTTP；T11 后续状态及跨模块处理待完成 |
| M1-003-A/B / T05 数据 | RoomCheckinCode、CheckinAttempt，不保存明文提交编码 | test_m1_models 的唯一性与字段校验 | T10 已实现编码、窗口与限频；团队评审及人工验收待完成 |
| M1-004-A/B/C/D / T06 | `d_outbox_events`、公共登记/领取/完成/重试/故障查询、独立 Worker、M1 通知/审计适配、0007；站内通知为真实收件箱 | test_outbox、test_migrations、Worker `--once` 冒烟 | T11 定时业务处理、外部邮件/微信、故障管理 HTTP/告警 T16/T17 |
| M1-005-A/B / T05 数据 | 历史读取 Repository、Violation 与事件结构 | test_m1_models | 本人历史 HTTP 与复用已接入 T09；违约展示 T14 待完成 |
| M1-006-B | 本轮仅保留需求 | 计划中的后续测试 | 管理参数与统计 T13/T15 |
| INT-001/002/003 基础 | 复用 D 来源事实、精确释放、同室来源叠加及迁移保留 | test_resources、test_allocations、既有 M3 回归 | 新来源版本/网络写回与真实跨模块验收 T12/T16 |

测试使用虚构用户和专用 MySQL `_test` 库。行为、约束、并发、迁移和关键规则破坏性验证的实际结果记录在 [验收记录](verification.md)，不能把表结构或健康检查当作完整业务测试。

T09 提案（待组内评审）：M1-002-A/C 与 M1-005 的本人创建、取消、列表/详情和历史复用由 `m1/reservations.py`、`m1/router.py` 实现，真实 MySQL 验证入口为 `tests/test_m1_reservations.py`。M1-002-B 签到已由 T10 实现；M1-004 的实际定时通知/违约处理属 T11，尚未完成；四条任务的持久登记不等于业务处理完成。公共资源接口及该提案仍需团队评审和人工验收。

T10 提案（待组内评审及人工验收）：M1-003-A/B 签到码、限频及 M1-002-B 签到由 `backend/app/modules/m1/checkin.py` 和 `backend/app/modules/m1/router.py` 实现，行为验证见 `backend/tests/test_m1_checkin.py`。T11 的超时、违约和实际定时通知仍未实现；进程级重启、超时任务竞争及跨模块网络验收待补充。

T07 资源裁决内核已实现：教室锁、来源冲突检查、精确释放、版本墓碑及来源乱序处理；0008 迁移和 `test_resources` 的版本及墓碑测试提供证据。T12 的 HTTP 操作记录与核对、真实网络入口和维修免责联动仍未完成。T08 搜索及独立管理员时段限制来源已实现，搜索与创建共享可预约判定。迁移至 0011；权限轮换的安全授权步骤见 [契约](contracts.md)。T11 处理器未注册，`m1_timer` 保持 PENDING，不代表定时业务已执行。T11–T17、真实跨模块网络和人工验收均未完成。
