# 2026-08-12 安全扫描记录

## 结论

整改后的锁定依赖、源码、仓库文件系统和远端运行镜像均通过门禁；没有保留漏洞例外：

| 检查 | 范围 | 结果 |
|---|---|---|
| pip-audit 2.10.1 | `requirements.lock` | 已知漏洞 0 |
| Bandit 1.9.4 | `revguard/`、`scripts/` | 中高风险问题 0 |
| Trivy 0.69.3 fs | `revguard/` | HIGH/CRITICAL 0；Secret 0；Dockerfile 误配置 0 |
| Trivy 0.69.3 image | 远端 `revguard-revguard-api`，Debian 13.6 | OS 与 Python 包 HIGH/CRITICAL 0 |

Trivy 采用 `--ignore-unfixed --severity HIGH,CRITICAL --exit-code 1`，因此上表指可修复的
HIGH/CRITICAL 门禁；不把它表述为覆盖所有等级或未知漏洞。

## 发现与整改

首次镜像扫描发现基础镜像的 `setuptools 79.0.1` 内捆绑 `jaraco.context 5.3.0`
（CVE-2026-23949）和 `wheel 0.45.1`（CVE-2026-24049），共 2 个 HIGH。RevGuard
运行时不需要 `setuptools`，因此 Docker 构建在安装锁定依赖后将它移除；重建镜像
`sha256:1522c18ab5cd8ef576a64aa1f0d82d859c92acd3b92bffc29a75daf353588350`
并复扫，结果归零，未创建例外。

## 供应链固定

扫描器安装包来自 Trivy 0.69.3 官方 release，校验值为：

- Linux 64-bit tar.gz：`1816b632dfe529869c740c0913e36bd1629cb7688bd5634f4a858c1d57c88b75`；
- macOS ARM64 tar.gz：`a2f2179afd4f8bb265ca3c7aefb56a666bc4a9a411663bc0f22c3549fbc643a5`。

CI 不使用可变 tag，而是固定 Aqua 安全公告列出的 Trivy Action 安全提交
`57a97c7e7821a5776cebc9bb87c984fa69cba8f1`。依据：
[Aqua Security Advisory GHSA-69fq-xp46-6x23](https://github.com/aquasecurity/trivy/security/advisories/GHSA-69fq-xp46-6x23)。

## 复现

Python 门禁一键运行：

```bash
make security
```

仓库和镜像 Trivy 命令固化在 `.github/workflows/revguard-security.yml`；每次影响
`revguard/` 的 push 或 pull request 都会重新执行。
