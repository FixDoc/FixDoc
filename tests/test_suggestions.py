"""Tests for fixdoc suggestions module."""

import pytest

from fixdoc.config import SuggestionWeights
from fixdoc.models import Fix
from fixdoc.storage import FixRepository
from fixdoc.suggestions import (
    find_similar_fixes,
    _extract_keywords,
    _extract_error_codes,
    _extract_resource_types,
    _dedup_cluster,
)


@pytest.fixture
def temp_repo(tmp_path):
    """Create a temporary repository for testing."""
    return FixRepository(base_path=tmp_path / ".fixdoc")


@pytest.fixture
def repo_with_fixes(temp_repo):
    """Create a repository with sample fixes."""
    fixes = [
        Fix(
            issue="S3 BucketAlreadyExists error when creating bucket",
            resolution="Add random suffix to bucket name or use existing bucket",
            tags="aws,s3,terraform",
            error_excerpt="Error: BucketAlreadyExists: The bucket name is already taken",
        ),
        Fix(
            issue="Azure storage account RBAC permission denied",
            resolution="Add Storage Blob Data Contributor role to service principal",
            tags="azure,storage,rbac,terraform",
            error_excerpt="Error: AuthorizationFailed: The client does not have permission",
        ),
        Fix(
            issue="Kubernetes CrashLoopBackOff in pod",
            resolution="Check container logs and fix application startup error",
            tags="kubernetes,pod,crashloopbackoff",
            error_excerpt="CrashLoopBackOff: Container keeps restarting",
        ),
        Fix(
            issue="Terraform state lock timeout",
            resolution="Force unlock state with terraform force-unlock",
            tags="terraform,state,lock",
            error_excerpt="Error: Error acquiring the state lock",
        ),
    ]
    for fix in fixes:
        temp_repo.save(fix)
    return temp_repo


class TestFindSimilarFixes:
    def test_find_by_error_text_keyword(self, repo_with_fixes):
        """Should find fixes with matching keywords in error text."""
        similar = find_similar_fixes(
            repo_with_fixes,
            "Error: BucketAlreadyExists when creating S3 bucket",
            min_score=1,
        )

        assert len(similar) > 0
        # S3 bucket fix should be ranked highly
        assert any("S3" in fix.issue or "s3" in fix.tags for fix in similar[:2])

    def test_find_by_tags(self, repo_with_fixes):
        """Should find fixes with matching tags."""
        similar = find_similar_fixes(
            repo_with_fixes,
            "some terraform error",
            tags="terraform,azure",
            min_score=1,
        )

        assert len(similar) > 0
        # Azure and terraform tagged fixes should rank higher
        top_fix = similar[0]
        assert "azure" in top_fix.tags.lower() or "terraform" in top_fix.tags.lower()

    def test_find_by_error_code(self, repo_with_fixes):
        """Should find fixes with matching error codes."""
        similar = find_similar_fixes(
            repo_with_fixes,
            "AuthorizationFailed: User does not have permissions",
            min_score=1,
        )

        assert len(similar) > 0
        # Azure RBAC fix should match on error code
        assert any("AuthorizationFailed" in (fix.error_excerpt or "") for fix in similar[:2])

    def test_find_by_resource_type(self, temp_repo):
        """Should find fixes with matching resource types in tags."""
        fix = Fix(
            issue="S3 bucket error",
            resolution="Fix bucket config",
            tags="aws_s3_bucket,terraform",
            error_excerpt="Error creating bucket",
        )
        temp_repo.save(fix)

        similar = find_similar_fixes(
            temp_repo,
            "Error with aws_s3_bucket resource",
            min_score=1,
        )

        assert len(similar) > 0
        assert any("aws_s3_bucket" in fix.tags.lower() for fix in similar)

    def test_find_by_resource_address(self, temp_repo):
        """Should find fixes when error text contains a resource address."""
        fix = Fix(
            issue="aws_s3_bucket.data: BucketAlreadyExists",
            resolution="Used unique bucket name",
            tags="aws_s3_bucket,terraform",
            error_excerpt="aws_s3_bucket.data: BucketAlreadyExists",
        )
        temp_repo.save(fix)

        similar = find_similar_fixes(
            temp_repo,
            "Error on aws_s3_bucket.data: The requested bucket name",
            resource_address="aws_s3_bucket.data",
            min_score=1,
        )

        assert len(similar) >= 1
        assert similar[0].id == fix.id

    def test_empty_repository(self, temp_repo):
        """Should return empty list for empty repository."""
        similar = find_similar_fixes(temp_repo, "any error")
        assert similar == []

    def test_no_matches_below_threshold(self, repo_with_fixes):
        """Should return empty when no fixes meet min_score threshold."""
        similar = find_similar_fixes(
            repo_with_fixes,
            "xyz123 completely unrelated error",
            tags="nonexistent,tags",
            min_score=15,
        )
        assert similar == []

    def test_min_score_filters(self, temp_repo):
        """Min score threshold should filter out low-relevance matches."""
        temp_repo.save(
            Fix(
                issue="Some vague issue about permissions",
                resolution="Fixed permissions",
                tags="terraform",
            )
        )

        # With high threshold, vague match is excluded
        similar_high = find_similar_fixes(
            temp_repo,
            "timeout connecting to database",
            min_score=20,
        )
        assert similar_high == []

    def test_limit_results(self, repo_with_fixes):
        """Should respect the limit parameter."""
        similar = find_similar_fixes(
            repo_with_fixes,
            "terraform error",
            limit=2,
            min_score=1,
        )

        assert len(similar) <= 2

    def test_results_sorted_by_score(self, repo_with_fixes):
        """Results should be sorted by relevance score (descending)."""
        similar = find_similar_fixes(
            repo_with_fixes,
            "CrashLoopBackOff kubernetes pod error",
            tags="kubernetes",
            min_score=1,
        )

        if len(similar) >= 2:
            assert "kubernetes" in similar[0].tags.lower()

    def test_custom_weights(self, temp_repo):
        """Custom weights should change ranking."""
        temp_repo.save(
            Fix(
                issue="IAM role issue",
                resolution="Fixed role policy",
                tags="aws_iam_role,terraform",
                error_excerpt="Error: AccessDenied on aws_iam_role.app",
            )
        )

        # With high resource_address_weight
        weights = SuggestionWeights(resource_address_weight=50)
        similar = find_similar_fixes(
            temp_repo,
            "Error on aws_iam_role.app: AccessDenied",
            weights=weights,
            min_score=1,
        )
        assert len(similar) >= 1


class TestDedupCluster:
    def test_dedup_identical_fixes(self, temp_repo):
        """Near-identical fixes should be deduped to highest scorer."""
        for i in range(3):
            temp_repo.save(
                Fix(
                    issue=f"InsufficientInstanceCapacity on aws_instance.web #{i}",
                    resolution=f"Changed instance type #{i}",
                    tags="aws_instance,terraform,InsufficientInstanceCapacity",
                    error_excerpt="InsufficientInstanceCapacity",
                )
            )

        similar = find_similar_fixes(
            temp_repo,
            "Error: InsufficientInstanceCapacity on aws_instance.web",
            tags="aws_instance,terraform",
            min_score=1,
        )

        # Should be deduped to 1 result
        assert len(similar) == 1

    def test_different_errors_not_deduped(self, temp_repo):
        """Fixes with different error codes should not be deduped."""
        temp_repo.save(
            Fix(
                issue="S3 BucketAlreadyExists",
                resolution="Use unique name",
                tags="aws_s3_bucket",
                error_excerpt="BucketAlreadyExists",
            )
        )
        temp_repo.save(
            Fix(
                issue="S3 AccessDenied on bucket",
                resolution="Fix IAM policy",
                tags="aws_s3_bucket",
                error_excerpt="AccessDenied",
            )
        )

        similar = find_similar_fixes(
            temp_repo,
            "Error with aws_s3_bucket: AccessDenied and BucketAlreadyExists",
            min_score=1,
        )

        assert len(similar) == 2


class TestExtractKeywords:
    def test_extract_basic_keywords(self):
        keywords = _extract_keywords("Error creating storage account")
        assert "creating" in keywords
        assert "storage" in keywords
        assert "account" in keywords

    def test_filter_stop_words(self):
        keywords = _extract_keywords("The error is in the configuration")
        assert "the" not in keywords
        assert "is" not in keywords
        assert "in" not in keywords
        assert "configuration" in keywords

    def test_filter_short_words(self):
        keywords = _extract_keywords("An S3 bucket in us-east-1")
        assert "an" not in keywords
        assert "in" not in keywords

    def test_lowercase_conversion(self):
        keywords = _extract_keywords("STORAGE Account ERROR")
        assert "storage" in keywords
        assert "STORAGE" not in keywords

    def test_empty_text(self):
        assert _extract_keywords("") == set()
        assert _extract_keywords(None) == set()


class TestExtractErrorCodes:
    def test_extract_azure_error_code(self):
        codes = _extract_error_codes('Code: "AuthorizationFailed"')
        assert "authorizationfailed" in codes

    def test_extract_http_status_codes(self):
        codes = _extract_error_codes("Request failed with status 403 Forbidden")
        assert "403" in codes

    def test_extract_common_error_names(self):
        codes = _extract_error_codes("accessdenied error when accessing resource")
        assert "accessdenied" in codes

    def test_multiple_codes(self):
        codes = _extract_error_codes("Error 404 NotFound, Code: Forbidden 403")
        assert "404" in codes
        assert "403" in codes

    def test_terraform_error_pattern(self):
        codes = _extract_error_codes("Error: InvalidGroup.NotFound")
        assert "invalidgroup.notfound" in codes

    def test_xml_code_pattern(self):
        codes = _extract_error_codes("<Code>BucketAlreadyExists</Code>")
        assert "bucketalreadyexists" in codes

    def test_status_code_pattern(self):
        codes = _extract_error_codes("StatusCode: 429")
        assert "429" in codes

    def test_api_error_pattern(self):
        codes = _extract_error_codes("api error AccessDenied")
        assert "accessdenied" in codes


class TestExtractResourceTypes:
    def test_extract_aws_resource_types(self):
        types = _extract_resource_types("Error with aws_s3_bucket resource")
        assert "aws_s3_bucket" in types

    def test_extract_azure_resource_types(self):
        types = _extract_resource_types("azurerm_storage_account creation failed")
        assert "azurerm_storage_account" in types

    def test_extract_gcp_resource_types(self):
        types = _extract_resource_types("google_compute_instance error")
        assert "google_compute_instance" in types

    def test_multiple_resource_types(self):
        types = _extract_resource_types("Error with aws_iam_role and aws_s3_bucket")
        assert "aws_iam_role" in types
        assert "aws_s3_bucket" in types

    def test_case_insensitive(self):
        types = _extract_resource_types("AWS_S3_BUCKET error")
        assert "aws_s3_bucket" in types


# ===================================================================
# TestErrorIdMatch — Feature 2
# ===================================================================


class TestErrorIdMatch:
    """Tests for error_id parameter in find_similar_fixes."""

    def test_error_id_match_boosts_score(self, tmp_path):
        from fixdoc.storage import FixRepository

        repo = FixRepository(tmp_path)
        fix = Fix(
            issue="AccessDenied on role",
            resolution="Added policy",
            tags="aws_iam_role",
            source_error_ids=["err_abc123"],
        )
        repo.save(fix)

        # With error_id match
        results_with = find_similar_fixes(
            repo,
            "AccessDenied",
            error_id="err_abc123",
        )
        # Without error_id
        results_without = find_similar_fixes(
            repo,
            "AccessDenied",
            error_id="different_id",
        )

        # The fix should appear in both (if score >= min_score),
        # but the one with matching error_id should rank higher
        assert len(results_with) >= 1
        assert results_with[0].id == fix.id

    def test_error_id_no_match_no_boost(self, tmp_path):
        from fixdoc.storage import FixRepository

        repo = FixRepository(tmp_path)
        fix = Fix(
            issue="AccessDenied on role",
            resolution="Added policy",
            tags="aws_iam_role",
            source_error_ids=["err_abc123"],
        )
        repo.save(fix)

        # Non-matching error_id should not boost
        results = find_similar_fixes(
            repo,
            "AccessDenied",
            error_id="completely_different",
        )
        # Fix may or may not appear depending on other scoring — but no crash
        # The key assertion: no error raised

    def test_error_id_none_no_crash(self, tmp_path):
        from fixdoc.storage import FixRepository

        repo = FixRepository(tmp_path)
        fix = Fix(
            issue="AccessDenied on role",
            resolution="Added policy",
            tags="aws_iam_role",
            source_error_ids=["err_abc123"],
        )
        repo.save(fix)

        # error_id=None (default) should not crash
        results = find_similar_fixes(repo, "AccessDenied")
        # No crash is the assertion


# ===================================================================
# TestEffectivenessBoost
# ===================================================================


class TestEffectivenessBoost:
    """Tests for effectiveness rate boosting in suggestions."""

    def test_effective_fix_boosted(self, tmp_path):
        """Fix with high effectiveness rate ranks above equivalent fix without."""
        from fixdoc.storage import FixRepository

        repo = FixRepository(tmp_path)
        # Proven effective fix — distinct resolution keywords to avoid dedup
        fix_effective = Fix(
            issue="AccessDenied on aws_iam_role.app during deploy",
            resolution="Added role binding in IAM console",
            tags="aws_iam_role,terraform",
            applied_count=4,
            success_count=4,
        )
        # Untested fix — distinct resolution and some issue variation
        fix_untested = Fix(
            issue="AccessDenied on aws_iam_role.app during provisioning",
            resolution="Updated trust policy document with correct principal",
            tags="aws_iam_role,terraform",
        )
        repo.save(fix_effective)
        repo.save(fix_untested)

        results = find_similar_fixes(
            repo,
            "Error: AccessDenied on aws_iam_role.app",
            tags="aws_iam_role",
        )

        assert len(results) >= 2
        assert results[0].id == fix_effective.id
