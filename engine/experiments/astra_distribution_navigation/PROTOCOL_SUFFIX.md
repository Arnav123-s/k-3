# Counterexample processing follow-up v2

This separately labeled development study follows v1's word-limit failure.
It does not replace that result or treat previously exposed worlds as held out.
The fixed comparison tests whether isolating a distinguishing suffix avoids
unnecessary expansion of the observation table while preserving full evidence.
The method is a modern adaptation of suffix-based counterexample processing,
not a new theorem or a recovered historical algorithm.

For a failed word w and candidate states q_i along its prefixes, compare target
endpoint values f(i) = output(access(q_i) concatenated with w[i:]). The endpoints
differ: f(0) is the observed result, while f(|w|) is the candidate's endpoint
output. Binary search locates adjacent unequal f(i), f(i+1). The suffix w[i+1:]
distinguishes the candidate transition's two supposedly equivalent histories.
Add it and its tails to the observation table instead of every prefix of w.
All target information comes from budgeted callback queries or retained actual
observations. Preserve every counterexample; failed reduction earns no support.

Thirteen cases: the exposed `generated_10_223_colors` regression; eight fresh
generated maps (8/12 states, seeds 307/331, visible/color observations); repeated
action triggers six/eight; and the two v1 substring-trigger worlds. None starts
at the goal. New generated maps are authored development variants, not a sealed
generalization test.

Six arms per case: prefix-based and suffix-based refinement, each with persistent
probes at seeds 701/809/907. All use the v1 length-16, epsilon=delta=1/20 sampling
contract and the same query/action/word/state/work ceilings. Keep all 78 outcomes.
The schedule PRNG seed matches within a pair; differing candidate revisions may
consume different numbers of words. This is not a claim that subsequent probes
or actual costs are identical.

Save protocol, sources, design and evaluator truth before runs. Report exact maps,
wrong finite passes, incomplete outcomes, actual resets/actions and all construction
work; compare maps only after learning. Evaluate both common distribution masses
and execute goal routes separately. Record per-run table cells, suffix-reduction
receipts and source identities. Avoid comparisons restricted only to successful
pairs as an overall gain claim. Fixed-seed evidence still has no IID certificate.
