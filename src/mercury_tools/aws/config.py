"""Load the static AWS Wave 0 configuration contract."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from mercury_tools.aws.models import Wave0Config


def load_wave0_config(path: Path) -> Wave0Config:
    """Read one YAML mapping and validate the closed Wave 0 contract."""

    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("wave0_config_document_invalid")
    return Wave0Config.model_validate(raw)
