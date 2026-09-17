# 官网运行回放页验收（2026-09-18）

[GitHub Pages 运行回放页](https://ld0574.github.io/revguard/replay.html) 按真实运行记录回放 RevGuard 的案件，评委不需要等待模型调用时间。本次构建、静态预览与 Chromium 浏览器检查全部在 10.10.10.202 Docker 内执行；本地只编辑文件、同步与 Git 操作。

## 页面结构

回放页不再把案件清单写死在 HTML 里：页面先读 `website/data/index.json`，按索引渲染案件标签，再按标签的 `file` 字段加载对应数据包。

- `index.json` 是唯一入口，列出已发布的案件、终态、订单号、步骤数、跨度数与标签文案；
- 每个案件一个 `case-<id>.json`，由导出脚本对真实运行栈生成；
- 请求的案件不在索引里时，页面给出提示并回落到已发布的运行记录，不会出现空白页。

## 回放数据的来源

回放数据由候选版本对真实运行栈导出，不是手写素材：

```bash
docker cp scripts/export_case_replay.py revguard-api-dev:/app/data/outputs/export_case_replay.py
docker exec revguard-api-dev python /app/data/outputs/export_case_replay.py \
  --base-url http://127.0.0.1:9000 --api-key <viewer-key> \
  --case CASE-2026-0001 --case CASE-2026-0008 --output-dir /app/data/outputs/replay
```

| 案件 | 终态 | 订单 | 本次运行跨度 | 本次运行审计事件 | 页面步骤 |
| --- | --- | --- | --- | --- | --- |
| CASE-2026-0001 | CLOSED | EZ202608001 | 53 | 109（序号 585–693） | 10 |
| CASE-2026-0008 | ROLLED_BACK | EZ202608008 | 60 | 120（序号 805–924） | 11 |

导出脚本以本次运行 Trace 的最早跨度作为时间线下界，只保留该时刻之后的审计事件，避免把同一案件不同排练代次拼成一条时间线。每个数据包都带 `provenance.snapshot_sha256`（原始快照摘要）与 `audit.chain_ok`（哈希链连续性校验）。

导出可复现：对同一运行重复导出，除 `generated_at` 外与已发布数据包逐字节一致（2026-09-18 对两条 rc3 运行各复导一次，`case-2026-0001.json`、`case-2026-0008.json` 与 `index.json` 均为 True）。

## 验收结果

浏览器检查在独立 Nginx 容器提供的 `/revguard/` 子路径下执行（与 GitHub Pages 项目站点的相对路径加载方式一致），检查项直接读 `index.json`，逐个切换索引里的案件，核对摘要卡片数、阶段数、步骤数与追踪表行数；另外核对首页「材料入口」的两个成片直链真实可下载。

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 首页入口 | 首页含 `replay.html` 入口，静态资源 200 | site-home.png |
| 材料入口直链 | 官网「08 · 材料入口」给出两个成片 mp4 的 Release 附件直链 | browser-result.json |
| 成片可下载 | 两个 mp4 直链 HEAD 均返回 200 | browser-result.json |
| 标签与索引一致 | 页面标签数 = `index.json` 案件数（2） | browser-result.json |
| CASE-2026-0001 渲染 | 摘要 8 项、阶段 10 个、步骤 10 步、追踪 53 行 | replay-case-2026-0001.png |
| CASE-2026-0008 渲染 | 摘要 8 项、阶段 11 个、步骤 11 步、追踪 60 行 | replay-case-2026-0008.png |
| 播放交互 | 播放 9 秒推进到第 3 步，按钮为“暂停” | replay-playing.png |
| 移动端 | 390×844 无横向溢出 | replay-mobile.png |
| 分享卡片元数据 | 首页 `og:image` 为绝对 PNG 直链，`og:url`/`og:image:type`/`twitter:card` 齐全 | browser-result.json |
| 分享卡片可抓取 | 卡片直链 HTTP 200、`image/png`、实际像素 1200×630、277,406 字节 | browser-result.json |
| 资源完整性 | 无 4xx/5xx 请求、无浏览器 SEVERE 日志 | browser-result.json |

`replay-case-82822305.png` 是 rc3 传输验证案件（只读运行，无资金写入）在同一套预览栈下的渲染结果，用于确认 rc3 数据包也能被页面正确加载。

部署完成后对公网站点 `https://ld0574.github.io/revguard/` 复跑同一套检查，**12 项全绿**：在原有 10 项之外新增“分享卡片元数据完整”与“分享卡片直链可抓取且为 1200×630 PNG”，结果与截图保存在 [`public/`](public/)（[`public/browser-result.json`](public/browser-result.json)）。两个成片直链的 HEAD 检查带 3 次重试：GitHub Release 附件在跨境网络下偶发超时，重试后仍非 200 才算失败，本轮两次探测均在第 1 次尝试返回 200。

## 社交分享卡片

`og:image` 原先是相对路径的 SVG。主流社交爬虫不解析相对地址，也普遍不支持 SVG，分享时只能显示空白卡片。
现在两页（`index.html`、`replay.html`）统一指向绝对地址 `https://ld0574.github.io/revguard/og-image.png`，
并补齐 `og:url`、`og:site_name`、`og:image:type/width/height` 与 `twitter:card=summary_large_image`。

`website/og-image.png`（1200×630，277,406 字节）由 `website/og-image.svg` 在 202 的浏览器容器内渲染导出，
不是手工拼图；渲染脚本与录制脚本同栈，站点仍是纯静态文件，GitHub Pages 只发布已提交内容。

静态门禁 [`scripts/check_website_replay.py`](../../../scripts/check_website_replay.py) 断言两个页面：
`og:image` 是绝对 PNG 地址、`og:url` 在同站点下、`twitter:card` 正确、PNG 文件存在且**从文件头读出的
像素尺寸**必须为 1200×630；`tests/test_website_replay_index.py` 另加两条反例（退回相对 SVG、删除 PNG
都必须让门禁失败），共 4 条用例。

## 数据代次

当前 `index.json`（`release: 0.6.0-rc3`）发布的是 **2026-09-18 录制代次**的两条完整运行记录：两条都走真实 AgentTeams Matrix 出站（`CAPTURED_FROM_RUNTIME`），真人 Matrix 审批、受限执行、独立复核与冲销恢复俱全。事实快照见 [`finals-recording-20260918/`](../finals-recording-20260918/README.md)。

`replay-case-82822305.png` 是更早一次 rc3 传输验证案件（只读运行、无资金写入）在同一套预览栈下的渲染结果。上一代次（rc2 传输）的截图归档在 [`archive-rc2/`](archive-rc2/) 下。

## 边界

回放节奏是展示加速，动画间隔不代表真实执行耗时；页面上的时点、金额、哈希与审批记录为记录原值。导出的数据包已移除凭据、内部地址、Matrix 用户与房间标识，审批人只保留角色与展示名。业务样本为合成数据，ERPNext、PostgreSQL 与监控组件为真实运行系统。

![CASE-2026-0008 回放](replay-case-2026-0008.png)
