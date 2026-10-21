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
