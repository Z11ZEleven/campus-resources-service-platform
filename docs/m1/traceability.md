# M1 T01–T05 需求追踪

日期：2026-10-22；范围与契约见 [基础契约](contracts.md)。原父需求保留，未实现部分不从基线删除。

| 需求/任务 | 本轮交付与实现 | 验证入口 | 仍未完成 |
| --- | --- | --- | --- |
| T01；全部 M1 需求的追踪基础 | 技术 ADR、公共层 ADR、责任登记、PLAN_M1、contracts、本文 | 文档链接检查、实现与测试映射核对 | 人员责任到人、组内评审和人工验收 |
| T02；公共运行基础 | core/http、main、config、现有 Db、Vue/Vite、锁文件和 CI | test_http_foundation、完整 pytest、Ruff、npm build | 共享测试自动部署、生产审批 T17 |
| M1-001-B / T03 | D auth sessions/permissions/dependencies、0004、前端会话恢复/身份选择 | test_auth、test_auth_foundation、test_unit、浏览器检查 | M1 创建/本人业务授权在 T09 接入 |
| M1-006-A / T04 | D resources opening/management/router/models、0005、ResourceManagementView | test_opening、test_resource_management、test_resources、浏览器检查 | 已有预约免责取消/中断 T13 |
| M1-001-A / T04 基础 | 空间座位、固定/移动插座属性及受控维护 | test_resource_management、公开资源查询 | M1 专用时间/属性搜索和分页 T08 |
| M1-002-A/B/C / T05 数据 | Reservation、Repository、来源/占用、0006 | test_m1_models、test_allocations、test_migrations | 预约状态机、本人取消与幂等 HTTP T07/T09 |
| M1-003-A/B / T05 数据 | RoomCheckinCode、CheckinAttempt，不保存明文提交编码 | test_m1_models 的唯一性与字段校验 | 编码生成、合法窗口、实际限频 T10 |
| M1-004-A/B/C/D | 本轮仅保留需求 | 计划中的后续测试 | 通知、后台恢复和失效处理 T06/T11 |
| M1-005-A/B / T05 数据 | 历史读取 Repository、Violation 与事件结构 | test_m1_models | 本人历史 HTTP、历史复用和违约展示 T09/T14 |
| M1-006-B | 本轮仅保留需求 | 计划中的后续测试 | 管理参数与统计 T13/T15 |
| INT-001/002/003 基础 | 复用 D 来源事实、精确释放、同室来源叠加及迁移保留 | test_resources、test_allocations、既有 M3 回归 | 新来源版本/网络写回与真实跨模块验收 T12/T16 |

测试使用虚构用户和专用 MySQL `_test` 库。行为、约束、并发、迁移和关键规则破坏性验证的实际结果记录在 [验收记录](verification.md)，不能把表结构或健康检查当作完整业务测试。
