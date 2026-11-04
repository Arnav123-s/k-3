"""Fixed ways to assemble solved parts into a method, with no model call.

In the kuttaka, the answer is rebuilt from the chain of quotients by arithmetic alone: walking
back up the valli needs no new insight. Brahmagupta's bhavana likewise combines two solutions
into a new one by a fixed rule, in an additive and a subtractive form
(research/kuttaka_method/STUDY.md). Here the parts are checked methods, stored by artifact id,
and the fixed rules are the simplest compositions of run(input) functions: each part alone,
the parts chained in the order given or in reverse, and the parts side by side on the same
input (their results as a list). Each candidate is only a proposal;
it counts as solved only when it passes the parent task's checks in the sandbox.
"""

PIPELINE = '''def run(value):
    for part in %r:
        value = use(part)(value)
    return value
'''

SINGLE = '''def run(value):
    return use(%r)(value)
'''

PAIR = '''def run(value):
    return [use(part)(value) for part in %r]
'''


def compositions(parts):
    """(label, source, uses) for each fixed composition of the artifact ids in `parts`.

    Order: the chain in the given order (the splitter is asked to list subtasks in build order),
    then each part alone, last first (the last subtask is often the whole task over the others),
    then the parts side by side, then the chain reversed. Duplicates are dropped.
    """
    parts = list(dict.fromkeys(parts))
    out = []
    if len(parts) > 1:
        out.append(('chain', PIPELINE % (parts,), parts))
    out.extend(('single', SINGLE % (part,), [part]) for part in reversed(parts))
    if len(parts) > 1:
        out.append(('side by side', PAIR % (parts,), parts))
        out.append(('reverse chain', PIPELINE % (parts[::-1],), parts))
    return out
