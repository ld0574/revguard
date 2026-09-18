-- RevGuard 审计哈希链复算（与 migrations/polardb/001_core.sql 的 BEFORE INSERT 触发器同构）
--
-- 用法（在 202 Docker 内，库容器为 revguard-dev-postgres）：
--   docker exec -i revguard-dev-postgres psql -U revguard -d revguard -X -q \
--     -v case_id=CASE-2026-0001 -f - < chain-verification.sql
--
-- 判定标准：三列 mismatch 必须全为 0，broken 必须为 0。

\echo == 全库：链接 + 行哈希
WITH checked AS (
    SELECT previous_hash, row_digest, row_hash,
           COALESCE(LAG(row_hash) OVER (PARTITION BY case_id ORDER BY seq), 'GENESIS') AS expected_previous,
           encode(digest(previous_hash || ':' || row_digest, 'sha256'), 'hex') AS expected_hash
      FROM audit_events
)
SELECT count(*) AS total,
       count(*) FILTER (WHERE previous_hash <> expected_previous OR row_hash <> expected_hash) AS broken
  FROM checked;

\echo == 单案：canonical row digest 复算 + 链接 + 行哈希
WITH rows AS (
    SELECT seq, previous_hash, row_digest, row_hash,
           encode(digest(jsonb_build_object(
               'case_id', case_id, 'actor', actor, 'event', event,
               'detail', detail, 'created_at', created_at)::text, 'sha256'), 'hex') AS digest_recomputed
      FROM audit_events
     WHERE case_id = :'case_id'
),
linked AS (
    SELECT *, lag(row_hash) OVER (ORDER BY seq) AS prior_hash FROM rows
)
SELECT count(*) AS rows,
       count(*) FILTER (WHERE digest_recomputed <> row_digest) AS digest_mismatch,
       count(*) FILTER (WHERE coalesce(prior_hash, 'GENESIS') <> previous_hash) AS link_mismatch,
       count(*) FILTER (WHERE row_hash <> encode(digest(previous_hash || ':' || row_digest, 'sha256'), 'hex')) AS hash_mismatch
  FROM linked;

\echo == 单案：链头与行数（与导出 JSON 的 summary 对比）
SELECT count(*) AS rows, min(seq) AS first_seq, max(seq) AS last_seq,
       (array_agg(row_hash ORDER BY seq DESC))[1] AS head_hash
  FROM audit_events WHERE case_id = :'case_id';
