# 公共层 D 与 M1/M2/M3 后端

已实现 FastAPI + SQLAlchemy + MySQL 的公共基础和 M3 文字报修闭环，已整合 M1 的共用身份、资源管理、预约与签到基础表；保留 M2/M3 业务表。M3 提供上报、查询、派单、维修、完工、验收、超时升级、一次性评价及真实站内通知；对应 Vue 前端已接入。M2 两级审批、作用域、超时、通知与真实资源网络确认已接入，运行方法见 [M2 实现与运行](../docs/m2-implementation.md)；M1 基础 API 已接入，完整范围见 [整合说明](../docs/m1/main-integration.md)。

M3 接口、状态流转、权限与幂等语义见 [M3 后端](../docs/m3-backend.md)与 [超时/评价/通知扩展](../docs/m3-extensions.md)。执行 `alembic upgrade head` 会升级至 `0011_admin_restrictions`，保留原公共层表与工单数据。新增 D 通知契约为本次用户确认的本地实现提案，待团队评审，不代表团队共识。

## 1. 准备与启动

Windows 可在仓库根目录运行 `start.cmd --setup` 安装依赖、补齐独立资源密钥、迁移和初始化公共层/M3/M2 演示数据，再联合启动主后端 8001、D 资源服务 8002 和前端 5173；后续运行 `start.cmd`。支持 `--resource-port` 并自动同步 M2 调用地址。先创建本机 MySQL 数据库及用户、填写本地配置，详见 [新设备启动说明](../docs/local-start.md)。以下为手动步骤。

环境：Python 3.12、MySQL 8.4；前端另需 Node.js 22.12+。MySQL 可使用本机 Windows 服务或 Docker Compose，连接参数一致。

在仓库根目录：

```powershell
# 首次配置使用，已有 .env 时保留并编辑现有文件。
Copy-Item .env.example .env
```

编辑 .env，填写本机数据库地址、`MYSQL_DATABASE/MYSQL_USER/MYSQL_PASSWORD`，替换 `JWT_SIGNING_KEY` 和 `SEED_USER_PASSWORD`。`RESOURCE_SERVICE_KEY` 必须为独立的至少 32 字符密钥，供两个后端共同使用；下方 `scripts.dev_config` 会补齐缺失/空/示例值，保留有效值。`M2_RESOURCE_BASE_URL` 默认指向 `http://127.0.0.1:8002`。本机 MySQL 须先创建数据库和用户；只有使用容器才配置 `MYSQL_ROOT_PASSWORD`。JWT 密钥可生成：

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

选择容器数据库时，在仓库根目录执行：

```powershell
docker compose --env-file .env -f deploy/compose.yml up -d mysql
docker compose --env-file .env -f deploy/compose.yml ps
```

等 MySQL 健康后，在 backend 目录执行（Windows PowerShell，无需激活虚拟环境）：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m scripts.dev_config
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m scripts.seed_m2
```

然后在 backend 目录用两个终端分别启动：

```powershell
# 终端一：独立 D 资源服务
.\.venv\Scripts\python.exe -m uvicorn app.resource_api:app --host 127.0.0.1 --port 8002
```

```powershell
# 终端二：主应用，读取 M2_RESOURCE_BASE_URL=http://127.0.0.1:8002
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8001
```

Linux/macOS 将 .\.venv\Scripts\python.exe 换为 .venv/bin/python，其余命令相同。

- 健康：http://127.0.0.1:8001/health
- 数据库连通检查：http://127.0.0.1:8001/ready
- 主接口文档：http://127.0.0.1:8001/docs
- D 资源服务文档：http://127.0.0.1:8002/docs（需要独立服务密钥调用）

没有自动建表；必须先执行 Alembic。停止开发服务按 Ctrl+C。Compose 只提供本地 MySQL，当前没有自动部署或生产发布功能。

后端默认每 60 秒自动扫描 M3 超时工单；待派单/已派单/待验收 24 小时、维修中 48 小时，严格超过截止才升级，阶段/轮次去重。配置变量、关闭方式、逐单事务、返修重新计时及站内通知边界见 [扩展配置](../docs/m3-extensions.md#4-配置与任务运行)。先迁移再启动 worker，修改配置后重启后端。

公共 Outbox 使用独立进程，不与 HTTP 或 M3 同进程任务混用：

```bash
# 常驻轮询；生产用进程管理器守护，收到 SIGTERM/SIGINT 后停止领取新批次。
.venv/bin/python -m app.worker
# 本地检查：只处理一批后退出。
.venv/bin/python -m app.worker --once --worker-id local-check
```

默认每批 10 条、租约 30 秒、重试退避 5–300 秒，可由 `OUTBOX_BATCH_SIZE`、`OUTBOX_LEASE_SECONDS`、`OUTBOX_RETRY_BASE_SECONDS`、`OUTBOX_RETRY_MAX_SECONDS`、`OUTBOX_POLL_INTERVAL_SECONDS` 或对应命令行参数覆盖。失败达到上限或不可重试时保留为 `MANUAL`，供服务层 `list_failures` 查询和人工处理。

## 2. 演示账号

所有账号密码来自本地 SEED_USER_PASSWORD，最少 12 位；脚本不打印密码。

| 用户名 | 身份/院系 | 角色 |
|---|---|---|
| student | STUDENT / CS | USER |
| student_ee | STUDENT / EE | USER |
| teacher | TEACHER / CS | USER |
| room_admin | STAFF / OPS | ROOM_ADMIN |
| property_admin | STAFF / OPS | PROPERTY_ADMIN |
| repairer | STAFF / OPS | REPAIRER |
| staff | STAFF / OPS | USER |
| dept_admin_cs / dept_admin_ee | STAFF / CS 或 EE | DEPT_ADMIN + 对应院系范围 |
| campus_admin | STAFF / OPS | CAMPUS_ADMIN + MAIN 校区范围 |
| m2_room_admin | STAFF / OPS | ROOM_ADMIN + M2 演示教室范围 |

`scripts.seed_m2` 包含基础种子，重复执行不重置密码、不启用被停用用户、不恢复已撤销角色/范围或重设已有教室类型。只用于开发演示，勿用真实学生数据。基础 2 间教室每间 3 座，B1-102 仅 EE 院系使用；另提供 M2-CAMPUS、M2-CS、M2-EE 三间整室教室与对应审批范围。完整两级审批演示见 [M2 运行说明](../docs/m2-implementation.md#6-演示数据)。

## 3. 已开放 HTTP 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /api/v1/auth/login | JSON 用户名和密码，建立 Cookie 会话，返回 authenticated |
| GET | /api/v1/auth/me | 当前用户、身份、角色，ID 为字符串 |
| GET | /api/v1/auth/users?role_code=REPAIRER | 管理员查询有效人员，分页 |
| GET | /api/v1/resources/rooms | 院系及角色过滤教室，分页 |
| GET | /api/v1/resources/rooms/{id} | 有权访问的教室详情 |
| GET | /api/v1/resources/rooms/{id}/seats?has_socket=true | 座位列表，分页与插座筛选 |
| GET | /api/v1/resources/rooms/{id}/availability?date=YYYY-MM-DD | 指定日每座位 24 小时状态 |
| GET / POST | /api/v1/m3/work-orders | 查询/上报，十个 M3 接口详见 [M3 指南](../docs/m3-backend.md#3-http-接口) |
| GET | /api/v1/notifications | 本人通知分页与未读数量 |
| GET | /api/v1/notifications/unread-count | 本人未读数量 |
| POST | /api/v1/notifications/{id}/read | 本人单条已读，重复幂等 |
| POST | /api/v1/notifications/read-all | 仅标记调用时可见的本人未读 ID 快照 |

登录示例：

```json
{"username":"student","password":"填写你的本地演示密码"}
```

使用同源前端登录，浏览器 Cookie 会话在刷新后恢复；修改请求自动携带 CSRF，退出撤销服务端会话。旧 JWT 已失效，不再将其填入 Authorize 使用。接口不暴露 password_hash，也不对终端用户开放任意资源来源增删接口。

## 4. 各模块如何接入

先阅读 [数据库统一契约](../docs/database-contract.md) 和 [公共层接入指南](../docs/public-layer-integration.md)。入口：

- 数据库依赖：app.modules.auth.dependencies.Db
- 登录依赖：app.modules.auth.dependencies.CurrentUser
- 角色检查：Principal.require、require_permission
- 房间锁：app.modules.resources.service.lock_room
- M1 占用：reserve_seat
- M2 整室占用：occupy_room
- M3 维修限制：start_maintenance
- 按来源释放：release_source
- 公共 Outbox 登记：app.modules.outbox.service.register_event
- M1 预约通知/审计适配：app.modules.m1.integrations.notify_reservation、audit_reservation
- 业务操作记录：app.modules.audit.service.record_operation（`USER`/`SERVICE`/`SYSTEM`，支持事件键幂等）
- 站内通知：app.modules.notifications.service.publish_notifications（真实收件箱，本地公共契约提案，待团队评审）
- 公共任务 Worker：`python -m app.worker`（独立进程，租约领取与失败查询）

HTTP 的 Db 依赖已经开启事务，且在成功响应发出前提交。模块函数只能 flush，不能内部 commit、再次 begin 或使用另一个 Session。脚本调用才需要自行 with session.begin()。

M1/M3 沿用公开资源函数；M2 继续通过独立 D 服务进行真实 HTTP 资源确认。Outbox/Worker 提供同库跨进程可靠投递与重试，跨主机故障补偿仍需后续完善。

## 5. 检查

```powershell
.\.venv\Scripts\python.exe -m ruff check app scripts tests alembic
.\.venv\Scripts\python.exe -m pytest tests/test_unit.py -q
```

MySQL 集成测试需要专用、可清空的 *_test 数据库，**不能使用开发或生产业务库**：

```powershell
$env:TEST_DATABASE_URL="mysql+pymysql://测试用户:测试密码@127.0.0.1:3306/campus_resources_test"
.\.venv\Scripts\python.exe -m pytest --require-mysql -q
```

测试会迁移、清空专用测试库数据，并验证迁移回放、登录与停用、权限、真实事务和行锁、重复预约、幂等、多来源恢复、缓存失效、Outbox 租约/并发/重试、通知与审计重投幂等及种子幂等。未设置测试地址时普通 pytest 会跳过 MySQL 测试；CI 使用 --require-mysql，缺少数据库不会假装通过。

测试入口显式关闭自动超时 worker，防止与数据库回放、清理竞争。2026-10-31 本机完整后端 **216 passed**、Ruff 通过；T06 的租约恢复、并发领取、接收重投及审计操作者证据见 [M1 验证记录](../docs/m1/verification.md)，不代表远端 CI 或人工验收。2026-10-21 M3 浏览器与保护破坏记录见 [扩展验证记录](../docs/m3-extensions.md#6-本地验证记录2026-10-21)。

## 6. 后续工作

M1 自动提醒、违约与维修联动、M3 局部业务权限适配、附件、撤销/转派/重开、Outbox 故障管理 HTTP/指标/告警、外部邮件/微信通知、真实模块间网络集成、测试部署、生产审批和发布回滚仍待后续实现。原完工请求缺少幂等轮次的限制仍保留，详见 [M3 已知限制](../docs/m3-backend.md#8-已记录的原设计限制)。

## 7. T01–T06 初始化与升级

升级先备份历史数据库，执行 `alembic upgrade head`，再运行 `scripts.seed_m2`；旧用户、来源、M3 工单和通知保留。`0007` 新建 `d_outbox_events` 并扩展审计操作者字段；已有用户审计回填为 `USER/SUCCESS`。旧客户端重新登录；人员责任和共同评审仍待人工完成。不要在共享业务库运行测试或 downgrade；降级回 M2 前的版本会触发既有业务/系统审计数据保护，须先制定人工回退方案。完整契约见 [M1 基础契约](../docs/m1/contracts.md)。

全局管理员首次初始化（使用部署操作员的数据库配置；已有同名用户不恢复权限、不改密码）：

```bash
# 从安全的本机环境注入 BOOTSTRAP_ADMIN_PASSWORD，至少12字符；不提交环境文件。
.venv/bin/python -m scripts.bootstrap_admin --username local_admin --display-name 本地管理员
```

管理员通过角色授权 API 配置作用域；SYS_ADMIN 没有隐式的 M1/M2/M3 业务特权。服务凭据必须从环境注入 SERVICE_AUTH_TOKEN（至少32字符），仅保存摘要：

```bash
# 将数字替换为已初始化的管理员和房间 ID；可重复指定 --room-id。
.venv/bin/python -m scripts.provision_service --actor-id 1 --name m2-service --module M2 --room-id 1
```

轮换用 --rotate；停用用 --disable。两者均审计；服务所属模块不可原地修改。命令不会输出凭据，不能用数据库手工写记录替代。生产 HTTPS 设置 SESSION_COOKIE_SECURE=true；本机 HTTP 可为 false，SESSION_MINUTES 控制会话期限。JWT_SIGNING_KEY 目前仍作为旧辅助代码配置项校验，但 HTTP 不接受 JWT。

首次运行测试需要真实 MySQL 8.4 专用 *_test 库。CI 会在 pytest 后执行 alembic check；Ruff、完整测试及生产构建命令沿用上文。实际本轮结果见 [验证记录](../docs/m1/verification.md)。
