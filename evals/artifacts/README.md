# Published Evaluation Evidence

All records here use synthetic, isolated fixtures, not actual student data.
The published runs preserve failures as well as successful observations.

- `upgrade-development-01` and `02`: failed provider compatibility warmups.
- `upgrade-development-03` and `04`: visible development runs used to refine routing.
- `upgrade-regression-330-v1`: complete original regression and source/fixture snapshots.
- `upgrade-regression-330-rescore-v2`: identical observations rescored after correcting
  numeric canary substring false positives. The manifest hashes its original inputs;
  `assertion-changes.json` lists every changed assertion. No new model calls were made.
- `retrieval-1789519291437872539.json`: vector-only versus hybrid retrieval control.

The legacy `heldout` split name does not establish independent unseen evaluation.
Human semantic-support annotations remain unset. Reports describe first validated
text latency, not raw model token latency, and are not idle-machine load benchmarks.

Source archives reflect the exact historical working tree used by each experiment.
Their synthetic fixture constants are not deployment credentials. Future local runs
remain ignored by Git unless explicitly reviewed for publication.
