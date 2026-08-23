"""Attribute-first fix relevance matching for fixdoc.

Redesigns fix relevance around an attribute-first, domain-aware philosophy.
A fix surfaces only if it answers: same failure, same risky change, same
dependency exposure, same control-plane domain, or same historical pattern.
"Same resource type was mentioned somewhere" is never enough.

Signal hierarchy:
  Primary (can surface a fix on their own): error_code, address,
  changed_attribute, change_domain, attribute_category.
  Secondary boosters (added to primary, never standalone): recency,
  module_path, resource_family, type_tag, type_action.
  Suppressed (removed entirely): type_text, standalone type_tag,
  standalone type_action.
"""

import hashlib
import re
from datetime import datetime, timezone
from typing import Optional

from .models import Fix

# ---------------------------------------------------------------------------
# Change domains — tight operational buckets
# ---------------------------------------------------------------------------

CHANGE_DOMAINS = {
    "iam_trust_boundary": {
        "attributes": {
            "assume_role_policy",
            "policy",
            "inline_policy",
            "managed_policy_arns",
            "policy_arn",
            "role",
            "iam_role",
            "service_role",
            "execution_role_arn",
            "task_role_arn",
            "role_arn",
        },
        "resource_families": {
            "aws_iam",
            "azurerm_role",
            "google_project_iam",
            "google_service_account",
        },
        "score": 85,
        "risk_label": "IAM trust boundary",
    },
    "secret_access": {
        "attributes": {
            "secret",
            "secret_string",
            "secret_binary",
            "kms_key_id",
            "kms_key_arn",
            "ssm_parameter",
            "secret_id",
            "secret_version",
            "password",
            "master_password",
            "admin_password",
        },
        "resource_families": {
            "aws_secretsmanager",
            "aws_ssm_parameter",
            "aws_kms",
            "azurerm_key_vault",
            "google_secret_manager",
        },
        "score": 85,
        "risk_label": "secret / vault access",
    },
    "network_perimeter": {
        "attributes": {
            "ingress",
            "egress",
            "cidr_blocks",
            "cidr_block",
            "security_groups",
            "from_port",
            "to_port",
            "protocol",
            "source_security_group_id",
            "prefix_list_ids",
            "ipv6_cidr_blocks",
            "self",
        },
        "resource_families": {
            "aws_security_group",
            "aws_network_acl",
            "azurerm_network_security",
            "google_compute_firewall",
        },
        "score": 80,
        "risk_label": "network perimeter / firewall rules",
    },
    "network_routing": {
        "attributes": {
            "route",
            "route_table_id",
            "destination_cidr_block",
            "gateway_id",
            "nat_gateway_id",
            "transit_gateway_id",
            "vpc_peering_connection_id",
            "network_interface_id",
        },
        "resource_families": {
            "aws_route",
            "aws_route_table",
            "aws_vpc_peering",
            "aws_transit_gateway",
            "aws_nat_gateway",
            "azurerm_route",
            "google_compute_route",
        },
        "score": 80,
        "risk_label": "network routing / peering",
    },
    "rbac_binding": {
        "attributes": {
            "role_ref",
            "subjects",
            "role_binding",
            "cluster_role",
            "service_account_name",
            "scope_id",
            "principal_id",
            "role_definition_id",
        },
        "resource_families": {
            "kubernetes_role",
            "kubernetes_cluster_role",
            "azurerm_role_assignment",
            "azurerm_role_definition",
        },
        "score": 80,
        "risk_label": "RBAC / role binding",
    },
    "encryption_keying": {
        "attributes": {
            "kms_key_id",
            "encrypted",
            "kms_key_arn",
            "server_side_encryption",
            "sse_algorithm",
            "key_id",
            "key_arn",
            "encryption_configuration",
            "at_rest_encryption_enabled",
        },
        "resource_families": {
            "aws_kms",
            "azurerm_key_vault_key",
            "google_kms",
        },
        "score": 80,
        "risk_label": "encryption / key management",
    },
    "network_attachment": {
        "attributes": {
            "subnet_id",
            "subnet_ids",
            "vpc_id",
            "network_interface_id",
            "availability_zone",
            "associate_public_ip_address",
            "private_ip",
        },
        "resource_families": {
            "aws_subnet",
            "aws_vpc",
            "aws_network_interface",
            "aws_eni",
            "azurerm_subnet",
            "azurerm_virtual_network",
            "google_compute_subnetwork",
        },
        "score": 70,
        "risk_label": "network attachment",
    },
    "capacity_sizing": {
        "attributes": {
            "instance_type",
            "instance_class",
            "node_type",
            "desired_capacity",
            "max_size",
            "min_size",
            "allocated_storage",
            "storage_type",
            "iops",
            "memory_size",
            "timeout",
            "reserved_concurrent_executions",
        },
        "resource_families": {
            "aws_instance",
            "aws_db_instance",
            "aws_autoscaling",
            "aws_lambda",
            "aws_rds",
            "azurerm_virtual_machine",
            "google_compute_instance",
        },
        "score": 70,
        "risk_label": "resource sizing / capacity",
    },
}


# ---------------------------------------------------------------------------
# Helpers (moved from change_impact.py)
# ---------------------------------------------------------------------------

_ERROR_CODE_RE = re.compile(
    r'(?:api error |error:\s*|code[:\s]*["\']?)(\w+(?:\.\w+)*)',
    re.IGNORECASE,
)
_CAMEL_CASE_RE = re.compile(r"\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b")


def _extract_error_codes_from_text(text: str) -> set:
    """Extract error codes from fix text (issue, error_excerpt, tags)."""
    codes = set()
    for m in _ERROR_CODE_RE.finditer(text):
        codes.add(m.group(1).lower())
    for m in _CAMEL_CASE_RE.finditer(text):
        codes.add(m.group(1).lower())
    return codes


def _fix_matches_resource_type(fix: Fix, resource_type: str) -> bool:
    """Check if a fix is related to a resource type via tags or text."""
    rt_lower = resource_type.lower()
    if fix.tags:
        for tag in fix.tags.split(","):
            if tag.strip().lower() == rt_lower:
                return True
    pattern = re.compile(r"\b" + re.escape(rt_lower) + r"\b", re.IGNORECASE)
    searchable = " ".join(filter(None, [fix.issue, fix.resolution, fix.error_excerpt]))
    return bool(pattern.search(searchable))


def _extract_module_path(address: str) -> Optional[str]:
    """Extract module path prefix from a resource address."""
    parts = address.split(".")
    module_parts = []
    i = 0
    while i < len(parts):
        if parts[i] == "module" and i + 1 < len(parts):
            module_parts.append(f"module.{parts[i + 1]}")
            i += 2
        else:
            break
    return ".".join(module_parts) if module_parts else None


def _strip_index(address: str) -> str:
    """Strip indexed suffix: aws_sg.bulk[0] -> aws_sg.bulk."""
    idx = address.find("[")
    return address[:idx] if idx >= 0 else address


def _leaf_address(address: str) -> str:
    """Strip module prefix to get leaf resource address."""
    parts = address.split(".")
    i = len(parts) - 1
    while i >= 1:
        if not parts[i - 1].startswith("module"):
            return ".".join(parts[i - 1 :])
        i -= 1
    return address


def _resource_family(resource_type: str) -> Optional[str]:
    """Extract provider+service family from a resource type.

    e.g. aws_iam_role -> aws_iam, aws_s3_bucket -> aws_s3,
    azurerm_role_assignment -> azurerm_role
    """
    rt = resource_type.lower()
    parts = rt.split("_")
    if len(parts) >= 3:
        return "_".join(parts[:2])
    if len(parts) == 2:
        return parts[0]
    return None


def _issue_family(issue_text: str) -> str:
    """Compute a normalized fingerprint of the fix's issue text.

    Used for dedup clustering — prevents collapsing distinct problems
    that share the same resource type and attribute.
    """
    text = (issue_text or "").lower().strip()
    # Remove common noise words
    text = re.sub(r"\b(the|a|an|is|was|were|been|be|have|has|had|do|does|did)\b", "", text)
    # Remove standalone numbers and ordinal suffixes (attempt 0, attempt 1, etc.)
    text = re.sub(r"\b\d+\b", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Take first 80 chars for fingerprinting
    fragment = text[:80]
    return hashlib.md5(fragment.encode()).hexdigest()[:8]


# ---------------------------------------------------------------------------
# RelevanceMatcher
# ---------------------------------------------------------------------------


class RelevanceMatcher:
    """Attribute-first fix relevance matching."""

    def __init__(self, fixes, change_domains=None):
        """Initialize with a list of Fix objects.

        Args:
            fixes: list of Fix objects from the repository.
            change_domains: optional override of CHANGE_DOMAINS dict.
        """
        self.fixes = fixes
        self.domains = change_domains or CHANGE_DOMAINS

    def match(self, nodes, max_total=10):
        """Score and rank fixes against changed nodes.

        Returns list[dict] with fix fields, score, confidence, match_reason,
        matched_resources, domain, similar_count, narrative.
        """
        from .change_impact import is_actionable_change

        actionable = [n for n in nodes if is_actionable_change(n)]
        if not actionable or not self.fixes:
            return []

        # Pre-compute per-node data
        node_data = []
        for node in actionable:
            fp = node.change_fingerprint or {}
            changed_attrs = set(fp.get("changed_attrs", []))
            attr_cats = fp.get("attr_categories", set())
            module_path = _extract_module_path(node.address)
            leaf_addr = _leaf_address(node.address)
            stripped_addr = _strip_index(node.address)
            node_domains = self._identify_domains(node.resource_type, changed_attrs)
            node_data.append(
                {
                    "node": node,
                    "changed_attrs": changed_attrs,
                    "attr_cats": attr_cats,
                    "module_path": module_path,
                    "leaf_addr": leaf_addr,
                    "stripped_addr": stripped_addr,
                    "domains": node_domains,
                }
            )

        now = datetime.now(timezone.utc)

        # Score each fix against each node
        fix_scores = {}  # fix.id -> best result dict

        for fix in self.fixes:
            fix_text = " ".join(
                filter(None, [fix.issue, fix.resolution, fix.error_excerpt])
            ).lower()
            fix_codes = _extract_error_codes_from_text(fix_text)
            fix_tags = set()
            if fix.tags:
                fix_tags = {t.strip().lower() for t in fix.tags.split(",") if t.strip()}

            # Recency bonus
            recency_bonus = 0
            age_days = None
            if fix.created_at:
                try:
                    ts = fix.created_at.replace("Z", "+00:00")
                    created = datetime.fromisoformat(ts)
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                    age_days = (now - created).days
                    if age_days < 90:
                        recency_bonus = 30
                except (ValueError, TypeError):
                    pass

            best_score = 0
            best_reason = None
            best_domain = None
            all_matched_resources = []

            for nd in node_data:
                node = nd["node"]
                rt_lower = (node.resource_type or "").strip().lower()

                # 1. Score primary signals
                score, signal, detail = self._score_primary(
                    fix, fix_text, fix_codes, fix_tags, node, nd, rt_lower
                )

                if score == 0:
                    continue

                # 2. Add secondary boosters
                boosters, booster_signals = self._score_boosters(
                    fix, fix_text, fix_tags, node, nd, rt_lower, age_days, recency_bonus
                )

                total = score + boosters

                # Determine domain if applicable
                domain = None
                if signal == "change_domain":
                    domain = detail

                # Track this node
                all_matched_resources.append(
                    {
                        "address": node.address,
                        "action": node.action,
                    }
                )

                if total > best_score:
                    best_score = total
                    best_domain = domain
                    best_reason = {
                        "signal": signal,
                        "detail": detail,
                        "resource_type": rt_lower,
                        "supporting_signals": booster_signals,
                    }

            if best_score == 0:
                continue

            # Deduplicate matched_resources by address
            seen_addrs = set()
            deduped_resources = []
            for mr in all_matched_resources:
                if mr["address"] not in seen_addrs:
                    seen_addrs.add(mr["address"])
                    deduped_resources.append(mr)

            # Confidence bands
            if best_score >= 120:
                confidence = "high"
            elif best_score >= 60:
                confidence = "medium"
            else:
                confidence = "low"

            best_reason["confidence"] = confidence

            entry = {
                "fix_id": fix.id,
                "id": fix.id,
                "short_id": fix.id[:8],
                "issue": fix.issue,
                "resolution": fix.resolution,
                "tags": fix.tags or "",
                "created_at": fix.created_at or "",
                "error_excerpt": fix.error_excerpt or "",
                "score": best_score,
                "confidence": confidence,
                "match_reason": best_reason,
                "matched_resources": deduped_resources,
                "domain": best_domain,
                "similar_count": 0,
                "narrative": "",
            }

            if fix.id not in fix_scores or best_score > fix_scores[fix.id]["score"]:
                fix_scores[fix.id] = entry
            elif fix.id in fix_scores:
                existing = fix_scores[fix.id]
                existing_addrs = {r["address"] for r in existing["matched_resources"]}
                for mr in deduped_resources:
                    if mr["address"] not in existing_addrs:
                        existing["matched_resources"].append(mr)

        if not fix_scores:
            return []

        # Sort by score desc, then recency
        sorted_fixes = sorted(
            fix_scores.values(),
            key=lambda w: (-w["score"], w.get("created_at", "") or ""),
            reverse=False,
        )

        # Sort matched_resources
        for entry in sorted_fixes:
            entry["matched_resources"].sort(key=lambda r: r["address"])

        # Cluster duplicates
        clustered = self._cluster_duplicates(sorted_fixes)

        # Generate narratives
        for entry in clustered:
            entry["narrative"] = format_match_narrative(entry)

        return clustered[:max_total]

    def _score_primary(self, fix, fix_text, fix_codes, fix_tags, node, nd, rt_lower):
        """Score primary signals. Returns (score, signal_name, detail)."""
        has_rt_match = _fix_matches_resource_type(fix, rt_lower) if rt_lower else False

        # Error code match (150) — requires resource type context
        if fix_codes and has_rt_match:
            for code in fix_codes:
                if len(code) > 3 and code not in ("error", "failed", "true", "false"):
                    return 150, "error_code", code

        # Address match (120)
        stripped = nd["stripped_addr"].lower()
        leaf = nd["leaf_addr"].lower()
        if stripped in fix_text or leaf in fix_text:
            return 120, "address", node.address

        # Attribute match (100) — requires resource type context
        if has_rt_match and nd["changed_attrs"]:
            for attr in nd["changed_attrs"]:
                attr_pattern = re.compile(r"\b" + re.escape(attr) + r"\b", re.IGNORECASE)
                if attr_pattern.search(fix_text):
                    return 100, "changed_attribute", attr

        # Change domain match (70-85)
        if nd["domains"]:
            for dom_info in nd["domains"]:
                dom_name = dom_info["name"]
                dom_score = dom_info["score"]
                dom_label = dom_info["risk_label"]
                domain_def = self.domains[dom_name]
                # Fix must match domain: resource family OR mentions domain attr
                fix_rt_family = _resource_family(rt_lower) if rt_lower else None
                fix_in_domain = False

                # Check if fix's resource type is in the domain's families
                if fix.tags:
                    for tag in fix.tags.split(","):
                        tag = tag.strip().lower()
                        tag_family = _resource_family(tag)
                        if tag_family and any(
                            tag_family.startswith(fam) for fam in domain_def["resource_families"]
                        ):
                            fix_in_domain = True
                            break
                        if tag in domain_def["resource_families"]:
                            fix_in_domain = True
                            break

                # Check if fix text mentions a domain attribute
                if not fix_in_domain:
                    for dom_attr in domain_def["attributes"]:
                        attr_pat = re.compile(r"\b" + re.escape(dom_attr) + r"\b", re.IGNORECASE)
                        if attr_pat.search(fix_text):
                            fix_in_domain = True
                            break

                if fix_in_domain:
                    return dom_score, "change_domain", dom_name

        # Attribute category match (80) — requires resource type context
        if has_rt_match and nd["attr_cats"]:
            for cat in nd["attr_cats"]:
                if cat in fix_tags:
                    return 80, "attribute_category", cat

        return 0, "", ""

    def _score_boosters(self, fix, fix_text, fix_tags, node, nd, rt_lower, age_days, recency_bonus):
        """Add secondary booster points. Returns (bonus, supporting_signals)."""
        bonus = 0
        supporting = []

        # Recency bonus (+30)
        if recency_bonus and age_days is not None:
            bonus += recency_bonus
            supporting.append(
                {
                    "signal": "recency",
                    "detail": f"{age_days} days ago",
                }
            )

        # Module path bonus (+20)
        if nd["module_path"] and nd["module_path"].lower() in fix_text:
            bonus += 20
            supporting.append(
                {
                    "signal": "module_path",
                    "detail": nd["module_path"],
                }
            )

        # Resource family bonus (+15)
        if rt_lower:
            node_family = _resource_family(rt_lower)
            if node_family and fix.tags:
                for tag in fix.tags.split(","):
                    tag = tag.strip().lower()
                    tag_family = _resource_family(tag)
                    if tag_family and tag_family == node_family and tag != rt_lower:
                        bonus += 15
                        supporting.append(
                            {
                                "signal": "resource_family",
                                "detail": node_family,
                            }
                        )
                        break

        # Type tag booster (+15, demoted from standalone 40)
        if rt_lower and rt_lower in fix_tags:
            bonus += 15
            supporting.append(
                {
                    "signal": "type_tag",
                    "detail": rt_lower,
                }
            )

        # Type action booster (+10, demoted from standalone 60)
        has_rt_tag = rt_lower and rt_lower in fix_tags
        if has_rt_tag:
            action_words = {"delete", "replace", "update", "create"}
            if any(w in fix_text for w in action_words if w == node.action):
                bonus += 10
                supporting.append(
                    {
                        "signal": "type_action",
                        "detail": f"{rt_lower} + {node.action}",
                    }
                )

        return bonus, supporting

    def _cluster_duplicates(self, results):
        """Cluster near-duplicate matches, keep best per cluster."""
        clusters = {}  # cluster_key -> best entry

        for entry in results:
            mr = entry.get("match_reason", {})
            rt = mr.get("resource_type", "")

            # Determine error_code from match_reason
            error_code = None
            if mr.get("signal") == "error_code":
                error_code = mr.get("detail")

            # Top changed attribute
            top_attr = None
            if mr.get("signal") in ("changed_attribute", "change_domain", "attribute_category"):
                top_attr = mr.get("detail")

            issue_fam = _issue_family(entry.get("issue", ""))
            cluster_key = (rt, error_code, top_attr, issue_fam)

            if cluster_key not in clusters:
                clusters[cluster_key] = {
                    "entry": entry,
                    "count": 1,
                }
            else:
                clusters[cluster_key]["count"] += 1
                existing = clusters[cluster_key]["entry"]
                # Keep highest score, tie-break by recency
                if (entry["score"] > existing["score"]) or (
                    entry["score"] == existing["score"]
                    and entry.get("created_at", "") > existing.get("created_at", "")
                ):
                    old_count = clusters[cluster_key]["count"]
                    clusters[cluster_key] = {
                        "entry": entry,
                        "count": old_count,
                    }

        # Build output
        output = []
        for cluster in clusters.values():
            entry = cluster["entry"]
            similar = cluster["count"] - 1
            entry["similar_count"] = similar
            output.append(entry)

        # Sort by score desc, then created_at desc
        output.sort(key=lambda w: (-w["score"], w.get("created_at", "") or ""))
        return output

    def _identify_domains(self, resource_type, changed_attrs):
        """Which CHANGE_DOMAINS does this node's change fall into?

        Returns list[dict] with name, score, risk_label for matching domains.
        """
        if not changed_attrs:
            return []

        matching = []
        for dom_name, dom_def in self.domains.items():
            overlap = changed_attrs & dom_def["attributes"]
            if overlap:
                matching.append(
                    {
                        "name": dom_name,
                        "score": dom_def["score"],
                        "risk_label": dom_def["risk_label"],
                        "matched_attrs": overlap,
                    }
                )

        # Sort by score desc so highest-scoring domain is tried first
        matching.sort(key=lambda d: -d["score"])
        return matching


# ---------------------------------------------------------------------------
# Template-based narrative formatting
# ---------------------------------------------------------------------------

_SIGNAL_TEMPLATES = {
    "error_code": "Previously encountered **{detail}** when changing {resource_type}.",
    "address": "A prior fix for this exact resource exists (**{detail}**).",
    "changed_attribute": (
        "When **{detail}** changed previously on {resource_type}, the team "
        "resolved it by: {resolution_summary}."
    ),
    "change_domain": (
        "This change overlaps with a prior **{risk_label}** issue: " "{issue_summary}."
    ),
    "attribute_category": ("Previous **{detail}** issue for {resource_type}: {issue_summary}."),
}


def format_match_narrative(match):
    """Template-based natural language for a single match.

    Returns a readable string describing why this fix is relevant.
    """
    mr = match.get("match_reason", {})
    if isinstance(mr, str):
        # Legacy format — match_reason is a plain string
        issue = match.get("issue", "")
        issue_summary = issue[:80] + "..." if len(issue) > 80 else issue
        return f"Related fix: {issue_summary}"
    signal = mr.get("signal", "")
    detail = mr.get("detail", "")
    rt = mr.get("resource_type", "")
    issue = match.get("issue", "")
    resolution = match.get("resolution", "")

    # Truncate for display
    issue_summary = issue[:80] + "..." if len(issue) > 80 else issue
    resolution_summary = resolution[:100] + "..." if len(resolution) > 100 else resolution

    template = _SIGNAL_TEMPLATES.get(signal)
    if not template:
        return f"Related fix for {rt}: {issue_summary}"

    # Build risk_label for domain matches
    risk_label = ""
    if signal == "change_domain":
        domain_name = detail
        domain_def = CHANGE_DOMAINS.get(domain_name, {})
        risk_label = domain_def.get("risk_label", domain_name)

    try:
        text = template.format(
            detail=detail,
            resource_type=rt,
            resolution_summary=resolution_summary,
            issue_summary=issue_summary,
            risk_label=risk_label,
        )
    except (KeyError, IndexError):
        text = f"Related fix for {rt}: {issue_summary}"

    return text
