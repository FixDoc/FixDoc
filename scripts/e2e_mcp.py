#!/usr/bin/env python3
"""End-to-end check of the FixDoc MCP server with REAL embeddings.

Requires: pip install -e ".[embed]" (first run downloads the ~130MB model).
Seeds a throwaway store in a temp dir, spawns the installed `fixdoc serve`,
and speaks actual JSON-RPC over pipes. Asserts the behaviors that matter:

  1. semantic match  — a query worded nothing like the stored symptom finds it
  2. the trap        — same vocabulary, wrong resource_type never surfaces
  3. record          — a new fix lands quarantined
  4. duplicate       — recording it again becomes a confirmation, no new file
  5. confirm         — occurrences increment and the event is logged

Run: python scripts/e2e_mcp.py
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from fixdoc.core.models import Entry  # noqa: E402


def seed(store: Path):
    ns = store / "knowledge" / "platform"
    ns.mkdir(parents=True)
    entries = [
        Entry(
            id="fx_a1b2c3d4",
            type="fix",
            status="validated",
            occurrences=3,
            title="AKS pods stuck Pending after nodepool scale-up",
            resource_type="kubernetes/aks",
            sections={
                "Symptom": "Pods remain in Pending state after adding a new "
                "nodepool. Scheduler reports '0/14 nodes are available'.",
                "Root cause": "Azure CNI reserves max_pods IPs per node; the "
                "nodepool subnet has no free IPs left.",
                "Fix": "Add a dedicated subnet for the nodepool or lower max_pods.",
                "Verification": "Pods schedule; availableIpAddressCount positive.",
            },
        ),
        Entry(
            id="fx_09ab23cd",
            type="fix",
            status="validated",
            occurrences=2,
            title="Databricks jobs stuck pending waiting for cluster",
            resource_type="databricks/jobs",
            sections={
                "Symptom": "Jobs sit in Pending; the job cluster never starts "
                "because the workspace hit its core quota.",
                "Root cause": "Regional vCPU quota exhausted by idle clusters.",
                "Fix": "Terminate idle clusters or raise the quota.",
                "Verification": "Job cluster provisions.",
            },
        ),
    ]
    for e in entries:
        (ns / f"{e.id}.md").write_text(e.to_markdown())


RECORD_ARGS = {
    "title": "RDS connections exhausted by leaked pool during deploy",
    "symptom": "App 5xx spikes during deploys; Postgres shows max_connections reached.",
    "root_cause": "Old pods keep DB pools open through the grace period.",
    "fix": "Close pools in a preStop hook; stagger the rollout.",
    "verification": "pg_stat_activity stays under the limit during a deploy.",
    "resource_type": "kubernetes/aks",
}


def main():
    fixdoc = shutil.which("fixdoc")
    if not fixdoc:
        sys.exit("fixdoc not on PATH — pip install -e '.[embed]' first")
    store = Path(tempfile.mkdtemp(prefix="fixdoc-e2e-"))
    seed(store)

    def call(name, args, i):
        return {
            "jsonrpc": "2.0",
            "id": i,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        }

    msgs = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "e2e", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        call(
            "search_fixes",
            {
                "query": "kubernetes workloads won't schedule after we " "grew the cluster",
                "resource_type": "kubernetes/aks",
            },
            2,
        ),
        call(
            "search_fixes", {"query": "jobs stuck pending", "resource_type": "databricks/jobs"}, 3
        ),
        call("record_fix", RECORD_ARGS, 4),
        call("record_fix", RECORD_ARGS, 5),
        call("confirm_fix", {"fix_id": "fx_a1b2c3d4"}, 6),
    ]
    proc = subprocess.Popen(
        [fixdoc, "serve", "--store", str(store)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    out, _ = proc.communicate("".join(json.dumps(m) + "\n" for m in msgs), timeout=600)
    text = {
        r["id"]: r["result"]["content"][0]["text"]
        for r in map(json.loads, out.splitlines())
        if "id" in r and r["id"] != 1
    }

    assert "fx_a1b2c3d4" in text[2] and "Azure CNI" in text[2], text[2]
    print("1) semantic match OK:", text[2].splitlines()[0])
    assert "fx_09ab23cd" in text[3] and "fx_a1b2c3d4" not in text[3], text[3]
    print("2) wrong-universe trap OK")
    assert "quarantined" in text[4], text[4]
    print("3) record -> quarantined OK")
    assert "confirmation" in text[5], text[5]
    print("4) duplicate -> confirmation OK")
    assert "4 incident" in text[6], text[6]
    print("5) confirm -> occurrences 3->4 OK")
    events = [json.loads(line) for line in (store / ".fixdoc-index" / "events.jsonl").open()]
    assert [e["type"] for e in events].count("retrieval_served") == 2
    print(f"6) events log OK ({len(events)} events)")
    shutil.rmtree(store)
    print("\nALL E2E CHECKS PASSED")


if __name__ == "__main__":
    main()
