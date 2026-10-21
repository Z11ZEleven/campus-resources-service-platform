# 公共层 D：第一次功能迭代

已实现 FastAPI + SQLAlchemy + MySQL 的公共基础，创建 **8 张 D 表**；另已接入 M2 首次迭代的申请业务表与审批接口，见 [M2 运行与验收](../docs/iteration-1/M2运行与验收.md)。M1/M3 业务表与页面尚未实现。

## 1. 准备与启动

环境：Python 3.12、Node.js 22.12+、Docker Compose。数据库使用 MySQL 8.4。

在仓库根目录：

```powershell
Copy-Item .env.example .env
```

编辑 .env，替换数据库密码、根密码、JWT_SIGNING_KEY 和 SEED_USER_PASSWORD。JWT 密钥至少 32 字符，可用下面命令生成：

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
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

登录示例：

```json
{"username":"student","password":"填写你的本地演示密码"}
```

在 /docs 点 Authorize 填入 Token 即可测试其余接口。接口不暴露 password_hash，也不对终端用户开放任意资源来源增删接口。

## 4. 各模块如何接入

先阅读 [公共层接入指南](../docs/public-layer-integration.md)。入口：

- 数据库依赖：app.modules.auth.dependencies.Db
- 登录依赖：app.modules.auth.dependencies.CurrentUser
- 角色检查：Principal.require、require_permission
- 房间锁：app.modules.resources.service.lock_room
- M1 占用：reserve_seat
- M2 整室占用：occupy_room
- M3 维修限制：start_maintenance
- 按来源释放：release_source
- 业务操作记录：app.modules.audit.service.record_operation

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

## 6. 后续工作

M1/M3 业务表和接口、M2 多级审批与超时任务、动态 RBAC、细粒度管理范围、通知、超时任务、签到违约、附件、网络集成、测试部署、生产审批和回滚均不在本 PR 中。
