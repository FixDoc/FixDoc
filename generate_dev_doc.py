"""Generate FixDoc Developer Documentation PDF — 2-page black design."""

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Preformatted,
    HRFlowable, Table, TableStyle,
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER

OUTPUT = "FixDoc_Developer_Documentation.pdf"

# ── Palette (all black) ──────────────────────────────────────────────────────
BLACK   = colors.HexColor("#000000")
DARK    = colors.HexColor("#111111")
MID     = colors.HexColor("#222222")
RULE    = colors.HexColor("#333333")
DIM     = colors.HexColor("#888888")
WHITE   = colors.HexColor("#FFFFFF")
OFF     = colors.HexColor("#CCCCCC")
ACCENT  = colors.HexColor("#FFFFFF")

# ── Styles ───────────────────────────────────────────────────────────────────
H1 = ParagraphStyle("H1", fontSize=11, leading=14, textColor=WHITE,
                    fontName="Helvetica-Bold", spaceBefore=10, spaceAfter=2)
H2 = ParagraphStyle("H2", fontSize=8.5, leading=12, textColor=OFF,
                    fontName="Helvetica-Bold", spaceBefore=7, spaceAfter=2)
BODY = ParagraphStyle("BODY", fontSize=7.5, leading=11, textColor=OFF,
                      fontName="Helvetica", spaceAfter=2)
CODE = ParagraphStyle("CODE", fontSize=7, leading=10.5, textColor=colors.HexColor("#A3E635"),
                      fontName="Courier", backColor=MID,
                      leftIndent=6, rightIndent=6,
                      borderPadding=(4, 6, 4, 6), spaceBefore=3, spaceAfter=3)
DIM_S = ParagraphStyle("DIM", fontSize=7, leading=10, textColor=DIM,
                       fontName="Helvetica", spaceAfter=1)

def h1(t):  return Paragraph(t, H1)
def h2(t):  return Paragraph(t, H2)
def body(t): return Paragraph(t, BODY)
def dim(t): return Paragraph(t, DIM_S)
def code(t): return Preformatted(t, CODE)
def sp(n=4): return Spacer(1, n)
def rule():
    return HRFlowable(width="100%", thickness=0.5, color=RULE,
                      spaceBefore=2, spaceAfter=4)

def tbl(data, widths):
    rows = [[Paragraph(str(c), BODY) for c in row] for row in data]
    t = Table(rows, colWidths=widths)
    t.setStyle(TableStyle([
        ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("TEXTCOLOR",     (0, 0), (-1, 0),  WHITE),
        ("TEXTCOLOR",     (0, 1), (-1, -1), OFF),
        ("BACKGROUND",    (0, 0), (-1, 0),  MID),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [DARK, colors.HexColor("#181818")]),
        ("FONTSIZE",      (0, 0), (-1, -1), 7),
        ("LEADING",       (0, 0), (-1, -1), 10),
        ("GRID",          (0, 0), (-1, -1), 0.3, RULE),
        ("LEFTPADDING",   (0, 0), (-1, -1), 5),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
    ]))
    return t

def on_page(canvas, doc):
    W, H = letter
    # Full black background
    canvas.setFillColor(BLACK)
    canvas.rect(0, 0, W, H, fill=1, stroke=0)
    # Top bar
    canvas.setFillColor(MID)
    canvas.rect(0, H - 22, W, 22, fill=1, stroke=0)
    canvas.setFillColor(WHITE)
    canvas.setFont("Helvetica-Bold", 8)
    canvas.drawString(0.5 * inch, H - 14, "FixDoc")
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(DIM)
    canvas.drawString(0.5 * inch + 36, H - 14, "Developer Documentation")
    canvas.drawRightString(W - 0.5 * inch, H - 14, f"Page {doc.page} / 2")
    # Bottom bar
    canvas.setFillColor(MID)
    canvas.rect(0, 0, W, 16, fill=1, stroke=0)
    canvas.setFillColor(DIM)
    canvas.setFont("Helvetica", 7)
    canvas.drawString(0.5 * inch, 5, "v0.0.4  ·  Python 3.9+  ·  MIT License  ·  github.com/fiyiogunkoya/fixdoc")

def main():
    doc = SimpleDocTemplate(
        OUTPUT,
        pagesize=letter,
        leftMargin=0.5 * inch,
        rightMargin=0.5 * inch,
        topMargin=0.42 * inch,
        bottomMargin=0.32 * inch,
        title="FixDoc Developer Documentation",
    )

    W = 7.5 * inch   # usable width
    COL = (W - 0.15 * inch) / 2  # two-column width

    story = []

    # ── Title ─────────────────────────────────────────────────────────────────
    title_row = Table(
        [[
            Paragraph("<font size=18><b>FixDoc</b></font>", ParagraphStyle(
                "T", fontSize=18, fontName="Helvetica-Bold", textColor=WHITE, leading=22)),
            Paragraph(
                "CLI tool for cloud engineers to capture, search, and share "
                "infrastructure fixes from Terraform and Kubernetes error output.",
                ParagraphStyle("TS", fontSize=7.5, fontName="Helvetica",
                               textColor=DIM, leading=11, alignment=TA_LEFT)),
        ]],
        colWidths=[1.4 * inch, W - 1.4 * inch],
    )
    title_row.setStyle(TableStyle([
        ("VALIGN",  (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING",   (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 0),
    ]))
    story.append(title_row)
    story.append(HRFlowable(width="100%", thickness=1, color=WHITE, spaceBefore=4, spaceAfter=6))

    # ── Two-column layout helper ──────────────────────────────────────────────
    def two_col(left, right):
        t = Table([[left, right]], colWidths=[COL, COL],
                  hAlign="LEFT", vAlign="TOP")
        t.setStyle(TableStyle([
            ("VALIGN",       (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING",  (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (0, 0), 8),
            ("RIGHTPADDING", (1, 0), (1, 0), 0),
            ("TOPPADDING",   (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 0),
        ]))
        return t

    # ══ LEFT COLUMN ══════════════════════════════════════════════════════════
    from reportlab.platypus import KeepInFrame

    left = []

    # Install
    left += [h1("Installation"), rule()]
    left += [body("<b>pipx</b> (recommended — isolated, auto-adds to PATH):")]
    left += [code("pipx install fixdoc")]
    left += [body("<b>pip:</b>")]
    left += [code("pip install fixdoc")]
    left += [body("<b>Development:</b>")]
    left += [code(
        "git clone https://github.com/fiyiogunkoya/fixdoc\n"
        "cd fixdoc\n"
        "python3 -m venv .venv && source .venv/bin/activate\n"
        "pip install -e '.[dev]'"
    )]
    left += [dim("Requires Python 3.9+. Runtime deps: click, pyyaml.")]

    # Quick start
    left += [sp(), h1("Quick Start"), rule()]
    left += [code(
        "# Interactive tour — no cloud account needed\n"
        "fixdoc demo tour\n"
        "\n"
        "# Seed sample fixes then browse\n"
        "fixdoc demo seed && fixdoc list\n"
        "\n"
        "# Wrap a command — auto-captures on failure\n"
        "fixdoc watch -- terraform apply\n"
        "\n"
        "# Or pipe error output\n"
        "terraform apply 2>&1 | fixdoc capture\n"
        "\n"
        "# Search your fix history\n"
        "fixdoc search 'RDS connection timeout'\n"
        "\n"
        "# Analyze a plan before apply\n"
        "terraform show -json plan.tfplan > plan.json\n"
        "fixdoc analyze plan.json"
    )]

    # Data store
    left += [sp(), h1("Data Store"), rule()]
    left += [code(
        "~/.fixdoc/\n"
        "├── fixes.json     # canonical database\n"
        "├── config.yaml    # user settings\n"
        "└── docs/<uuid>.md # one markdown file per fix"
    )]
    left += [dim("Set FIXDOC_HOME to override ~/.fixdoc.")]

    # ══ RIGHT COLUMN ═════════════════════════════════════════════════════════
    right = []

    # Commands
    right += [h1("Commands"), rule()]
    right += [tbl(
        [
            ["Command", "What it does"],
            ["fixdoc capture [-t tags]",           "Pipe error output in; prompts for resolution"],
            ["fixdoc watch -- <cmd>",               "Wraps command; captures errors on failure"],
            ["fixdoc capture -q 'issue | fix'",     "Quick one-liner capture"],
            ["fixdoc search <query>",               "Full-text search across all fix fields"],
            ["fixdoc show <id>",                    "View a single fix in full detail"],
            ["fixdoc list [--limit N]",             "All fixes, most recent first"],
            ["fixdoc stats",                        "Tag distribution and totals"],
            ["fixdoc edit <id> [-I]",               "Update fields; -I for interactive mode"],
            ["fixdoc delete <id>",                  "Remove a fix permanently"],
            ["fixdoc analyze plan.json",            "Blast radius + history match before apply"],
            ["fixdoc blast-radius plan.json",       "Standalone risk score"],
            ["fixdoc pending",                      "List deferred errors (stored at git root)"],
            ["fixdoc pending capture <id|#>",       "Capture a deferred error by ID or number"],
            ["fixdoc sync init <url>",              "Connect to a shared git fix repo"],
            ["fixdoc sync push / pull",             "Share or receive fixes from teammates"],
            ["fixdoc demo seed / tour",             "Seed sample data or run guided tour"],
        ],
        [2.3 * inch, COL - 2.3 * inch],
    )]

    # Watch flags
    right += [sp(), h1("Key Flags"), rule()]
    right += [tbl(
        [
            ["Flag", "Command", "Effect"],
            ["--tags / -t",   "capture, watch", "Pre-set tags on captured fixes"],
            ["--no-prompt",   "watch",           "Auto-capture all errors silently"],
            ["--exit-on",     "analyze, blast-radius", "Exit 1 if severity ≥ threshold (low/medium/high/critical)"],
            ["--format json", "analyze, blast-radius", "Machine-readable output for CI"],
            ["--limit N",     "search, list",    "Cap result count"],
            ["-I",            "edit",            "Interactive re-prompt all fields"],
            ["-y",            "delete",          "Skip confirmation prompt"],
        ],
        [0.85 * inch, 1.1 * inch, COL - 1.95 * inch],
    )]

    # Testing
    right += [sp(), h1("Testing"), rule()]
    right += [code(
        "python3 -m pytest                    # 475 tests\n"
        "python3 -m pytest tests/test_models.py\n"
        "python3 -m pytest --cov=fixdoc tests/\n"
        "bash scenarios/run_all.sh            # 17 e2e scenarios (needs LocalStack)"
    )]
    right += [tbl(
        [
            ["Pattern", "Rule"],
            ["Isolated repos",    "Use tmp_path fixture for FixRepository — no global state"],
            ["Module patching",   "importlib.import_module('fixdoc.commands.watch') — __init__.py shadows names"],
            ["Subprocess patch",  "patch.object(mod.subprocess, 'Popen') — not the global"],
            ["JSON CLI output",   "CliRunner(mix_stderr=False) to separate stderr from stdout"],
            ["Type hints",        "Use Optional[str], not str | None — project targets Python 3.9"],
        ],
        [1.1 * inch, COL - 1.1 * inch],
    )]

    # Architecture footnote
    right += [sp(), h1("Architecture"), rule()]
    right += [body(
        "<b>Entry:</b> fix.py → cli.py → command modules.  "
        "<b>Parsers:</b> TerraformParser / KubernetesParser / generic fallback, routed by "
        "detect_and_parse().  "
        "<b>Storage:</b> FixRepository writes fixes.json + docs/&lt;id&gt;.md on every save.  "
        "<b>Blast radius:</b> BFS on terraform graph DOT + sigmoid score (0–100); "
        "thresholds: low &lt;25, medium 25–49, high 50–74, critical ≥75.  "
        "<b>Sync:</b> Markdown files are the git unit; rebuild_json_from_markdown() re-derives "
        "the full DB on pull."
    )]

    story.append(two_col(
        KeepInFrame(COL, 9.5 * inch, left, mode="shrink"),
        KeepInFrame(COL, 9.5 * inch, right, mode="shrink"),
    ))

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    print(f"PDF written → {OUTPUT}")

if __name__ == "__main__":
    main()
