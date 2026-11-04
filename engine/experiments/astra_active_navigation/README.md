# Astra: learn state distinctions, act, preserve, and revise

This is the immutable original study at `a2b53fc`/`07ff126`. For byte-level source
replay use that revision or the archived source snapshots. Later optional
[constructed test policies](../astra_model_experiments/README.md) have separate
records and additional journal metadata; they do not rewrite this evidence.

Six developer-authored resettable worlds expose only actions `a`, `b`, `c` and
short observations. The same controller starts without state IDs, a map or a
state count. It constructs predictive states, corrects a coarse hypothesis after
a counterexample, plans to an authorized observable target, checks actual
execution, and reuses exact saved model/route IDs after reopening memory and in
a separate Python process. No model endpoint is called and no weights change.

This is **ENGINEERED** controller capability. Fixture labels suggest possible
applications; these are finite simulations, not robotics or scientific field
validation, an LLM comparison, a blind benchmark, or universal discovery.

| Authored fixture | Learned states | Reset queries | Exploration actions | Fresh route and separate-process reuse | Shortlex resets / actions |
|---|---:|---:|---:|---|---:|
| Aliased rooms | 4 | 154 | 515 | `ab`, both pass | 5 / 7 |
| Protocol handshake | 5 | 160 | 547 | `abc`, both pass | 18 / 39 |
| Delayed actuator | 6 | 181 | 661 | `abbc`, both pass | 54 / 162 |
| Locked workflow | 6 | 241 | 926 | `bacb`, both pass | 74 / 242 |
| Renamed handshake | 5 | 162 | 551 | `cab`, both pass | 32 / 81 |
| Three-phase cycle | 5 | 175 | 618 | `aab`, both pass | 14 / 27 |

Each positive case first proposes a coarse model, observes one predictive
counterexample and constructs 4–6 states. The final model matches all **121 fresh
words through depth four**, under identical ceilings of 4,096 reset queries,
50,000 actions, 32 actions per word and 128 states. A caller-provided state count
or world label does not guide exploration. The evaluator knows each fixture.
All six goals are reached in initial fresh execution, after reopening memory,
and again in a separate interpreter using the saved route IDs. Separate-process
reuse costs one reset plus 2–4 actions, with zero new learning queries.

**Shortlex goal search is cheaper on every positive case.** A stationary policy
that maps only the current observation to an action fails all six, illustrating
why memory is needed, but it is a weak diagnostic baseline. Shortlex enumeration
solves all six and could retain its successful word too. These results establish
revisable state construction and checked reuse, not an efficiency advantage over
that stronger baseline. The learner spends extra actions testing a predictive map.

## Controls and correction

- **Unspecified goal:** a branching map is learned while the intended marker is
  unresolved. A host-recorded user clarification authorizes A; after reload, a
  new authorized goal B produces a new route using the same model. The old A route
  is rejected under the B binding before any action.
- **Unknown beyond the horizon:** an all-idle world and a world with a six-action
  goal trigger produce identical queried word/output traces at depth two. Both
  return `no_route_in_hypothesis`; neither certifies real-world unreachability.
- **Missing reset:** returns `reset_unavailable`, with no qualified map.
- **Insufficient query budget:** returns `budget_exhausted`, with no qualified map.
- **Changed behavior:** the route `ab` fails after the world changes; the model and
  dependent route become invalidated and a question artifact preserves the
  counterexample. An explicitly versioned new context learns `ca` and reaches G.

Additional tests reject a route that forges an easier target, prevent a shorter
challenge schedule from forgetting earlier observations, stop on the first
conflicting action, and require a new context after repeated behavior changes.
An independent product-automaton checker verifies the learner against **all 64
two-state/two-action/two-output machines**; that small exhaustive family does not
give a general finite-world completeness guarantee.

## Memory and evidence

The six learned model bodies occupy **273–391 JSON bytes**. Route bodies occupy
335–357 bytes. These exclude interpreter/code, qualifications and evidence.
Per-case stores are about 12 KB; investigation journals are about 0.73–1.24 MB
before the extra separate-process replay. The implementation retains evidence;
it does not establish bounded total RAM/disk or universal lossless compression.

The checked record is in [recorded/summary.json](recorded/summary.json),
[fresh-process-replay.json](recorded/fresh-process-replay.json), and
[verification.json](recorded/verification.json). The complete
[evidence archive](recorded/evidence.tar.gz) contains 64 files, 12 artifact stores,
18,856 hash-validated journal events, and six exact source snapshots. Its SHA-256
is `c4125b3b3ccb6041820aa544a3bd841b6754400e96315293c8eb423cefe3528c`.
The compressed archive is 1,202,094 bytes. The archive includes the full source
manifest and hash receipts; generated run IDs mean a rerun need not be byte-identical.

Run from the repository root with Python 3.11+ and the standard library:

```bash
python3 -m unittest tests.test_astra_active_navigation tests.test_astra_navigation_tasks tests.test_astra_navigation_experiment
python3 -m experiments.astra_active_navigation.run --record /tmp/astra-navigation-new-record
```

The output directory must not already exist. For uncompressed investigation:

```bash
python3 -m experiments.astra_active_navigation.run --output /tmp/astra-navigation-full
python3 -m experiments.astra_active_navigation.run --replay /tmp/astra-navigation-full
```

`NavigationTools` exposes JSON tools to a host agent; domain-specific bounded
reset/step callbacks and the observable meaning of an authorized goal remain host
responsibilities. No existing frozen evaluation or transport contract changed.
See the [research derivation](../../docs/research/predictive-state/ACTIVE_NAVIGATION.md)
for the mathematics, sources and explicit limits.
