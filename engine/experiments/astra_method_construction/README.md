# Constructing a checked method from a relation

This authored experiment turns a supplied monotone polynomial into a radix inverse method. It derives an increment expression, checks its exact polynomial identity independently, stores the method as JSON, and executes it by artifact ID after a disk reload. It is a bounded modern constructor inspired by the operational structure of root algorithms; no historical intent, recovered method or live-model discovery is claimed.

```sh
python -B -m experiments.astra_method_construction.run --output /tmp/method-construction-new
python -B -m unittest tests.test_astra_method_construction -v
```

The output directory must be new. No endpoint, Docker execution, downloads or arbitrary source execution are involved. `compiler.py` is trusted authored code; generated artifacts are a closed polynomial-data representation.

Recorded result: six constructed methods, 23 checked executions after artifact reload, zero model calls. Cases cover six supplied polynomials within one mathematical class. Eight unit tests pass against independent binary search and certificate checks, including radix variation, a 1024-bit target, invalid inputs, corrupted increments, and interrupted-search residuals.

Controls retain negative outcomes: a copied square solver returns 225 for the cubic input 50653 and fails the cubic goal; the constructed cube method returns 37. Altered algebraic increments, negative coefficients and degree beyond the implementation limit are rejected. A one-probe budget returns `incomplete`.

The source relation is supplied and retained in each method. No inference from unknown data or learned constructor is demonstrated. The strategy is an authored radix scheme; the runtime-generated part is its specialized update expression. The detailed [derivation and historical hypothesis](../../docs/research/reconstructive-memory/METHOD_CONSTRUCTOR_HYPOTHESIS.md) give the invariant and completion argument.

Supported contract: integer coefficients of at most 256 bits, all nonnegative, degree 1–8 with positive leading coefficient, radix 2–16, nonnegative target of at most 1024 bits, and at most 10000 probes. Rejecting an input outside this contract is not a mathematical impossibility claim. Compiler and store bytes are recorded separately; source, interpreter, checking and trace costs are not hidden in the artifact size. No performance advantage over binary search is claimed.

`recorded/events.jsonl` retains construction identities, execution attempts, full digit traces and independent checks. `recorded/memory` contains six immutable method artifacts; `recorded/results.json` summarizes all outcomes and controls. A development run is retained at `/tmp/astra-method-construction-development-1`.
