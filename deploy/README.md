# 本地 MySQL 环境

`compose.yml` 提供 MySQL 8.4，持久化到 Docker 命名卷，仅映射本机回环端口。

在仓库根复制 `.env.example` 为 `.env` 并替换全部占位凭据后：

```bash
docker compose --env-file .env -f deploy/compose.yml up -d mysql
docker compose --env-file .env -f deploy/compose.yml ps
```

首次初始化后修改 `.env` 的数据库密码不会自动修改已有卷内账号；请正常通过 MySQL 账号管理流程更改。不要删除有业务数据的卷来重置密码。

数据库健康后按 `backend/README.md` 执行迁移与种子，再启动后端和前端。

当前只有本地数据库配置和 CI 检查，没有自动部署、生产审批和发布回滚流水线；这些保留为后续交付任务。

## M1/M3 应用交付配置（待共享环境验收）

新增 `compose.app.yml` 覆盖配置，包含一次性迁移、API、独立 Outbox Worker、前端同源代理；M3 超时扫描只在 API 单实例启用。当前没有 M2 业务模块，本配置不是全部模块共享部署完成证明。

从根目录执行，先在本机 `.env` 配置数据库密码、JWT_SIGNING_KEY、M1_CHECKIN_SECRET（两项密钥独立随机且至少32字符）和 RELEASE_VERSION（经评审的提交 SHA/tag）。不提交 `.env`。本机 HTTP 使用 SESSION_COOKIE_SECURE=false；当前配置仅完成回环 HTTP 演示。HTTPS 还须配置外置 TLS 的原始协议透传和受限代理信任，并以真实浏览器验证 Origin/CSRF 后设为 true；仅设置 Cookie Secure 不足以支持 HTTPS，当前不作为生产配置。

```bash
docker compose --env-file .env -f deploy/compose.yml -f deploy/compose.app.yml config --quiet
docker compose --env-file .env -f deploy/compose.yml -f deploy/compose.app.yml build
docker compose --env-file .env -f deploy/compose.yml -f deploy/compose.app.yml up -d
docker compose --env-file .env -f deploy/compose.yml -f deploy/compose.app.yml ps
```

前端默认只监听 `127.0.0.1:8080`，API 不映射宿主端口。首次开发演示账号通过 `docker compose ... run --rm -e SEED_USER_PASSWORD migrate python -m scripts.seed` 单独初始化，密码从本机环境传入；不在生产自动种子或重置密码。初始化管理员和服务主体沿用 backend/README.md 的受审计脚本。

### 发布步骤与责任

1. 发布负责人先取得共享环境部署窗口/互斥锁，确认所有模块当前版本；本地配置不能替代团队互斥流程。
2. 记录提交 SHA、镜像 digest、迁移前版本及目标 `0014_service_operation_scope`，备份并验证恢复流程。禁止以删除卷替代回滚。
3. 人工批准后先停止写入及 Worker；只允许一个迁移进程执行升级，再启动相同版本 API/Worker/前端。检查 `migrate` 退出0、API就绪、Worker日志和真实业务闭环。
4. 回退优先恢复兼容应用版本并保留数据库结构；不能对共享数据自动执行 downgrade。0012 有操作历史、0013 参数已改动、0014 同模块不同主体存在同键时均有降级限制，须前向修复或由负责人在停机备份后处理。
5. 填写发布记录：环境、操作者、人工批准人、SHA/tag、镜像、迁移、验证结果、回退版本及恢复证据。AI 不填写虚假批准人或演练结果。

生产审批、真实 M2 联调、共享部署互斥与远端分支保护仍须团队完成。
