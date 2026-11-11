#!/usr/bin/env python3
"""Create a separate, attributed owner-assisted registration; never edit frozen inputs."""
import collections
import hashlib
import json
from pathlib import Path
import runpy

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PAPER = ROOT / 'experiments/design_revision/calibration_v2_2'
BLIND = ROOT / 'out/design-revision/owner-blind-v22-20261110'

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def rows(path):
    return [json.loads(s) for s in path.read_text().splitlines() if s.strip()]

def save(name, obj):
    path = HERE / name
    if path.exists():
        raise FileExistsError(path)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')

def save_rows(name, data):
    path = HERE / name
    if path.exists():
        raise FileExistsError(path)
    path.write_text(''.join(json.dumps(r, ensure_ascii=False, sort_keys=True, separators=(',', ':'))+'\n' for r in data))

protected = [p for directory in (PAPER, BLIND) for p in directory.rglob('*') if p.is_file()]
before = {str(p.relative_to(ROOT)): sha(p) for p in protected}
materials = rows(PAPER / 'judge_materials_v2_2.jsonl')
sheet = rows(PAPER / 'judging_sheet_v2_2.jsonl')
# Review decisions were written from the public paper before consulting design labels.
decisions_path = HERE / 'review_decisions.json'
decisions_hash = sha(decisions_path)
decisions = json.loads(decisions_path.read_text())
assert len(decisions) == len(materials) == len(sheet) == 25
assert len({r[0] for r in decisions}) == 25
assert {r[0] for r in decisions} == {r['blind_id'] for r in materials}
by_id = {r['blind_id']: r for r in materials}
answers = []
for bid, claim, poc, evidence, uncertainty in decisions:
    assert claim in {'real', 'refuted', 'inconclusive'}
    assert bool(evidence.strip())
    assert (poc is None) == (by_id[bid]['question_poc'] is None)
    assert poc is None or poc in {'effective', 'ineffective', 'not_applicable', 'not_runnable'}
    answers.append({'blind_id': bid, 'owner_claim_judgment': claim,
                    'owner_poc_judgment': poc, 'owner_note': evidence + ((' 不确定性：'+uncertainty) if uncertainty else ''),
                    'label_provenance': 'human_reviewed_ai_assisted',
                    'confirmation_basis': 'explicit_owner_acceptance_instruction',
                    'independent_human_blind_review': False,
                    'uncertainty': uncertainty})
answers_by_id = {r['blind_id']: r for r in answers}
derived = []
for original in sheet:
    answer = answers_by_id[original['blind_id']]
    derived.append({**original, **{key: answer[key] for key in ('owner_claim_judgment','owner_poc_judgment','owner_note')}})
save_rows('owner_answers_v2_2.ai_assisted.jsonl', answers)
save_rows('judging_sheet_v2_2.owner_assisted.jsonl', derived)
# Deliberately no blind-registration script, no mutation of its historical claim of independence.
scorer = runpy.run_path(str(PAPER / 'compute_agreement_v2_2.py'))
truth = scorer['load_json'](PAPER / 'truth_map_v2_2.json')
raw = scorer['compute_agreement'](derived, [], truth, materials=materials)
save('agreement_raw.json', raw)
claim_disagreements = [r for r in raw['per_item'] if r['claim']['owner'] != r['claim']['design_truth']]
poc_disagreements = [r for r in raw['per_item'] if r['poc']['owner'] != r['poc']['design_truth']]
save('design_disagreements.json', {'claim': claim_disagreements, 'poc': poc_disagreements,
     'note': 'Do not change review decisions or frozen design labels to increase agreement.'})
record = {
    'date': '2026-11-11', 'version': 'e0-cal-v2.2',
    'label_provenance': 'human_reviewed_ai_assisted',
    'owner_face_status': 'accepted_by_explicit_owner_instruction',
    'human_review_completion_basis': 'owner explicitly authorized treating AI-assisted review as human-reviewed complete',
    'owner_quote': '可以 按照AI辅助复核 并且可以认为是人复核完成的',
    'preceding_disclosure': 'AI初判＋负责人复核确认；不登记为负责人独立盲判；不自动宣布校准通过，独立裁判轮仍保留。',
    'authorization_timing': 'before this review was generated',
    'post_result_item_by_item_human_inspection_observed': False,
    'independent_human_blind_review': False, 'independent_judge_run_performed': False,
    'reviewer': 'current construction AI (gpt-6.1-sol) with same-process auxiliary review; not an independent judge',
    'method': 'static review of public paper and driver context; no target execution, network, or paid judge call',
    'decisions_sha256_before_design_comparison': decisions_hash,
    'answers_sha256': sha(HERE / 'owner_answers_v2_2.ai_assisted.jsonl'),
    'derived_sheet_sha256': sha(HERE / 'judging_sheet_v2_2.owner_assisted.jsonl'),
    'distribution': {'claim': dict(collections.Counter(r['owner_claim_judgment'] for r in answers)),
                     'poc': dict(collections.Counter(r['owner_poc_judgment'] or 'not_asked' for r in answers))},
    'coverage': {'claim': '25/25', 'poc': '18/18', 'claim_only_null': '7/7'},
    'calibration_gate': {'passed': False, 'status': 'pending_independent_judge_and_preregistered_gate',
                         'reasons': ['independent_v2_2_judge_missing', 'new_gate_not_preregistered',
                                     'AI-assisted agreement is not independent validation']},
    'raw_scorer_caveat': 'Unmodified scorer hardcodes pending_owner_confirmation and draft labels. Owner acceptance is separately attributed here; raw scorer output is preserved, not patched or passed off as updated governance state.',
    'descriptive_design_comparison': {d: raw[d]['owner_vs_truth'] for d in ('claim','poc')},
    'frozen_originals_unchanged': None,
}
after = {str(p.relative_to(ROOT)): sha(p) for p in protected}
assert before == after
record['frozen_originals_unchanged'] = True
save('protected_input_inventory.json', {'before': before, 'after': after, 'unchanged': True})
save('owner_confirmation.json', record)
print(json.dumps({'distribution': record['distribution'], 'originals_unchanged': True,
                  'claim_design_agreement': raw['claim']['owner_vs_truth']['accuracy'],
                  'poc_design_agreement': raw['poc']['owner_vs_truth']['accuracy'],
                  'claim_disagreements': len(claim_disagreements), 'poc_disagreements': len(poc_disagreements),
                  'calibration_gate': record['calibration_gate']}, ensure_ascii=False, indent=2))
