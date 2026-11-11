"""Register explicit owner confirmation, without changing frozen AI/source evidence."""
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path('/opt/projects/qswarm')
AI = BASE / 'out/design-revision/ai-review-20261109'
OUT = BASE / 'out/design-revision/human-confirmed-20261109'
SHEET = BASE / 'experiments/design_revision/calibration_v2/judging_sheet_v2.jsonl'
BLIND = BASE / 'experiments/design_revision/calibration_v2/judge_materials_v2.jsonl'
sha = lambda data: hashlib.sha256(data).hexdigest()
original = SHEET.read_bytes()
ai_bytes = (AI / 'ai_judgments.jsonl').read_bytes()
frozen = json.loads((AI / 'review_manifest.json').read_text())
assert sha(original) == frozen['source_sha256'][str(SHEET.relative_to(BASE))], 'Owner sheet changed since AI review; stop.'
assert sha(BLIND.read_bytes()) == frozen['source_sha256'][str(BLIND.relative_to(BASE))]
assert sha(ai_bytes) == frozen['judgments_sha256']
source_lines = original.decode().splitlines(keepends=True)
source = [json.loads(s) for s in source_lines]
ai = [json.loads(s) for s in ai_bytes.decode().splitlines()]
assert len(source) == len(ai) == 25
assert [r['item_id'] for r in source] == [r['item_id'] for r in ai]
assert len({r['item_id'] for r in ai}) == 25
assert all(r.get(k) is None for r in source for k in ('owner_claim_judgment', 'owner_poc_judgment', 'owner_note'))
assert all(r['ai_claim_judgment'] in ('real', 'refuted', 'inconclusive') for r in ai)
assert all(r['ai_poc_judgment'] in (None, 'not_applicable') for r in ai)

owner_rows = []
updated_lines = []
placeholder = '"owner_claim_judgment": null, "owner_note": null, "owner_poc_judgment": null'
for line, src, review in zip(source_lines, source, ai):
    note = (
        '2026-11-09 负责人明确确认“我审查过了  没问题 可以当作人工真值”；'
        '来源=AI初判后负责人复核认可（human_reviewed_ai_assisted），非独立盲审。'
        f"主张范围：{review['inferred_claim']}。"
        f"依据：{review['claim_evidence']}"
        f"限制/不确定性：{review['claim_uncertainty']}"
        f"PoC：{review['poc_evidence']}"
        '确认记录：out/design-revision/human-confirmed-20261109/owner_confirmation.json。'
    )
    fields = {'owner_claim_judgment': review['ai_claim_judgment'], 'owner_note': note, 'owner_poc_judgment': review['ai_poc_judgment']}
    assert line.count(placeholder) == 1
    # Only replace the three previously-null owner fields; retain all other bytes.
    replacement = json.dumps(fields, ensure_ascii=False)[1:-1]
    new_line = line.replace(placeholder, replacement, 1)
    parsed = json.loads(new_line)
    assert {k: v for k, v in parsed.items() if k not in fields} == {k: v for k, v in src.items() if k not in fields}
    updated_lines.append(new_line)
    owner_rows.append({'item_id': src['item_id'], **fields, 'label_provenance': 'human_reviewed_ai_assisted', 'accepted_as_owner_ground_truth': True, 'independent_blind_human_label': False, 'claim_scope': review['claim_scope'], 'inferred_claim': review['inferred_claim'], 'poc_assessment_status': review['poc_assessment_status']})
updated = ''.join(updated_lines).encode()
assert dict(Counter(r['owner_claim_judgment'] for r in owner_rows)) == {'real': 10, 'refuted': 6, 'inconclusive': 9}
assert sum(r['owner_poc_judgment'] is None for r in owner_rows) == 18
assert sum(r['owner_poc_judgment'] == 'not_applicable' for r in owner_rows) == 7
OUT.mkdir(parents=True, exist_ok=False)
with (OUT / 'judging_sheet_v2.before_owner_confirmation.jsonl').open('xb') as f:
    f.write(original)
with (OUT / 'judging_sheet_v2.owner_confirmed.jsonl').open('xb') as f:
    f.write(updated)
with (OUT / 'owner_judgments.jsonl').open('x') as f:
    for r in owner_rows:
        f.write(json.dumps(r, ensure_ascii=False) + '\n')
confirmation = {
    'schema': 'qswarm.owner_confirmation.v1',
    'registered_at': datetime.now(ZoneInfo('Asia/Hong_Kong')).isoformat(),
    'confirmation_source': 'direct_top_level_human_message_in_current_session',
    'confirmation_quote': '我审查过了  没问题 可以当作人工真值',
    'confirmation_request_time_reference': '2026-11-09T23:16:41+08:00 (request context time sample, not a claimed exact signing time)',
    'label_provenance': 'human_reviewed_ai_assisted',
    'accepted_as_owner_ground_truth': True,
    'owner_claim_labels_registered': 25,
    'claim_counts': dict(Counter(r['owner_claim_judgment'] for r in owner_rows)),
    'poc_counts': {'pending_missing_materials': 18, 'not_applicable_question_not_set': 7},
    'owner_review_confirmation_complete': True,
    'dual_question_labels_complete': False,
    'independent_blind_human_label': False,
    'cross_family_judge_agreement_verified': False,
    'calibration_gate': 'NOT_VERIFIED',
    'claim_scope': 'Owner accepts the explicitly scoped/inferred claims, evidence, inconclusive findings and limitations in the frozen AI review; no new exact claim or PoC evidence is inferred.',
    'poc_null_meaning': 'Owner-confirmed lack of supplied material, not an effective/ineffective/not_runnable label.',
    'source_ai_judgments': str((AI / 'ai_judgments.jsonl').relative_to(BASE)),
    'source_ai_judgments_sha256': sha(ai_bytes),
    'sheet_before_sha256': sha(original),
    'sheet_after_sha256': sha(updated),
    'blind_materials_sha256': sha(BLIND.read_bytes()),
    'owner_judgments_sha256': sha((OUT / 'owner_judgments.jsonl').read_bytes()),
    'original_design_truth_modified': False,
    'new_model_calls_or_target_executions': False,
}
with (OUT / 'owner_confirmation.json').open('x') as f:
    json.dump(confirmation, f, ensure_ascii=False, indent=2)
# Validate again immediately before targeted owner-field update.
assert SHEET.read_bytes() == original
SHEET.write_bytes(updated)
assert SHEET.read_bytes() == (OUT / 'judging_sheet_v2.owner_confirmed.jsonl').read_bytes()
print('Registered 25 owner-confirmed claim labels (10 real / 6 refuted / 9 inconclusive).')
print('Preserved 18 missing-material PoC nulls and 7 question-not-set not_applicable labels.')
print('Frozen original AI evidence and pre-confirmation owner sheet retained; calibration gate NOT_VERIFIED.')
