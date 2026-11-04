"""Two causes read from W4-j's journals (w4j-glimmer-kuttaka, interim at 2d8a432; no hidden case opened).

h-c1-regex: the delivered method raised on a postfix operator following another (`a**`). Its probes never
held one: a field took only its first four variants, and for a lower-case pattern those all brought a
character its contract excludes (`A`, `Z`, `0`). A text lengthened by its own last character now comes
first, and the varied inputs take two batches.

h-r1-drag: the planner's property rejected six of the method's outputs; the blind re-derivation agreed
with the method on one of them, and that one agreement withdrew all six rejections. Disputes are now
settled input by input, as differential testing settles a reference.

Every method here is written for this test and run only by the local executor.
"""
import unittest
from unittest import mock

from kuttaka_runtime import pramana
from kuttaka_runtime.pramana import VARIED_CHARS
from tests.test_solve_review_regressions import Model, check, events, make_solver, store

# A pattern may nest groups as deep as its contract allows; the scale test now wraps a check's groups 300 deep
# (`_nested_inputs`), and this descent takes four frames a level, so the right method raises the limit.
DEEP = 'import sys\nsys.setrecursionlimit(20000)\n\n\n'
MATCHER = DEEP + '''def _parse(p):
    pos = 0

    def expr():
        nonlocal pos
        alts = [term()]
        while pos < len(p) and p[pos] == '|':
            pos += 1
            alts.append(term())
        return ('alt', alts) if len(alts) > 1 else alts[0]

    def term():
        nonlocal pos
        items = []
        while pos < len(p) and p[pos] not in '|)':
            items.append(factor())
        return ('cat', items)

    def factor():
        nonlocal pos
        node = atom()
        while pos < len(p) and p[pos] in '*+?':
            node = (p[pos], node)
            pos += 1
        return node

    def atom():
        nonlocal pos
        c = p[pos]
        if c == '(':
            pos += 1
            node = expr()
            if pos >= len(p) or p[pos] != ')':
                raise ValueError('unbalanced')
            pos += 1
            return node
        if c == '.' or 'a' <= c <= 'z':
            pos += 1
            return ('dot',) if c == '.' else ('lit', c)
        raise ValueError('unexpected ' + c)

    node = expr()
    if pos != len(p):
        raise ValueError('trailing')
    return node


def _ends(node, text, i):
    kind = node[0]
    if kind == 'lit':
        return {i + 1} if i < len(text) and text[i] == node[1] else set()
    if kind == 'dot':
        return {i + 1} if i < len(text) else set()
    if kind == 'cat':
        cur = {i}
        for item in node[1]:
            cur = {e for s in cur for e in _ends(item, text, s)}
        return cur
    if kind == 'alt':
        return {e for a in node[1] for e in _ends(a, text, i)}
    if kind == '?':
        return {i} | _ends(node[1], text, i)
    frontier = _ends(node[1], text, i) if kind == '+' else {i}
    reach = set(frontier)
    while frontier:
        frontier = {e for s in frontier for e in _ends(node[1], text, s)} - reach
        reach |= frontier
    return reach


def run(i):
    return len(i['text']) in _ends(_parse(i['pattern']), i['text'], 0)
'''
# The W4-j method's shape: one postfix operator per atom, and a raise on any other character there.
STRICT = MATCHER.replace("        while pos < len(p) and p[pos] in '*+?':", "        if pos < len(p) and p[pos] in '*+?':")
REFERENCE = MATCHER.replace('def run(i):', 'def reference(i):')
assert STRICT != MATCHER

SPACE = {}
exec(MATCHER[len(DEEP):], SPACE)   # the auditor's truth, in this process: its limit stays as it is


def truth(i):
    try:
        return SPACE['run'](i)
    except (ValueError, IndexError):
        return None   # outside the grammar: the auditor gives no value


REGEX = ('run(input) receives {"pattern": str, "text": str}. The pattern uses only lowercase letters, "." (any one '
         'character), the postfix operators "*", "+" and "?", alternation "|" and parentheses for grouping, with the '
         'usual meanings and precedence. Return true if the whole text matches the pattern, otherwise false.')
W4J_CHECKS = [check({'pattern': 'a', 'text': 'a'}, True), check({'pattern': 'a', 'text': 'b'}, False),
              check({'pattern': 'a*', 'text': ''}, True), check({'pattern': 'a+', 'text': ''}, False),
              check({'pattern': 'a?b', 'text': 'b'}, True), check({'pattern': 'a|b', 'text': 'c'}, False),
              check({'pattern': '.', 'text': 'z'}, True), check({'pattern': 'a.b', 'text': 'acb'}, True),
              check({'pattern': '(a|b)*c', 'text': 'aabc'}, True)]   # the nine checks W4-j settled


OLD_VARIED = {}
exec(compile('def old_varied_inputs(inputs, limit=16):\n    """pramana.varied_inputs as pinned in W4-j (8f8b97c), kept here for the control."""\n    def texts(t):\n        out = []\n        if t:\n            out.append(t[0].swapcase() + t[1:])\n            out += [t[:len(t) // 2] + c + t[len(t) // 2:] for c in VARIED_CHARS[:4]]\n            out += [t[::-1], t[:-1], t + t[-1]]\n        else:\n            out += [\'a\', \'A0\']\n        return [x for x in dict.fromkeys(out) if x != t]\n\n    def lists(xs):\n        out = []\n        if xs:\n            out += [list(reversed(xs)), xs[1:] + xs[:1], xs[:-1], xs + xs[:1]]\n            try:\n                out.append(sorted(xs))\n            except TypeError:\n                pass\n        else:\n            out.append([])\n        return [x for x in out if x != xs]\n\n    def variants(value, depth=0):\n        if depth > 3:\n            return []\n        if isinstance(value, str):\n            return texts(value)\n        if isinstance(value, list):\n            per_item = [lists(value)] + [[value[:i] + [v] + value[i + 1:] for v in variants(item, depth + 1)[:3]]\n                                         for i, item in enumerate(value[:4])]\n            return [row[n] for n in range(max(map(len, per_item), default=0)) for row in per_item if n < len(row)]\n        if isinstance(value, dict):   # the fields take turns, so a cap on probes still reaches every field\n            per_field = [[dict(value, **{k: v}) for v in variants(item, depth + 1)[:4]] for k, item in value.items()]\n            return [row[n] for n in range(max(map(len, per_field), default=0)) for row in per_field if n < len(row)]\n        return []\n\n    per_input = [variants(value) for value in inputs]\n    out = []\n    for round_ in range(max((len(v) for v in per_input), default=0)):\n        out.extend(v[round_] for v in per_input if round_ < len(v))\n    return out[:limit * max(1, len(inputs))]\n', 'old_varied', 'exec'), {'VARIED_CHARS': VARIED_CHARS}, OLD_VARIED)


def regex_run():
    writes = []

    def act(request):
        writes.append(request)
        return store(STRICT if len(writes) == 1 else MATCHER)
    plan = lambda c: {'contract': 'run(i) returns whether i["text"] fully matches the pattern i["pattern"]',
                      'input': None, 'checks': W4J_CHECKS, 'reference': REFERENCE,
                      'small_inputs': [{'pattern': 'ab', 'text': 'ab'}, {'pattern': 'a|b', 'text': 'b'}]}
    solver, _ = make_solver(Model(plan, act, audit=lambda c: {'expected': [truth(i) for i in c['inputs']]}),
                            max_calls=30)
    return solver.solve(REGEX), writes


class StackedPostfix(unittest.TestCase):
    def test_a_lower_case_pattern_is_lengthened_by_its_own_last_character(self):
        made = pramana.varied_inputs([c['input'] for c in W4J_CHECKS])
        self.assertIn({'pattern': 'a**', 'text': ''}, made)
        self.assertIn({'pattern': 'a++', 'text': ''}, made)

    def test_the_method_that_raises_on_a_second_operator_is_met_and_rewritten(self):
        result, writes = regex_run()
        self.assertEqual(result['status'], 'checked')
        self.assertEqual(result['source'], MATCHER)
        self.assertEqual(len(writes), 2, 'the one-operator method was met beyond the checks and rewritten')
        recorded = [e for e in events(result, 'solve_differential') if e.get('outcome') == 'disagree']
        self.assertTrue(any({'pattern': 'a**', 'text': ''} in e['disputed_inputs'] for e in recorded))
        self.assertTrue(all('auditor_values' in e for e in recorded))   # the auditor's answers are in the journal

    def test_control_with_the_w4j_probes_the_method_is_delivered(self):
        with mock.patch('kuttaka_runtime.pramana.varied_inputs', OLD_VARIED['old_varied_inputs']), \
                mock.patch('kuttaka_runtime.solve.VARIED_BATCHES', 1):
            result, writes = regex_run()
        self.assertEqual((result['status'], result['source']), ('checked', STRICT))
        self.assertEqual(len(writes), 1)


TRI = lambda n: n * (n + 1) // 2
RIGHT = 'def run(n):\n    return n * (n + 1) // 2\n'
WRONG = 'def run(n):\n    return n * (n + 1) // 2 if n < 4 or n == 5 else n * n\n'   # wrong at 4 and from 6 on
PICKY = 'def verify(n, y):\n    return y == n * (n + 1) // 2 and n != 5\n'          # wrong at 5 only


def property_run(source_order):
    order = list(source_order)
    plan = lambda c: {'contract': 'run(n) returns the n-th triangular number', 'input': None,
                      'checks': [check(1, 1), check(2, 3), check(3, 6)], 'verify': PICKY}   # probes 4, 5, 7
    solver, ws = make_solver(Model(plan, lambda r: store(order.pop(0) if len(order) > 1 else order[0]),
                                   audit=lambda c: {'expected': [TRI(n) for n in c['inputs']]}), max_calls=30)
    return solver.solve('The n-th triangular number.'), ws


class PropertyInputByInput(unittest.TestCase):
    def test_mixed_disagreements_are_retained_without_certifying_an_unreliable_property(self):
        result, ws = property_run([WRONG, RIGHT])
        self.assertEqual(result['status'], 'incomplete')
        tested = events(result, 'solve_property_test')
        overruled = [e for e in tested if e.get('outcome') == 'property_overruled']
        self.assertEqual(overruled[0]['overruled_on'], [5])           # wrong there: the auditor agrees with the method
        self.assertEqual(overruled[0]['auditor_values'], [10, 15, 28])
        # Review 15 A1: after discarding the property, its other verdicts cannot become independent
        # confirmations. The disagreements still block delivery, and the candidate stays unverified.
        self.assertEqual([h['input'] for h in result['disputed_property']['hypotheses']], [4, 7])
        self.assertTrue(result['unverified']['artifact'])
        self.assertFalse(ws.refuted())
        self.assertFalse(ws.confirmed('The n-th triangular number.'))
        self.assertFalse(ws._read(ws.solved_path))

    def test_control_a_method_right_everywhere_still_overrules_the_property(self):
        result, ws = property_run([RIGHT])
        self.assertEqual((result['status'], result['source']), ('checked', RIGHT))
        overruled = [e for e in events(result, 'solve_property_test') if e.get('outcome') == 'property_overruled']
        self.assertEqual(overruled[0]['overruled_on'], [5])
        self.assertFalse(ws.refuted())


if __name__ == '__main__':
    unittest.main()
