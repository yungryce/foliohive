# Expand the TECHNICAL_TERMS dictionary for more comprehensive matches
# including: common file extensions, version patterns, and technical terms.
import logging
import re
from typing import List, Sequence


logger = logging.getLogger("cloudfolio.data_filter")
logger.setLevel(logging.INFO)
logger.propagate = True


STANDARD_CONFIG_FILE_CANDIDATES: Sequence[str] = (
    # Docker / Compose
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",

    # Python
    "requirements.txt",
    "requirements-dev.txt",
    "pyproject.toml",
    "poetry.lock",
    "Pipfile",
    "Pipfile.lock",
    "setup.py",
    "setup.cfg",
    "tox.ini",

    # Node / JS / TS
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "tsconfig.json",
    "jsconfig.json",
    ".nvmrc",

    # .NET
    "global.json",
    "Directory.Build.props",
    "Directory.Build.targets",
    "NuGet.config",

    # Java / JVM
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "settings.gradle.kts",

    # Go
    "go.mod",
    "go.sum",

    # Rust
    "Cargo.toml",
    "Cargo.lock",

    # Ruby
    "Gemfile",
    "Gemfile.lock",

    # PHP
    "composer.json",
    "composer.lock",

    # Terraform
    "main.tf",
    "variables.tf",
    "outputs.tf",
    "providers.tf",
    "terraform.tfvars",

    # CI common filenames (no directory listing; try the common ones)
    ".github/workflows/ci.yml",
    ".github/workflows/ci.yaml",
    ".github/workflows/tests.yml",
    ".github/workflows/tests.yaml",
    ".github/workflows/build.yml",
    ".github/workflows/build.yaml",
)


def get_standard_config_file_candidates(*, limit: int = 40) -> List[str]:
    """Return a deterministic, bounded list of config file paths to try-fetch."""
    bounded = list(STANDARD_CONFIG_FILE_CANDIDATES)[: max(0, int(limit))]
    # De-dupe while preserving order
    seen = set()
    result: List[str] = []
    for path in bounded:
        if not path or path in seen:
            continue
        seen.add(path)
        result.append(path)
        logger.info("Added standard config file candidate: %s", path)
    return result
