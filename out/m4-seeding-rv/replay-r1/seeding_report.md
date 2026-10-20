# 播种报告（组织出生证明，SPEC-6 §4.4）

- bank_version: 2 · entry_items: 12 · seed: 0
- generated_at: 2026-10-19T23:36:48.071826Z · contract_version: cv1
- degraded: False（无效格占比 0.0000）
- totals: {"cells": 20, "invalid_cells": 0, "deposits": 20, "exam_result_rows": 240, "model_calls": 240, "schema_fail": 1, "infra_fail": 0, "canary_hit": 0}

| agent | role | tier | QC min_pass | accuracy | passed | status |
|---|---|---|---|---|---|---|
| cheap:zhipu/glm-4.5-air@cv1 | scout | cheap | 0.8 | 0.9750 | True | active |
| cheap:zhipu/glm-4.6@cv1 | scout | cheap | 0.8 | 0.9500 | True | active |
| strong:zhipu/glm-5.3-flash@cv1 | auditor | strong | 0.8 | 0.9000 | True | active |
| engineering:zhipu/glm-5.3-flash@cv1 | verifier | engineering | 0.8 | 0.6750 | False | provisioning |

## 逐格判定

- cheap:zhipu/glm-4.5-air@cv1 × injection: status=ok real 7/8 fp_reject 2/4 acc=0.875 dir=1 w=0.425 schema_fail=0 infra_fail=0 canary_hit=0 deposit=True
- cheap:zhipu/glm-4.5-air@cv1 × crypto: status=ok real 8/8 fp_reject 2/4 acc=1.0 dir=1 w=0.5 schema_fail=0 infra_fail=0 canary_hit=0 deposit=True
- cheap:zhipu/glm-4.5-air@cv1 × authz: status=ok real 8/8 fp_reject 0/4 acc=1.0 dir=1 w=0.5 schema_fail=0 infra_fail=0 canary_hit=0 deposit=True
- cheap:zhipu/glm-4.5-air@cv1 × config_dep: status=ok real 8/8 fp_reject 3/4 acc=1.0 dir=1 w=0.5 schema_fail=0 infra_fail=0 canary_hit=0 deposit=True
- cheap:zhipu/glm-4.5-air@cv1 × mem_safety: status=ok real 8/8 fp_reject 0/4 acc=1.0 dir=1 w=0.5 schema_fail=0 infra_fail=0 canary_hit=0 deposit=True
- cheap:zhipu/glm-4.6@cv1 × injection: status=ok real 8/8 fp_reject 4/4 acc=1.0 dir=1 w=0.5 schema_fail=0 infra_fail=0 canary_hit=0 deposit=True
- cheap:zhipu/glm-4.6@cv1 × crypto: status=ok real 8/8 fp_reject 4/4 acc=1.0 dir=1 w=0.5 schema_fail=0 infra_fail=0 canary_hit=0 deposit=True
- cheap:zhipu/glm-4.6@cv1 × authz: status=ok real 6/8 fp_reject 0/4 acc=0.75 dir=1 w=0.35 schema_fail=0 infra_fail=0 canary_hit=0 deposit=True
- cheap:zhipu/glm-4.6@cv1 × config_dep: status=ok real 8/8 fp_reject 4/4 acc=1.0 dir=1 w=0.5 schema_fail=0 infra_fail=0 canary_hit=0 deposit=True
- cheap:zhipu/glm-4.6@cv1 × mem_safety: status=ok real 8/8 fp_reject 0/4 acc=1.0 dir=1 w=0.5 schema_fail=0 infra_fail=0 canary_hit=0 deposit=True
- strong:zhipu/glm-5.3-flash@cv1 × injection: status=ok real 6/8 fp_reject 4/4 acc=0.75 dir=1 w=0.35 schema_fail=0 infra_fail=0 canary_hit=0 deposit=True
- strong:zhipu/glm-5.3-flash@cv1 × crypto: status=ok real 8/8 fp_reject 4/4 acc=1.0 dir=1 w=0.5 schema_fail=0 infra_fail=0 canary_hit=0 deposit=True
- strong:zhipu/glm-5.3-flash@cv1 × authz: status=ok real 6/8 fp_reject 4/4 acc=0.75 dir=1 w=0.35 schema_fail=0 infra_fail=0 canary_hit=0 deposit=True
- strong:zhipu/glm-5.3-flash@cv1 × config_dep: status=ok real 8/8 fp_reject 4/4 acc=1.0 dir=1 w=0.5 schema_fail=0 infra_fail=0 canary_hit=0 deposit=True
- strong:zhipu/glm-5.3-flash@cv1 × mem_safety: status=ok real 8/8 fp_reject 2/4 acc=1.0 dir=1 w=0.5 schema_fail=0 infra_fail=0 canary_hit=0 deposit=True
- engineering:zhipu/glm-5.3-flash@cv1 × injection: status=ok real 6/8 fp_reject 3/4 acc=0.75 dir=1 w=0.35 schema_fail=0 infra_fail=0 canary_hit=0 deposit=True
- engineering:zhipu/glm-5.3-flash@cv1 × crypto: status=ok real 5/8 fp_reject 3/4 acc=0.625 dir=1 w=0.275 schema_fail=0 infra_fail=0 canary_hit=0 deposit=True
- engineering:zhipu/glm-5.3-flash@cv1 × authz: status=ok real 2/8 fp_reject 4/4 acc=0.25 dir=-1 w=0.35 schema_fail=0 infra_fail=0 canary_hit=0 deposit=True
- engineering:zhipu/glm-5.3-flash@cv1 × config_dep: status=ok real 6/8 fp_reject 3/4 acc=0.75 dir=1 w=0.35 schema_fail=1 infra_fail=0 canary_hit=0 deposit=True
- engineering:zhipu/glm-5.3-flash@cv1 × mem_safety: status=ok real 8/8 fp_reject 3/4 acc=1.0 dir=1 w=0.5 schema_fail=0 infra_fail=0 canary_hit=0 deposit=True
