"""Tests for fixdoc.ingestion.redact — secrets never reach the store."""

from fixdoc.ingestion.redact import redact


class TestPatterns:
    def test_aws_access_key(self):
        text, counts = redact("key was AKIAIOSFODNN7EXAMPLE in env")
        assert "AKIAIOSFODNN7EXAMPLE" not in text
        assert "[REDACTED:aws-key]" in text
        assert counts["aws-key"] == 1

    def test_credential_assignment_keeps_key_name(self):
        text, counts = redact("password=hunter2 and api_key: 'abc123xyz'")
        assert "hunter2" not in text and "abc123xyz" not in text
        assert "password" in text  # the variable name survives, the value dies
        assert counts["credential"] == 2

    def test_bearer_token(self):
        text, counts = redact("Authorization: Bearer abc123def456ghi789")
        assert "abc123def456ghi789" not in text
        assert counts["bearer-token"] == 1

    def test_jwt(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9P"
        text, counts = redact(f"token {jwt} leaked")
        assert jwt not in text
        assert counts["jwt"] == 1

    def test_private_key_block(self):
        pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nsecretbits\n-----END RSA PRIVATE KEY-----"
        text, counts = redact(f"dumped\n{pem}\ndone")
        assert "secretbits" not in text
        assert counts["private-key"] == 1

    def test_url_credentials(self):
        text, counts = redact("postgres://svc:s3cretpw@db.internal/app")
        assert "s3cretpw" not in text
        assert "db.internal/app" in text  # host survives
        assert counts["url-credential"] == 1

    def test_clean_text_untouched(self):
        clean = "Pods pending after scale-up; subnet exhausted."
        text, counts = redact(clean)
        assert text == clean
        assert counts == {}
