# Verify Report — TRIAL-prod

- tier: process · mode: pr · 名义 0 cents（D33 双轨；沙箱非 LLM 成本见 sandbox_disclosure）

## 漏斗

可验簇 2 → 选取 2 → verified_real 2 / verified_fp 0 / verify_error 0（预算跳过 0 / 无 verifier 0）

## 簇明细

| 簇 | 结论 | error_kind | attempts | PoC 来源 | 比对 | 耗时 s |
|---|---|---|---|---|---|---|
| C-20261020-000001 | verified_real | - | 1 | template | True | 0.1 |
| C-20261020-000002 | verified_real | - | 1 | template | True | 0.1 |

## 失败与披露（如实计数）

- env_build_fail 0 / poc_fail 0 / timeout 0 / flaky 0 / template_unsupported 0 / bee_schema_fail 0
- 沙箱墙钟 0.1s（2 次）· env 构建 0.0s（非 LLM 成本披露，R7-②）
