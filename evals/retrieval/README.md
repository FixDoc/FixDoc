# Retrieval eval

Golden cases for the retrieval engine. `cases.yaml` is human-approved ground
truth; `store/knowledge/` is a fictional 14-entry store built so the cases have
confusable neighbours (three reasons pods go Pending, two lookalike Terraform
failures, DNS versus ingress) rather than an unrelated corpus that proves nothing.

## Labeling rules

- `relevant`: the answer. A hit is any relevant id in the top K (K=3 by default:
  with a 2k-token budget, top-3 or it doesn't exist).
- `acceptable`: fine to surface, but not a hit on its own. Reported separately
  so a case "saved" only by an acceptable id gets eyeballed.
- `must_not_return`: the trap. Similar words, wrong universe. Surfacing it
  anywhere in the results, not just the top K, fires the trap. Target: zero.
- Queries are worded the way incidents present, never echoing entry titles.

## Running

```bash
make eval                       # real embeddings; gates on Recall@3 and traps
fixdoc eval retrieval           # report only, no gate
```

Adding a case that fails is the signal, not a problem: label it, run it, then
fix retrieval. Never relabel to make a number pass.
