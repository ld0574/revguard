# Evidence 目录

`demo-rehearsal/` 由 `make evidence-bundle` 从干净临时数据库生成，记录同一案件经过官方
MCP Client/Server、20 个 StageTask、人审暂停、显式排练批准、验证失败与回滚复核的证据。

安全和真实性约定：

- 所有业务数据均为 `SYNTHETIC`；
- 排练批准标为 `simulated_human=true`，不冒充录屏中的真人点击；
- 原始 capability token 不进入证据包，只保留不可授权指纹；
- AgentTeams 完整房间与云 PolarDB 证据在真实采集前保持 `PENDING_EXTERNAL_CAPTURE`；
- `manifest.json` 为每个证据文件记录 SHA-256，修改后必须重新生成整个包。
