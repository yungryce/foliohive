# Expand the TECHNICAL_TERMS dictionary for more comprehensive matches
# including: common file extensions, version patterns, and technical terms.
import logging
import json
import re
import os
from fnmatch import fnmatch
from typing import List, Sequence

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None


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
    logger.info("Generating standard config file candidates with limit=%d", limit)
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
    
    logger.info("***********************cdcl.********************")
    logger.info("Generated count=%d standard config file candidates", len(result))
    
    return result


def _error_payload(raw_content: str, message: str) -> dict:
    sample = (raw_content or "")[:300]
    return {
        "error": message,
        "raw_sample": sample,
    }


def _extract_requirements_txt(raw_content: str) -> dict:
    dependencies: list[str] = []
    constraints: list[str] = []

    for line in (raw_content or "").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if text.startswith(("-", "--")):
            constraints.append(text)
            continue
        # Extract package name only, strip version specifiers (==, >=, <=, ~=, !=, >, <)
        package_name = re.split(r'[=!<>~]', text)[0].strip()
        if package_name:
            dependencies.append(package_name)

    return {
        "dependencies": dependencies,
        "constraints": constraints,
    }


def _extract_pyproject_toml(raw_content: str) -> dict:
    if tomllib is None:
        return _error_payload(raw_content, "tomllib_unavailable")
    try:
        parsed = tomllib.loads(raw_content or "")
    except Exception:
        return _error_payload(raw_content, "invalid_toml")

    project_deps = []
    project = parsed.get("project") if isinstance(parsed, dict) else None
    if isinstance(project, dict):
        deps = project.get("dependencies")
        if isinstance(deps, list):
            # Extract package names only, strip version specifiers
            for dep in deps:
                dep_str = str(dep)
                package_name = re.split(r'[=!<>~\[;]', dep_str)[0].strip()
                if package_name:
                    project_deps.append(package_name)

    poetry_deps: list[str] = []
    tool = parsed.get("tool") if isinstance(parsed, dict) else None
    if isinstance(tool, dict):
        poetry = tool.get("poetry")
        if isinstance(poetry, dict):
            deps = poetry.get("dependencies")
            if isinstance(deps, dict):
                # Extract only package names, ignore version values
                for key in deps.keys():
                    poetry_deps.append(str(key))

    return {
        "project_dependencies": project_deps,
        "poetry_dependencies": poetry_deps,
    }


def _extract_package_json(raw_content: str) -> dict:
    try:
        parsed = json.loads(raw_content or "{}")
    except Exception:
        return _error_payload(raw_content, "invalid_json")

    if not isinstance(parsed, dict):
        return _error_payload(raw_content, "invalid_json_root")

    # Extract only package names, not version values
    dependencies = list(parsed.get("dependencies", {}).keys()) if isinstance(parsed.get("dependencies"), dict) else []
    dev_dependencies = list(parsed.get("devDependencies", {}).keys()) if isinstance(parsed.get("devDependencies"), dict) else []
    scripts = list(parsed.get("scripts", {}).keys()) if isinstance(parsed.get("scripts"), dict) else []

    return {
        "dependencies": dependencies,
        "devDependencies": dev_dependencies,
        "scripts": scripts,
    }


def _extract_dockerfile(raw_content: str) -> dict:
    base_images: list[str] = []
    exposed_ports: list[str] = []
    env_vars: dict[str, str] = {}

    for line in (raw_content or "").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        upper = text.upper()
        if upper.startswith("FROM "):
            parts = text.split(maxsplit=1)
            if len(parts) == 2:
                base_images.append(parts[1].strip())
        elif upper.startswith("EXPOSE "):
            parts = text.split(maxsplit=1)
            if len(parts) == 2:
                exposed_ports.extend([p for p in parts[1].split() if p])
        elif upper.startswith("ENV "):
            body = text[4:].strip()
            for token in body.split():
                if "=" in token:
                    key, value = token.split("=", 1)
                    env_vars[key.strip()] = value.strip()

    return {
        "base_images": base_images,
        "exposed_ports": exposed_ports,
        "env_vars": env_vars,
    }


def _extract_docker_compose(raw_content: str) -> dict:
    services: list[str] = []
    networks: list[str] = []
    volumes: list[str] = []

    current_section = None
    section_indent = None
    for line in (raw_content or "").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())
        stripped = line.strip()

        if stripped in {"services:", "networks:", "volumes:"}:
            current_section = stripped[:-1]
            section_indent = indent
            continue

        if current_section and section_indent is not None and indent <= section_indent:
            current_section = None
            section_indent = None

        if not current_section or not stripped.endswith(":"):
            continue

        key = stripped[:-1].strip()
        if not key:
            continue

        if current_section == "services":
            services.append(key)
        elif current_section == "networks":
            networks.append(key)
        elif current_section == "volumes":
            volumes.append(key)

    return {
        "services": services,
        "networks": networks,
        "volumes": volumes,
    }


def _extract_terraform(raw_content: str) -> dict:
    resources: list[str] = []
    providers: list[str] = []

    resource_pattern = re.compile(r'^\s*resource\s+"([^"]+)"\s+"([^"]+)"')
    provider_pattern = re.compile(r'^\s*provider\s+"([^"]+)"')

    for line in (raw_content or "").splitlines():
        resource_match = resource_pattern.match(line)
        if resource_match:
            resources.append(f"{resource_match.group(1)}.{resource_match.group(2)}")
            continue

        provider_match = provider_pattern.match(line)
        if provider_match:
            providers.append(provider_match.group(1))

    return {
        "resources": resources,
        "providers": providers,
    }


def _extract_bicep(raw_content: str) -> dict:
    resources: list[str] = []
    modules: list[str] = []

    resource_pattern = re.compile(r"^\s*resource\s+([A-Za-z_][A-Za-z0-9_]*)")
    module_pattern = re.compile(r"^\s*module\s+([A-Za-z_][A-Za-z0-9_]*)")

    for line in (raw_content or "").splitlines():
        resource_match = resource_pattern.match(line)
        if resource_match:
            resources.append(resource_match.group(1))
            continue

        module_match = module_pattern.match(line)
        if module_match:
            modules.append(module_match.group(1))

    return {
        "resources": resources,
        "modules": modules,
    }


CONFIG_EXTRACTION_SCHEMAS = {
    "requirements.txt": _extract_requirements_txt,
    "pyproject.toml": _extract_pyproject_toml,
    "package.json": _extract_package_json,
    "dockerfile": _extract_dockerfile,
    "docker-compose.yml": _extract_docker_compose,
    "docker-compose.yaml": _extract_docker_compose,
    "compose.yml": _extract_docker_compose,
    "compose.yaml": _extract_docker_compose,
    "main.tf": _extract_terraform,
    "*.tf": _extract_terraform,
    "*.bicep": _extract_bicep,
}


def get_config_extractor(file_path: str):
    normalized = os.path.basename((file_path or "").strip()).lower()
    if not normalized:
        return None

    if normalized in CONFIG_EXTRACTION_SCHEMAS:
        return CONFIG_EXTRACTION_SCHEMAS[normalized]

    for pattern, extractor in CONFIG_EXTRACTION_SCHEMAS.items():
        if "*" in pattern and fnmatch(normalized, pattern):
            return extractor

    return None

