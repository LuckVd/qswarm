"""prompts.version：契约版本锚点（SPEC-3 §1）。

``agent_id ≡ (模型, 契约版本)``（设计 §16.7-2）的版本半边：

- ``agent_id = f"{model_ref}@{CONTRACT_VERSION}"``；
- ``model_ref = f"{tier}:{provider}/{model}"``（如 ``cheap:deepseek/deepseek-chat``，
  即 models.yaml 档位条目的机器 id）；
- bump 规则：templates.py 任一模板常量、parse.py 提取纪律、或输出 schema 语义
  任一变更 → 本值 +1（"cv1" → "cv2" → …）。bump 必须与变更同一次提交落位——
  机器可检：黄金测试对全部模板常量做 sha256 钉死
  （tests/golden/prompts/template_hashes.json），文本变化而版本未 bump → 测试红；
- 换代语义（§16.7-2 逐字）：版本 bump = 全体 agent 换代——新 agent_id 重新走
  入职考播种（SPEC-6 §5），旧 agent 转 dormant（AgentStatus，历史账保留）。
  能力变了账不能继承，防"新模板蹭旧口碑"。
"""

#: 契约版本（bump 规则见模块 docstring；黄金 hash 钉死见 tests/golden/prompts/）。
CONTRACT_VERSION: str = "cv1"

#: 考试调用温度恒 0（§16.7-8 逐字；SPEC-6 §3.2 adapter 消费）。
#: 生产调用温度 ≤ 0.2（上限值落 models.yaml runtime.production_temp，不在此处）。
EXAM_TEMPERATURE: float = 0.0

__all__ = ["CONTRACT_VERSION", "EXAM_TEMPERATURE"]
