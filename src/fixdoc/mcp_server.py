"""The FixDoc MCP server: four tools over the core engine, spoken over stdio.

This is the product surface. The tool DESCRIPTIONS below are not comments,
they are the UI — the model is the user, and the descriptions teach it when
to search, how to write a root cause, and to close the loop with confirm_fix.
Iterate on those strings the way you'd iterate on a landing page.

Transport is hand-rolled newline-delimited JSON-RPC 2.0, the MCP stdio
framing, on stdlib only.
# ponytail: stdio-only, no SDK — zero deps and ~80 lines cover the tools-only
# surface; adopt the official mcp package when Streamable HTTP / hosted lands.

The server holds no state of its own: everything lives in the store and the
index, so several instances can share one repo, and killing the process
loses nothing.
"""

import json
import sys
import time
from pathlib import Path
from typing import Optional

from .core.events import log_event
from .core.models import Entry
from .core.record import confirm_entry, record_entry
from .core.retrieval import search

PROTOCOL_FALLBACK = "2025-03-26"
try:  # single source of truth: the installed package metadata
    from importlib.metadata import version as _pkg_version

    SERVER_VERSION = _pkg_version("fixdoc")
except Exception:  # not installed (e.g. running from a raw checkout)
    SERVER_VERSION = "0.0.0"

# Guard rails for the write path. A looping agent should annoy, not flood:
# 10 writes/min caps the damage at "a screenful of quarantined entries",
# and the field cap keeps a runaway payload from becoming a 2MB markdown file.
MAX_WRITES_PER_MINUTE = 10
MAX_FIELD_CHARS = 8000

TOOLS = [
    {
        "name": "search_fixes",
        "description": (
            "Search your team's validated incident knowledge. Call this FIRST, before "
            "debugging a production symptom from scratch — if this incident (or one like it) "
            "was solved before, the validated fix comes back in seconds with proof of how "
            "often it worked. Query with the symptom as you observe it, e.g. "
            "'pods stuck Pending after nodepool scale-up'. Results are ranked by relevance "
            "and trust (validated status, prior confirmed resolutions) and fit a token "
            "budget. If a returned fix resolves your issue, call confirm_fix with its id."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The symptom, phrased as observed."},
                "resource_type": {
                    "type": "string",
                    "description": "Narrow to a platform, e.g. 'kubernetes/aks', "
                    "'terraform/aws'. Strongly recommended: it "
                    "filters out similar-sounding fixes from the "
                    "wrong system.",
                },
                "env": {
                    "type": "string",
                    "description": "Environment you are debugging, e.g. 'prod'.",
                },
                "entry_type": {
                    "type": "string",
                    "enum": ["fix", "playbook", "insight"],
                    "description": "Restrict to one knowledge type.",
                },
                "token_budget": {
                    "type": "integer",
                    "description": "Max tokens of context to return "
                    "(default 2000). Raise it if you have a large "
                    "context window and a complex incident.",
                },
                "include_quarantined": {
                    "type": "boolean",
                    "description": "Also return unreviewed entries. "
                    "They are NOT validated — treat as "
                    "hints, not answers.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "record_fix",
        "description": (
            "Record a fix you just verified, so the next agent (or engineer) who hits this "
            "incident resolves it in minutes. Call this after resolving an incident that "
            "search_fixes did not cover. root_cause must be CAUSAL (why it happened), not "
            "correlational ('restarting helped' is not a root cause). verification must say "
            "how you know it worked. The entry lands in quarantine for human review — it "
            "will not be retrievable until a person validates it, so write for that reviewer. "
            "If your fix duplicates an existing entry, it is counted as a confirmation of "
            "that entry instead of creating a copy."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "One line naming the incident, symptom-first.",
                },
                "symptom": {
                    "type": "string",
                    "description": "What was observed, as a future searcher would " "phrase it.",
                },
                "root_cause": {
                    "type": "string",
                    "description": "Why it happened. Causal, specific.",
                },
                "fix": {
                    "type": "string",
                    "description": "What resolved it. Concrete commands/steps.",
                },
                "verification": {
                    "type": "string",
                    "description": "How you confirmed the fix worked.",
                },
                "resource_type": {
                    "type": "string",
                    "description": "Platform, e.g. 'kubernetes/aks'.",
                },
                "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                "env_scope": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Environments this applies to; omit for all.",
                },
                "match_keys": {
                    "type": "object",
                    "description": "Flat string map of exact-match signals, e.g. "
                    '{"error_class": "FailedScheduling"}.',
                },
            },
            "required": ["title", "symptom", "root_cause", "fix", "verification"],
        },
    },
    {
        "name": "confirm_fix",
        "description": (
            "Report that a fix retrieved from search_fixes resolved your incident. Always "
            "call this when a retrieved fix worked — it is one call, and it is how the "
            "knowledge base learns which fixes actually help: confirmations raise the "
            "entry's trust, so future searches rank it higher."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "fix_id": {"type": "string", "description": "The entry id, e.g. fx_7c2a91e4."},
                "note": {
                    "type": "string",
                    "description": "Optional: anything that differed from the entry.",
                },
            },
            "required": ["fix_id"],
        },
    },
    {
        "name": "get_fix",
        "description": (
            "Fetch one knowledge entry in full by id (search_fixes returns ids). Use when "
            "search returned a summary or title and you need the complete body."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"fix_id": {"type": "string"}},
            "required": ["fix_id"],
        },
    },
]


class FixDocServer:
    def __init__(self, store_dir, index, namespace="shared"):
        self.store_dir = Path(store_dir)
        self.index = index
        self.namespace = namespace  # where record_fix writes; per-team config later
        self._write_times = []
        index.sync(self.store_dir)

    # ── transport ─────────────────────────────────────────────────────────

    def run(self, stdin=None, stdout=None):
        """Newline-delimited JSON-RPC over stdio. stdout is protocol-only;
        anything human goes to stderr."""
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                print(f"fixdoc: skipping unparseable line: {line[:80]}", file=sys.stderr)
                continue
            response = self.handle_message(message)
            if response is not None:
                stdout.write(json.dumps(response) + "\n")
                stdout.flush()

    def handle_message(self, message) -> Optional[dict]:
        method = message.get("method", "")
        msg_id = message.get("id")
        params = message.get("params") or {}

        if method.startswith("notifications/"):
            return None  # notifications never get a response
        if method == "initialize":
            return self._result(
                msg_id,
                {
                    # Echo the client's protocol version: this server's surface
                    # (tools only) is compatible across published revisions.
                    "protocolVersion": params.get("protocolVersion", PROTOCOL_FALLBACK),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fixdoc", "version": SERVER_VERSION},
                },
            )
        if method == "ping":
            return self._result(msg_id, {})
        if method == "tools/list":
            return self._result(msg_id, {"tools": TOOLS})
        if method == "tools/call":
            text, is_error = self._call_tool(params.get("name"), params.get("arguments") or {})
            return self._result(
                msg_id,
                {
                    "content": [{"type": "text", "text": text}],
                    "isError": is_error,
                },
            )
        if msg_id is not None:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"method not found: {method}"},
            }
        return None

    @staticmethod
    def _result(msg_id, result):
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    # ── tools ─────────────────────────────────────────────────────────────

    def _call_tool(self, name, args):
        handlers = {
            "search_fixes": self._search,
            "record_fix": self._record,
            "confirm_fix": self._confirm,
            "get_fix": self._get,
        }
        handler = handlers.get(name)
        if handler is None:
            return f"unknown tool: {name}. Available: {', '.join(handlers)}", True
        oversized = [k for k, v in args.items() if isinstance(v, str) and len(v) > MAX_FIELD_CHARS]
        if oversized:
            return (
                f"field(s) too large: {', '.join(oversized)} "
                f"(limit {MAX_FIELD_CHARS} chars). Trim and retry."
            ), True
        try:
            return handler(args)
        except Exception as exc:  # errors speak agent: say what broke, invite a retry
            return f"{name} failed: {exc}", True

    def _search(self, args):
        self.index.sync(self.store_dir)  # content-hash incremental: ms when nothing changed
        results = search(
            self.index,
            self.store_dir,
            args["query"],
            entry_type=args.get("entry_type"),
            resource_type=args.get("resource_type"),
            env=args.get("env"),
            match_keys=args.get("match_keys"),
            token_budget=args.get("token_budget", 2000),
            include_quarantined=bool(args.get("include_quarantined")),
        )
        log_event(
            self.index.index_dir,
            "retrieval_served",
            {
                "query": args["query"],
                "filters": {k: args.get(k) for k in ("entry_type", "resource_type", "env")},
                "served": [
                    {
                        "id": r.id,
                        "score": round(r.score, 4),
                        "similarity": round(r.similarity, 4),
                        "detail": r.detail,
                    }
                    for r in results
                ],
            },
        )
        if not results:
            # An empty result is a real answer. Quarantined candidates are
            # named but their bodies withheld: unreviewed knowledge must be
            # asked for, never slipped in.
            pending = [
                r.id
                for r in self.index.live(
                    entry_type=args.get("entry_type"), include_quarantined=True
                )
                if r.status == "quarantined"
            ]
            msg = "No validated fixes matched."
            if pending:
                msg += (
                    f" {len(pending)} quarantined (unreviewed) candidate(s) exist: "
                    f"{', '.join(pending)}. Pass include_quarantined=true to read them, "
                    "and treat them as hints, not answers."
                )
            return msg, False
        blocks = []
        for r in results:
            proof = (
                f"resolved {r.occurrences} prior incidents"
                if r.occurrences
                else "not yet field-confirmed"
            )
            header = f"[{r.id}] {r.title} — {r.status}, {proof}, similarity {r.similarity:.2f}"
            blocks.append(header if not r.content else f"{header}\n{r.content}")
        blocks.append("If one of these resolves your issue, call confirm_fix with its id.")
        return "\n\n".join(blocks), False

    def _record(self, args):
        # ponytail: fixed window on a monotonic clock; per-caller quotas when
        # the server is ever shared.
        now = time.monotonic()
        self._write_times = [t for t in self._write_times if now - t < 60]
        if len(self._write_times) >= MAX_WRITES_PER_MINUTE:
            return (
                "write rate limit reached (10/min). If you are retrying a failure, "
                "fix the reported problem first; if these are distinct fixes, wait a "
                "minute and continue."
            ), True
        entry = Entry(
            id="",
            type="fix",
            title=args["title"],
            sections={
                "Symptom": args["symptom"],
                "Root cause": args["root_cause"],
                "Fix": args["fix"],
                "Verification": args["verification"],
            },
            resource_type=args.get("resource_type"),
            severity=args.get("severity"),
            env_scope=args.get("env_scope") or [],
            match_keys=args.get("match_keys") or {},
        )
        result = record_entry(self.store_dir, self.index, entry, namespace=self.namespace)
        if result.action == "invalid":
            return "could not record: " + "; ".join(result.problems) + ". Fix and retry.", True
        self._write_times.append(now)
        if result.action == "confirmed_existing":
            return (
                f"This fix already exists as {result.matched_id} "
                f"(similarity {result.similarity:.2f}) — your report was counted as a "
                "confirmation of it instead of creating a duplicate."
            ), False
        note = ""
        if result.action == "created_related":
            note = (
                f" It looks related to {result.matched_id}; a reviewer will decide "
                "whether to merge them."
            )
        return (
            f"Recorded as {result.entry_id} (quarantined).{note} It becomes retrievable "
            "once a human reviews and validates it."
        ), False

    def _confirm(self, args):
        occurrences = confirm_entry(
            self.store_dir, self.index, args["fix_id"], note=args.get("note")
        )
        if occurrences is None:
            return (
                f"no entry found with id {args['fix_id']!r}. Use an id returned by " "search_fixes."
            ), True
        return f"Confirmed {args['fix_id']}: it has now resolved {occurrences} incident(s).", False

    def _get(self, args):
        rel = self.index.path_for(args["fix_id"])
        if rel is None:
            return f"no entry found with id {args['fix_id']!r}.", True
        entry = Entry.from_markdown((self.store_dir / rel).read_text())
        body = "\n\n".join(f"## {name}\n\n{text}" for name, text in entry.sections.items())
        proof = (
            f"resolved {entry.occurrences} prior incidents"
            if entry.occurrences
            else "not yet field-confirmed"
        )
        return f"[{entry.id}] {entry.title} — {entry.status}, {proof}\n\n{body}", False
