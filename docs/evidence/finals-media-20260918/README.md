# 决赛视频成片：真实运行栈逐帧录制（2026-09-18）

本目录记录 2026-09-18 在 `10.10.10.202` Docker 内录制的两个决赛视频文件，以及
录制脚本、逐帧时间戳、场景日志、抽帧与压字卡的哈希。视频本身作为附件挂在
GitHub Release [`v0.6.0-rc3`](https://github.com/ld0574/revguard/releases/tag/v0.6.0-rc3)，
仓库内副本位于 `submission/finals-media/`（Gitee 主仓库）。

## 1. 成片

| 文件 | 分辨率 | 时长 | 大小 | SHA-256 |
|---|---|---|---|---|
| `revguard_finals_demo_v0.6.0-rc3.mp4` | 1920×1080 / 30 fps | 244.4 s | 6,110,541 B | `ee0743241914ada528b090b63e76837f0b9225e0e6d7d770c40783c4ce766aed` |
| `revguard_finals_recovery_90s_v0.6.0-rc3.mp4` | 1920×1080 / 30 fps | 87.0 s | 2,213,678 B | `4ef35c697771e8bfb178a0840f197c4e938d0cd0f4a826f699c5c4cad5ea448e` |

- 主视频画面顺序：官网首屏 → `CASE-2026-0001` 概览 → 决策依据 → 执行与审计 →
  独立验证 → `CASE-2026-0008` 偏差与冲销 → Grafana 可观测大屏（Demo UI iframe）→
  公网回放页。
- 故障备用片段画面顺序：`CASE-2026-0008` 终态概览 → 偏差/冻结/冲销审计 → 资金恢复
  面板 → 公网回放页。
- 两个文件**都没有音轨**：旁白与字幕由人工后期补；现场答辩仍以真实栈现场演示为主，
  视频是现场异常时的备用材料。

## 2. 录制与编码方式（可复现）

全部在 `202` Docker 内完成，本地只同步文件。

1. 镜像 `revguard-recorder:20260918` = `revguard-grafana-browser:20260918` + `ffmpeg`
   （Dockerfile 见 `/root/rgops/Dockerfile.record`，容器内 ffmpeg 7.1.5）。
2. 逐帧录制：`scripts/record_finals_walkthrough.py`（主视频）与
   `scripts/record_finals_recovery_clip.py`（故障片段）。headless Chromium 使用
   `--window-size=1920,1223` 以获得**真 1920×1080 视口**（实测 `innerWidth/Height`
   写入 `record-log.json` 的 `viewport`），按 ~4 fps 截图，同时记录**每帧真实墙钟**到
   `frame-times.json`。
3. 编码：`scripts/encode_finals_walkthrough.py` 把逐帧时间差写进 ffmpeg concat 列表的
   `duration`，按真实时间轴合成，再按 `SPEED` 做展示加速（主视频 1.43×、故障片段
   1.5×）。这样不会出现"按固定帧率重采样导致时长被压缩/丢帧"的问题。
4. 片头/片尾压字卡由 ffmpeg `drawtext` + Noto Serif CJK 生成（`title-in.png`、
   `title-out.png`、`title-recovery.png`），文案与本目录抽帧一致。

实际执行的命令（含容器挂载与参数）保存在 [`record-commands.sh`](record-commands.sh)。

## 3. 采集事实

| | 主视频 | 故障片段 |
|---|---|---|
| 帧数 | 936 | 356 |
| 真实采集墙钟 | 350.6 s | 136.9 s |
| 视口 | 1920×1080 | 1920×1080 |
| 场景数 | 10 | 5 |
| 逐帧时间戳 | `frame-times.json`（在 202 帧目录内） | 同左 |
| 场景日志 | `record-log.json` | `recovery-record-log.json` |

录制栈：演示栈 `http://10.10.10.202:19088/demo/`（`REVGUARD_RELEASE_VERSION=0.6.0-rc3`）
与公网 `https://ld0574.github.io/revguard/`；画面中的案件为
`CASE-2026-0001`（`REC-36B14AC3`，`CLOSED`）与 `CASE-2026-0008`
（`REC-63A0C9EC`，`ROLLED_BACK`），与 `docs/evidence/finals-recording-20260918/`
的两条真实 AgentTeams Matrix 运行记录同代次。

## 4. 这一版修掉的录制缺陷

上一版录制里「执行与审计」页签渲染的是**整条累积审计链**，会把上一运行代次的
`REQ-MCP-*` 行和本次 Run 的 `REQ-AGT-*` 行混在一屏，破坏"同一条 Run 的证据链"口径
（`CASE-2026-0001` 历史 435 行、`CASE-2026-0008` 335 行）。本次先修读模型
（`revguard/demo_dashboard.py`：按 `recording_id` 收口到当前代次，并返回
`audit_generation`），演示栈实测降为 110 / 121 行，再重新录制，因此本目录的抽帧里
只剩当前代次的 `REQ-AGT-*` / `TASK-*` 事件。完整历史链仍由案件接口返回，未被删除。

## 5. 抽帧

| 文件 | 对应画面 |
|---|---|
| `still-home.png` | 官网首屏与两个案例入口 |
| `still-case1.png` | `CASE-2026-0001` 概览 + ERPNext 证据链 + 复算账本 |
| `still-audit.png` | 「执行与审计」当前代次审计行（无跨代次混行） |
| `still-obs.png` | Demo UI iframe 内的 Grafana 运行与资金恢复面板 |
| `still-replay.png` | 公网回放页 `replay.html` |

## 6. 边界

- 视频是**已完成运行记录的回放/浏览**，不是现场重新跑一遍 Agent；页面里的时点、
  金额、终态都取自 2026-09-18 的真实运行。
- 成片时间轴是**展示加速**，不代表真实耗时：主视频 1.43×、故障片段 1.5×；真实墙钟
  见上表。
- 审批人是演示账号（`@admin:matrix-local.agentteams.io:8086`，显示名"财务负责人（演示）"），
  不是真实企业员工。
- 主案例的企业、合同、政策与佣金争议为合成数据；ERPNext 为真实运行的只读事实来源。
