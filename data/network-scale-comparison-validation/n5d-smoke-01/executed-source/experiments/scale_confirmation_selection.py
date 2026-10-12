"""按完整六部署资格和冻结排序确定共同静态初始化。"""
from collections import Counter

from check_rolling_reference import same


def select(summary, selected):
    same(len(summary), 120, '确认完整案例分母')
    scores, cells = [], []
    for cell in selected:
        candidates = []
        for candidate in cell['selected']:
            group = sorted([r for r in summary if (r['network_id'], r['scenario'], r['candidate_id']) ==
                            (cell['network_id'], cell['scenario'], candidate['candidate_id'])],
                           key=lambda r: (r['block'], r['business_seed']))
            same(Counter((r['block'], r['business_seed']) for r in group),
                 Counter((b, s) for b in (0, 1, 2) for s in (11, 29)), '每候选三块两业务完整且不重复')
            valid_age = all(r['controller_loss'] is not None for r in group)
            export = None if any(r['export_bytes_per_s'] is None for r in group) else sum(r['export_bytes_per_s'] for r in group) / 6
            eligible = all(r['status'] == 'passed' and r['window_feasibility'] == 'within' for r in group)
            score = dict(network_id=cell['network_id'], scenario=cell['scenario'], candidate_id=candidate['candidate_id'],
                config=candidate['config'], training_rank=candidate['rank'], eligible=eligible,
                feasibility=[r['window_feasibility'] for r in group],
                worst_controller_upper=max(r['controller_loss'][1] for r in group) if valid_age else None,
                mean_controller_upper=sum(r['controller_loss'][1] for r in group) / 6 if valid_age else None,
                mean_controller_loss=[sum(r['controller_loss'][i] for r in group) / 6 for i in (0, 1)] if valid_age else None,
                mean_collector_loss=[sum(r['collector_loss'][i] for r in group) / 6 for i in (0, 1)]
                    if all(r['collector_loss'] is not None for r in group) else None,
                mean_export_bytes_per_s=export)
            scores.append(score)
            if eligible:
                if not valid_age or export is None:
                    raise ValueError('达标候选缺少实际目标或费用')
                candidates.append(score)
        candidates.sort(key=lambda r: (r['worst_controller_upper'], r['mean_controller_upper'],
            r['mean_export_bytes_per_s'], tuple(-1 if v is None else v for v in r['config'])))
        cells.append(dict(network_id=cell['network_id'], scenario=cell['scenario'],
            eligible_candidates=len(candidates), selected=candidates[0] if candidates else None,
            status='frozen_common_initialization' if candidates else 'no_common_feasible_candidate'))
    same(len(scores), 20, '全部候选评分')
    return cells, scores
