# Prefix-coverage compaction follow-up v4

V3's outcome-cover hybrid fixes the observed transition/outcome miss, but its
deterministic component repeats many proper prefixes as separate reset tests.
This new authored study examines a structural compression: retain only
prefix-maximal deterministic words, with a certificate mapping every original
word to a retained extension. The original v3 outcomes stay unchanged.

For any stationary deterministic reset system, the trace on word u is the prefix
of the trace on an extension uv. Therefore agreement on the retained extensions
implies agreement on every original word. This preserves finite full-trace
obligations for a fixed candidate; it does not prove unknown-world completeness,
preserve detection under arbitrary drift, or force the same learning trajectory.

Never remove or deduplicate random draws: every sampled word still receives a
fresh reset test, including repeats and words covered by deterministic tests.
Charge trie construction, certificate production and test construction work.
If any budget prevents completing the declared cover, no partial cover qualifies.
Keep original and compact word counts and both hash commitments in the record.

Use all thirteen exposed v2/v3 cases, none initially at G. Six arms per case:
full and compact outcome-cover hybrid, both using suffix refinement and persistent
probes at seeds 701/809/907. All 78 runs are new records under the same v3 resource
ceilings. Fixed seeds are reproducible probes, never IID certificates.

Snapshot sources, protocols, design and truth before all runs. Verify each
coverage certificate independently against the saved original word obligations.
Record exact final maps, all missed distinctions/incomplete runs, physical costs,
construction work, goal receipts and all archives. Report changes in discovery
trajectory separately from the coverage theorem. A lower action count does not
establish a broader task or live-model capability gain.
