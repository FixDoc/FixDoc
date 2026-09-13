# Slack Importer Design

> Decisions hashed out 2026-08-31. Batch, model-extracted, confidence-gated.

## The decision space, and what was chosen

Reading Slack requires a Slack app in some form; the design choice is burden.
Four options were weighed:

| Option | Install burden | Backfill | In-flow capture | Infra |
|---|---|---|---|---|
| Emoji convention (legacy) | token | future threads only | no | none |
| **Model-extracted batch (CHOSEN)** | token, 5-min manifest | all history | no | none |
| @fixdoc mention bot | full app + approval | no | yes | always-on |
| Agent-side via record_fix | none | manual | yes | none |

- **The emoji convention is dead.** It demanded team-wide behavior change,
  applied retroactively to unmarked history. It was the pre-agent workaround
  for not having a cheap model that reads threads.
- **Model extraction is safe because quarantine exists.** A wrong extraction
  is a quarantined entry a reviewer rejects — the same trust posture as
  record_fix from an agent. Provenance (thread permalink) makes review cheap.
- **The @mention bot is deferred to the hosted tier**: it needs an always-on
  event listener, the first FixDoc piece that cannot be a CLI. Interim
  on-demand capture: paste the thread to your agent, `record_fix` does the
  rest.

## Confidence gate (user decision)

- The extractor scores every thread. `confidence` means: the probability a
  reviewer will accept this entry as accurate.
- **The rubric is user-defined.** `.fixdoc/config.yaml` may carry
  `import.confidence_rubric` in the team's own words; it is injected into the
  extraction prompt verbatim, so the model scores by what THE USER means by
  confidence. A built-in anchor scale is the default.
- Below `import.confidence_threshold` (default 0.6): not written; counted and
  reported with permalinks (nothing silently disappears; rerun with a lower
  `--threshold` to include).
- At/above threshold: auto-imported to `status: quarantined`, score stored in
  the entry's `confidence` field (visible to reviewers and to retrieval's
  trust term).
- The `--limit` cap keeps the highest-confidence extractions.

## Config

```yaml
import:
  model: claude-haiku-4-5        # user decision: cheap tier is right for import
  api_key_env: ANTHROPIC_API_KEY # NAME of the env var; the key never enters config
  base_url: https://...          # optional Anthropic-compatible endpoint
  confidence_threshold: 0.6
  confidence_rubric: |           # optional, the team's own definition
    0.9+ only when the thread explicitly confirms the fix worked...
```

Slack access: bot token via SLACK_TOKEN (read-only scopes: channels:history,
channels:read, users:read), app created from the shipped manifest
(docs/slack-app-manifest.yaml), bot invited to chosen channels.

## Pipeline

1. Fetch threads (resurrected legacy plumbing: pagination, 429 retry, mrkdwn
   cleanup, user-name cache) for each channel since `--since` days.
2. Deterministic prefilter (cost control only): threads with at least one
   reply.
3. **Redact before the model sees anything** — secrets must not reach the
   API either.
4. Model extraction per thread: resolved-with-stated-remediation? If yes:
   title, symptom, causal root cause, fix, verification, confidence. If a
   field is not stated in the thread, it is null — placeholders, never
   invention.
5. Gate on threshold; rank survivors by confidence; cap; write quarantined
   entries typed via classify_memory_type, id = hash(channel + thread_ts)
   (idempotent re-runs), Notes carry channel, permalink, and date.

## Costs and privacy

A thread is 1-3k tokens; a 200-thread backfill on haiku is well under a
dollar. Thread content leaves Slack for the model API during import (only
chosen channels; base_url for gateway routing; the query path stays
zero-network). The importer is model-mandatory: no key, no Slack import.
