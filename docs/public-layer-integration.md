# 公共层 D 接入指南

2026-10-31：M1 T06 新增 D 公共 Outbox、独立 Worker、站内通知接收和按 `USER/SERVICE/SYSTEM` 区分的审计接收，迁移至 0007。此前 T01–T05 扩展了 D 会话、身份/范围授权、主数据维护与开放规则、资源占用明细以及 M1 五张模型表。以下首次迭代说明是历史边界；新增接口与升级兼容以 [M1 基础契约](m1/contracts.md) 为准。M1 预约/签到业务及实际 M2/M3 HTTP 写回仍待后续任务。

## 1. 已交付边界

第一次迭代实现 MySQL 公共层的 8 张 D 表、版本化迁移、种子、认证和查询 API、资源公开函数、最小操作记录及公共 Vue 示例。随后在 `0003_m3_extensions` 中新增 D 站内通知表/服务/API/前端中心，T06 在 `0007_outbox_audit` 新增 `d_outbox_events` 并扩展操作记录，当前为 **10 张 D 表**；M3 已实现 3 张业务表、报修闭环、超时任务与评价。公共字段基线见[数据库统一契约](database-contract.md)，M1/M2/M3 私有业务表与状态仍由各模块实现。

新增公共 D 通知、Outbox 和审计操作者字段为本次用户确认的本地实现提案，影响公共模型注册、迁移、应用启动与运维方式，**待团队评审**，不宣称已形成团队共识或变更共享负责人。规则与验收见 [M3 扩展说明](m3-extensions.md)与 [M1 基础契约](m1/contracts.md)。

资源服务是唯一资源写入入口。业务单存在性、归属、当前合法状态与人数/负责人等业务规则，必须由各模块先验证。没有向普通用户开放直接创建或释放来源的 HTTP 接口。

## 2. 固定公共契约

| 项目 | 约定 |
|---|---|
| MySQL | 8.4，InnoDB，utf8mb4 |
| 事务 | READ COMMITTED，先房间锁，再业务单锁 |
| ID | 数据库 BIGINT UNSIGNED；Python int；HTTP JSON string |
| 时间 | 输入 aware datetime，存 DATETIME(3) UTC，输出含 Z，页面北京时间 |
| 角色 | USER、ROOM_ADMIN、PROPERTY_ADMIN、REPAIRER、SYS_ADMIN |
| 身份 | STUDENT、TEACHER、STAFF；多身份由会话选定且校验归属 |
| 错误 | 所有 HTTP 错误 → detail.code/message/request_id；422 附安全 fields；响应头 X-Request-ID |
| 真实来源 | d_resource_blocks，唯一 (source_module, source_id) |
| 汇总 | 按需生成、按房间 resource_version 失效；不是最终冲突依据 |

学生预约还须有 USER；教师申请还须有 USER；ROOM_ADMIN 审批；REPAIRER 开始/完工；PROPERTY_ADMIN 派单/验收。SYS_ADMIN 不隐式拥有全部业务能力。

## 3. HTTP 接入

```python
from fastapi import APIRouter
from app.modules.auth.dependencies import CurrentUser, Db
from app.modules.auth.permissions import require_permission

router = APIRouter(prefix="/api/v1/m1", tags=["M1"])
# 在 app/main.py 引入并 app.include_router(router)。
# 路由使用 session: Db、actor: CurrentUser。
# actor.user_id、identity_type、department_code、roles 来自已验证身份。
# actor.require("m1:create") 校验固定权限；业务归属还要自己检查。
```

Db 依赖在请求进入时开启一个事务，成功响应发出前提交；异常则回滚。禁止在模块里 session.commit()、session.begin()、使用 begin_nested() 冒充完整业务事务，或换另一个 Session。

脚本/后台调用使用：

```python
from app.core.database import get_session_factory
with get_session_factory()() as session, session.begin():
    # 同一个 session 传给模块函数和 D 函数
    pass
```

## 4. 资源函数签名与职责

所有函数位于 app.modules.resources.service，不自行提交。

| 函数 | 参数要点 | 作用 |
|---|---|---|
| lock_room | session、room_id | SELECT FOR UPDATE，先于业务单锁调用 |
| reserve_seat | room_id、seat_id、reservation_id、start_at、end_at、actor | 学生个人占用、同座位/整室冲突检查 |
| occupy_room | room_id、application_id、applicant_id、start_at、end_at、actor | 管理员批准整室占用；验证教师资格与院系，拒绝自审 |
| start_maintenance | room_id、work_order_id、actor | 指定维修人员的模块校验后添加维修限制 |
| release_source | room_id、source_module、source_id、actor | 只释放本来源；模块校验合法取消/验收在前 |
| availability | room_id、day、actor | 持房间锁校验可见性并按需重算汇总 |
| set_room_active | room_id、is_active、actor | 内部管理钩子，按锁更新版本 |
| set_seat_active | room_id、seat_id、is_active、actor | 内部管理钩子，按锁更新版本 |

函数均接收同一个 session。M1 的 reserve_seat 会检查身份、时长、开放时间和院系；M2 的 occupy_room 会读取 applicant_id 对应当前身份。M3 的 start_maintenance 不知道工单 assignee_id，调用方必须验证当前用户确实被分配到该单。

release_source 不知道业务单属于谁，调用方必须校验所有权/验收权。不能把它直接映射成允许终端用户传模块和来源 ID 的通用路由。

## 5. M1 创建示例（业务模型由 M1 组补齐）

下面是模块编写模板，不是已提供的 M1 接口；Reservation、ReservationView 和请求模型需由 M1 自行实现。

```python
from sqlalchemy import select
from app.modules.resources.service import lock_room, reserve_seat
from app.modules.audit.service import record_operation

def create_reservation(body, session, actor):
    actor.require("m1:create")
    lock_room(session, body.room_id)
    # 房间锁后，检查用户+request_key。相同内容返回原对象，
    # 不同内容返回 409；不要复用其他用户的请求结果。
    reservation = Reservation(
        user_id=actor.user_id, room_id=body.room_id, seat_id=body.seat_id,
        start_at=to_utc(body.start_at), end_at=to_utc(body.end_at),
        status="RESERVED", request_key=body.request_key,
    )
    session.add(reservation)
    session.flush()  # 得到 ID，尚未提交
    reserve_seat(
        session, room_id=reservation.room_id, seat_id=reservation.seat_id,
        reservation_id=reservation.id, start_at=body.start_at, end_at=body.end_at,
        actor=actor,
    )
    record_operation(
        session, module="M1", business_id=reservation.id,
        action="CREATE_RESERVATION", actor_id=actor.user_id,
        to_status="RESERVED",
    )
    return ReservationView.model_validate(reservation)
```

任意一步失败，依赖回滚业务单、来源、日志和房间版本。D 已记录资源获得/释放事件，模块仍需记录自身业务状态变化。

取消：从本人可见记录取得不可变 room_id → lock_room → 锁预约 → 检查所有权、开始前和当前状态 → release_source(M1) → 更新状态并记录业务事件。

## 6. M2 与 M3 调用时机

| 模块 | 校验与操作 |
|---|---|
| M2 审批 | 房间锁 → 锁申请 → 复查 PENDING、审批角色、非自审、容量 → occupy_room → 改 APPROVED、写业务历史 |
| M2 冲突 | DomainError 409，整个请求回滚，申请保持待审；不要 catch 后继续提交 APPROVED |
| M3 开始 | 房间锁 → 锁工单 → 复查 ASSIGNED、assignee_id 等于 actor.user_id → start_maintenance → 改 REPAIRING |
| M3 完工 | 保留维修来源，不调用 release_source |
| M3 驳回 | 保留同一来源，回到 REPAIRING，保存原因和轮次 |
| M3 验收 | 房间锁 → 锁工单 → 复查 WAIT_ACCEPTANCE、验收角色、非自验收、批次 → release_source(M3) → 改 COMPLETED |

主单的 room_id 和占用时段不得在审批/维修中随意更改；如以后支持改期，必须增加专门变更事务。

## 7. 幂等、并发和恢复

- 同来源相同有效资源重复调用返回原记录；不同资源/时段返回 409。
- 已释放来源不可重新激活。release_source 重复释放返回当前已释放记录。
- 相邻 [09:00,10:00) 与 [10:00,11:00) 不冲突。
- M1 只与本座位或整室来源冲突，同室别的座位不阻塞。
- M2 与同室任一重叠来源冲突。
- M3 允许与已有预约并存，首轮不取消已有预约，阻止后续新预约。
- 验收一个维修单只撤销一个来源；有其他维修/M2 占用时仍不可预约。
- 所有变更在同一房间行锁下写入，房间锁之前不先锁业务单。
- 所有来源和启停变更增加 resource_version；汇总是查询缓存，提交不依赖 AVAILABLE。
- d_user_identities、d_rooms 等字段的维护不能绕过公共规则；T04 已增加受控资源编辑 API，冲突变更返回409，免责业务仍待后续实现。

### D 站内通知公共服务（本地提案，待团队评审）

`app.modules.notifications.service.publish_notifications(session, *, recipients, source_module, business_id, event_key, title, content, link)` 复用调用方同一事务，只 flush、不 commit，不读取 M3 私有表。业务模块先验证操作、收件人和对应事件；D 过滤停用用户/身份，再写真实站内收件箱。`source_module` 允许 `D/M1/M2/M3`，事件键自动添加模块前缀，调用方不应再添加相同前缀；使用稳定键，同一收件人/事件只生成一条。重复同内容保留首次通知和已读时间，异内容冲突使业务事务一并回滚。

`business_id` 为正 ID；调用方事件键最长 150 字符，标题 160、正文 1000、链接 200。链接须为服务端允许的站内路径，不能使用外部 URL。当前 M3 提供 `/m3/work-orders/{id}`，前端也只允许此类通知跳转。通知内容应为用户可理解的纯文本，不包含凭据。

本人 HTTP 接口：`GET /api/v1/notifications`、`GET /api/v1/notifications/unread-count`、`POST /api/v1/notifications/{id}/read`、`POST /api/v1/notifications/read-all`。全部已读只更新当时可见未读 ID 快照；单条重复操作幂等。参数、输出、当前通知收件人与严格超时边界见 [扩展 API](m3-extensions.md#3-http-接口)。

收件箱事务提交即站内送达；这不是邮件、微信等外部通道，也不是跨模块网络投递。当前 M3 与 D 同库同事务，站内通知失败不能 catch 后继续提交业务成功。

### D 公共 Outbox 与审计适配（本地提案，待团队评审）

业务模块用 `app.modules.outbox.service.register_event` 在自身事务内登记任务，唯一键为 `(source_module, event_key)`；同载荷重试幂等，改载荷/处理器/业务版本返回 409，业务回滚会同时撤销任务。D 的独立 Worker（`python -m app.worker`）用 `FOR UPDATE SKIP LOCKED` 领取到期或租约过期任务，写租约令牌、到期时间、尝试次数和乐观版本。完成/重试/转人工都必须匹配令牌和版本，旧 Worker 不能覆盖新结果；进程崩溃后租约到期可被重新领取。

任务状态为 `PENDING/PROCESSING/SUCCEEDED/MANUAL`。瞬时失败按 5–300 秒指数退避；达到上限或不可重试错误进入 `MANUAL`，保留错误分类和最后错误，可用 `list_failures` 查询后人工处理。通知和审计适配默认最多 6 次；必须无限重试的业务定时/释放任务可显式传 `max_attempts=None`，不与投递策略混用。

`deliver_notification` 写真实 `d_notifications` 站内收件箱，靠收件人+模块事件键去重；重投不会重复通知或重置已读时间。`receive_audit` 扩展 `record_operation`：用户审计为 `USER + actor_id`，服务主体为 `SERVICE + service_principal_id`，Worker/系统动作为 `SYSTEM` 且不伪造用户；`(module, event_key)` 唯一使重投幂等。审计仍有服务层读取函数，未新增公共 HTTP 管理页面。

## 8. 前端接入

复用 src/api/request.ts、stores/auth.ts、公共布局和 router；ID 全部使用 string。将 M1/M2/M3 路由放在现有 catch-all 前，表单按钮按角色显示，最终以服务端结果为准。

可用性接口返回 seats 和 slots，每格包含 slot_start、slot_end、status、source_count、source_module、primary_block_id、resource_version、bookable。bookable 仅为展示提示，仍需提交时检查。

不要使用浏览器本地时区推断学校时间；明确 Asia/Shanghai。不要把用户的 user_id、role_code 当作可信提交字段。

## 9. 验证与限制

公共层首轮曾使用真实 MySQL 8.0.46 验证；2026-10-31 在本机 MySQL 8.4 专用测试库完整验证 **216 passed**、Ruff 和 `alembic check` 通过。2026-10-21 M3 扩展记录为 145 passed。远端 CI 和 PR 检查结果仍以 GitHub 当次实际运行结果为准。

已配置的测试覆盖认证、权限、事务回滚、真实房间行锁、并发预约、M2/M3 占用、幂等、来源恢复、汇总重算、MySQL 外键、迁移往返、Outbox 租约恢复/并发领取/重试以及通知和审计重投幂等。前端执行 TypeScript 检查与生产构建。

M3 已在公共层之上实现工单/评价/升级表、十个 M3 API、真实前端、维修来源联动，以及自动超时任务与站内通知，见 [M3 后端](m3-backend.md)、[前端](m3-frontend.md)与 [扩展说明](m3-extensions.md)。T06 已补公共 Outbox、独立 Worker、站内通知和审计适配。仍待完成：M1 业务 API 与预约通知触发点、M2 业务主表与 API、附件、细粒度范围、撤销/转派/重开、外部通知、Outbox 故障管理 HTTP/指标、真实模块间网络集成、自动部署及生产发布。现有同进程资源函数、数据库 Outbox 及同库站内通知不等于完成课程要求的跨模块网络链路。

## 10. T01–T06 会话、授权与任务升级

Cookie 登录返回 authenticated，不返回原 access_token；前端从统一会话恢复当前用户，修改请求附会话绑定 CSRF。旧 JWT HTTP 凭据升级后失效，所有调用方重新登录。测试用的 token_headers 创建真实数据库会话，不再签发虚构有效 JWT。

Principal.require/has 仅对全局授权成立；房间范围使用 require_for_room/can_for_room；SQL 用 room_scope_predicate，不能把独立角色的动作和范围拼接。旧 M3 只支持原全局业务角色；新局部授权保守拒绝，完整适配单列后续任务。服务主体与用户会话分离，使用 require_service(module) 或 authenticate_service 验证模块与房间。

0006 来源写入生成占用明细并精确释放；历史逻辑 M1 来源允许空 typed 业务关联，不创造预约事实。现有 D 单服务事务不等于真实跨模块网络验收。

## 资源来源版本扩展（T07，待团队评审）

迁移 `0008_resource_revision` 和资源服务增加可选非负整数 `revision`，影响 M1/M2/M3 调用方和公共数据库契约，须经团队评审。历史来源以版本 0 保留，旧 M3 调用可省略版本；来源首次显式提供版本后持久化为版本化来源，此后无版本请求一律拒绝，旧式来源仍可按旧协议使用；版本范围为 0 至 2^64−1，超出或递增溢出拒绝；旧式释放未知来源仍返回不存在。显式版本的未知来源释放建立终态墓碑，禁止后续激活；M2 释放必须携带原始 `start_at`、`end_at`，包括未知来源墓碑，相同来源的时段必须一致；M1/M3 释放不接受时段；时间转换为 UTC 后向下截断到毫秒，重试按该精度比较；低版本拒绝，同版本仅相同状态与载荷可重试，高版本可跳号但不能改变资源载荷或复活。释放仅影响对应来源；冲突不推进版本。来源写入与房间版本、审计处于调用方同一事务中。跨房间同来源并发由唯一约束保护，内部保存点仅隔离插入冲突，不能代替业务事务。版本推进同步记录房间资源版本和审计。降级前必须处理无法由旧结构表示的墓碑和非零来源版本或版本模式标识，迁移会拒绝有损降级。
