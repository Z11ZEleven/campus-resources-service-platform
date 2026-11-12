# PR #25 后续交付整合

本次整合 `main@5c93ddf` 与 `feat/m1-delivery-t11-t17@f36f014`，只更新 PR 来源分支，不直接更新远端 main。main 已包含 PR #24 的基础整合，因此本次实际有 6 个冲突文件。

## 逐项冲突处理

| 文件 | 整合方式 |
| --- | --- |
| README.md | 保留 M2 两级审批与真实 D 服务说明，加入 M1 后续功能范围。 |
| backend/README.md | 保留 M2/M3 启动及接口，迁移目标更新为 0014。 |
| backend/app/main.py | 同时注册原有 M2/M3、M1 业务与新服务接口；保留原有 M2/M3 生命周期。 |
| frontend/README.md | 保留 M2 文档并加入 M1 页面、组件与浏览器验证方法。 |
| frontend/src/App.vue | 同时保留 M1 预约/管理、M2 申请/待办、M3 工单与通知导航。 |
| frontend/src/utils/navigation.ts | 合并三个模块的登录与通知跳转白名单，保留非法跳转拒绝。 |

## 共用层和模块边界

M1 接入来源分支已有的定时提醒、未签到违约、使用完成、维修免责、历史复用、管理统计/参数及学生/管理前端。M3 维修开始时，通过 M1 的公开回调在原事务内取消未使用预约或中断使用中的预约，不写 M1 私有表，也不产生免责违约。M3 工单状态和维修来源仍由原有 M3 流程管理。

M2 继续使用既有独立 D 服务，不改换为此次新增的通用服务来源入口。超时释放 M2 来源时同步释放该来源的座位明细投影；不释放其他来源，测试同时保留有效 M3 维修限制。该投影回归在修复前失败、修复后通过。

自动合并的资源管理、时段规则、权限、审计、模型与迁移测试也纳入完整验证，不以 Git 无冲突视为业务正确。基础整合中的 M2 鉴权短事务、单一迁移链及旧调用兼容保留。

## 迁移、启动与部署

从已整合基础功能的 main 数据库升级，先备份，再在 backend 执行 `.venv/bin/python -m alembic upgrade head`。原有历史迁移保持 main 版本，后续依次执行 0012、0013、0014，唯一 head 为 `0014_service_operation_scope`。共享数据库不得使用测试或自动 downgrade 清理数据。

本地继续使用仓库根目录的 `python scripts/dev.py` / Windows `start.cmd` 启动主应用、独立 D 和 Vue；另在 backend 运行 `.venv/bin/python -m app.worker` 处理 M1 定时任务与 Outbox。签到仍需配置独立 `M1_CHECKIN_SECRET`。

新增的 `deploy/compose.app.yml` 已补齐 M2 独立 D 服务、独立 `RESOURCE_SERVICE_KEY`、容器内服务地址及鉴权/数据库健康检查。主 API 等待 D 就绪，M2/M3 扫描只在主 API 开启，Outbox 使用单独 worker。CI 配置验证同步声明所需密钥；不在代码或镜像中写入真实凭据。

镜像交付和配置检查不代表生产发布、HTTPS 或共享环境验收通过；这些仍按 deploy/README.md 的人工流程执行。

## 验证

本地使用 MySQL 8.4、Python 3.12、真实系统 Chromium；数据库测试仅连接显式专用的 `campus_resources_test`。真实浏览器经 Vue/Vite → FastAPI → MySQL 验证，不拦截或伪造业务请求，并检查 M1 操作后仍可进入 M2/M3 页面。

检查范围包括完整后端、M2 真正跨进程 HTTP 审批与故障恢复、M3 回归、M1 维修联动与计时并发、升级数据保留、空库重放、模型一致性、前端组件/跳转测试、类型检查、构建和启动器测试。具体结果见本次交付说明及 PR 对应 CI；不将本地结果写作远端或人工验收。

本次本地结果（2026-11-12）：完整后端 401 passed（含真实 Chromium、无跳过），前端 22 passed，类型检查与构建、Ruff、Alembic 模型一致性及 Compose 配置检查通过；启动器 8 通过、1 项 Windows 专用测试在 Linux 跳过。实际重启三服务后，同源登录、M1/M2/M3 查询、通知与登出撤销均通过，Outbox worker 单轮执行通过。仅有第三方测试客户端弃用与既有前端大 chunk 提示。远端 CI 另以本次推送后的结果为准。
