"""Authored method-construction exercise with exact checks and artifact reload."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from kuttaka_runtime import ArtifactMemory
from kuttaka_runtime.store import Journal
from .compiler import construct, solve, validate


def check(coefficients, target, result):
    """Direct power-sum reference; independent of compiler Horner/update code."""
    evaluate = lambda x: sum(c*x**i for i, c in enumerate(coefficients))
    if result['status'] == 'no_solution':
        return target < evaluate(0)
    if result['status'] != 'solved':
        return False
    root = result['root']
    return (evaluate(root) <= target < evaluate(root+1)
            and result['remainder'] == target-evaluate(root)
            and result['next_gap'] == evaluate(root+1)-target)


def run(output):
    output.mkdir(parents=True, exist_ok=False)
    fixtures = [
        ('square', [0, 0, 1], [0, 1, 1520, 1521, 1522]),
        ('cube', [0, 0, 0, 1], [0, 1, 50652, 50653, 50654]),
        ('seventh_power', [0]*7+[1], [2, 10**100+123]),
        ('mixed_cubic', [7, 2, 3, 1], [0, 6, 7, 8, 328, 10**50+71]),
        ('six_times_sum_of_squares', [0, 1, 3, 2], [6*500, 6*10000]),
        ('quartic_load', [11, 3, 0, 2, 1], [11, 13, 10**40+57]),
    ]
    methods, reports = [], []
    compiler_path = Path(__file__).with_name('compiler.py')
    compiler_source = compiler_path.read_bytes()
    with Journal(output/'events.jsonl') as journal:
        with ArtifactMemory(output/'memory') as memory:
            for name, coefficients, targets in fixtures:
                method = construct(coefficients)
                aid = memory.put({'kind': 'method', 'body': method, 'dependencies': [],
                    'provenance': {'producer': 'authored polynomial method constructor',
                                   'compiler_sha256': hashlib.sha256(compiler_source).hexdigest()},
                    'applicability': {'class': 'specified nonnegative integer polynomial inverse',
                                      'relation_is_supplied': True}})
                journal.append('method_constructed', {'artifact': aid, 'name': name,
                    'relation': coefficients, 'generated_terms': len(method['increment'])})
                methods.append({'name': name, 'artifact': aid, 'targets': targets})
        # Retain only artifact IDs and new inputs. There is no table of answers.
        del fixtures, method, coefficients, targets
        with ArtifactMemory(output/'memory') as memory:
            for item in methods:
                record = memory.get(item['artifact'])['body']
                for target in item['targets']:
                    journal.append('method_execution_attempt', {'artifact': item['artifact'], 'target': target})
                    result = solve(record, target)
                    accepted = check(record['coefficients'], target, result)
                    journal.append('method_execution_result', {'artifact': item['artifact'],
                        'target': target, 'result': result, 'independent_check': accepted})
                    if not accepted:
                        raise AssertionError('constructed method failed the original relation')
                    reports.append({'name': item['name'], 'artifact': item['artifact'],
                                    'target': target, 'status': result['status'],
                                    'root': result.get('root'), 'probes': result['steps'],
                                    'independent_check': accepted})
            square = memory.get(methods[0]['artifact'])['body']
            cube = memory.get(methods[1]['artifact'])['body']
            copied_solver = solve(square, 50653)
            changed = deepcopy(cube)
            changed['increment'][0]['coefficient'] += 1
            rejected = {}
            for name, operation in [
                    ('corrupt_increment', lambda: validate(changed)),
                    ('negative_coefficient', lambda: construct([0, 3, -1])),
                    ('degree_outside_contract', lambda: construct([0]*9+[1]))]:
                try:
                    operation()
                except ValueError:
                    rejected[name] = True
                else:
                    raise AssertionError('negative control was accepted: ' + name)
            budget = solve(cube, 50653, max_steps=1)
            controls = {**rejected, 'exhausted_budget_status': budget['status'],
                'copied_square_solver_root': copied_solver['root'],
                'copied_square_solver_satisfies_cube_goal': check(cube['coefficients'], 50653, copied_solver)}
            if budget['status'] != 'incomplete' or controls['copied_square_solver_satisfies_cube_goal']:
                raise AssertionError('control failed to distinguish method construction from solver copying')
            journal.append('controls', controls)
        result = {'attribution': 'engineered constructor and authored relations; no LLM discovery claim',
            'scope': 'six supplied relations within ONE monotone polynomial inverse class',
            'compiler_sha256': hashlib.sha256(compiler_source).hexdigest(),
            'compiler_source_bytes': len(compiler_source), 'constructed_methods': len(methods),
            'checked_executions_after_reload': len(reports), 'model_calls': 0,
            'cases': reports, 'controls': controls}
        journal.append('summary', result)
    result['artifact_store_bytes'] = sum(p.stat().st_size for p in (output/'memory').rglob('*') if p.is_file())
    (output/'results.json').write_text(json.dumps(result, indent=2)+'\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    report = run(parser.parse_args().output)
    print(json.dumps({k: report[k] for k in ('constructed_methods', 'checked_executions_after_reload',
                                            'controls', 'model_calls')}, indent=2))
