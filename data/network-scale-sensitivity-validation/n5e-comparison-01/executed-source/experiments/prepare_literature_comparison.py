"""准备有界文献训练、校准和后续比较规格，不生成测试业务。"""
import argparse
import copy
import itertools
import json
from pathlib import Path
import random

from literature_contract import method_contract, PARAMETERS, ROOT, sha


def grids():
    return {'intopp_due': [dict(PARAMETERS, early_fraction=early, weighted_dependencies=weighted)
                for early, weighted in itertools.product((0.0, 0.5, 1.0), (False, True))],
            'd_into_tt': [dict(PARAMETERS, value_scale=value, average_change_bytes=change)
                for value, change in itertools.product((48.0, 144.0, 432.0), (6.0, 12.0))]}


def protocol():
    contract = method_contract()
    return dict(status='matrix_prepared_pending_training', execution_ready=False,
        profiles=contract['profiles'], common_initialization='保持N4c冻结四配置',
        reference_contract_sha256=contract['reference_contract_sha256'],
        literature_methods=contract['methods'], parameter_grids=grids(),
        observation_period_us=100000, decision_period_us=1000000, capacity=1,
        grid_us=[50000, 100000, 150000, 200000, 400000, 600000, 800000, 1000000],
        max_candidates=48, max_update_rpcs=6, candidate_universe_size=6561,
        native_unique_assignment=True, native_flow_sum_capacity_used_online=False,
        training=dict(seeds=[11, 29], presets_per_method=6, cases=96, deployment_blocks=1,
                      selected_global_presets_per_method=2),
        calibration=dict(seeds=[1301, 1423], presets_per_method=2, cases=32, deployment_blocks=1,
                         selected_global_presets_per_method=1),
        selection_key=['infeasible_or_unavailable_count', 'maximum_profile_mean_controller_loss_upper',
                       'pooled_mean_controller_loss_upper', 'mean_api_bytes_per_s', 'preset_index'],
        performance=dict(methods=['static', 'feedback', 'local', 'joint', 'phase_joint', 'uniform_guard',
            'phase_guard', 'phase_local_guard', 'intopp_due', 'd_into_tt'],
            business_count=4, deployment_blocks=2, cases=320, independent_business_units=4,
            fresh_business_seeds=[], consumed_test_seeds=list(range(3001, 3009)),
            fresh_seed_rule='入口完成和参数冻结后重新审计，取3001起最小四个未消费整数，再冻结后生成',
            existing_eight_methods_retuned=False, static_candidates_expanded=False,
            primary_observer='controller', secondary_observer='collector'),
        cost_layers=['e1_inband_frame_bytes', 'e2_inband_frame_bytes', 'e3_inband_frame_bytes',
                     'export_attempt_frame_bytes', 'api_serialized_bytes', 'local_notice_payload_bytes'],
        retention='全部活跃任务、运行、超限与不可评价点；同业务配对，部署块不扩充独立业务分母',
        budget_scope='逐链路、导出、API和本地通知分别评价；平均测量窗口，不混加不同层字节',
        prepared_scope='参数候选与矩阵准备，尚未冻结性能参数或未见业务，未运行训练或比较')


def training_order(spec):
    entries = []
    for profile in spec['profiles']:
        for seed in spec['training']['seeds']:
            for method, presets in spec['parameter_grids'].items():
                for index, parameters in enumerate(presets):
                    entries.append(dict(copy.deepcopy(profile), method=method, seed=seed, block=1,
                        preset_index=index, literature=parameters,
                        name=f"{profile['scenario']}-{profile['budget_level']}-s{seed}-{method}-p{index}"))
    random.Random(42426417).shuffle(entries)
    return [dict(row, position=i) for i, row in enumerate(entries)]


def prepare(output):
    spec = protocol()
    output.mkdir(parents=True, exist_ok=False)
    for name, value in (('protocol.json', spec), ('training-order.json', training_order(spec))):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    manifest = dict(status=spec['status'], execution_ready=False, business_generated=False, fresh_inputs_generated=False,
        script_sha256=sha(ROOT / 'experiments/prepare_literature_comparison.py'),
        artifact_sha256={name: sha(output / name) for name in ('protocol.json', 'training-order.json')})
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(dict(status=spec['status'], training_cases=96, calibration_cases=32,
                         performance_cases=320, business_generated=False)))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    prepare(p.parse_args().output)
