# Reasoning evidence input example

`reasoning_synthetic.json` is a small complete schema example. **Its engine
identity, hashes, search receipts and scores are invented. It is not real
Stockfish analysis and cannot establish chess strength.** The producer's
`simulation:true` and `method:synthetic_fixture` prevent operator acceptance
from promoting it to external engine evidence.

The root is the exact legal history `1. e4 e5`. The two made-up final-iteration
lines compare `Nf3 Nc6 Bc4` (+35 cp White) and `Nc3 Nf6 Bc4` (+10 cp White).
The 25 cp difference tests software arithmetic, not whether either move is
actually better.

Validation performs no execution or writes:

```sh
.venv/bin/python -m src.reasoning_import validate examples/reasoning_synthetic.json
```

To use a demo, explicitly accept the synthetic file into a separate canonical
artifact directory (the command creates that directory, immutable acceptance
envelope and bounded catalog only):

```sh
.venv/bin/python -m src.reasoning_import accept examples/reasoning_synthetic.json --artifact-dir /absolute/canonical/path/to/reasoning-demo --synthetic
```

For the API, set `CHESS_COACH_REASONING_DIR` to that directory and explicitly set
`CHESS_COACH_REASONING_ALLOW_SYNTHETIC=true` **only for contract/demo viewing**.
The review panel still labels every such comparison SYNTHETIC CONTRACT
VALIDATION, NOT CHESS STRENGTH. With that flag absent the fixture is readable
for availability but supplies no ranked reasoning result.

An independently generated non-simulated export must declare
`producer.method:external_export`, `simulation:false`, `uncertain:false`, and
contain the actual UCI identity, streamed binary/build/NNUE identifiers, complete
applied option inventory, option-application/reset receipts, exact full-history
contexts and final co-depth exact PV iteration data. Do not fill these fields
from the synthetic example. Missing metadata is a reason to refuse admission.
The importer does not generate analysis, download files or launch an exporter.

Only after independently reviewing the source of an existing genuine export,
use `--acknowledge-external-engine` rather than `--synthetic`. This records
operator attestation bound to the exact payload hash. It does not
cryptographically prove that an engine executed or that its values are true.
Do not accept fabricated fixture metadata as real analysis.

For `SIBLING_CHILD_COHORT`, each child record has `edge_uci`, an appended exact
history/context, matching full settings/cohort/session and equal achieved depth.
Each child's PV begins with the opponent's response. Root and child scores are
never blended into one ranking. Returned MultiPV may be incomplete, restricted
or bound-only for inspection, but such records cannot qualify a numerical
comparison. A user alternative absent from a cohort remains unassessed.

Existing evaluation/progressive/practice stores and GameLibrary are never
written. `data/reasoning/` is ignored for local artifacts; an arbitrary operator
path is not automatically Git-ignored, so keep personal exports in that ignored
location or outside the repository. Never place accepted artifacts in source,
tests, documentation, backup or Git directories.
