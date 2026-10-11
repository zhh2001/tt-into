"""绑定最终共同初始化，冻结六预设动态参数的有限网络校准清单。"""
import argparse
import json
from pathlib import Path
import random
import shutil

from confirmation_contract import PROFILES, FROZEN_PLAN_SHA256, sha
from run_matched_reference import AdaptiveSettings, GUARDED, METHODS, PARAMETERS, plain, settings
from rolling_workloads import budget_for

CALIBRATION_SEEDS = (1301, 1423)
ORDER_SEED = 42426406
ROOT = Path(__file__).resolve().parents[1]


def prepare(analysis, output):
    manifest = json.loads((analysis / 'manifest.json').read_text())
    if manifest['status'] != 'passed' or len(manifest['cases']) != 36 or manifest['confirmation_plan_sha256'] != FROZEN_PLAN_SHA256:
        raise ValueError('必须先完成冻结合同下的完整静态确认')
    names = ('confirmation-plan.json', 'confirmation-scores.json', 'frozen-static.json', 'summary-checks.json')
    for name in names:
        if sha(analysis / name) != manifest['artifact_sha256'][name]:
            raise ValueError('静态确认来源摘要不同')
    if json.loads((analysis / 'summary-checks.json').read_text())['status'] != 'passed':
        raise ValueError('静态确认尚未独立通过')
    frozen = json.loads((analysis / 'frozen-static.json').read_text())
    if [(r['scenario'], r['budget_level']) for r in frozen] != list(PROFILES) or any(r['status'] != 'frozen' for r in frozen):
        raise ValueError('四场景缺少可行共同初始化')
    presets = []
    for method in METHODS:
        if method in GUARDED:
            presets.extend(dict(method=method, parameter_index=i, adaptive=plain(p)) for i, p in enumerate(PARAMETERS))
        else:
            presets.append(dict(method=method, parameter_index=None, adaptive=plain(AdaptiveSettings())))
    order = []
    for r in frozen:
        for seed in CALIBRATION_SEEDS:
            for preset in presets:
                suffix = 'fixed' if preset['parameter_index'] is None else f"p{preset['parameter_index']:02d}"
                order.append(dict(preset, scenario=r['scenario'], budget_level=r['budget_level'], seed=seed,
                    initial_config=r['selected_config'], name=f"{r['scenario']}-{r['budget_level']}-s{seed}-{preset['method']}-{suffix}"))
    random.Random(ORDER_SEED).shuffle(order)
    order = [dict(r, position=i, order_seed=ORDER_SEED) for i, r in enumerate(order)]
    if len(order) != 184 or len({r['name'] for r in order}) != 184:
        raise ValueError('网络校准完整分母错误')
    sources = ('experiments/prepare_dynamic_network_calibration.py', 'experiments/confirmation_contract.py',
               'experiments/run_matched_reference.py', 'experiments/rolling_workloads.py',
               'src/control/matched_live.py', 'src/orchestration/matched_grid.py', 'src/orchestration/adaptive.py')
    plan = dict(status='prepared', scope='有限真实网络校准，未执行；1301/1423 已用于模型校准，不是新测试业务',
        analysis_manifest_sha256=sha(analysis / 'manifest.json'), input_sha256={n: sha(analysis / n) for n in names},
        source_sha256={n: sha(ROOT / n) for n in sources}, calibration_seeds=list(CALIBRATION_SEEDS),
        training_seeds=[11, 29], fresh_test_seeds_generated=False, methods=list(METHODS), presets=presets,
        expected_cases=184, cases_per_guarded_parameter=8, repeats_per_setting_business=1,
        cases_by_kind={'guarded_parameter_calibration': 144, 'fixed_dynamic_reference': 32, 'static_reference': 8},
        common_initialization=frozen, settings=plain(settings()), fixed_packet_record_limit=1,
        profiles=[dict(scenario=s, budget_level=b, budget=plain(budget_for(s, b)), notice_payload_bytes_per_s=128) for s, b in PROFILES],
        duration_us=10000000, warmup_us=[0, 2000000], measurement_window_us=[2000000, 10000000],
        notification_offsets_us=[0, 5000000], order_seed=ORDER_SEED, execution_order=order,
        selection=dict(unit='每修复方法一个跨四场景、两业务的全局参数，八例完整，不逐场景挑参数',
            evaluability='八例均有有限费用上界及有效生成 AoI 条件界；缺失或工程错误不完成选择',
            rank=['非 within 案例数', '逐案例各层 max(费用上界/限额-1,0) 的总和', '八例采集端年龄损失上界均值', '成功字段更新总数', '参数索引'],
            layers=['e1', 'e2', 'e3', 'export', 'api_serialized', 'local_notice_payload'],
            no_feasible_parameter='仍按相同规则选有限最佳参数，明确全部达标失败，不提高预算或隐藏失败'),
        information='实际取得的合法报告前缀、已计费返回计数、确认历史、自有实际费用及实收通知；未来业务和发送仪表只用于离线检查',
        stopping_rule='固定六预设全部执行后冻结参数；不凭不利结果扩张调参，新正式测试列表在校准和比较合同完成后生成',
        readiness='候选、预算、初始化及顺序已冻结；下一阶段需独立执行入口、完整检查器和首例前源码合同')
    output.mkdir(parents=True, exist_ok=False)
    for name in sources:
        target = output / 'executed-source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    (output / 'calibration-plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    print(json.dumps({'status': 'prepared', 'expected_cases': 184, 'fresh_test_seeds_generated': False}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--analysis', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    prepare(args.analysis, args.output)
