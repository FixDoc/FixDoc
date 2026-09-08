"""Retrieval eval: run labeled query cases through the real engine, score them.

Case format (YAML list):

    - query: "pods won't schedule after adding nodes"   # as incidents present
      resource_type: kubernetes/aks                     # optional filter
      env: prod                                         # optional filter
      relevant: [fx_subnet01]        # a hit = any of these in the top K
      acceptable: [fx_maxpods01]     # fine to return, but not a hit on its own
      must_not_return: [fx_dbx01]    # the trap: must never surface at all

Two metrics, both decision-oriented: Recall@K on relevant (the headline —
with a 2k token budget, top-3 or it doesn't exist) and trap rate (the
classic RAG failure, target zero). Everything else is reporting.
"""

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from fixdoc.core.index import Index
from fixdoc.core.retrieval import search

EMBED_MODEL_LABEL = "eval"
DEFAULT_K = 3


@dataclass
class EvalReport:
    cases: int = 0
    hits: int = 0
    acceptable_only: int = 0  # cases saved only by an 'acceptable' id — worth eyeballing
    trap_cases: int = 0
    traps_fired: int = 0
    misses: list = field(default_factory=list)
    trap_hits: list = field(default_factory=list)

    @property
    def recall(self):
        return self.hits / self.cases if self.cases else 0.0

    @property
    def trap_rate(self):
        return self.traps_fired / self.trap_cases if self.trap_cases else 0.0


def run_retrieval_eval(cases_path, store_dir, embed_fn, k=DEFAULT_K):
    """Score every case against the real engine. The index is built in a temp
    dir so evals never leave state behind in the fixture store."""
    cases = yaml.safe_load(Path(cases_path).read_text()) or []
    store_dir = Path(store_dir)
    report = EvalReport(cases=len(cases))

    with tempfile.TemporaryDirectory() as tmp:
        index = Index(Path(tmp), embed_fn, EMBED_MODEL_LABEL)
        index.sync(store_dir)
        for case in cases:
            results = search(
                index,
                store_dir,
                case["query"],
                resource_type=case.get("resource_type"),
                env=case.get("env"),
            )
            returned = [r.id for r in results]
            top_k = returned[:k]

            relevant = set(case.get("relevant") or [])
            acceptable = set(case.get("acceptable") or [])
            if relevant & set(top_k):
                report.hits += 1
            else:
                if acceptable & set(top_k):
                    report.acceptable_only += 1
                report.misses.append(
                    {"query": case["query"], "expected": sorted(relevant), "top": top_k}
                )

            forbidden = set(case.get("must_not_return") or [])
            if forbidden:
                report.trap_cases += 1
                fired = forbidden & set(returned)  # anywhere in results counts
                if fired:
                    report.traps_fired += 1
                    report.trap_hits.append({"query": case["query"], "returned": sorted(fired)})
    return report
