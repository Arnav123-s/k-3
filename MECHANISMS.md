# Kuttaka 3 — How every piece works

This document explains every mechanism in the submission: what it does technically,
which idea it embodies, where it originates, why it exists, and how it is used.
Worked examples run through the whole document, using real tasks from the repo's
own test suite. Nothing here is marketing — it is the code, described.

**How to read it.** Part I follows one task through the system in order —
that's the spine; read it straight through. Parts II–IV go deeper into memory,
machinery, and the judge-facing interface — dip in as needed. Part V is
reference: the extras, the peripheral modules, the tests, and the glossary of
names. Every section stands alone, but the worked examples (gcd, then lcm)
carry across sections.

The submission ships three engines. Mechanisms are tagged with which engines
contain them: **E** = engine/ (default, pin 89deb23), **S** = engine-speed/
(pin cff12a9), **B** = engine-best/ (pin 8ce0951).

The running example: the task `Write run([a,b]) returning gcd(a,b)`, with checks
`[6,9] → 3` and `[7,5] → 1`. A second task, `Write run([a,b]) returning lcm(a,b)`,
reuses the first task's method. Both are verbatim from the repo's campaign tests.

---

**Part I — The solve, in order.** One task enters; a checked answer or a
structured refusal comes out. §§1–9 follow it through.

---

## 1. The solve loop

**What it is (E, S, B).** Every task flows through `Solver.solve(prompt,
examples)` → `_solve` → `_solve_given` → `_solve_planned`, in order:

1. `plan()` builds the check contract (§2); `_settle_given` may declare the
   task void (śūnya) if unanswerable.
2. `adopt()` — sthira: try stored methods by id before any model call (§10).
3. `prastara()` — enumerate small compositions of stored methods (§5).
4. `attempt()` — the model writes code; then the climb loop: each failed
   check triggers repair with notes, and checked candidates pass gate rounds (§7).
   The climb's next move comes from a strategy ladder, "placed as in the
   kuṭṭaka" — audit, split, repair, verify, mati, replan, in an order the
   evidence dictates: "A failed check that rests on the model's hand value
   alone is questioned before code is rewritten to fit it (pramāṇa first).
   The guess (mati) belongs at the bottom, where little is unknown." Each
   strategy at most once per stall; the wheel turns until something gives.
5. Delivery requires `status == 'checked'`; otherwise the result is
   `incomplete`, never a success claim. With `--rounds N`, incomplete rounds
   restart on the same workspace — the next round inherits the ledger, the
   lessons, and the refuted methods.

**Worked example.** The lcm task — `Write run([a,b]) returning lcm(a,b)` —
enters the loop. At stage 2, `adopt()` finds the gcd method stored from the
earlier task and tries it. At stage 4, the model writes
`run(ab) = ab[0]*ab[1] // gcd(ab)` using the stored method by id. The checks
`[4,6] → 12`, `[3,5] → 15` run in the sandbox. It passes; status `checked`.

**The idea.** The climb from failure to success, inside one attempt: try, fail,
perceive the failure, learn, retry. A failure never perceived ends the climb
before it starts.

**Why.** A model left alone delivers wrong answers confidently (four wrong in
the W4-o baseline). The loop exists so that no answer leaves without surviving
its checks.

**The point.** First-try accuracy is not the goal. The checked answer coming
out of the climb is.

---

## 2. The planner

**What it is (E, S, B).** Before any code is written, the planner — a model
role driven by the `PLAN_INSTRUCTIONS` prompt — turns the task into a check
contract. Stages:

1. **Context assembly.** The planner sees the task, the user examples, the
   workspace's lessons, and `available_methods` — stored methods that might
   apply (see §11). Previous contracts come with a warning: "It is not a truth
   certificate. Return a valid plan."
2. **Ask (up to 2 attempts).** The reply must be exactly one JSON object:
   `contract` (1–2 sentences on what `run(input)` receives/returns), `checks`
   (3–6 small hand-worked cases, at least 2 different expected results, one
   edge case), `input`, `verify` (a property, when checking is easier than
   computing), `reference` (a slow-but-obviously-correct implementation),
   `small_inputs`, `large_inputs`, `relations`, `decomposition`.
3. **Validation — the plan is not trusted.** Rejections include: non-JSON
   reply; blank contract; malformed checks; **degenerate checks** — all checks
   with the same expected value ("A constant passes every such check: they pin
   nothing"); **answer-only checks** — all inputs null with no examples
   ("run(None) has one value, so its only check is the planner's guess at the
   answer"). A rejected plan is evented and the reason fed back for the retry.
4. **Settlement — pramāṇa before any code.** The planner's slow reference runs
   on every check input in the sandbox; agreement becomes the check's support.
   A model-written check contradicted by reference is settled two-against-one:
   if reference and property agree against the check, the check is revised; if
   a blind auditor must decide, one model call re-derives the value. A user's
   check is never revised — a contradicting reference is dropped instead.

**Worked example.** For the gcd task, the planner returns something like:

```json
{
  "contract": "run([a,b]) receives a pair of positive integers and returns their greatest common divisor.",
  "checks": [
    {"input": [6, 9], "expected": 3},
    {"input": [7, 5], "expected": 1},
    {"input": [12, 18], "expected": 6}
  ],
  "reference": "def reference(ab):\n    a, b = ab\n    while b:\n        a, b = b, a % b\n    return a",
  "verify": "def verify(ab, out):\n    a, b = ab\n    return a % out == 0 and b % out == 0"
}
```

Three checks, two different expected values, one edge case (`[7,5] → 1`, the
coprime case). The reference is the obvious Euclid loop. Before any method is
written, the reference runs on all three checks in the sandbox — if the planner
hand-computed `gcd(12,18)` wrong, the reference overrules it.

**The idea.** The task's conditions must exist before the model writes code.
Pramāṇa: testimony counts only from a trustworthy speaker, and even the
planner's testimony is cross-examined.

**Origin.** The śāstra's pramāṇa doctrine — the ranking of evidence — rebuilt
as a pipeline stage.

**Why.** If the checks are wrong, everything downstream is theater. The planner
is a model; models miscompute. So the plan is validated, then settled against
an independent reference, before a single line of solution code exists.

---

## 3. The checks

Each check carries one support label, strongest first: `GIVEN` (the user's
example) > `TWO_DERIVATIONS` > `REFERENCE` > `PROPERTY` > `HAND` (the model's
own worked value). A method is only as strong as its weakest check.

**pramana.py — evidence ranking (E, S, B).** Pure helpers. `summarize(labels)`
reports the weakest label among a method's checks. Also `near_misses()`,
`varied_inputs()`, `near_inputs()` — model-free input generators that probe
generality — and `shape()`/`form()`, JSON-type signatures used to match stored
methods to new tasks.

**check_contracts.py — bounded contracts (E, S, B).** Finite by construction:
`MAX_CHECKS = 64`, `MAX_MODEL_CHECKS = 12`, with caps on value size, depth,
and nodes. Revisions recorded as heuristics with evidence, never silently.

**check_memorization.py — the hardcoding detector (E, S, B).** AST-based:
finds literals equal to a check's expected value selected under equality
guards or in tables keyed by check inputs. A flagged method is refuted: it
"returns checks literally instead of computing them."

**Worked example.** Suppose the model "solves" gcd with:

```python
def run(ab):
    if ab == [6, 9]: return 3
    if ab == [7, 5]: return 1
    return 1
```

It passes both checks. The memorization check reads the AST, finds literals
`3` and `1` returned under equality guards on the check inputs, and refutes
the method. The climb continues — the lookup table is not a solution.

**check_substitution.py — the runtime counterpart (E, S, B).** Instead of
reading source, it *changes* each constant to an unused value, reruns the
method in the sandbox on check inputs plus `near_inputs`, and flags constants
whose change alters the answer on exactly one check's input. Its finding is "a
question about the method, not a verdict" — a blind auditor is then asked
about the check's input.

**The idea.** A guess is allowed — only if it is checked. Brahmagupta's mati
rule, fifteen centuries old: the bounded guess must be validated.

**Why.** Models memorize check answers and hardcode them. Without these
checks, a "passing" method can be a lookup table wearing a costume.

**The point.** Evidence ranked by support, not by assertion. The weakest link
sets the strength.

---

## 4. The three solver forms

**Recipe / prastāra (E, S, B).** Complete enumeration over a small stated
space: every well-formed recipe up to `MAX_SIZE = 5` nodes composed of stored
methods, typed by input/output forms, listed by size then method order.
"Nothing here calls a model or runs code; the Solver screens each recipe on
every check input."

**Vallī** (`solve_valli.py`; E, S, B). The model writes only a reduction step — `measure`,
`small`, `reduce`, `combine` (+ optional `base`) — and Kuttaka assembles the
recursion (`CALL_LIMIT = 200000`). Runtime-verified invariants: every reduced
input has strictly smaller measure (well-founded), computed answers memoized
in `_chain` (shared subproblems computed once), each answer handed out as a
copy. `step_check()` tests the reduction against the planner's reference on
small inputs.

**Worked example.** The vallī for gcd — the model writes only this:

```python
measure = lambda ab: ab[0] + ab[1]
small   = lambda ab: ab[1] == 0
reduce  = lambda ab: [ab[1], ab[0] % ab[1]]
combine = lambda ab, sub: sub
base    = lambda ab: ab[0]
```

Kuttaka checks the invariant on the trace before accepting the form:
`[6,9] → reduce → [9,6]`, measures 15 → 15 — not a strict decrease. Rejected.
The model reorders the pair first; now `[9,6] → [6,3] → [3,0]`, measures
15 → 9 → 3, strictly decreasing — accepted, and `step_check()` verifies each
step against the planner's reference on small inputs. The recursion's
termination is *verified*, not hoped for. (Illustrative — the point is the
invariant check, which really runs.)

**Krama (E, S only).** The model writes one step — `start`, `moves`, `done`,
`answer` (+ optional `exhausted`) — and Kuttaka runs breadth-first search
(`_walk`), remembering every visited state (`came_from` map,
`STATE_LIMIT = 200000`), so the first done state uses the fewest moves.
`path()` reconstructs the taken path for the model when a check fails: "the
memory of the steps is outside the thinker."

**The idea.** Three ways of orchestrating thought: compose what exists
(recipe), reduce to a smaller self (vallī), walk step by step with the memory
of the walk outside the thinker (krama).

**Origin.** The vallī is Āryabhaṭa's quotient chain, rebuilt as a
computational form. Krama is the walk remembered, not just the answer.

**Why.** Different problems yield to different shapes. One form alone is a
single kind of perception; three forms are three.

---

## 5. The composition compiler

**What it is (E, S, B).** `solve_prastara.py` enumerates recipes; `composition.py`
compiles them. The enumeration (after Piṅgala's prastāra):

- Methods are typed by input/output *forms* (`pramana.shape` — JSON-type
  signatures from the checks each method passed). A method may be applied only
  if the value has the form of the inputs it was checked on — Brahmagupta's
  bhāvanā as the typing law.
- Terms build level by level up to 5 nodes: `get` (field extraction), `call`
  (apply method), `map` (apply over a list, only if elements match the
  method's input form), `array` (two terms side by side), `object` (fields).
- Only recipes whose output form matches the task's output form, and that
  actually use a stored method, are offered. Cut at `TERM_LIMIT = 4000` terms.

The compiler: "Recipes are data, never Python source. The compiler emits a
small fixed Python wrapper that invokes stored methods by artifact ID."
Recipe JSON is exactly `{'version': 1, 'output': <expression>}` with ops
`input`, `literal`, `call`, `map`, `array`, `object`, `get` — bounded (64KB,
128 nodes, depth 16, ≤16 methods, 64-hex artifact IDs). `compile_recipe`
validates, resolves each artifact id to a stored method payload (checked as
data, never executed here), and emits a fixed `def run(value):` wrapper. The
compiled method's identity is the SHA-256 of source+manifest+uses.

**Crucially: compilation is not verification.** "The resulting method still
needs the normal, current-task ProgramTools qualification before use" — the
Solver screens the compiled method on every check input of the current task
like any candidate.

**Worked example.** The lcm task again. The stored gcd method is bound to a
symbol, say `gcd0`, resolved to its artifact id at compile time. The recipe
the enumeration finds (simplified; real nodes carry the exact `{'op': ...}`
shape the compiler validates):

```json
{"version": 1, "output": {"op": "call", "method": "div0", "arg":
  {"op": "array", "items": [
    {"op": "call", "method": "mul0", "arg": {"op": "input"}},
    {"op": "call", "method": "gcd0", "arg": {"op": "input"}}
  ]}}}
```

i.e. `lcm(a,b) = div([mul([a,b]), gcd([a,b])])` — composed from stored `mul`,
`div`, and the gcd method, no model call. The compiler emits the fixed
wrapper (`_invoke('…', …)` per call, `_get_index`/`_get_key` for field
access); the Solver runs it against `[4,6] → 12` and `[3,5] → 15` in the
sandbox. Only then is it a result.

**The idea.** Composition is enumeration, not inspiration. Every well-formed
small thing, in a fixed order, screened on the checks.

**Why.** The model is not needed where arithmetic suffices. And the typing law
— a method applies by what it takes, not by how its task was worded — is what
makes cross-task composition sound.

---

## 6. The engine block and the repair machinery

**runtime.py — the engine block (E, S, B).** The lower-level infrastructure
that `solve.py` is built on: `investigate(request, model, tools, checker)` —
the engineered model/tool/checker loop; `Budget` — call and execution
accounting; `TransportFailure`; and `ProgramTools` — generated-program
execution delegated to the bounded Docker executor ("this loop never uses
exec"). Roughly: runtime.py is the engine block, solve.py is the car. The
Solver imports `Budget, TransportFailure, investigate` and orchestrates
planning, contracts, sthira, the climb, gates, the void, and the ledger on
top.

**programs.py — the execution interface (E, S, B).** "Bounded method storage,
isolated execution, and task-scoped validation. Stored Python source is data"
— never executed in the host process. `ProgramTools` is the solver's handle
on execution; `ProgramTask` the unit of work. This is the narrow waist
everything runs through: the model proposes, the tools execute in the box,
the solver judges.

**solve_compose.py — bolting the pieces together (E, S, B).** "Fixed ways to
assemble solved parts into a method, with no model call." After a
decomposition is solved piece by piece, `compositions(parts)` yields
(label, source, uses) for a tiny fixed rule set: chain the parts in order,
each part alone (reversed), side by side, reverse chain. `Solver.assemble()`
takes the first rule whose result passes the parent's checks.

**Decomposition (`_decomposition`).** Before a single call is spent on
subtasks, the split itself is validated: at most four subtasks, each with a
distinct lowercase id, a request, and checks (each held to the same CheckBook
rules as a full task), plus a recipe whose symbols must reference *exactly*
the declared subtasks. An unvalidated split never runs — "validate an
explicit assembly before spending calls on its subtasks." This is different
from prastāra (§5): compose is "we split it, now bolt the pieces together
four obvious ways" — no search, no model call; prastāra is the
combinatorial search over the library.

**solve_progress.py — the evidence frontier (E, S, B).** `EvidenceFrontier`
tracks which checks the best attempt passes and which it fails — the frontier
between established and open. Repair (§1, stage 4) works from this frontier:
the next strategy is chosen from the evidence of exactly where the attempt
stands. `candidate_fingerprint`, `subproblem_key`, and
`boundary_restatement_key` give every candidate and subproblem a stable
identity, so the climb never loses its place.

**solve_sketches.py — repair by substitution (E, S, B).** `SketchCatalogue`:
"bounded AST-guided substitution for finite solve sketches — parses and edits
source text; it never runs it." During repair, instead of asking the model
for a whole new method, the solver can substitute parts of the abstract
syntax tree — bounded, surgical edits. Generated source still must pass the
sandbox path: a sketch is a proposal, not a result.

**campaign.py — attempt accounting (E, S, B).** "Task-stream campaigns: the
same frozen model with and without retained method memory" — the MODEL_ALONE
vs KUTTAKA arms. The solver uses `_closure` and `_account_attempts` for
attempt accounting: every attempt charged to its arm, so the comparison
numbers (8/12 vs 12/12, 40 vs 53 calls) are booked honestly.

**The idea.** The climb needs an engine, a frontier, and tools. Runtime is
the engine; the frontier says where the climb stands; sketches and compose
are two ways to move — surgical edit, or bolt the solved pieces together.

---

## 7. The gates

**What it is (E, S, B).** After the climb, checked candidates pass gate
rounds. Each gate can send the solve back for another attempt: memorization,
no-answer, witness, parīkṣā, raised, rival, property, relation, scale,
differential, against-both.

**The null probe (`_no_answer`).** "Whether the method returns null on the
requested input, run once as a probe." If the method answers null, the
no-answer gate fires — the task may be void (§8). A probe timeout says
nothing here: "the final run has its own limit." The probe asks only what it
can answer: did the method *say* nothing, or did it merely run long?

**The scale test (`scale_test`).** "Run the method once on each of the
planner's large inputs; the ones it does not finish in time." Only running
out counts: a timeout, a kill at the memory limit, or a traceback ending in
MemoryError or RecursionError. Any other exception "may be the method
rightly refusing an input the planner got wrong, so it is not a scale
failure." When no input could be built or run, "the test did not run, and
says so (it never reads as held)." No model call. And the reuse (§9): a
large input the probe already finished is marked `by: 'an earlier probe of
the same code'` — not rerun.

**Worked example.** The model proposes this gcd "solution":

```python
def run(ab):
    a, b = ab
    while b > 1:
        a, b = b, a % b
    return a
```

It passes `[6,9] → 3` (trace: [6,9] → [9,6] → [6,3] → [3,0], returns 3). But
the holdout `[7,5] → 1`: the loop `while b > 1` runs `[7,5] → [5,2] → [2,1]`,
exits with `b = 1`, and returns `a = 2`. Wrong — the property gate checks
`a % out == 0 and b % out == 0`: `7 % 2 = 1 ≠ 0`. Rejected, and the scale gate
on larger inputs confirms the failure. Back to the climb with notes. The
delivered method is the corrected loop (`while b:`).

**The idea.** Awareness of failure, built in. Each gate is a kind of perception
the system has gained: its own errors (memorization), the void (no-answer),
others' published work (witness), precedent (parīkṣā), independent agreement
(differential, against-both).

**Why.** One check can be fooled; a round of independent gates, each able to
reject, is much harder to fool. Feynman's standard: "The first principle is
that you must not fool yourself — and you are the easiest person to fool."

**The point.** No single gate is the truth. The round is.

---

## 8. The void (śūnya)

**What it is (E, S, B).** Śūnya operates at three places, and it is a vote —
not just a withhold.

**1. The last-step vote (`_answer_gate`).** "Śūnya at the last step: the answer
to the requested input is delivered only if nothing outvotes it." The delivery
rule leans safe — "a void costs one answer, a wrong delivery costs trust":

- The planner's reference, if it survived settlement, is run on the requested
  input in *every* round. If it disagrees, the answer is void. If no execution
  budget is left to run it, the answer is not delivered either.
- Every voice recorded about the input is counted: blind re-derivations,
  rival methods not shown wrong, the reference when it agrees. The method
  itself counts one voice. **If another value has as many voices as the
  answer, the answer is disputed and void.**

**2. The piece gate (`_piece_gate`).** For decomposed tasks, each piece's
answer faces its own voices on the input it is handed, by the same rule: if
another value has as many voices, the whole is disputed.

**3. Settlement voids (`_settle_given`).** If the checks cannot be settled —
fewer than the minimum would remain and no user check stands — the task is
void before any method is written. (This step never voids a task that has
settled checks; it only refuses to proceed on quicksand.)

**What a void keeps (`_void`).** "Śūnya: no verified answer, with what is
known kept in its place. Nothing is delivered, but the place is not empty."
A void returns: the reason, the checks as settled with their support, the
best attempt and how many checks it passed, and the methods shown wrong on
the way.

**How voices are recorded (`_voice`).** Voices are appended per task, per
*input value* — "on every input, not only the requested one, since a parent
hands a piece inputs of its own." Exactly three kinds exist:

- `'reference'` — the planner's reference derivation, but only on inputs the
  planner declared *small* ("K7's rule").
- `'auditor'` — a blind re-derivation by the auditor role, which sees
  neither output nor verdict.
- `'rival'` — a rival method's value, kept *before* the auditor is asked:
  "a disagreement the auditor cannot settle still disputes the answer."

The design rationale, verbatim: "Voices outlive a replan that rewords the
contract: a stale voice can only void an answer, while a dropped one can let
the same mistake through again."

**How voices are counted (`_heard`).** The dedup rule leans safe: "one that
agrees with the answer counts once however often it was heard (one
derivation, asked twice, is not two), one that dissents counts each time
(the gate leans to void)." At count time, three filters apply: a reference
voice from a superseded reference is dropped ("a reference set aside since
says nothing"); rival voices from refuted sources, or from the delivered
method itself, are dropped; null voices count only when the answer is null.
Then the tally: the method counts one; if any other value reaches as many
voices, the answer is disputed — void.

**What a void teaches.** Every void is learned:
`ws.learn(prompt, '{contract} -> void: {reason}')`. The CLI prints it plainly:
"Void, no verified answer: {reason}. The lesson is kept for next time." As the
code puts it: "a task left void teaches the next task of its kind."

**Worked example.** A task arrives: `Write run(n) returning the 10^100-th
prime.` The planner cannot produce a reference (no slow-but-obviously-correct
way at that scale), the checks cannot be settled — void at settlement, before
a single model call. The void record keeps the unsettled checks and the
reason; the lesson is stored. Next time a task of this kind arrives, the
planner sees the lesson: this shape of problem voids. Or the closer case: a
method passes every check, but at the last step a blind re-derivation voices
a different value on the requested input — two voices against two. Disputed.
Void. The best attempt is kept (it passed 5 of 6 checks), the rival value is
recorded, and nothing unverified leaves.

**The idea.** The void is the system knowing its limits — and it is
democratic: no single voice, not even the method's own, outranks the count.
A false stop counts as the failure to fix — a failure never perceived ends
the climb before it starts.

**Origin.** Śūnya — the śāstra's empty place, kept open rather than filled
with a guess. But here the empty place is *structured*: what is known stands
in it.

**The point.** In the recorded W4-o run: four wrong answers from the model
alone, zero from Kuttaka. Withholding is a feature, not a gap — and every
withholding teaches.

---

## 9. Value reuse within a solve (the W4-r speedup)

**What it is (E, S only — not in B).** In a solve, the checked method was
running four times on the same input: the null probe, the scale test, the
differential, and the final run. The change, quoted from the code:

```python
# A value that finished before under the same source is not computed again either (first-hand session p:
# the method ran four times on the requested input, 9 s each: the null probe, the scale test, the
# differential and the final run). A method is a function of its input; only identical jobs are reused.
```

`self._probe_values`, keyed `(source_digest, canonical(input))`, caches
finished probe values; `self._slow_probes` remembers timed-out inputs
(reported out-of-time at once, not rerun); `self._finished_runs` lets the
scale test skip inputs a probe already finished. The final run still runs —
the honest one is never skipped.

**Worked example.** On the gcd task, the null probe runs the candidate on
`[6,9]` (9 seconds in the sandbox). The scale test would run it again on the
same input — instead it reuses the finished value, tagged
`by: 'an earlier probe of the same code'`. The differential reuses it too.
Only the final run executes fresh. Four runs become effectively two.

**The idea.** Don't redo work already done. A speedup is not a better score —
it is the same quality for less work: 11/12 in 40 calls vs 53.

**Why.** Model calls are the currency. Four runs of the same input is paying
four times for one answer.

**The point.** Nearly the same quality, a quarter fewer calls. Faster without
losing much — which is what a speedup is for.

---

**Part II — Memory.** What the system keeps between tasks, and how it reuses
it: the method library, the similarity ranking, the ledger. §§10–12.

---

## 10. The method library and sthira reuse

Two layers, all three engines.

**artifacts.py (`ArtifactMemory`).** Content-addressed store: `put()` writes
immutable zlib-compressed blobs keyed by SHA-256 id, with `kind`
(`method`/`representation`/`investigator`/`question`), `dependencies` (must
already exist — composition pinned to exact content), `provenance`,
`applicability`. `catalog()` searches name/description substrings;
`qualify`/`invalidate` manage per-scope standing.

**store.py (`Journal`).** Single-writer, hash-chained, append-only event log
(`prev` hash links, `fcntl` lock). `Journal.read` validates the full hash
chain on open. As the code notes: "Hash integrity does not establish truth."

**Sthira reuse.** `Solver.adopt()`: "A stored method that already passes these
checks is used as it is, by id, with no model call" — checked methods of
similar earlier tasks are tried in the sandbox before the model writes
anything.

**shastra.py.** The read-only structured view: rule, applies-to forms,
examples with support, rationale, uses, lineage
(supersedes/superseded-by/shown-wrong), open places (void tasks). The default
engine additionally records bīja (conditions + derivation).

**Worked example.** After the gcd task, the library holds the gcd method with
its checks and support labels. The lcm task arrives. `adopt()` tries the gcd
method — it doesn't solve lcm, but the model then writes one line using it by
id. The lcm method is stored in turn. A third task, `gcd of three numbers`,
adopts the gcd method directly: `gcd(a,b,c) = gcd(gcd(a,b),c)` — zero model
calls for the core logic. This is the repo's own demo test: "t2 reuses the
method written on t1."

**The idea.** "Thinking begets thinking." What was checked once need not be
earned again — but reuse is retest, not trust: the stored method must pass
*these* checks, now.

**Origin.** The śāstra's commentary lineage: Bhāskara II correcting
Brahmagupta across centuries. The ledger keeping the genealogy.

**The point.** The workspace grows while the model stays fixed.

---

## 11. The similarity ranking

**What it is (E, S, B).** `Workspace.helpful_methods` decides which stored
methods to offer. The metric is Jaccard similarity over word sets:

```python
def _words(text):
    return set(re.findall(r'[a-z0-9]+', text.lower())) - STOPWORDS

def _similar(a, b):
    a, b = _words(a), _words(b)
    return len(a & b) / len(a | b) if a and b else 0.0
```

Procedure, quoted from the docstring: "Paribhāṣā: a method applies by what it
takes, not by how its task was worded. Methods for tasks worded alike come
first; then, when `form` is given, methods checked on inputs of the same form
(`pramana.form`), however their tasks were worded. A method later shown wrong
(differential testing, or returning checks literally) is not offered."

1. Rank all solved rows by `_similar(prompt, row['task'])`, keep ≥ 0.25, top
   12, each annotated with its similarity score. Refuted artifacts skipped.
2. If fewer than 4 found and the task's input form is known: rows checked on
   inputs of the same form, sorted by similarity, tagged `by='form'`.
3. Return the first 4.

**Worked example.** The lcm task prompt: "Write run([a,b]) returning lcm(a,b)."
The stored gcd task: "Write run([a,b]) returning gcd(a,b)." Word sets (minus
stopwords): {write, run, returning, lcm} vs {write, run, returning, gcd} —
intersection 3, union 5 → similarity 0.6 (about 0.7 if single letters survive
the stoplist; the exact stoplist is an implementation detail). Either way,
well above the 0.25 bar: offered first, with its score attached. A method is
never offered on vibes — the score is on the record.

**The idea.** Applicability by what a method takes, not by how its task was
worded. Wording gets you candidates; the form check (§5) and the sandbox get
you the truth.

**Why.** Retrieval must be cheap and honest. A scored, auditable ranking beats
a black box — and refuted methods are never offered again.

---

## 12. Ledger and receipts

**store.py (`Journal`) (E, S, B).** Every solve appends hash-chained events:
`solve_started`, `solve_check_contract`, `solve_adoption`,
`solve_scale_test`, `solve_repair_two_agras`, `solve_finished`, …

**receipts.py (E, S, B).** Strict validator splitting executor receipts into
core (`status`, `exit_code`, `output`) and detached metadata (timings,
container name, dependency calls — the `use(id)` trace), rejecting unknown
keys.

**The idea.** The genealogy of everything: every attempt, every check, every
artifact, by identity. "Thinking begets thinking" — but only if the thinking
is written down.

**Why.** Without the ledger, a result is a claim. With it, the result is
re-executable: the W4-o numbers were independently re-run from the raw rows.

**The point.** Scope follows proof, and proof lives in the ledger.

---

**Part III — The machinery underneath.** What the solve loop stands on: the
sandbox everything runs in. §13.

---

## 13. Sandboxed execution

**What it is (E, S, B — functionally identical).** `execution.py`:
`PythonExecutor(timeout=20, output_limit=262144, memory='256m')` runs candidate
`run(input)` source in a fresh Docker container: `--network=none`,
`--read-only`, `--cap-drop=ALL`, `--security-opt=no-new-privileges`,
`--pids-limit=32`, `--memory=256m`, `--cpus=1`, `--user=65534:65534`,
`--tmpfs=/tmp:rw,noexec,nosuid,size=16m`, all proxy env vars emptied,
`python -I`. Memory choices 256m–4g; timeout ceiling 300s. The job is sent on
Docker stdin, not the command line.

**Worked example.** The repo's smoke test, run in all three engines:

```python
def run(x): return sorted(set(x))
# input [8, 3, 8, -1] → output [-1, 3, 8]
```

The source goes into a fresh container with no network, read-only filesystem,
256MB of memory, one CPU, 20 seconds. The output comes back `[-1, 3, 8]`.
Every check in this document was executed this way — in a box like this one.

**The idea.** Trust nothing the model writes until it runs. The sandbox is
where claims meet reality.

**The point.** Every check that matters was executed, not asserted.

---

**Part IV — What judges touch.** The interface and the three engines to
choose from. §14, then the comparison table in Part V.

---

## 14. The server and the CLI

**What it is (E, S, B — the packaged surface judges touch).** `serve.py`,
`__main__.py`:

HTTP: `python -m kuttaka_runtime.serve http --port 8765`
- `POST /solve` takes `{"task": str, "examples": [{"input","expected"}], "max_calls": int}`
  and returns the result JSON: status (`checked`/`incomplete`), method source,
  checks, cost, per-check evidence, and `void` when nothing is delivered.
- `GET /stats` → workspace history. `GET /library` → the full method library.
- One task at a time per workspace; a request may lower `max_calls`, never
  raise it. Docker preflight required.

MCP (stdio JSON-RPC): tools `solve`, `stats`, `library` — the same three.
A `solve` result is flagged `isError` unless `status == 'checked'`.

CLI: `python -m kuttaka_runtime '<task in plain words>'`
- `--example '{"input":...,"expected":...}'` (repeatable, trusted checks);
  `--workspace`; `--max-calls` (default 80); `--max-executions` (default 512,
  sandbox budget); `--rounds`; `--json`; `--witness oeis` (off by default);
  `--conditions` (off by default). Exit 0 only if `status == 'checked'`.
- The literals `stats` and `library` print workspace history and the method
  library.
- `settings(**overrides)` builds the same configuration programmatically, for
  callers embedding the solver rather than shelling out.

**Worked example.** The judge quickstart's live task:

```
python -m kuttaka_runtime 'Write run(n) to return the sum of the integers from 1 through n.' \
  --example '{"input": 4, "expected": 10}'
```

Exit 0 means the method passed every gate. Anything else means it didn't —
and the JSON says exactly where it stopped.

**The idea.** The system meets the world through a narrow, honest interface:
a task in, a checked answer or a refusal out.

**Why.** Judges shouldn't need to read 5,555 lines to verify the claim. One
command, one verdict.

---

**Part V — Reference.** Everything else, for the judge who wants to go
deeper: the default engine's extras, the supporting and peripheral modules,
the tests and experiments, the glossary of names, and the engine comparison.

---

## 15. Default-engine additions (E only)

In the default engine (pin 89deb23), not in speed or best:

- **The other agra.** During repair, finds "an attempt right on checks the
  best one fails" from the evidence frontier and shows both side by side:
  "Each is right where the other is wrong: write one method that keeps what
  each gets right." Thinking orchestrated with another attempt.
- **Parīkṣā.** Adds checks where prior knowledge and a blind re-derivation
  agree against the method. Thinking orchestrated with precedent — knowing
  isn't the same as learning.
- **Witness (`witness.py`, `_testify`).** Checks the method against published sequences;
  asks again on disagreement. Thinking orchestrated with the world — "if you
  can't verify your own work then you look for others' works that can verify
  your work."
- **Krama solver method, roles, `_recalled_methods`, bīja analysis**
  (`_condition_test`/`_derivation`): a `krama()` convenience entry on the
  Solver (distinct from the `solve_krama.py` walk form in §4, which speed also
  has), plus role-assignment enumeration over stored methods (`roles()`,
  capped at 48 assignments) and the recorded conditions under which a method
  holds.

**The idea.** The mechanisms are the system gaining new kinds of perception.
Each addition is one more way for the system to see.

**Why.** The project is complete; these are improvements — demonstrated, not
claimed.

---

## 16. Supporting modules (E, S, B)

- **beliefs.py** — finite Bayesian hypothesis tracking over caller-supplied
  likelihood tables. Evaluates declarative probability tables only; never
  executes code.
- **inquiry.py** — engineered finite evidence-driven inquiry policy over
  supplied candidates. Generation, measurement, and noise stay external.
- **investigators.py** — bounded executable investigators. Untrusted
  investigator source; each policy decision runs in a fresh isolated
  executor; actions validated before observations are committed.
- **models.py** — the model client: the frozen thinker the system
  orchestrates. Its weights never change; everything else does.
- **demo.py** — loads stored methods by id for the piece gate and powers the
  scripted campaign/demo end-to-end tests (the gcd/lcm pair in this document
  comes from there).

---

## 17. Peripheral modules

Nineteen modules ship in the package but are not on the live solve path.
They are research and experiment tooling, planning/memory abstractions,
evaluation helpers, and demo rendering — surveyed in `engine/`; honest
one-line accounts:

- **active_navigation.py** — L*-style learning of finite Moore-machine
  abstractions from action/observation histories (experiment tooling).
- **model_experiments.py** — bounded conformance words for learned Moore
  models (pairs with active_navigation).
- **adaptive_planning_memory.py** — goal-scoped persistent views over
  caller-supplied Boolean models; does not infer models or goals.
- **factored_memory.py** — syntactic dependency projection ("conservative
  causal cone") of a supplied transition system.
- **planning_memory.py** — exact abstraction for finite deterministic models
  via partition refinement.
- **query_memory.py** — finite task-relative query sufficiency; declarative
  JSON tables only.
- **context_memory.py** — bounded model-facing views over host-owned
  history; "never summarizes, rewrites, or removes history."
- **sequence_memory.py** — exact finite integer memory with a bounded codec
  family; "not a recovered historical algorithm."
- **session.py** — one persistent host toolbox for goals, discovery, and
  checked reuse.
- **goals.py** — provisional goal interpretations; "the ledger does not
  infer intent."
- **navigation_tasks.py** — goal-bound use of learned maps through exact
  artifact IDs.
- **discovery.py** — evidence-linked executable conjectures for an
  investigator's toolbox.
- **distribution_probes.py** — reproducible bounded probes with an explicit
  IID risk bound.
- **representation_search.py** — forward-only investigation over a declared
  polynomial/shift language; "does not invent its grammar."
- **rational_ratio.py** — bounded exact fits of rational term ratios;
  "neither a fit nor a forecast proves an infinite law."
- **recurrence.py** — bounded exact fits of constant-coefficient
  recurrences; "does not establish an infinite law."
- **shift_relation.py** — bounded exact polynomial shift relations from
  finite samples; exact finite fit only.
- **c3.py** — stream lint and pre-registered analysis for the evaluator's
  task streams.
- **demo_page.py** — self-contained HTML trace page rendered from journals
  and sandbox re-execution.
- **response_feedback.py** — content-safe feedback for invalid model action
  sentinels (3.4KB, trivial).

The pattern across them is the same discipline as the live path: finite,
bounded, exact — and explicit about what each tool does *not* prove.

---

## 18. Tests and experiments

**tests/ (E).** The suite behind the "1,248 tests pass with Docker" claim.
By filename, it covers: the gates (`test_against_both.py`,
`test_blind_derivation.py`), the substitution check's corpora
(`substitution_corpus*.py` — three files of test data for the constant-swap
auditor), check contracts (`test_check_contracts.py`), closed limits,
conditions, decomposition progress, the Erdős runner
(`test_erdos_runner.py`), full-pass qualification, handoff tools, host
protocol, indefinite search, input limits, invalid-JSON limits, and known
tasks (`test_known.py`). The substitution corpora and the blind-derivation
tests are the executable form of §3's auditors — the tests don't just assert
outcomes, they encode the adversarial cases (hardcoded answers, swapped
constants) the checks were built to catch.

**experiments/ (E).** Four harnesses: `erdos979/` — the Erdős #979 prompt
experiments whose failures taught the planner its rejection rules (the
degenerate-checks and answer-only-checks rejections in §2 are credited to
ERDŐS-979-c and ERDŐS-979-d: "learned from" sessions, recorded in the code);
`solve_suite/` — the hard-suite runner behind the W4 numbers;
`kuttaka_c1/` and `kuttaka_c3/` — campaign harnesses for the C1/C3
comparisons. These are the instruments the recorded numbers were played on —
the reason the evidence section can point at runs instead of claims.

---

*The philosophy is the inspiration that brings the result to life, not a proof
of it. What is proved is the bounded claim: orchestrated, coarse-grained,
verified thinking beats thinking alone.*
## The names: what everything is called, and why

Every mechanism in this system carries a Sanskrit name. None of them is
decoration. Each names the exact shape of the job it does — and each comes
from the field where that shape was first worked out. This glossary gives, for
each name: what it means, what it does here, where it comes from, an example
from its own field, and why it was chosen.

**kuṭṭaka** (कुट्टक) — "pulverizer"
- *Does here:* the name of the whole project and its method: grind the hard
  problem down into checkable steps, then climb back up.
- *From:* Āryabhaṭa, Āryabhaṭīya, Gaṇitapāda 32–33 — the algorithm for solving
  ax + by = c in integers by repeated division, each step smaller than the
  last.
- *In its own field:* calendrical astronomy — finding the day-count
  (ahargaṇa) on which two planetary cycles align, by pulverizing the
  remainders until the numbers yield.
- *Why chosen:* the system's shape *is* the algorithm's shape: reduce,
  check each reduction, ascend. The name is the method.

**vallī** (वल्ली) — "creeper, chain"
- *Does here:* the solver form where the model writes only a reduction step
  and Kuttaka assembles the recursion (§4).
- *From:* the kuṭṭaka itself — the vallī is the chain of quotients produced
  by the repeated division, the "creeper" linking each step to the next.
- *In its own field:* solving 137x + 60y = 10 — the quotients 2, 3, 1, 1, 8
  form the vallī; working back up it gives x = 50, y = −114
  (137·50 − 60·114 = 6850 − 6840 = 10).
- *Why chosen:* the form is literally that chain: each reduced input hangs
  from the last, and the answers climb back up it.

**krama** (क्रम) — "step, sequence, order"
- *Does here:* the solver form where the model writes one step and Kuttaka
  walks breadth-first, remembering every visited state (§4).
- *From:* the general Sanskrit sense of ordered procedure — a krama is a
  going-step-by-step, as in the Vedic krama-pāṭha (stepwise recitation).
- *In its own field:* krama-pāṭha recites word-pairs in overlapping steps
  (1-2, 2-3, 3-4…) so no word is ever lost — the sequence itself is the
  memory.
- *Why chosen:* the walk's memory lives outside the walker. The krama is the
  steps remembered, not just the destination — "the memory of the steps is
  outside the thinker."

**sthira** (स्थिर) — "stable, firm, that which stays"
- *Does here:* cross-task reuse — "Sthira: what is solved stays solved. A
  stored method that passes these checks is used by id" (§10).
- *From:* the ordinary philosophical sense — sthira is what does not move,
  what endures; opposed to what is provisional.
- *In its own field:* a siddhānta (established conclusion) is sthira — once
  proved by upapatti, it stands and later work builds on it without re-proof.
- *Why chosen:* a checked method is a small siddhānta. It stays. Later tasks
  stand on it — but retest it, because sthira is earned, not assumed.

**śūnya** (शून्य) — "void, empty"
- *Does here:* the structured refusal — no verified answer, with what is
  known kept in its place, and the lesson learned (§8).
- *From:* Brahmagupta, Brāhmasphuṭasiddhānta — the first systematic rules
  for zero as a number: a debt subtracted from zero, zero divided, and the
  famous hard case he wrestled with.
- *In its own field:* Brahmagupta's arithmetic of nothing — 0 + 0 = 0, and
  his honest struggle with 0/0. The void is treated as a *thing*, with rules,
  not as an absence to skip over.
- *Why chosen:* the system's void is likewise a thing with rules — the vote,
  the kept record, the lesson — not a gap where an answer should be.

**pramāṇa** (प्रमाण) — "measure, means of valid knowledge"
- *Does here:* the evidence ranking — every check carries a support label,
  strongest first, and a method is only as strong as its weakest check (§3).
- *From:* Nyāya philosophy — the pramāṇas are the valid means of knowing:
  pratyakṣa (perception), anumāna (inference), śabda (testimony), upamāna
  (comparison). Knowledge counts only through a valid means.
- *In its own field:* a Nyāya debate — a claim supported only by śabda
  (someone said so) loses to one supported by pratyakṣa (seen directly).
  The *means* decides, not the loudness.
- *Why chosen:* the support labels *are* pramāṇas — GIVEN outranks HAND the
  way perception outranks hearsay. `summarize(labels)` reports the weakest
  means, because a chain of evidence is only as valid as its weakest link.

**prastāra** (प्रस्तार) — "spreading out, enumeration"
- *Does here:* the complete enumeration of small compositions of stored
  methods, in a fixed order (§5).
- *From:* Piṅgala's Chandaḥśāstra — the prastāra enumerates *every* possible
  meter: for n syllables, all 2ⁿ patterns of long and short, listed in order.
- *In its own field:* the 3-syllable prastāra: SSS, ISS, SIS, IIS, SSI, ISI,
  SII, III — eight meters, none missing, none repeated. The world's earliest
  binary enumeration.
- *Why chosen:* the recipe enumeration does exactly this — every well-formed
  small thing, in a fixed order, nothing skipped, nothing repeated. "Nothing
  here calls a model or runs code" — like Piṅgala's table, it is complete
  before it is used.

**bhāvanā** (भावना) — "bringing into being, composition"
- *Does here:* the typing law of composition — a method may be applied only
  if the value has the form of the inputs it was checked on (§5).
- *From:* Brahmagupta's bhāvanā — the composition law for x² − Ny² = k: from
  two solutions, *compose* a third. The law that makes new solutions out of
  old ones, legitimately.
- *In its own field:* from (x₁,y₁) and (x₂,y₂) solving x² − 2y² = 1, bhāvanā
  gives (x₁x₂ + 2y₁y₂, x₁y₂ + x₂y₁) — e.g. (3,2) composed with itself gives
  (17,12), and 17² − 2·12² = 1. Composition *preserves* solutionhood.
- *Why chosen:* recipes compose methods the way bhāvanā composes solutions —
  and the typing law is what makes the composition *legitimate*: applied only
  where the form fits, the result inherits the checked-ness.

**parīkṣā** (परीक्षा) — "examination, test"
- *Does here:* the gate that adds checks where prior knowledge and a blind
  re-derivation agree against the method (§15) — "knowing isn't the same as
  learning."
- *From:* the ordinary śāstric sense — a parīkṣā is an examination, a
  testing; a student is parīkṣita, examined, not merely taught.
- *In its own field:* the examination hall — the student who can recite the
  rule but not apply it fails the parīkṣā. Knowing the statement isn't the
  same as having learned it.
- *Why chosen:* the method *claims* to know; parīkṣā examines the claim
  against precedent re-derived blind. Passing the checks isn't the same as
  withstanding examination.

**agra** (अग्र) — "tip, foremost, front"
- *Does here:* the other attempt — during repair, the attempt "right on
  checks the best one fails," shown side by side: "Each is right where the
  other is wrong" (§15).
- *From:* the ordinary sense — the agra is the leading tip, the foremost
  point; an agraja is the elder, the one in front.
- *In its own field:* the tip of the vallī — the foremost quotient, where
  the reduction begins; or the vanguard of an argument, the point that goes
  first.
- *Why chosen:* in repair, the system looks for the attempt that is *ahead*
  on exactly the checks where the best is behind — the foremost attempt on
  those checks — and sets the two tips against each other so the repair can
  keep what each gets right.

**śāstra** (शास्त्र) — "systematic treatise, teaching"
- *Does here:* the read-only structured view of the method library — rule,
  applies-to, examples, rationale, lineage, open voids (§10).
- *From:* the śāstra form itself — a systematic discipline laid out as
  sūtra (rule), paribhāṣā (metarule), udāharaṇa (example), upapatti
  (demonstration), paramparā (lineage).
- *In its own field:* Pāṇini's Aṣṭādhyāyī — ~4,000 sūtras, each with its
  paribhāṣās, examples, and commentary lineage stretching over two
  millennia, still the most precise grammar ever written.
- *Why chosen:* the method library is kept the way a śāstra is kept — not
  as a heap of tricks but as rules with examples, proofs, and lineage, so a
  later reader (or a later task) can see *why* each rule stands.

**sūtra / paribhāṣā / udāharaṇa / upapatti / paramparā**
(सूत्र / परिभाषा / उदाहरण / उपपत्ति / परम्परा) — "thread-rule / metarule /
example / demonstration / lineage"
- *Do here:* the five fields of every śāstra entry — the rule, what it
  applies to, worked examples, the rationale tested where no check reaches,
  and the lineage (supersedes / superseded-by / shown-wrong).
- *From:* the classical commentary structure — Pāṇini's sūtras governed by
  paribhāṣās, illustrated by udāharaṇas, defended by upapatti, handed down
  through paramparā.
- *In their own field:* a single Pāṇinian sūtra like *iko yaṇ aci* (6.1.77)
  — three words that, with their paribhāṣās and examples, govern thousands
  of sound changes; the upapatti shows *why* the rule must be so shaped.
- *Why chosen:* a stored method without these five is folklore. With them it
  is a rule of the system — statable, bounded, exemplified, justified, and
  placed in its history.

**bīja** (बीज) — "seed"
- *Does here:* the conditions plus derivation recorded with a method in the
  default engine — *when* it holds and *why* (§15).
- *From:* Bhāskara II's Bījagaṇita — "seed computation," algebra: the seed
  from which the whole plant of the solution grows.
- *In its own field:* Bhāskara's bīja problems — "tell me the number which,
  multiplied by 3 and increased by 10…" — the seed-statement from which the
  unknown is grown by systematic steps.
- *Why chosen:* a method's bīja is its seed — the conditions that germinate
  it and the derivation that grows it. Stored with the method so the *why*
  travels with the *what*.

**mati** (मति) — "thought, judgment, the assumed"
- *Does here:* the bounded guess — and the `mati()` repair step. It takes
  the best attempt's failed checks (up to 6), asks the sketcher role for a
  source template with literal holes, and enumerates fillings via
  `SketchCatalogue` — each qualified in the sandbox against the full check
  contract ("Preserve unknown validation instead of disguising it as
  refutation"). Then the ranking: "Best, not first (the cakravala's choice:
  the constraint admits, the objective ranks)." After the first passing
  filling, it tries up to 32 more candidates (`MATI_EXTRA`) hunting for up
  to 4 passing fillings (`MATI_COMPARE`); with a reference, they are ranked
  by agreement with it on the small non-check inputs, earliest wins ties.
  Losers are *invalidated* in the artifact memory: "mati: the reference
  disagrees more often than with the chosen filling" — "A filling the
  reference contradicts more often must not be offered to later tasks."
- *Does here:* the bounded guess — and the `mati()` step that ranks
  candidates: "Best, not first (the cakravala's choice: the constraint
  admits, the objective ranks)" (§3, §16).
- *From:* Brahmagupta's kuṭṭaka procedure — the *mati* is the "assumed
  number": "multiplied by an assumed number [mati] such that the product,
  having added to it the difference of the remainders, may be exactly
  divisible." The guess is *bounded* — then validated.
- *In its own field:* solving 137x − 60y = 10 — assume (mati) a multiplier
  for 137 such that 137·mati + 10 is divisible by 60; try mati = 2: 284/60
  no; mati = 50: 6860/60 no… the assumption is tried and *checked*, never
  trusted.
- *Why chosen:* every guess in this system is a mati — assumed, bounded,
  then validated. And among passing candidates the choice is mati too:
  judgment, not first-come.

**cakravāla** (चक्रवाल) — "cyclic, wheel-like"
- *Does here:* the strategy choice — "Cakravala-style: the next strategy is
  chosen from the evidence, each at most once per stall" (§1).
- *From:* Bhāskara II's cakravāla — the cyclic method for x² − Ny² = 1,
  which composes (bhāvanā) a triple with a chosen multiplier, cycling until
  the equation yields. The most powerful algorithm of its age.
- *In its own field:* x² − 61y² = 1 — Fermat challenged Europe with it;
  the cakravāla cycles through and lands on x = 1766319049, y = 226153980.
  The wheel turns until it closes.
- *Why chosen:* the climb cycles through strategies the way the cakravāla
  cycles through triples — each turn informed by the last, each strategy
  tried once per stall, the wheel turning until the failure yields.

---

## The three engines, compared

| | engine/ (default) | engine-speed/ | engine-best/ |
|---|---|---|---|
| Pin | 89deb23 | cff12a9 | 8ce0951 |
| Recorded | 1,248 tests pass (Docker) | 11/12, 40 calls | 12/12, 53 calls, 79/79 hidden, 0 wrong |
| Value reuse | yes | yes | no |
| Krama | yes | yes | no |
| Agra / parīkṣā / witness / roles / bīja | yes | no | no |
| Planner, checks, gates, sandbox, ledger, server/CLI | yes | yes | yes |
| Engine block, compose, frontier, sketches, programs, campaign | yes | yes | yes (byte-identical across all three — sha256-verified) |

Use one engine directory per run and a separate workspace for each. The
historical scores come from separate development runs.

## A note on drive.py

The W4-r commit message mentions `drive.py` (solver event printing) and
`KUTTAKA_JOB_TIMES` (per-job timing). Neither is in the submission — they are
developer tooling from the private kuttaka side. The in-submission
equivalents: solver events via `Solver._event(...)` persisted in the workspace
journal, viewable through the CLI `stats` command, HTTP `GET /stats`, or the
MCP `stats` tool.

---

