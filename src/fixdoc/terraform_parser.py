## This file will parse terraform plan/apply and extract the relevant errors using regular expression matching

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class TerraformError:
    ##Parsed error object created from terraform output

    resource_type: str
    resource_name: str
    resource_address: str
    error_code: Optional[str]
    error_message: str
    file: Optional[str]
    line: Optional[int]
    raw_output: str

    def short_error(self) -> str:
        """Short error description for display."""
        if self.error_code:
            return f"{self.error_code}: {self.error_message[:100]}"
        return self.error_message[:100]


def parse_terraform_error(output: str) -> Optional[TerraformError]:
    """Parse a terraform error block."""

    # Find error block
    error_match = re.search(r"│?\s*Error:\s*(.+?)(?=\n│?\s*\n|\n\n|$)", output, re.DOTALL)
    if not error_match:
        error_match = re.search(r"Error:\s*(.+?)(?=\n\n|$)", output, re.DOTALL)
    if not error_match:
        return None

    error_block = error_match.group(0)

    # Extract resource address with regex
    resource_match = re.search(
        r"with\s+((?:module\.[^,\s]+\.)?([a-z_]+)\.([a-z0-9_-]+))",
        output,
        re.IGNORECASE,
    )

    if resource_match:
        resource_address = resource_match.group(1)
        resource_type = resource_match.group(2)
        resource_name = resource_match.group(3)
    else:
        resource_type, resource_name, resource_address = "unknown", "unknown", "unknown"

    # Extract file and line
    file_match = re.search(r"on\s+([^\s]+\.tf)\s+line\s+(\d+)", output)
    file = file_match.group(1) if file_match else None
    line = int(file_match.group(2)) if file_match else None

    # Extract error code
    error_code = _extract_error_code(output)

    # Extract error message
    error_message = _extract_error_message(output, error_block)

    return TerraformError(
        resource_type=resource_type,
        resource_name=resource_name,
        resource_address=resource_address,
        error_code=error_code,
        error_message=error_message,
        file=file,
        line=line,
        raw_output=output,
    )


def _extract_error_code(output: str) -> Optional[str]:
    ##Extract error code from terraform
    code_match = re.search(r'Code:\s*["\']?([A-Za-z0-9_]+)["\']?', output)
    if code_match:
        return code_match.group(1)

    status_match = re.search(r"Status:\s*(\d+\s*[A-Za-z]+)", output)
    if status_match:
        return status_match.group(1)

    return None


def _extract_error_message(output: str, error_block: str) -> str:
    ##Extract error code from terraform
    msg_match = re.search(r'Message:\s*["\']?(.+?)["\']?(?=\n│|\n\n|$)', output, re.DOTALL)
    if msg_match:
        message = msg_match.group(1).strip()
    else:
        first_line = error_block.split("\n")[0]
        message = re.sub(r"^│?\s*Error:\s*", "", first_line).strip()

    message = re.sub(r"\s+", " ", message).strip()
    return message[:500]


def parse_terraform_output(output: str) -> list[TerraformError]:
    ##Parse terraform output for all errors.
    errors = []
    parts = re.split(r"(?=│?\s*Error:)", output)

    for part in parts:
        if "Error:" in part:
            parsed = parse_terraform_error(part)
            if parsed:
                errors.append(parsed)

    # resource addresses should be unique
    seen = set()
    unique = []
    for e in errors:
        if e.resource_address not in seen:
            seen.add(e.resource_address)
            unique.append(e)

    return unique


def is_terraform_output(text: str) -> bool:
    indicators = [
        "Error:",
        "azurerm_",
        "aws_",
        "google_",
        ".tf line",
        "with module.",
        "Plan:",
        "Apply",
    ]
    text_lower = text.lower()
    return any(ind.lower() in text_lower for ind in indicators)
