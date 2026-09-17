# rc3 清洁部署验收（2026-09-18，202 内全新 Docker 守护进程）

## 结论

候选发行版 `v0.6.0-rc3` 的工作区在一个**全新、空的 Docker 守护进程**里从零执行
`bash scripts/deploy_demo.sh --local --observability`，部署验收通过：

- 健康检查：`{"status":"ok","release":"0.6.0rc3","cases":8,"ready":true,"backend":"sqlite-demo","read_replica":false,"maintenance":false}`；
- WebUI `http://127.0.0.1:19000/demo/` 可访问，8 个 Golden Case 已在空数据库中播种；
- 8 个可观测组件（Prometheus、Grafana、Loki、Tempo、OTel Collector、Alloy、Blackbox、Alertmanager）全部启动；
- Prometheus 4/4 抓取目标 `up`（`revguard`、`otel-collector`、`readiness`、`prometheus`）；
- Grafana 12.1.1 健康检查 `database: ok`，只读共享看板启用且不暴露管理员凭据。

同一工作区随后再跑一次部署（幂等重跑，工作区已同步到最终 rc3 目录树）同样通过。两轮部署都发生在隔离环境中，
没有复用 202 上既有彩排栈或常驻演示栈的容器、网络、卷与数据库。

## 隔离方式

| 维度 | 取值 |
| --- | --- |
| 守护进程 | `docker:27-dind`（Docker 27.5.1），独立 data root，独立卷 `rg-clean-dind-data` |
| 网络 | 独立 bridge 网络 `rg-clean-net`，不接入 202 既有 AgentTeams / RevGuard 网络 |
| 镜像来源 | `--registry-mirror https://docker.m.daocloud.io`，从空镜像缓存开始拉取 |
| 工作区 | rc3 目录树（834 个文件，无 `.git`）；文件清单与 `git ls-tree -r v0.6.0-rc3` 逐行一致 |
| 数据库 | `--local` 模式自建 `sqlite-demo`（空库），不使用 202 上的演示数据库 |
| 端口 | 仅在 DinD 内发布（19000/13001 等），与主机端口互不影响 |

工作区文件清单比对命令与结果见 [`clean_collect.sh`](clean_collect.sh)；比对只多出部署过程自己生成的
`.env`、`.runtime/observability/*`（权限受限的运行期凭据文件，未纳入证据）。

## 执行记录

```bash
# 1) 全新 DinD（独立守护进程、独立卷、独立网络）
docker run -d --name rg-clean-dind --privileged --network rg-clean-net \
  -e DOCKER_TLS_CERTDIR= -v rg-clean-dind-data:/var/lib/docker \
  docker.m.daocloud.io/library/docker:27-dind --registry-mirror=https://docker.m.daocloud.io
# 2) 容器内依赖 + 从零部署
docker exec rg-clean-dind apk add --no-cache bash python3 curl openssl util-linux coreutils
docker cp <rc3 工作区> rg-clean-dind:/work
docker exec rg-clean-dind sh -c 'cd /work && bash scripts/deploy_demo.sh --local --observability'
```

脚本全文见 [`clean_deploy.sh`](clean_deploy.sh)（启动与首轮部署）与 [`clean_collect.sh`](clean_collect.sh)（证据采集）。

## 验收证据

| 证据 | 文件 | 要点 |
| --- | --- | --- |
| 首轮从零部署日志（尾段） | `clean_deploy_full.log` | 构建镜像 → 维护封锁 → 空库播种 → 观测组件启动 → 全部验收通过 |
| 最终目录树重跑日志（完整 144 行） | `clean_deploy_final.log` | 与 `v0.6.0-rc3` 一致的工作区，验收再次通过 |
| 健康检查 | `health.json` | `release=0.6.0rc3`、`cases=8`、`ready=true`、`maintenance=false` |
| 容器清单 | `containers.txt` / `containers-all.txt` | `revguard-api` healthy + 8 个观测组件 |
| 镜像清单 | `images.txt` | 9 个固定版本上游镜像 + 源码构建的 `work-revguard-api:latest` |
| 卷清单 | `volumes.txt` | 10 个卷（库、输出、报告与各观测组件数据） |
| Prometheus 目标 | `prom-targets.json` | 4 个 active target 全部 `up`，0 个 dropped |
| Grafana 健康 | `grafana-health.json` | `database: ok`，版本 12.1.1 |
| 运行期配置（脱敏） | `db-env.txt` / `env-version.txt` | 后端与池参数名、版本变量名，值全部脱敏 |

## 边界

- 本次冷部署使用 `--local` 模式，后端为 `sqlite-demo`；PostgreSQL 主从复制、读副本路由与
  `RESULT_UNKNOWN` 恢复由发布门禁中带 DSN 的 83 项 PostgreSQL/Matrix 集成用例覆盖，不由本文件的
  本地模式冷部署覆盖；本地主从验证也不等同于云 PolarDB 生产高可用。
- ERPNext、AgentTeams/Matrix、Higress 是 202 上独立运行的真实系统，不属于这次 DinD 冷部署的对象；
  它们的连接与权限由各自的运行证据记录。
- 证据包不包含任何部署凭据：DinD 内生成的 `.env` 与 `.runtime/observability/*` 保留在 202 上，
  未下载、未提交。
