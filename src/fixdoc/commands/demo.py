"""Demo commands for fixdoc — seed sample data and interactive tour."""

import json
import os
import tempfile

import click

from ..demo_data import (
    DEMO_TAG,
    SAMPLE_TERRAFORM_PLAN,
    TERRAFORM_AWS_ERROR,
    KUBERNETES_CRASHLOOP_ERROR,
    get_seed_fixes,
)
from ..storage import FixRepository
from .capture_handlers import handle_piped_input


@click.group()
@click.pass_context
def demo(ctx):
    """Demo utilities :seed sample fixes or take a guided tour."""
    pass


@demo.command()
@click.option(
    "--clean",
    is_flag=True,
    default=False,
    help="Remove previously seeded demo fixes before adding new ones.",
)
@click.pass_context
def seed(ctx, clean: bool):
    """Populate the fix database with realistic sample fixes."""
    repo = FixRepository(ctx.obj["base_path"])

    if clean:
        _clean_demo_fixes(repo)

    fixes = get_seed_fixes()
    for fix in fixes:
        repo.save(fix)

    click.echo(f"Seeded {len(fixes)} demo fixes:")
    for fix in fixes:
        click.echo(f"  {fix.summary()}")
    click.echo("\nRun `fixdoc list` to see them, or `fixdoc search S3` to try searching.")


def _clean_demo_fixes(repo: FixRepository) -> None:
    """Remove all fixes tagged with the demo tag."""
    all_fixes = repo.list_all()
    removed = 0
    for fix in all_fixes:
        if fix.tags and DEMO_TAG in [t.strip() for t in fix.tags.split(",")]:
            repo.delete(fix.id)
            removed += 1
    if removed:
        click.echo(f"Removed {removed} previous demo fix(es).")


@demo.command()
@click.pass_context
def tour(ctx):
    """Interactive guided walkthrough of fixdoc's capture flow."""
    config = ctx.obj.get("config")
    repo = FixRepository(ctx.obj["base_path"])

    # -- Welcome --
    click.echo("=" * 56)
    click.echo("  Welcome to the fixdoc tour!")
    click.echo("=" * 56)
    click.echo()
    click.echo(
        "This tour walks you through capturing real cloud errors\n"
        "using fixdoc's parser pipeline. You'll experience:\n"
        "\n"
        "  1. Capturing a Terraform (AWS) error\n"
        "  2. Capturing a Kubernetes CrashLoopBackOff\n"
        "  3. Searching your fix database\n"
        "  4. Viewing list & stats\n"
        "  5. Analyzing a Terraform plan\n"
        "\n"
        "Fixes captured during the tour are saved to your\n"
        "local database so you can explore them afterwards.\n"
    )
    click.pause("Press Enter to start...")
    click.echo()

    # -- Step 1: Terraform capture --
    click.echo("=" * 56)
    click.echo("  Step 1: Capture a Terraform AWS error")
    click.echo("=" * 56)
    click.echo()
    click.echo("Imagine you just ran `terraform apply` and got this:\n")
    click.echo(TERRAFORM_AWS_ERROR)
    click.echo(
        "fixdoc will auto-detect the error source, extract the\n"
        "provider, resource, and error code, then prompt you\n"
        "for the resolution.\n"
    )
    click.pause("Press Enter to start the capture flow...")
    click.echo()

    tf_fix = handle_piped_input(TERRAFORM_AWS_ERROR, None, config=config)
    if tf_fix:
        repo.save(tf_fix)
        click.echo(f"\nFix saved! (id: {tf_fix.id[:8]})")
    click.echo()

    # -- Step 2: Kubernetes capture --
    click.echo("=" * 56)
    click.echo("  Step 2: Capture a Kubernetes error")
    click.echo("=" * 56)
    click.echo()
    click.echo("Now imagine `kubectl get pods` shows this:\n")
    click.echo(KUBERNETES_CRASHLOOP_ERROR)
    click.echo(
        "fixdoc detects Kubernetes errors too — it extracts\n"
        "the pod name, namespace, restart count, and status.\n"
    )
    click.pause("Press Enter to start the capture flow...")
    click.echo()

    k8s_fix = handle_piped_input(KUBERNETES_CRASHLOOP_ERROR, None, config=config)
    if k8s_fix:
        repo.save(k8s_fix)
        click.echo(f"\nFix saved! (id: {k8s_fix.id[:8]})")
    click.echo()

    # -- Step 3: Search --
    click.echo("=" * 56)
    click.echo("  Step 3: Search your fixes")
    click.echo("=" * 56)
    click.echo()
    click.echo('Searching for "S3"...\n')

    results = repo.search("S3")
    if results:
        for fix in results:
            click.echo(f"  {fix.summary()}")
    else:
        click.echo("  (no results — the term may not match your entries)")
    click.echo()

    # -- Step 4: List & Stats --
    click.echo("=" * 56)
    click.echo("  Step 4: List & Stats")
    click.echo("=" * 56)
    click.echo()

    all_fixes = repo.list_all()
    all_fixes.sort(key=lambda f: f.created_at, reverse=True)
    click.echo(f"Total fixes: {len(all_fixes)}\n")
    for fix in all_fixes[:10]:
        click.echo(f"  {fix.summary()}")

    # Quick stats
    all_tags: list[str] = []
    for fix in all_fixes:
        if fix.tags:
            all_tags.extend([t.strip() for t in fix.tags.split(",")])
    tag_counts: dict[str, int] = {}
    for tag in all_tags:
        tag_counts[tag] = tag_counts.get(tag, 0) + 1

    if tag_counts:
        click.echo("\nTop tags:")
        sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
        for tag, count in sorted_tags[:5]:
            click.echo(f"  {tag}: {count}")
    click.echo()

    # -- Step 5: Analyze a Terraform plan --
    click.echo("=" * 56)
    click.echo("  Step 5: Analyze a Terraform plan")
    click.echo("=" * 56)
    click.echo()
    click.echo(
        "fixdoc can analyze a Terraform plan JSON and warn you\n"
        "about resources that have caused issues before.\n"
        "\n"
        "We'll analyze a sample plan with 3 resources:\n"
        "  - aws_s3_bucket.app_data\n"
        "  - aws_instance.web_server\n"
        "  - aws_security_group.web_sg\n"
    )
    click.pause("Press Enter to run the analysis...")
    click.echo()

    try:
        from .analyze import TerraformAnalyzer

        fd, plan_path = tempfile.mkstemp(suffix=".json", prefix="fixdoc_demo_plan_")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(SAMPLE_TERRAFORM_PLAN, f)

            from pathlib import Path

            analyzer = TerraformAnalyzer(repo=repo)
            output = analyzer.analyze_and_format(Path(plan_path))
            click.echo(output)
        finally:
            os.unlink(plan_path)
    except Exception as e:
        click.echo(f"  (analyze step skipped: {e})")
    click.echo()

    # -- Closing --
    click.echo("=" * 56)
    click.echo("  Tour complete!")
    click.echo("=" * 56)
    click.echo()
    click.echo(
        "Next steps:\n"
        "  fixdoc list              — see all your fixes\n"
        "  fixdoc search <term>     — search by keyword\n"
        "  fixdoc show <id>         — view a fix in detail\n"
        "  fixdoc capture           — capture a new fix interactively\n"
        "  fixdoc analyze plan.json — analyze a Terraform plan\n"
        "  fixdoc demo seed         — add more sample fixes\n"
        "  fixdoc sync init <url>   — sync with your team via git\n"
    )
