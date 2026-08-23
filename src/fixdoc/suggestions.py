"""Similar fix suggestions for fixdoc.
This module finds fixes similar to a new error before creating duplicates.
"""

import re
from typing import Optional

import click

from .config import SuggestionWeights
from .models import Fix
from .storage import FixRepository

# Patterns to extract resource addresses (type.name)
_ADDRESS_RE = re.compile(
    r"((?:aws|azurerm|google)_\w+\.\w+)",
    re.IGNORECASE,
)

# Patterns to extract resource types only
_RESOURCE_TYPE_RE = re.compile(
    r"((?:aws|azurerm|google)_\w+)",
    re.IGNORECASE,
)


def find_similar_fixes(
    repo: FixRepository,
    error_text: str,
    tags: Optional[str] = None,
    limit: int = 5,
    weights: Optional[SuggestionWeights] = None,
    min_score: int = 15,
    resource_address: Optional[str] = None,
    error_id: Optional[str] = None,
) -> list[Fix]:
    """
    Find fixes similar to the given error text and tags.

    Args:
        repo: Fix repository to search.
        error_text: The error text to match against.
        tags: Comma-separated tags to filter by.
        limit: Max number of results to return.
        weights: Custom scoring weights.
        min_score: Minimum score threshold for results.
        resource_address: Parsed resource address (e.g. aws_instance.web).
        error_id: Error ID to boost fixes captured for this exact error.
    """
    all_fixes = repo.list_all()
    if not all_fixes:
        return []

    if weights is None:
        weights = SuggestionWeights()

    scored_fixes: list[tuple[Fix, int]] = []

    # Extract keywords from error text
    error_keywords = _extract_keywords(error_text)
    error_lower = error_text.lower()

    # Parse input tags
    input_tags = set()
    if tags:
        input_tags = {t.strip().lower() for t in tags.split(",") if t.strip()}

    # Extract resource addresses and types from error text
    error_addresses = set()
    if resource_address:
        error_addresses.add(resource_address.lower())
    error_addresses.update(a.lower() for a in _ADDRESS_RE.findall(error_lower))

    error_resource_types = _extract_resource_types(error_lower)

    # Extract error codes from the error text
    error_codes = _extract_error_codes(error_lower)

    for fix in all_fixes:
        score = 0

        # 1. Resource address matching (highest weight)
        if error_addresses:
            fix_searchable = " ".join(
                filter(None, [fix.issue, fix.error_excerpt, fix.tags])
            ).lower()
            for addr in error_addresses:
                if addr in fix_searchable:
                    score += weights.resource_address_weight

        # 2. Error code matching
        if fix.error_excerpt:
            excerpt_lower = fix.error_excerpt.lower()
            for code in error_codes:
                if code in excerpt_lower:
                    score += weights.error_code_weight
        if fix.issue:
            issue_lower = fix.issue.lower()
            for code in error_codes:
                if code in issue_lower:
                    score += weights.error_code_weight

        # 3. Error message similarity (token overlap)
        if fix.error_excerpt or fix.issue:
            fix_text = " ".join(filter(None, [fix.error_excerpt, fix.issue]))
            fix_keywords = _extract_keywords(fix_text)
            if error_keywords and fix_keywords:
                overlap = error_keywords & fix_keywords
                union = error_keywords | fix_keywords
                if union:
                    ratio = len(overlap) / len(union)
                    score += int(ratio * weights.error_similarity_weight)

        # 4. Resource type matching
        for rt in error_resource_types:
            if fix.tags and rt in fix.tags.lower():
                score += weights.resource_type_weight

        # 5. Tag matching
        if fix.tags:
            fix_tags = {t.strip().lower() for t in fix.tags.split(",") if t.strip()}
            # Filter out generic resource type tags for tag scoring
            non_type_input = {t for t in input_tags if not _RESOURCE_TYPE_RE.fullmatch(t)}
            non_type_fix = {t for t in fix_tags if not _RESOURCE_TYPE_RE.fullmatch(t)}
            tag_overlap = non_type_input & non_type_fix
            score += len(tag_overlap) * weights.tag_weight

        # 6. Keyword matching in issue
        issue_keywords = _extract_keywords(fix.issue)
        keyword_overlap = error_keywords & issue_keywords
        score += len(keyword_overlap) * weights.issue_keyword_weight

        # 7. Keyword matching in resolution
        resolution_keywords = _extract_keywords(fix.resolution)
        resolution_overlap = error_keywords & resolution_keywords
        score += len(resolution_overlap) * weights.resolution_keyword_weight

        # 8. Source error ID match — this fix was captured for this exact error
        if error_id and fix.source_error_ids and error_id in fix.source_error_ids:
            score += 30

        # 9. Effectiveness boost — proven fixes rank higher
        if fix.applied_count >= 2:
            rate = fix.effectiveness_rate
            if rate is not None and rate >= 0.75:
                score += 10
            elif rate is not None and rate < 0.25 and fix.applied_count >= 3:
                score -= 5

        if score >= min_score:
            scored_fixes.append((fix, score))

    # Sort by score descending
    scored_fixes.sort(key=lambda x: x[1], reverse=True)

    # Dedup clustering: group by resource_type + error_code + top tokens
    deduped = _dedup_cluster(scored_fixes)

    return [fix for fix, score in deduped[:limit]]


def _dedup_cluster(
    scored_fixes: list[tuple[Fix, int]],
) -> list[tuple[Fix, int]]:
    """Deduplicate similar fixes by clustering on resource_type + error_code + top tokens.

    Returns only the highest-scored fix per cluster.
    """
    seen_clusters: dict[str, tuple[Fix, int]] = {}

    for fix, score in scored_fixes:
        # Build cluster key
        fix_types = _extract_resource_types(" ".join(filter(None, [fix.tags, fix.error_excerpt])))
        fix_codes = _extract_error_codes((fix.error_excerpt or "").lower())
        fix_keywords = sorted(_extract_keywords(fix.issue))[:3]

        cluster_key = (
            "|".join(sorted(fix_types))
            + "||"
            + "|".join(sorted(fix_codes))
            + "||"
            + "|".join(fix_keywords)
        )

        if cluster_key not in seen_clusters or score > seen_clusters[cluster_key][1]:
            seen_clusters[cluster_key] = (fix, score)

    # Return in score order
    result = list(seen_clusters.values())
    result.sort(key=lambda x: x[1], reverse=True)
    return result


def prompt_similar_fixes(
    repo: FixRepository,
    error_text: str,
    tags: Optional[str] = None,
    limit: int = 5,
) -> Optional[Fix]:
    """
    Find similar fixes and prompt user to select one or create new.
    """
    similar = find_similar_fixes(repo, error_text, tags, limit=limit)

    if not similar:
        return None

    click.echo("\n Found similar fixes:\n")

    for i, fix in enumerate(similar, 1):
        tags_str = f" [{fix.tags}]" if fix.tags else ""
        issue_preview = fix.issue[:60] + "..." if len(fix.issue) > 60 else fix.issue
        click.echo(f"  [{i}] {fix.id[:8]}{tags_str}")
        click.echo(f"      {issue_preview}")
        resolution_preview = (
            fix.resolution[:60] + "..." if len(fix.resolution) > 60 else fix.resolution
        )
        click.echo(f"      -> {resolution_preview}")
        click.echo()

    click.echo(f"  [n] Create new fix\n")

    choice = click.prompt(
        "Use existing fix?",
        default="n",
        show_default=True,
    )

    if choice.lower() == "n":
        return None

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(similar):
            return similar[idx]
    except ValueError:
        pass

    click.echo("Invalid choice, creating new fix.")
    return None


def _extract_keywords(text: str) -> set[str]:
    """Extract meaningful keywords from text."""
    if not text:
        return set()

    # Common stop words to ignore
    stop_words = {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "must",
        "shall",
        "can",
        "need",
        "dare",
        "ought",
        "used",
        "to",
        "of",
        "in",
        "for",
        "on",
        "with",
        "at",
        "by",
        "from",
        "as",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "between",
        "under",
        "again",
        "further",
        "then",
        "once",
        "here",
        "there",
        "when",
        "where",
        "why",
        "how",
        "all",
        "each",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "no",
        "nor",
        "not",
        "only",
        "own",
        "same",
        "so",
        "than",
        "too",
        "very",
        "just",
        "and",
        "but",
        "if",
        "or",
        "because",
        "until",
        "while",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "error",
        "failed",
        "failure",
    }

    # Extract words, lowercase, filter
    words = set()
    for word in text.lower().split():
        # Clean punctuation
        word = "".join(c for c in word if c.isalnum() or c == "_")
        if word and len(word) > 2 and word not in stop_words:
            words.add(word)

    return words


def _extract_error_codes(text: str) -> set[str]:
    """Extract common error codes from text."""
    codes = set()

    # Azure style: Code: "AuthorizationFailed"
    azure_codes = re.findall(r'code[:\s]*["\']?(\w+)["\']?', text, re.IGNORECASE)
    codes.update(c.lower() for c in azure_codes)

    # HTTP status codes
    http_codes = re.findall(r"\b(4\d{2}|5\d{2})\b", text)
    codes.update(http_codes)

    # Terraform Error: <ErrorName>
    tf_errors = re.findall(r"error:\s+(\w+(?:\.\w+)*)", text, re.IGNORECASE)
    codes.update(e.lower() for e in tf_errors)

    # XML-ish cloud errors: <Code>ErrorName</Code>
    xml_codes = re.findall(r"<code>(\w+)</code>", text, re.IGNORECASE)
    codes.update(c.lower() for c in xml_codes)

    # StatusCode patterns
    status_codes = re.findall(r"statuscode[=:]?\s*(\d+)", text, re.IGNORECASE)
    codes.update(status_codes)

    # AWS SDK: api error ErrorName
    api_errors = re.findall(r"api error (\w+(?:\.\w+)*)", text, re.IGNORECASE)
    codes.update(e.lower() for e in api_errors)

    # Common error names
    error_patterns = [
        "accessdenied",
        "authorizationfailed",
        "forbidden",
        "unauthorized",
        "notfound",
        "timeout",
        "connectionrefused",
        "permissiondenied",
        "invalidrequest",
        "quotaexceeded",
        "throttled",
        "conflict",
    ]
    for pattern in error_patterns:
        if pattern in text:
            codes.add(pattern)

    return codes


def _extract_resource_types(text: str) -> set[str]:
    """Extract cloud resource types from text."""
    types = set()

    # AWS resource types
    aws_types = re.findall(r"(aws_\w+)", text, re.IGNORECASE)
    types.update(t.lower() for t in aws_types)

    # Azure resource types
    azure_types = re.findall(r"(azurerm_\w+)", text, re.IGNORECASE)
    types.update(t.lower() for t in azure_types)

    # GCP resource types
    gcp_types = re.findall(r"(google_\w+)", text, re.IGNORECASE)
    types.update(t.lower() for t in gcp_types)

    return types
