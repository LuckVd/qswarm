# 播种报告（组织出生证明，SPEC-6 §4.4）

- bank_version: 3 · entry_items: 12 · seed: 0
- generated_at: 2026-10-21T17:01:09.505536Z · contract_version: cv1
- degraded: False（无效格占比 0.0000）
- totals: {"cells": 5, "invalid_cells": 0, "deposits": 5, "exam_result_rows": 59, "model_calls": 70, "schema_fail": 3, "infra_fail": 1, "canary_hit": 0}

| agent | role | tier | QC min_pass | accuracy | passed | status |
|---|---|---|---|---|---|---|
| strong:zhipu/glm-5.3-flash@cv1 | verifier | engineering | 0.8 | 0.7250 | False | provisioning |

## 逐格判定

- strong:zhipu/glm-5.3-flash@cv1 × injection: status=ok real 5/8 fp_reject 3/4 acc=0.625 dir=1 w=0.275 schema_fail=1 infra_fail=0 canary_hit=0 deposit=True
- strong:zhipu/glm-5.3-flash@cv1 × crypto: status=ok real 5/8 fp_reject 2/4 acc=0.625 dir=1 w=0.275 schema_fail=1 infra_fail=1 canary_hit=0 deposit=True
- strong:zhipu/glm-5.3-flash@cv1 × authz: status=ok real 6/8 fp_reject 4/4 acc=0.75 dir=1 w=0.35 schema_fail=0 infra_fail=0 canary_hit=0 deposit=True
- strong:zhipu/glm-5.3-flash@cv1 × config_dep: status=ok real 5/8 fp_reject 4/4 acc=0.625 dir=1 w=0.275 schema_fail=1 infra_fail=0 canary_hit=0 deposit=True
- strong:zhipu/glm-5.3-flash@cv1 × mem_safety: status=ok real 8/8 fp_reject 3/4 acc=1.0 dir=1 w=0.5 schema_fail=0 infra_fail=0 canary_hit=0 deposit=True
