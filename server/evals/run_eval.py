"""GeoMind Agent 评测集 runner

断言全部基于可观测事实（工具调用序列、render_scene 入参、最终场景、错误、
回复关键词），不使用 LLM-as-judge，保证确定性与可复现。

用法：
  ./.venv/bin/python evals/run_eval.py                # 全量 30 条
  ./.venv/bin/python evals/run_eval.py --smoke        # 每类抽 1 条（6 条）
  ./.venv/bin/python evals/run_eval.py --ids eval-07,eval-29
  ./.venv/bin/python evals/run_eval.py --concurrency 3
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent import agent_events  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.registry import DatasetRegistry  # noqa: E402
from app.session import Session  # noqa: E402

CASES_FILE = Path(__file__).parent / 'cases.json'
REPORT_DIR = Path(__file__).parent / 'reports'


async def run_case(case: dict, sem: asyncio.Semaphore) -> dict:
    async with sem:
        settings = load_settings()
        registry = DatasetRegistry()
        session = Session()  # 每条 case 独立会话
        called: list[str] = []
        replace_seen = False
        render_args_blob = ''
        render_dataset_ids: set[str] = set()
        render_basemap = None
        render_terrain = None
        had_tool_error = False
        scene = None
        reply = ''
        t0 = time.monotonic()
        runtime_error = None

        try:
            for turn in case['turns']:
                async for ev in agent_events(turn, settings, registry, session, auto_approve=True):
                    if ev['type'] == 'tool_call':
                        called.append(ev['name'])
                        if ev['name'] == 'render_scene':
                            rargs = ev['args']
                            if rargs.get('replaceScene'):
                                replace_seen = True
                            render_args_blob += json.dumps(rargs, ensure_ascii=False)
                            for layer in rargs.get('layers') or []:
                                if layer.get('datasetId'):
                                    render_dataset_ids.add(layer['datasetId'])
                            if rargs.get('basemap'):
                                render_basemap = rargs['basemap']
                            if rargs.get('terrain'):
                                render_terrain = rargs['terrain']
                    elif ev['type'] == 'tool_result':
                        result = ev.get('result') or {}
                        if isinstance(result, dict) and 'error' in result:
                            had_tool_error = True
                    elif ev['type'] == 'done':
                        reply = ev.get('reply') or ''
                        if ev.get('scene'):
                            scene = ev['scene']
                    elif ev['type'] == 'error':
                        runtime_error = ev['message']
        except Exception as exc:  # noqa: BLE001 - 评测要捕获所有失败
            runtime_error = f'{type(exc).__name__}: {exc}'

        elapsed = time.monotonic() - t0
        failures = check(case['expect'], called, replace_seen, render_args_blob,
                         render_dataset_ids, render_basemap, render_terrain,
                         had_tool_error, scene, reply, runtime_error)
        return {
            'id': case['id'],
            'category': case['category'],
            'passed': not failures,
            'failures': failures,
            'called_tools': called,
            'steps': len(called),
            'hadToolError': had_tool_error,
            'elapsedSec': round(elapsed, 1),
            'runtimeError': runtime_error,
            'replyHead': reply[:120],
        }


def check(expect: dict, called: list[str], replace_seen: bool, args_blob: str,
          render_dataset_ids: set[str], render_basemap: str | None,
          render_terrain: str | None, had_tool_error: bool,
          scene: dict | None, reply: str, runtime_error: str | None) -> list[str]:
    failures: list[str] = []
    called_set = set(called)
    layers = (scene or {}).get('layers') or []
    kinds = {l.get('kind') for l in layers}

    if runtime_error:
        failures.append(f'运行时错误：{runtime_error[:120]}')
    for tool in expect.get('must_call', []):
        if tool not in called_set:
            failures.append(f'未调用必需工具 {tool}')
    for tool in expect.get('forbid_call', []):
        if tool in called_set:
            failures.append(f'调用了被禁止的工具 {tool}')
    for kind in expect.get('must_render_kinds', []):
        if kind not in kinds:
            failures.append(f'最终场景缺少图层类型 {kind}（实际：{sorted(kinds)}）')
    for ds in expect.get('must_render_datasets', []):
        if ds not in render_dataset_ids:
            failures.append(f'render_scene 未引用数据集 {ds}（实际：{sorted(render_dataset_ids)}）')
    if 'final_layer_count' in expect and len(layers) != expect['final_layer_count']:
        failures.append(f'图层数={len(layers)}，期望 {expect["final_layer_count"]}')
    if expect.get('must_replace') and not replace_seen:
        failures.append('未使用 replaceScene=true 全量重建')
    if expect.get('forbid_replace') and replace_seen:
        failures.append('不应触发 replaceScene，但检测到了全量重建')
    if expect.get('render_args_has_metric_values') and 'metricValues' not in args_blob:
        failures.append('render_scene 入参缺少 metricValues')
    for needle in expect.get('render_args_contains', []):
        if needle not in args_blob:
            failures.append(f'render_scene 入参未包含 "{needle}"')
    if expect.get('camera_set') and not (scene or {}).get('camera'):
        failures.append('场景未设置 camera')
    if 'final_basemap' in expect and render_basemap != expect['final_basemap']:
        failures.append(f'basemap={render_basemap}，期望 {expect["final_basemap"]}')
    if 'final_terrain' in expect and render_terrain != expect['final_terrain']:
        failures.append(f'terrain={render_terrain}，期望 {expect["final_terrain"]}')
    for kw in expect.get('reply_contains', []):
        if kw not in reply:
            failures.append(f'回复未包含关键词 "{kw}"')
    # 工具 error 后自纠成功属设计能力，不判失败；仅显式要求时判定
    if expect.get('must_no_tool_errors') and had_tool_error:
        failures.append('过程中出现工具 error（用例要求 must_no_tool_errors）')
    return failures


async def main_async(args: argparse.Namespace) -> int:
    cases = json.loads(CASES_FILE.read_text(encoding='utf-8'))
    if args.ids:
        wanted = {x.strip() for x in args.ids.split(',')}
        cases = [c for c in cases if c['id'] in wanted]
    elif args.smoke:
        seen_cat: set[str] = set()
        picked = []
        for c in cases:
            if c['category'] not in seen_cat:
                seen_cat.add(c['category'])
                picked.append(c)
        cases = picked

    print(f'评测 {len(cases)} 条用例，并发 {args.concurrency}\n')
    sem = asyncio.Semaphore(args.concurrency)
    results = await asyncio.gather(*(run_case(c, sem) for c in cases))
    results.sort(key=lambda r: r['id'])

    print(f'{"ID":<9}{"类别":<17}{"结果":<6}{"步数":<5}{"耗时":<7}说明')
    print('-' * 86)
    for r in results:
        mark = 'PASS' if r['passed'] else 'FAIL'
        note = '' if r['passed'] else '；'.join(r['failures'])[:52]
        print(f'{r["id"]:<9}{r["category"]:<17}{mark:<6}{r["steps"]:<5}{r["elapsedSec"]:<7.1f}{note}')

    passed = sum(1 for r in results if r['passed'])
    total = len(results)
    avg_steps = sum(r['steps'] for r in results) / max(total, 1)
    total_sec = sum(r['elapsedSec'] for r in results)

    print('\n' + '=' * 86)
    err_cases = sum(1 for r in results if r['hadToolError'])
    print(f'pass@1: {passed}/{total} = {passed / max(total, 1):.1%}')
    print(f'平均工具步数: {avg_steps:.1f} | 含工具 error 自纠的用例: {err_cases}/{total} | 总耗时（并行）: {total_sec:.0f}s')
    by_cat: dict[str, list[bool]] = {}
    for r in results:
        by_cat.setdefault(r['category'], []).append(r['passed'])
    for cat, marks in sorted(by_cat.items()):
        print(f'  {cat:<17} {sum(marks)}/{len(marks)}')

    report = {
        'runAt': time.strftime('%Y-%m-%d %H:%M:%S'),
        'provider': load_settings().endpoint().provider,
        'model': load_settings().endpoint().model,
        'total': total,
        'passed': passed,
        'passAt1': round(passed / max(total, 1), 4),
        'avgSteps': round(avg_steps, 2),
        'totalElapsedSec': round(total_sec, 1),
        'results': results,
    }
    REPORT_DIR.mkdir(exist_ok=True)
    out = REPORT_DIR / f'eval-{time.strftime("%Y%m%d-%H%M%S")}.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n报告已写入：{out}')
    return 0 if passed == total else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--smoke', action='store_true', help='每类抽 1 条')
    parser.add_argument('--ids', default='', help='逗号分隔的指定用例 id')
    parser.add_argument('--concurrency', type=int, default=3)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == '__main__':
    main()
