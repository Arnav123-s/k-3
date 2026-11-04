# KUTTAKA-C3 task families: specification and generation rules for the evaluator

This file is for the **evaluator session** that writes the sealed C3 stream. The protocol that uses it is `docs/KUTTAKA_C3_PROTOCOL.md`. The builder wrote this specification and the development stream next to it (`dev_stream.json`, `dev_holdout.json`, `make_dev.py`). The builder never sees the sealed tasks.

## Where the sealed files live

- Write them in an evaluator-only folder that is never committed: `out/sealed-c3/` for `stream.json` (the runner reads it) and `out/sealed-c3-evaluator/` for `holdout.json` (only `campaign score` reads it, after both arms have finished).
- Builders never read any path or branch matching `sealed*`, `out/sealed*` or `claude/c1-sealed-run*`.
- Write fresh tasks. Do not derive them from the C1 sealed tasks, from the C1 development tasks or from the C3 development chains `kal` and `meru`.

## What the stream has to make possible

A task late in a chain should be hard to get fully right from scratch within 6 model calls, and easy to get right by calling methods verified on earlier tasks of the same chain. Every task must still be solvable from its own request alone. Both arms get the same request text, so this is fair. Only the effort differs, and that is what the study measures.

Three mechanisms make this so:
- **Depth.** Each later level needs the rules and procedures of earlier levels.
- **Verified core.** The base task fixes a convention-heavy core with edge-case checks. Later tasks need that core again, so a from-scratch rewrite gets a fresh chance to slip.
- **Variants.** The last task asks again for a deep function, with new input and output encodings.

## Size and order (sealed stream)

- **Size:** 4 families × 2 chains × 5 tasks = **40 tasks**. The families are `lang`, `calendar`, `numbers` and `routes`.
- **Chain order:** chains appear one after another, and two consecutive chains never come from the same family. Example: lang, calendar, numbers, routes, lang, calendar, numbers, routes.
- **Task order:** within a chain, tasks are contiguous and run level 1 to 5.

## Roles

| Level | Role | `needs` (earlier task ids of the same chain) |
|---|---|---|
| 1 | `base` | none |
| 2 | `extend` | level 1 |
| 3 | `extend` or `compose` | level 2, and possibly level 1 |
| 4 | `compose` | at least two of levels 1–3 |
| 5 | `variant` | exactly its `target`, which is level 3 or level 4 |

- **`extend` and `compose`.** An extend task adds new logic on top of one earlier function. A compose task needs at least two earlier functions. Its reference solution, written as a composition, calls them.
- **`variant`.** A variant computes its target's function with a **different input encoding and a different output encoding**. Examples:
  - an object becomes a list, or a list becomes a string;
  - 0-based reporting becomes 1-based;
  - `null` becomes a sentinel string;
  - a single input becomes a batch of inputs.

  It restates every rule of the target in full and adds no new rule beyond the encoding. So a correct solution can always be: convert the input, compute the target, convert the result.

## Task JSON

`stream.json` is a list of task objects with exactly these keys:

| Key | Content |
|---|---|
| `id` | short, unique, safe (letters, digits and `-`) |
| `chain` | the chain id |
| `family` | one of the four families |
| `level` | 1–5 |
| `role` | as in the table above |
| `needs` | list of earlier task ids of the same chain |
| `target` | variant only |
| `request` | the task text |
| `checks` | list of `{"input", "expected"}` |

- The runner passes only `request` and `checks` to the model. The other keys are analysis labels that the model never sees.
- `holdout.json` maps every task id to a list of `{"input", "expected"}`.

## Generation rules

- **G1 Fields.** Use exactly the keys above. Do not use `context` or `input` fields.
- **G2 Chain structure.** Follow the roles table. `needs` and `target` name only earlier tasks of the same chain.
- **G3 Visible checks.**
  - A base task has 4–8 visible checks. Together they exercise every clause of every rule it defines: each exception, each tie-break, each invalid-input case.
  - Every other task has 3–8 visible checks that exercise its new logic. They need not re-cover the edge cases of inherited rules.
  - Inputs within a task are distinct.
- **G4 Exact JSON.** Inputs and expected values are integers, strings, booleans, `null`, lists and objects. No floats. Values are deterministic.
- **G5 Self-contained requests.**
  - Each request begins with `Write run(p).`, states the input and output formats, and restates every rule the task needs. Repeating the same rule text across the tasks of a chain is expected.
  - A request never refers to another task, an earlier step, a stored method, memory, artifacts or `use(`, and never names another task id.
  - A request has at most 6000 characters.
- **G6 Holdout.**
  - Each task has at least 6 holdout cases, and no holdout input equals one of its visible inputs.
  - For every non-base task, at least 2 holdout cases exercise an edge case of an **inherited** rule that the task's own visible checks do not exercise. Examples: a leap-year exception, a tie-break, an overflow limit.
- **G7 Discriminative checks.** No earlier task's reference solution in the chain passes all the visible checks of a later task. `make_dev.py` (`discriminative`) shows the test.
- **G8 No standard-library shortcut.**
  - The rules are fictional or explicitly non-standard, so that no single Python standard-library facility computes the answer. Examples of such facilities: `datetime`, `calendar`, `fractions.Fraction.limit_denominator`, `eval` or `ast` on Python syntax, `math.comb`, `heapq` with default tie-breaks.
  - Where an expression language is used, its semantics differ from Python's somewhere that matters (for example truncating division, precedence or unary minus).
- **G9 Feasibility.**
  - Each reference solution takes under 1 s per input in plain CPython.
  - A composed reference makes at most 200 calls to earlier-level functions per input.
  - The request, all checks and the largest input together stay under 20 000 characters.
- **G10 Variety.** The two chains of a family use different fictional rules, for example two different calendars or two different languages. No chain copies the development chains.

`python3 -m kuttaka_runtime.c3 lint --stream S --holdout H --sealed` checks G1–G6 mechanically, together with the sealed size and order rules (S1, S2). G6's inherited-edge-case requirement, G7, G8 and G9 are the evaluator's responsibility. The generator should check G7 the way `make_dev.py` does.

## Families

Each family below lists the kind of rules to invent and one possible chain outline. The outlines are illustrations only; do not copy them.

### `lang`: tiny languages with stated semantics

Tokenizers, parsers and evaluators for a small invented language. The rules to state: token classes, precedence, associativity, unary operators, integer semantics (truncating or flooring division, remainder sign, overflow limits) and error values.
- **Outline:** L1 tokenize → L2 convert to postfix or a tree → L3 evaluate with variables → L4 run a multi-line program (uses L1 and L3) → L5 variant of L4 with a single `;`-separated string and a sorted `name=value` list.
- **Development chain:** `kal`.

### `calendar`: invented calendars

Month lengths, a leap rule with at least two exceptions, an epoch, a week of 5–8 named days, and festival or holiday rules.
- **Outline:** L1 date to day number with validity → L2 weekday or day-of-year → L3 a schedule within a month or year (uses L1 and L2) → L4 counting or finding dates under the schedule (uses L2 and L3) → L5 variant with string dates and a formatted result.
- **Development chain:** `meru`.

### `numbers`: exact integer arithmetic with stated normal forms

Invented numeral systems (for example a negative or mixed radix, or balanced digits with named symbols), or modular arithmetic with explicit result ranges and tie-breaks.
- **Outline:** L1 parse and format a numeral with a canonical form and invalid cases → L2 add two numerals → L3 multiply (uses L2) → L4 evaluate a list of operations, or compare and sort numerals under a stated order (uses L1–L3) → L5 variant of L4 with decimal input and output.

### `routes`: planning on weighted grids or graphs

Explicit move order, terrain costs and blocked cells, plus a fully stated tie-break among equal-cost paths, for example the lexicographically smallest move string in a stated move order.
- **Outline:** L1 validate a route and give its cost → L2 cheapest route between two cells with the tie-break → L3 a route through ordered waypoints (uses L2) → L4 the best order for at most 4 waypoints, with the tie-break between orders (uses L2 and L3) → L5 variant with the map as strings and the result as a move string plus cost.

## Evaluator procedure

1. Write a generator with reference solutions. `make_dev.py` is a template: it computes expected values from the references and runs the G7 check and the lint.
2. Run `python3 -m kuttaka_runtime.c3 lint --stream out/sealed-c3/stream.json --holdout out/sealed-c3-evaluator/holdout.json --sealed`. It must print `"ok": true`.
3. Compute `sha256sum` of both files and give the two hashes to the owner for the ledger before any sealed call.
4. Give the runner only `stream.json`.
5. After both arms have finished and passed their replays, run `campaign score` on each arm with `holdout.json`, then run `python3 -m kuttaka_runtime.c3 analyze` (see the protocol).
