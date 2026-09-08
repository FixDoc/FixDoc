"""fixdoc eval — the judgment test suite (development tooling, not a user feature).

An `eval` group so dedup and extraction evals can slot in as later steps;
today it holds exactly one subcommand.
"""

from pathlib import Path

import click

from fixdoc.evals.retrieval import DEFAULT_K, run_retrieval_eval


def _embedder():
    # Real embeddings only: fake vectors would measure nothing. Seam for tests.
    from fixdoc.core.embedding import get_embedder

    return get_embedder()


@click.group("eval")
def eval_group():
    """Run the judgment evals (development tooling)."""


@eval_group.command("retrieval")
@click.option(
    "--cases",
    "cases_path",
    default="evals/retrieval/cases.yaml",
    show_default=True,
    help="YAML file of labeled query cases.",
)
@click.option(
    "--store",
    "store_dir",
    default="evals/retrieval/store",
    show_default=True,
    help="Store root containing knowledge/ to evaluate against.",
)
@click.option("--k", default=DEFAULT_K, show_default=True, help="Top-K window for recall.")
def eval_retrieval(cases_path, store_dir, k):
    """Score labeled queries against the real retrieval engine."""
    if not Path(cases_path).exists():
        raise click.ClickException(f"no cases file at {cases_path}")
    if not (Path(store_dir) / "knowledge").is_dir():
        raise click.ClickException(f"no knowledge/ under {store_dir}")
    try:
        embed_fn = _embedder()
    except RuntimeError as exc:
        raise click.ClickException(str(exc))

    report = run_retrieval_eval(cases_path, store_dir, embed_fn, k=k)
    click.echo(f"retrieval eval: {report.cases} cases")
    click.echo(f"  Recall@{k}: {report.hits}/{report.cases} ({report.recall:.2f})")
    click.echo(
        f"  Trap rate: {report.traps_fired}/{report.trap_cases} "
        f"({report.trap_rate:.2f})   [target: zero]"
    )
    if report.acceptable_only:
        click.echo(
            f"  {report.acceptable_only} case(s) saved only by an "
            "'acceptable' id — worth eyeballing"
        )
    for miss in report.misses:
        click.echo(
            f"  MISS  {miss['query']!r}: expected {miss['expected']}, " f"top-{k} was {miss['top']}"
        )
    for trap in report.trap_hits:
        click.echo(f"  TRAP  {trap['query']!r}: forbidden {trap['returned']} surfaced")
