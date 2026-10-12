# 播种报告（组织出生证明，SPEC-6 §4.4）

- bank_version: 1 · entry_items: 12 · seed: 0
- generated_at: 2026-10-12T02:32:35.036684Z · contract_version: cv1
- degraded: False（无效格占比 0.0000）
- totals: {"cells": 20, "invalid_cells": 0, "deposits": 3, "exam_result_rows": 240, "model_calls": 254, "schema_fail": 45, "infra_fail": 0, "canary_hit": 0}

| agent | role | tier | QC min_pass | accuracy | passed | status |
|---|---|---|---|---|---|---|
| cheap:zhipu/glm-4.5-air@cv1 | scout | cheap | 0.8 | 0.9250 | True | active |
| cheap:zhipu/glm-4.6@cv1 | scout | cheap | 0.8 | 0.8500 | True | active |
| strong:zhipu/glm-5.2@cv1 | auditor | strong | 0.8 | 0.4000 | False | provisioning |
| strong:zhipu/glm-5@cv1 | auditor | strong | 0.8 | 0.3000 | False | provisioning |

## 逐格判定

- cheap:zhipu/glm-4.5-air@cv1 × injection: status=ok real 8/8 fp_reject 3/4 acc=1.0 dir=1 w=0.5 schema_fail=0 infra_fail=0 canary_hit=0 deposit=False
- cheap:zhipu/glm-4.5-air@cv1 × crypto: status=ok real 8/8 fp_reject 3/4 acc=1.0 dir=1 w=0.5 schema_fail=0 infra_fail=0 canary_hit=0 deposit=False
- cheap:zhipu/glm-4.5-air@cv1 × authz: status=ok real 5/8 fp_reject 0/4 acc=0.625 dir=1 w=0.275 schema_fail=0 infra_fail=0 canary_hit=0 deposit=False
- cheap:zhipu/glm-4.5-air@cv1 × config_dep: status=ok real 8/8 fp_reject 4/4 acc=1.0 dir=1 w=0.5 schema_fail=0 infra_fail=0 canary_hit=0 deposit=False
- cheap:zhipu/glm-4.5-air@cv1 × mem_safety: status=ok real 8/8 fp_reject 1/4 acc=1.0 dir=1 w=0.5 schema_fail=0 infra_fail=0 canary_hit=0 deposit=False
- cheap:zhipu/glm-4.6@cv1 × injection: status=ok real 7/8 fp_reject 1/4 acc=0.875 dir=1 w=0.425 schema_fail=3 infra_fail=0 canary_hit=0 deposit=False
- cheap:zhipu/glm-4.6@cv1 × crypto: status=ok real 7/8 fp_reject 4/4 acc=0.875 dir=1 w=0.425 schema_fail=1 infra_fail=0 canary_hit=0 deposit=False
- cheap:zhipu/glm-4.6@cv1 × authz: status=ok real 4/8 fp_reject 0/4 acc=0.5 dir=1 w=0.2 schema_fail=2 infra_fail=0 canary_hit=0 deposit=False
- cheap:zhipu/glm-4.6@cv1 × config_dep: status=ok real 8/8 fp_reject 4/4 acc=1.0 dir=1 w=0.5 schema_fail=0 infra_fail=0 canary_hit=0 deposit=False
- cheap:zhipu/glm-4.6@cv1 × mem_safety: status=ok real 8/8 fp_reject 0/4 acc=1.0 dir=1 w=0.5 schema_fail=1 infra_fail=0 canary_hit=0 deposit=False
- strong:zhipu/glm-5.2@cv1 × injection: status=ok real 2/8 fp_reject 3/4 acc=0.25 dir=-1 w=0.35 schema_fail=0 infra_fail=0 canary_hit=0 deposit=False
- strong:zhipu/glm-5.2@cv1 × crypto: status=ok real 1/8 fp_reject 4/4 acc=0.125 dir=-1 w=0.425 schema_fail=7 infra_fail=0 canary_hit=0 deposit=False
- strong:zhipu/glm-5.2@cv1 × authz: status=ok real 2/8 fp_reject 4/4 acc=0.25 dir=-1 w=0.35 schema_fail=4 infra_fail=0 canary_hit=0 deposit=False
- strong:zhipu/glm-5.2@cv1 × config_dep: status=ok real 4/8 fp_reject 4/4 acc=0.5 dir=1 w=0.2 schema_fail=4 infra_fail=0 canary_hit=0 deposit=False
- strong:zhipu/glm-5.2@cv1 × mem_safety: status=ok real 7/8 fp_reject 2/4 acc=0.875 dir=1 w=0.425 schema_fail=3 infra_fail=0 canary_hit=0 deposit=False
- strong:zhipu/glm-5@cv1 × injection: status=ok real 1/8 fp_reject 3/4 acc=0.125 dir=-1 w=0.425 schema_fail=2 infra_fail=0 canary_hit=0 deposit=False
- strong:zhipu/glm-5@cv1 × crypto: status=ok real 1/8 fp_reject 3/4 acc=0.125 dir=-1 w=0.425 schema_fail=5 infra_fail=0 canary_hit=0 deposit=False
- strong:zhipu/glm-5@cv1 × authz: status=ok real 2/8 fp_reject 4/4 acc=0.25 dir=-1 w=0.35 schema_fail=3 infra_fail=0 canary_hit=0 deposit=True
- strong:zhipu/glm-5@cv1 × config_dep: status=ok real 3/8 fp_reject 4/4 acc=0.375 dir=-1 w=0.275 schema_fail=5 infra_fail=0 canary_hit=0 deposit=True
- strong:zhipu/glm-5@cv1 × mem_safety: status=ok real 5/8 fp_reject 1/4 acc=0.625 dir=1 w=0.275 schema_fail=5 infra_fail=0 canary_hit=0 deposit=True
