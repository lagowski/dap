"""XML well-formedness + root element validation.

Uses defusedxml to protect against XXE, billion laughs, external entities.
"""

from __future__ import annotations

from typing import Final

from defusedxml import ElementTree as DefusedET
from pydantic import BaseModel, ConfigDict

DEFAULT_ROOT: Final[str] = "agent_prompt"


class ValidationOutcome(BaseModel):
    """Result of XML validation."""

    model_config = ConfigDict(extra="forbid")

    valid: bool
    root_element: str | None = None
    errors: list[str] = []
    warnings: list[str] = []


def validate_xml(xml: str, *, expected_root: str = DEFAULT_ROOT) -> ValidationOutcome:
    """Validate XML well-formedness and root element.

    Returns ValidationOutcome with valid=True only if:
    - XML parses cleanly via defusedxml
    - Root element matches `expected_root`
    """
    errors: list[str] = []
    warnings: list[str] = []
    root_element: str | None = None

    try:
        root = DefusedET.fromstring(xml)
    except DefusedET.ParseError as exc:
        errors.append(f"XML parse error: {exc}")
        return ValidationOutcome(valid=False, root_element=None, errors=errors, warnings=warnings)
    except Exception as exc:  # defusedxml may raise EntitiesForbidden, DTDForbidden, ...
        errors.append(f"XML rejected: {type(exc).__name__}: {exc}")
        return ValidationOutcome(valid=False, root_element=None, errors=errors, warnings=warnings)

    root_element = root.tag

    if root_element != expected_root:
        errors.append(f"Root element must be <{expected_root}>, got <{root_element}>")

    return ValidationOutcome(
        valid=not errors,
        root_element=root_element,
        errors=errors,
        warnings=warnings,
    )
