# 公共层 D 与 M3 后端

已实现 FastAPI + SQLAlchemy + MySQL 的公共基础和 M3 文字报修闭环，现包含 **11 张 D 表、3 张 M3 表、4 张 M2 表**。M3 提供上报、查询、派单、维修、完工、验收、超时升级、一次性评价及真实站内通知；对应 Vue 前端已接入。M2 两级审批、作用域、超时、通知与真实资源网络确认已接入，运行方法见 [M2 实现与运行](../docs/m2-implementation.md)；M1 尚未实现。

M3 接口、状态流转、权限与幂等语义见 [M3 后端](../docs/m3-backend.md)与 [超时/评价/通知扩展](../docs/m3-extensions.md)。执行 `alembic upgrade head` 会升级至 `0004_m2_two_stage`，保留原公共层表与工单数据。新增 D 通知契约为本次用户确认的本地实现提案，待团队评审，不代表团队共识。

## 1. 准备与启动

环境：Python 3.12、MySQL 8.4；前端另需 Node.js 22.12+。MySQL 可使用本机 Windows 服务或 Docker Compose，连接参数一致。

在仓库根目录：

```powershell
# 首次配置使用，已有 .env 时保留并编辑现有文件。
Copy-Item .env.example .env
```

编辑 .env，填写本机数据库地址、`MYSQL_DATABASE/MYSQL_USER/MYSQL_PASSWORD`，替换 `JWT_SIGNING_KEY` 和 `SEED_USER_PASSWORD`。本机安装 MySQL 时先创建目标数据库、应用用户并授权，无需运行下列 Compose 命令；只有使用容器时才配置 `MYSQL_ROOT_PASSWORD`。JWT 密钥至少 32 字符，可生成：

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
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m scripts.seed
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

Linux/macOS 将 .\.venv\Scripts\python.exe 换为 .venv/bin/python，其余命令相同。

- 健康：http://127.0.0.1:8000/health
- 数据库连通检查：http://127.0.0.1:8000/ready
- 接口文档：http://127.0.0.1:8000/docs

没有自动建表；必须先执行 Alembic。停止开发服务按 Ctrl+C。Compose 只提供本地 MySQL，当前没有自动部署或生产发布功能。

后端默认每 60 秒自动扫描 M3 超时工单；待派单/已派单/待验收 24 小时、维修中 48 小时，严格超过截止才升级，阶段/轮次去重。配置变量、关闭方式、逐单事务、返修重新计时及站内通知边界见 [扩展配置](../docs/m3-extensions.md#4-配置与任务运行)。先迁移再启动 worker，修改配置后重启后端。

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

种子重复执行不重置密码、不启用被停用用户、不恢复已撤销角色。只用于开发演示，勿用真实学生数据。初始化了 2 间教室、每间 3 个座位；B1-102 仅 EE 院系使用。

## 3. 已开放 HTTP 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /api/v1/auth/login | JSON 用户名和密码，返回 Bearer Token |
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

在 /docs 点 Authorize 填入 Token 即可测试其余接口。接口不暴露 password_hash，也不对终端用户开放任意资源来源增删接口。

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
- 业务操作记录：app.modules.audit.service.record_operation
- 站内通知：app.modules.notifications.service.publish_notifications（本地公共契约提案，待团队评审）

HTTP 的 Db 依赖已经开启事务，且在成功响应发出前提交。模块函数只能 flush，不能内部 commit、再次 begin 或使用另一个 Session。脚本调用才需要自行 with session.begin()。

本轮为同进程公开服务函数，不是跨模块网络链路；课程最终的网络调用与失败补偿仍需后续完成。

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

测试会迁移、清空专用测试库数据，并验证迁移回放、登录与停用、权限、真实事务和行锁、重复预约、幂等、多来源恢复、缓存失效与种子幂等。未设置测试地址时普通 pytest 会跳过 MySQL 测试；CI 使用 --require-mysql，缺少数据库不会假装通过。

测试入口显式关闭自动超时 worker，防止与数据库回放、清理竞争。2026-10-21 本机完整后端 **145 passed**、Ruff 通过；包括超时、一次性评价、站内通知及并发回滚/已读快照等验证。真实浏览器、保护破坏实验和本机迁移证据见 [扩展验证记录](../docs/m3-extensions.md#6-本地验证记录2026-10-21)，不代表远端 CI 或人工验收。

## 6. 后续工作

M1/M2 业务表和接口、动态 RBAC、细粒度管理范围、签到违约、附件、撤销/转派/重开、完整独立业务事件流、外部邮件/微信通知、真实模块间网络集成与跨进程可靠补偿、测试部署、生产审批和发布回滚仍待后续实现。原完工请求缺少幂等轮次的限制仍保留，详见 [M3 已知限制](../docs/m3-backend.md#8-已记录的原设计限制)。
