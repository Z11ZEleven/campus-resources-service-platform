# 公共层 D 接入指南（第一次功能迭代）

## 1. 已交付边界

公共层使用 MySQL，已实现精简方案的 8 张 D 表、版本化迁移、种子、认证和查询 API、资源公开函数、最小操作记录，以及可运行的公共 Vue 示例。M1/M2/M3 的业务表和业务单状态由各组实现。

资源服务是唯一资源写入入口。业务单存在性、归属、当前合法状态与人数/负责人等业务规则，必须由各模块先验证。没有向普通用户开放直接创建或释放来源的 HTTP 接口。

## 2. 固定公共契约

| 项目 | 约定 |
|---|---|
| MySQL | 8.4，InnoDB，utf8mb4 |
| 事务 | READ COMMITTED，先房间锁，再业务单锁 |
| ID | 数据库 BIGINT UNSIGNED；Python int；HTTP JSON string |
| 时间 | 输入 aware datetime，存 DATETIME(3) UTC，输出含 Z，页面北京时间 |
| 角色 | USER、ROOM_ADMIN、PROPERTY_ADMIN、REPAIRER、SYS_ADMIN |
| 身份 | STUDENT、TEACHER、STAFF；首轮每用户一个有效身份 |
| 错误 | DomainError → detail.code、detail.message；422 字段校验保留 FastAPI 格式 |
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
- d_user_identities、d_rooms 等字段的直接维护不能绕过公共规则；本轮资源由种子初始化，完整编辑 API 后续实现。

## 8. 前端接入

复用 src/api/request.ts、stores/auth.ts、公共布局和 router；ID 全部使用 string。将 M1/M2/M3 路由放在现有 catch-all 前，表单按钮按角色显示，最终以服务端结果为准。

可用性接口返回 seats 和 slots，每格包含 slot_start、slot_end、status、source_count、source_module、primary_block_id、resource_version、bookable。bookable 仅为展示提示，仍需提交时检查。

不要使用浏览器本地时区推断学校时间；明确 Asia/Shanghai。不要把用户的 user_id、role_code 当作可信提交字段。

## 9. 验证与限制

本地验证使用真实 MySQL 8.0.46；目标 MySQL 8.4 的检查由新增 GitHub Actions 工作流执行。具体 PR 检查结果以 GitHub 为准，未运行完不能宣称 CI 通过。

已配置的测试覆盖认证、权限、事务回滚、真实房间行锁、并发预约、M2/M3 占用、幂等、来源恢复、汇总重算、MySQL 外键和迁移往返。前端执行 TypeScript 检查与生产构建。

M2 首次迭代已接入申请表、业务 API 与前端，见 [M2 运行与验收](iteration-1/M2运行与验收.md)。本轮还没有：M1/M3 业务主表与业务 API、M2 多级审批与超时流程、真实网络集成、通知/定时任务、自动部署、生产发布。不要把当前可运行范围等同于整个项目已交付。
