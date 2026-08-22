"""Tests for fixdoc.mcp_server — the four-tool stdio MCP server."""

import io
import json

from fixdoc.core.events import read_events
from fixdoc.core.index import Index
from fixdoc.core.models import Entry
from fixdoc.mcp_server import FixDocServer


def embed(text):
    return [float(len(text)), 1.0]


def make_fix(entry_id, title, status="validated", occurrences=0, **overrides):
    fields = dict(
        id=entry_id,
        type="fix",
        title=title,
        status=status,
        occurrences=occurrences,
        sections={
            "Symptom": "Pods pending after scale-up.",
            "Root cause": "Autoscaler race.",
            "Fix": "Restart the autoscaler.",
            "Verification": "All pods Running.",
        },
        resource_type="kubernetes/aks",
    )
    fields.update(overrides)
    return Entry(**fields)


def build_server(tmp_path, entries=()):
    store = tmp_path / "knowledge"
    for entry in entries:
        ns = store / "platform"
        ns.mkdir(parents=True, exist_ok=True)
        (ns / f"{entry.id}.md").write_text(entry.to_markdown())
    index = Index(tmp_path / "idx", embed, "test-model")
    return FixDocServer(store, index)


def rpc(method, params=None, msg_id=1):
    msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if msg_id is not None:
        msg["id"] = msg_id
    return msg


def call_tool(server, name, arguments):
    resp = server.handle_message(rpc("tools/call", {"name": name, "arguments": arguments}))
    result = resp["result"]
    return result["content"][0]["text"], result.get("isError", False)


class TestProtocol:
    def test_initialize_handshake(self, tmp_path):
        server = build_server(tmp_path)
        resp = server.handle_message(
            rpc(
                "initialize",
                {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "claude-code", "version": "1.0"},
                },
            )
        )
        assert resp["id"] == 1
        assert resp["result"]["protocolVersion"] == "2025-03-26"  # echoed back
        assert resp["result"]["serverInfo"]["name"] == "fixdoc"
        assert "tools" in resp["result"]["capabilities"]

    def test_initialized_notification_gets_no_response(self, tmp_path):
        server = build_server(tmp_path)
        assert server.handle_message(rpc("notifications/initialized", msg_id=None)) is None

    def test_tools_list(self, tmp_path):
        server = build_server(tmp_path)
        tools = server.handle_message(rpc("tools/list"))["result"]["tools"]
        names = {t["name"] for t in tools}
        assert names == {"search_fixes", "record_fix", "confirm_fix", "get_fix"}
        for tool in tools:
            assert len(tool["description"]) > 40  # descriptions are product surface
        record = next(t for t in tools if t["name"] == "record_fix")
        assert set(record["inputSchema"]["required"]) == {
            "title",
            "symptom",
            "root_cause",
            "fix",
            "verification",
        }

    def test_unknown_method_is_json_rpc_error(self, tmp_path):
        server = build_server(tmp_path)
        resp = server.handle_message(rpc("resources/list"))
        assert resp["error"]["code"] == -32601

    def test_run_loop_speaks_newline_delimited_json(self, tmp_path):
        server = build_server(tmp_path)
        stdin = io.StringIO(
            json.dumps(rpc("initialize", {"protocolVersion": "x"}))
            + "\n"
            + json.dumps(rpc("tools/list", msg_id=2))
            + "\n"
        )
        stdout = io.StringIO()
        server.run(stdin=stdin, stdout=stdout)
        lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
        assert lines[0]["id"] == 1 and "serverInfo" in lines[0]["result"]
        assert lines[1]["id"] == 2 and "tools" in lines[1]["result"]


class TestSearchFixes:
    def test_returns_validated_with_provenance(self, tmp_path):
        server = build_server(tmp_path, [make_fix("fx_00000001", "Pods Pending", occurrences=3)])
        text, is_error = call_tool(server, "search_fixes", {"query": "pods pending"})
        assert not is_error
        assert "fx_00000001" in text
        assert "validated" in text
        assert "resolved 3 prior incidents" in text
        assert "Restart the autoscaler." in text  # returned body, not just metadata

    def test_quarantined_hidden_by_default_and_named_when_nothing_matches(self, tmp_path):
        server = build_server(
            tmp_path, [make_fix("fx_00000002", "Unreviewed", status="quarantined")]
        )
        text, is_error = call_tool(server, "search_fixes", {"query": "pods pending"})
        assert not is_error  # an empty result is a real answer, not an error
        assert "No validated" in text
        assert "fx_00000002" in text  # candidate id named, body withheld
        assert "Restart the autoscaler." not in text

    def test_include_quarantined_opts_in(self, tmp_path):
        server = build_server(
            tmp_path, [make_fix("fx_00000002", "Unreviewed", status="quarantined")]
        )
        text, _ = call_tool(
            server, "search_fixes", {"query": "pods pending", "include_quarantined": True}
        )
        assert "Restart the autoscaler." in text

    def test_retrieval_served_event_logged(self, tmp_path):
        server = build_server(tmp_path, [make_fix("fx_00000001", "Pods Pending")])
        call_tool(server, "search_fixes", {"query": "pods pending"})
        event = read_events(server.index.index_dir)[-1]
        assert event["type"] == "retrieval_served"
        assert event["payload"]["query"] == "pods pending"
        assert event["payload"]["served"][0]["id"] == "fx_00000001"

    def test_files_added_after_startup_are_found(self, tmp_path):
        server = build_server(tmp_path, [make_fix("fx_00000001", "First")])
        late = make_fix("fx_00000002", "Late arrival", occurrences=1)
        (tmp_path / "knowledge" / "platform" / f"{late.id}.md").write_text(late.to_markdown())
        text, _ = call_tool(server, "search_fixes", {"query": "late arrival"})
        assert "fx_00000002" in text  # index re-syncs per search


VALID_RECORD = {
    "title": "AKS pods stuck Pending after nodepool scale-up",
    "symptom": "Pods stay Pending though nodes joined Ready.",
    "root_cause": "Azure CNI reserves max_pods IPs per node; subnet exhausted.",
    "fix": "Add a subnet or lower max_pods on the new nodepool.",
    "verification": "Pods schedule; availableIpAddressCount is positive.",
    "resource_type": "kubernetes/aks",
}


class TestRecordFix:
    def test_creates_quarantined_entry(self, tmp_path):
        server = build_server(tmp_path)
        text, is_error = call_tool(server, "record_fix", VALID_RECORD)
        assert not is_error
        assert "quarantined" in text
        files = list((tmp_path / "knowledge").rglob("fx_*.md"))
        assert len(files) == 1
        assert Entry.from_markdown(files[0].read_text()).status == "quarantined"

    def test_duplicate_narrates_confirmation(self, tmp_path):
        server = build_server(tmp_path)
        call_tool(server, "record_fix", VALID_RECORD)
        text, is_error = call_tool(server, "record_fix", VALID_RECORD)
        assert not is_error
        assert "already" in text.lower() or "confirm" in text.lower()
        assert len(list((tmp_path / "knowledge").rglob("fx_*.md"))) == 1

    def test_empty_field_error_names_whats_missing(self, tmp_path):
        server = build_server(tmp_path)
        args = dict(VALID_RECORD, root_cause="")
        text, is_error = call_tool(server, "record_fix", args)
        assert is_error
        assert "Root cause" in text

    def test_oversized_field_rejected(self, tmp_path):
        server = build_server(tmp_path)
        args = dict(VALID_RECORD, symptom="x" * 9000)
        text, is_error = call_tool(server, "record_fix", args)
        assert is_error
        assert "8000" in text

    def test_write_rate_limit(self, tmp_path):
        server = build_server(tmp_path)
        for i in range(10):
            _, is_error = call_tool(
                server,
                "record_fix",
                dict(
                    VALID_RECORD, title=f"unique title {i}", symptom=f"unique symptom {i} {'y' * i}"
                ),
            )
        text, is_error = call_tool(server, "record_fix", dict(VALID_RECORD, title="one more"))
        assert is_error
        assert "rate limit" in text.lower()


class TestConfirmFix:
    def test_confirm_bumps_and_narrates(self, tmp_path):
        server = build_server(tmp_path, [make_fix("fx_00000001", "Pods Pending")])
        text, is_error = call_tool(server, "confirm_fix", {"fix_id": "fx_00000001"})
        assert not is_error
        assert "1" in text
        assert read_events(server.index.index_dir)[-1]["type"] == "entry_confirmed"

    def test_unknown_id_is_helpful_error(self, tmp_path):
        server = build_server(tmp_path)
        text, is_error = call_tool(server, "confirm_fix", {"fix_id": "fx_deadbeef"})
        assert is_error
        assert "fx_deadbeef" in text


class TestGetFix:
    def test_returns_full_entry(self, tmp_path):
        server = build_server(tmp_path, [make_fix("fx_00000001", "Pods Pending", occurrences=2)])
        text, is_error = call_tool(server, "get_fix", {"fix_id": "fx_00000001"})
        assert not is_error
        assert "Pods Pending" in text
        assert "Root cause" in text and "Autoscaler race." in text
        assert "validated" in text

    def test_unknown_id_errors(self, tmp_path):
        server = build_server(tmp_path)
        text, is_error = call_tool(server, "get_fix", {"fix_id": "fx_deadbeef"})
        assert is_error
