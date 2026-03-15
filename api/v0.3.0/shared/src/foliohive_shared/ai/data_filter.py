# Expand the TECHNICAL_TERMS dictionary for more comprehensive matches
# including: common file extensions, version patterns, and technical terms.
import configparser
import logging
import json
import re
import os
import xml.etree.ElementTree as ET
from fnmatch import fnmatch
from typing import List, Sequence

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None


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

    # Cloud / platform
    "host.json",
    "staticwebapp.config.json",
    "appsettings.json",
    "appsettings.Development.json",

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

    # Kubernetes / Helm
    "deployment.yml",
    "deployment.yaml",
    "service.yml",
    "service.yaml",
    "ingress.yml",
    "ingress.yaml",
    "k8s.yml",
    "k8s.yaml",
    "Chart.yaml",
    "values.yaml",

    # Game development
    "project.godot",
    "ProjectSettings/ProjectVersion.txt",
    "ProjectSettings/TagManager.asset",
    "Packages/manifest.json",

    # CI common filenames (no directory listing; try the common ones)
    ".github/workflows/ci.yml",
    ".github/workflows/ci.yaml",
    ".github/workflows/tests.yml",
    ".github/workflows/tests.yaml",
    ".github/workflows/build.yml",
    ".github/workflows/build.yaml",
)


def get_standard_config_file_candidates() -> List[str]:
    """Return a deterministic, bounded list of config file paths to try-fetch."""
    bounded = list(STANDARD_CONFIG_FILE_CANDIDATES)
    # De-dupe while preserving order
    seen = set()
    result: List[str] = []
    for path in bounded:
        if not path or path in seen:
            continue
        seen.add(path)
        result.append(path)
    
    return result


def _error_payload(raw_content: str, message: str) -> dict:
    sample = (raw_content or "")[:300]
    return {
        "error": message,
        "raw_sample": sample,
    }


def _strip_json_comments_and_trailing_commas(raw_content: str) -> str:
    text = (raw_content or "").replace("\ufeff", "", 1)

    result_chars: list[str] = []
    in_string = False
    escape_next = False
    index = 0
    length = len(text)

    while index < length:
        char = text[index]
        next_char = text[index + 1] if index + 1 < length else ""

        if in_string:
            result_chars.append(char)
            if escape_next:
                escape_next = False
            elif char == "\\":
                escape_next = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            result_chars.append(char)
            index += 1
            continue

        if char == "/" and next_char == "/":
            index += 2
            while index < length and text[index] not in ("\n", "\r"):
                index += 1
            continue

        if char == "/" and next_char == "*":
            index += 2
            while index + 1 < length and not (text[index] == "*" and text[index + 1] == "/"):
                index += 1
            index += 2 if index + 1 < length else 0
            continue

        result_chars.append(char)
        index += 1

    without_comments = "".join(result_chars)
    return re.sub(r",\s*([}\]])", r"\1", without_comments)


def _load_json_robust(raw_content: str):
    raw_text = (raw_content or "").replace("\ufeff", "", 1)
    try:
        return json.loads(raw_text)
    except Exception:
        sanitized = _strip_json_comments_and_trailing_commas(raw_text)
        return json.loads(sanitized)


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
        parsed = _load_json_robust(raw_content or "{}")
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


def _extract_package_lock_json(raw_content: str) -> dict:
    """Extract top-level dependencies from npm package-lock.json."""
    try:
        parsed = _load_json_robust(raw_content or "{}")
    except Exception:
        return _error_payload(raw_content, "invalid_json")

    if not isinstance(parsed, dict):
        return _error_payload(raw_content, "invalid_json_root")

    packages: list[str] = []
    lock_version = parsed.get("lockfileVersion", "unknown")

    # Extract top-level dependencies
    dependencies = parsed.get("dependencies", {})
    if isinstance(dependencies, dict):
        packages = list(dependencies.keys())

    return {
        "packages": packages,
        "lockfile_version": str(lock_version),
    }


def _extract_pnpm_lock_yaml(raw_content: str) -> dict:
    packages: list[str] = []
    importers: list[str] = []

    package_pattern = re.compile(r"^\s{2}/?(@?[^:/\s][^:]*)\:")
    importer_pattern = re.compile(r"^\s{2}([^:\s][^:]*)\:")

    current_section = ""
    for line in (raw_content or "").splitlines():
        stripped = line.rstrip()
        if not stripped or stripped.lstrip().startswith("#"):
            continue

        if stripped == "packages:":
            current_section = "packages"
            continue
        if stripped == "importers:":
            current_section = "importers"
            continue
        if not line.startswith("  "):
            current_section = ""
            continue

        if current_section == "packages":
            match = package_pattern.match(line)
            if match:
                package_name = match.group(1).split("(", 1)[0].strip("/")
                if package_name and package_name not in packages:
                    packages.append(package_name)
        elif current_section == "importers":
            match = importer_pattern.match(line)
            if match:
                importer = match.group(1).strip()
                if importer and importer not in importers:
                    importers.append(importer)

    return {
        "packages": packages,
        "importers": importers,
    }


def _extract_tsconfig_json(raw_content: str) -> dict:
    """Extract TypeScript compiler configuration."""
    try:
        parsed = _load_json_robust(raw_content or "{}")
    except Exception:
        return _error_payload(raw_content, "invalid_json")

    if not isinstance(parsed, dict):
        return _error_payload(raw_content, "invalid_json_root")

    compiler_options = parsed.get("compilerOptions", {})
    if not isinstance(compiler_options, dict):
        compiler_options = {}

    # Extract key compiler settings
    settings = {
        "target": compiler_options.get("target", ""),
        "module": compiler_options.get("module", ""),
        "lib": compiler_options.get("lib", []),
        "strict": compiler_options.get("strict", False),
        "esModuleInterop": compiler_options.get("esModuleInterop", False),
    }

    # Remove empty values
    settings = {k: v for k, v in settings.items() if v}

    extends = parsed.get("extends")
    extends_value = [extends] if extends else []

    include_files = parsed.get("include", [])
    if not isinstance(include_files, list):
        include_files = []

    exclude_files = parsed.get("exclude", [])
    if not isinstance(exclude_files, list):
        exclude_files = []

    return {
        "compiler_options": settings,
        "extends": extends_value,
        "include": include_files,
        "exclude": exclude_files,
    }


def _extract_json_config(raw_content: str) -> dict:
    try:
        parsed = json.loads(raw_content or "{}")
    except Exception:
        return _error_payload(raw_content, "invalid_json")

    if not isinstance(parsed, dict):
        return _error_payload(raw_content, "invalid_json_root")

    return {
        "keys": sorted(parsed.keys()),
    }


def _extract_host_json(raw_content: str) -> dict:
    try:
        parsed = json.loads(raw_content or "{}")
    except Exception:
        return _error_payload(raw_content, "invalid_json")

    if not isinstance(parsed, dict):
        return _error_payload(raw_content, "invalid_json_root")

    extension_bundle = parsed.get("extensionBundle", {})
    logging_config = parsed.get("logging", {})

    return {
        "version": parsed.get("version", ""),
        "extensions": sorted(parsed.get("extensions", {}).keys()) if isinstance(parsed.get("extensions"), dict) else [],
        "extension_bundle": {
            "id": extension_bundle.get("id", "") if isinstance(extension_bundle, dict) else "",
            "version": extension_bundle.get("version", "") if isinstance(extension_bundle, dict) else "",
        },
        "logging_keys": sorted(logging_config.keys()) if isinstance(logging_config, dict) else [],
    }


def _extract_staticwebapp_config_json(raw_content: str) -> dict:
    try:
        parsed = json.loads(raw_content or "{}")
    except Exception:
        return _error_payload(raw_content, "invalid_json")

    if not isinstance(parsed, dict):
        return _error_payload(raw_content, "invalid_json_root")

    routes = parsed.get("routes", [])
    response_overrides = parsed.get("responseOverrides", {})
    global_headers = parsed.get("globalHeaders", {})
    navigation_fallback = parsed.get("navigationFallback", {})

    route_patterns: list[str] = []
    allowed_roles: list[str] = []
    if isinstance(routes, list):
        for route in routes:
            if not isinstance(route, dict):
                continue
            route_value = route.get("route")
            if route_value:
                route_patterns.append(str(route_value))
            roles = route.get("allowedRoles", [])
            if isinstance(roles, list):
                for role in roles:
                    role_text = str(role)
                    if role_text not in allowed_roles:
                        allowed_roles.append(role_text)

    return {
        "route_count": len(route_patterns),
        "routes": route_patterns[:20],
        "allowed_roles": allowed_roles,
        "response_override_keys": sorted(response_overrides.keys()) if isinstance(response_overrides, dict) else [],
        "global_header_keys": sorted(global_headers.keys()) if isinstance(global_headers, dict) else [],
        "navigation_fallback": navigation_fallback.get("rewrite", "") if isinstance(navigation_fallback, dict) else "",
    }


def _extract_yarn_lock(raw_content: str) -> dict:
    """Extract package names and versions from yarn.lock format."""
    packages: list[str] = []
    version_count = 0

    # Parse yarn.lock format: package_name@version_spec:
    # Example: "lodash@^4.17.21:"
    package_pattern = re.compile(r'^([a-zA-Z0-9._@/-]+)@')

    for line in (raw_content or "").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue

        # Match package@version: pattern at line start
        match = package_pattern.match(text)
        if match:
            package_name = match.group(1)
            if package_name not in packages:
                packages.append(package_name)
            version_count += 1

    return {
        "packages": packages,
        "dependency_count": version_count,
    }


def _extract_nvmrc(raw_content: str) -> dict:
    version = (raw_content or "").strip().splitlines()
    resolved = version[0].strip() if version else ""
    return {
        "node_version": resolved,
    }


def _extract_ini_config(raw_content: str) -> dict:
    parser = configparser.ConfigParser()
    try:
        parser.read_string(raw_content or "")
    except Exception:
        return _error_payload(raw_content, "invalid_ini")

    sections = parser.sections()
    section_keys = {
        section: sorted(list(parser[section].keys()))
        for section in sections
    }
    return {
        "sections": sections,
        "section_keys": section_keys,
    }


def _extract_assignment_config(raw_content: str) -> dict:
    keys: list[str] = []
    pattern = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_-]*)\s*=')

    for line in (raw_content or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("//"):
            continue
        match = pattern.match(line)
        if match:
            keys.append(match.group(1))

    return {
        "keys": keys,
    }


def _extract_yaml_assignment_config(raw_content: str) -> dict:
    keys: list[str] = []
    pattern = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_-]*)\s*:')

    for line in (raw_content or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = pattern.match(line)
        if match:
            key = match.group(1)
            if key not in keys:
                keys.append(key)

    return {
        "keys": keys,
    }


def _extract_pipfile(raw_content: str) -> dict:
    if tomllib is None:
        return _error_payload(raw_content, "tomllib_unavailable")
    try:
        parsed = tomllib.loads(raw_content or "")
    except Exception:
        return _error_payload(raw_content, "invalid_toml")

    if not isinstance(parsed, dict):
        return _error_payload(raw_content, "invalid_toml_root")

    packages = parsed.get("packages", {})
    dev_packages = parsed.get("dev-packages", {})
    requires = parsed.get("requires", {})

    return {
        "packages": sorted(packages.keys()) if isinstance(packages, dict) else [],
        "dev_packages": sorted(dev_packages.keys()) if isinstance(dev_packages, dict) else [],
        "python_version": requires.get("python_version", "") if isinstance(requires, dict) else "",
    }


def _extract_pipfile_lock(raw_content: str) -> dict:
    try:
        parsed = json.loads(raw_content or "{}")
    except Exception:
        return _error_payload(raw_content, "invalid_json")

    if not isinstance(parsed, dict):
        return _error_payload(raw_content, "invalid_json_root")

    default_packages = parsed.get("default", {})
    develop_packages = parsed.get("develop", {})

    return {
        "default": sorted(default_packages.keys()) if isinstance(default_packages, dict) else [],
        "develop": sorted(develop_packages.keys()) if isinstance(develop_packages, dict) else [],
    }


def _extract_poetry_lock(raw_content: str) -> dict:
    package_names: list[str] = []
    package_pattern = re.compile(r'^name\s*=\s*"([^"]+)"')

    for line in (raw_content or "").splitlines():
        match = package_pattern.match(line.strip())
        if match:
            package_names.append(match.group(1))

    return {
        "packages": package_names,
    }


def _extract_go_mod(raw_content: str) -> dict:
    module_name = ""
    go_version = ""
    requires: list[str] = []

    in_require_block = False
    for line in (raw_content or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        if stripped.startswith("module "):
            module_name = stripped.split(None, 1)[1].strip()
            continue
        if stripped.startswith("go "):
            go_version = stripped.split(None, 1)[1].strip()
            continue
        if stripped == "require (":
            in_require_block = True
            continue
        if in_require_block and stripped == ")":
            in_require_block = False
            continue
        if stripped.startswith("require "):
            dep = stripped[len("require "):].split()[0]
            requires.append(dep)
            continue
        if in_require_block:
            dep = stripped.split()[0]
            requires.append(dep)

    return {
        "module": module_name,
        "go_version": go_version,
        "dependencies": requires,
    }


def _extract_cargo_toml(raw_content: str) -> dict:
    if tomllib is None:
        return _error_payload(raw_content, "tomllib_unavailable")
    try:
        parsed = tomllib.loads(raw_content or "")
    except Exception:
        return _error_payload(raw_content, "invalid_toml")

    if not isinstance(parsed, dict):
        return _error_payload(raw_content, "invalid_toml_root")

    package = parsed.get("package", {})
    dependencies = parsed.get("dependencies", {})
    features = parsed.get("features", {})

    return {
        "name": package.get("name", "") if isinstance(package, dict) else "",
        "edition": package.get("edition", "") if isinstance(package, dict) else "",
        "dependencies": sorted(dependencies.keys()) if isinstance(dependencies, dict) else [],
        "features": sorted(features.keys()) if isinstance(features, dict) else [],
    }


def _extract_gemfile(raw_content: str) -> dict:
    gems: list[str] = []
    sources: list[str] = []

    gem_pattern = re.compile(r'^gem\s+["\']([^"\']+)["\']')
    source_pattern = re.compile(r'^source\s+["\']([^"\']+)["\']')

    for line in (raw_content or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        gem_match = gem_pattern.match(stripped)
        if gem_match:
            gems.append(gem_match.group(1))
            continue
        source_match = source_pattern.match(stripped)
        if source_match:
            sources.append(source_match.group(1))

    return {
        "gems": gems,
        "sources": sources,
    }


def _extract_composer_json(raw_content: str) -> dict:
    try:
        parsed = _load_json_robust(raw_content or "{}")
    except Exception:
        return _error_payload(raw_content, "invalid_json")

    if not isinstance(parsed, dict):
        return _error_payload(raw_content, "invalid_json_root")

    require = parsed.get("require", {})
    require_dev = parsed.get("require-dev", {})
    autoload = parsed.get("autoload", {})

    return {
        "require": sorted(require.keys()) if isinstance(require, dict) else [],
        "require_dev": sorted(require_dev.keys()) if isinstance(require_dev, dict) else [],
        "autoload_keys": sorted(autoload.keys()) if isinstance(autoload, dict) else [],
    }


def _extract_composer_lock(raw_content: str) -> dict:
    try:
        parsed = _load_json_robust(raw_content or "{}")
    except Exception:
        return _error_payload(raw_content, "invalid_json")

    if not isinstance(parsed, dict):
        return _error_payload(raw_content, "invalid_json_root")

    package_names: list[str] = []
    package_dev_names: list[str] = []

    packages = parsed.get("packages", [])
    if isinstance(packages, list):
        for pkg in packages:
            if isinstance(pkg, dict):
                name = pkg.get("name")
                if isinstance(name, str) and name:
                    package_names.append(name)

    packages_dev = parsed.get("packages-dev", [])
    if isinstance(packages_dev, list):
        for pkg in packages_dev:
            if isinstance(pkg, dict):
                name = pkg.get("name")
                if isinstance(name, str) and name:
                    package_dev_names.append(name)

    content_hash = ""
    metadata = parsed.get("content-hash")
    if isinstance(metadata, str):
        content_hash = metadata
    elif isinstance(parsed.get("metadata"), dict):
        content_hash = str(parsed["metadata"].get("content-hash", ""))

    return {
        "packages": package_names,
        "packages_dev": package_dev_names,
        "content_hash": content_hash,
    }


def _extract_xml_config(raw_content: str) -> dict:
    try:
        root = ET.fromstring(raw_content or "")
    except Exception:
        return _error_payload(raw_content, "invalid_xml")

    tags: list[str] = []
    for elem in root.iter():
        tag = elem.tag.split("}", 1)[-1]
        if tag not in tags:
            tags.append(tag)

    return {
        "root": root.tag.split("}", 1)[-1],
        "tags": tags[:25],
    }


def _extract_pom_xml(raw_content: str) -> dict:
    try:
        root = ET.fromstring(raw_content or "")
    except Exception:
        return _error_payload(raw_content, "invalid_xml")

    def _find_text(name: str) -> str:
        for elem in root.iter():
            if elem.tag.split("}", 1)[-1] == name and elem.text:
                return elem.text.strip()
        return ""

    dependencies: list[str] = []
    current_group = ""
    for elem in root.iter():
        tag = elem.tag.split("}", 1)[-1]
        text = (elem.text or "").strip()
        if tag == "groupId":
            current_group = text
        elif tag == "artifactId" and text:
            dependencies.append(f"{current_group}:{text}" if current_group else text)

    return {
        "group_id": _find_text("groupId"),
        "artifact_id": _find_text("artifactId"),
        "version": _find_text("version"),
        "dependencies": dependencies[1:],
    }


def _extract_gradle(raw_content: str) -> dict:
    plugins: list[str] = []
    dependencies: list[str] = []

    plugin_pattern = re.compile(r'id\s+["\']([^"\']+)["\']')
    dependency_pattern = re.compile(r'["\']([A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+:[^"\']+)["\']')

    for line in (raw_content or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        plugin_match = plugin_pattern.search(stripped)
        if plugin_match:
            plugins.append(plugin_match.group(1))
        dependency_match = dependency_pattern.search(stripped)
        if dependency_match:
            dependencies.append(dependency_match.group(1))

    return {
        "plugins": plugins,
        "dependencies": dependencies,
    }


def _extract_settings_gradle(raw_content: str) -> dict:
    includes: list[str] = []
    root_project_name = ""

    include_pattern = re.compile(r'include\(([^)]+)\)|include\s+(.+)')
    root_pattern = re.compile(r'rootProject\.name\s*=\s*["\']([^"\']+)["\']')

    for line in (raw_content or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        root_match = root_pattern.search(stripped)
        if root_match:
            root_project_name = root_match.group(1)
        include_match = include_pattern.search(stripped)
        if include_match:
            include_body = include_match.group(1) or include_match.group(2) or ""
            parts = [part.strip().strip("\"'") for part in include_body.split(",")]
            includes.extend([part for part in parts if part])

    return {
        "root_project": root_project_name,
        "includes": includes,
    }


def _extract_github_actions(raw_content: str) -> dict:
    jobs: list[str] = []
    actions: list[str] = []

    current_section = ""
    section_indent = None
    job_pattern = re.compile(r'^\s{2}([A-Za-z0-9_-]+):\s*$')
    uses_pattern = re.compile(r'\buses:\s*([^\s#]+)')

    for line in (raw_content or "").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())

        if stripped == "jobs:":
            current_section = "jobs"
            section_indent = indent
            continue

        if current_section and section_indent is not None and indent <= section_indent:
            current_section = ""
            section_indent = None

        if current_section == "jobs":
            job_match = job_pattern.match(line)
            if job_match:
                jobs.append(job_match.group(1))

        uses_match = uses_pattern.search(stripped)
        if uses_match:
            actions.append(uses_match.group(1))

    return {
        "jobs": jobs,
        "actions": actions,
    }


def _extract_kubernetes_yaml(raw_content: str) -> dict:
    kinds: list[str] = []
    api_versions: list[str] = []
    resource_names: list[str] = []
    container_images: list[str] = []

    kind_pattern = re.compile(r'^\s*kind\s*:\s*(.+?)\s*$')
    api_version_pattern = re.compile(r'^\s*apiVersion\s*:\s*(.+?)\s*$')
    name_pattern = re.compile(r'^\s*name\s*:\s*(.+?)\s*$')
    image_pattern = re.compile(r'^\s*image\s*:\s*(.+?)\s*$')

    in_metadata = False
    metadata_indent = 0
    for line in (raw_content or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())
        if stripped == "metadata:":
            in_metadata = True
            metadata_indent = indent
            continue
        if in_metadata and indent <= metadata_indent and stripped != "metadata:":
            in_metadata = False

        kind_match = kind_pattern.match(line)
        if kind_match:
            kind = kind_match.group(1).strip('"\'')
            if kind not in kinds:
                kinds.append(kind)
            continue

        api_version_match = api_version_pattern.match(line)
        if api_version_match:
            api_version = api_version_match.group(1).strip('"\'')
            if api_version not in api_versions:
                api_versions.append(api_version)
            continue

        if in_metadata:
            name_match = name_pattern.match(line)
            if name_match:
                resource_name = name_match.group(1).strip('"\'')
                if resource_name not in resource_names:
                    resource_names.append(resource_name)
                continue

        image_match = image_pattern.match(line)
        if image_match:
            image = image_match.group(1).strip('"\'')
            if image not in container_images:
                container_images.append(image)

    return {
        "api_versions": api_versions,
        "kinds": kinds,
        "resource_names": resource_names,
        "container_images": container_images,
    }


def _extract_helm_chart_yaml(raw_content: str) -> dict:
    values = _extract_yaml_assignment_config(raw_content)
    keys = values.get("keys", []) if isinstance(values, dict) else []

    def _find_scalar(field: str) -> str:
        pattern = re.compile(rf'^\s*{re.escape(field)}\s*:\s*(.+?)\s*$')
        for line in (raw_content or "").splitlines():
            match = pattern.match(line)
            if match:
                return match.group(1).strip('"\'')
        return ""

    return {
        "name": _find_scalar("name"),
        "chart_type": _find_scalar("type"),
        "version": _find_scalar("version"),
        "app_version": _find_scalar("appVersion"),
        "keys": keys,
    }


def _extract_godot_project(raw_content: str) -> dict:
    parser = configparser.ConfigParser()
    try:
        parser.read_string(raw_content or "")
    except Exception:
        return _error_payload(raw_content, "invalid_ini")

    sections = parser.sections()
    return {
        "config_version": parser.defaults().get("config_version", ""),
        "sections": sections,
        "has_autoload": any(section.startswith("autoload") for section in sections),
    }


def _extract_unity_project_version(raw_content: str) -> dict:
    version = ""
    revision = ""
    for line in (raw_content or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("m_EditorVersion:"):
            version = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("m_EditorVersionWithRevision:"):
            revision = stripped.split(":", 1)[1].strip()

    return {
        "editor_version": version,
        "editor_revision": revision,
    }


def _extract_unity_tagmanager(raw_content: str) -> dict:
    tags: list[str] = []
    layers: list[str] = []
    current_section = ""

    for line in (raw_content or "").splitlines():
        stripped = line.strip()
        if stripped == "tags:":
            current_section = "tags"
            continue
        if stripped == "layers:":
            current_section = "layers"
            continue
        if stripped.endswith(":") and stripped not in {"tags:", "layers:"}:
            current_section = ""
            continue
        if stripped.startswith("- "):
            value = stripped[2:].strip()
            if current_section == "tags" and value:
                tags.append(value)
            elif current_section == "layers" and value:
                layers.append(value)

    return {
        "tags": tags,
        "layers": layers,
    }


CONFIG_EXTRACTION_SCHEMAS = {
    "requirements.txt": _extract_requirements_txt,
    "requirements-dev.txt": _extract_requirements_txt,
    "pyproject.toml": _extract_pyproject_toml,
    "poetry.lock": _extract_poetry_lock,
    "pipfile": _extract_pipfile,
    "pipfile.lock": _extract_pipfile_lock,
    "setup.cfg": _extract_ini_config,
    "tox.ini": _extract_ini_config,
    "package.json": _extract_package_json,
    "package-lock.json": _extract_package_lock_json,
    "pnpm-lock.yaml": _extract_pnpm_lock_yaml,
    "tsconfig.json": _extract_tsconfig_json,
    "jsconfig.json": _extract_tsconfig_json,
    "yarn.lock": _extract_yarn_lock,
    ".nvmrc": _extract_nvmrc,
    "global.json": _extract_json_config,
    "host.json": _extract_host_json,
    "staticwebapp.config.json": _extract_staticwebapp_config_json,
    "appsettings.json": _extract_json_config,
    "appsettings.development.json": _extract_json_config,
    "directory.build.props": _extract_xml_config,
    "directory.build.targets": _extract_xml_config,
    "nuget.config": _extract_xml_config,
    "pom.xml": _extract_pom_xml,
    "build.gradle": _extract_gradle,
    "build.gradle.kts": _extract_gradle,
    "settings.gradle": _extract_settings_gradle,
    "settings.gradle.kts": _extract_settings_gradle,
    "go.mod": _extract_go_mod,
    "cargo.toml": _extract_cargo_toml,
    "gemfile": _extract_gemfile,
    "composer.json": _extract_composer_json,
    "composer.lock": _extract_composer_lock,
    "dockerfile": _extract_dockerfile,
    "docker-compose.yml": _extract_docker_compose,
    "docker-compose.yaml": _extract_docker_compose,
    "compose.yml": _extract_docker_compose,
    "compose.yaml": _extract_docker_compose,
    "deployment.yml": _extract_kubernetes_yaml,
    "deployment.yaml": _extract_kubernetes_yaml,
    "service.yml": _extract_kubernetes_yaml,
    "service.yaml": _extract_kubernetes_yaml,
    "ingress.yml": _extract_kubernetes_yaml,
    "ingress.yaml": _extract_kubernetes_yaml,
    "k8s.yml": _extract_kubernetes_yaml,
    "k8s.yaml": _extract_kubernetes_yaml,
    "chart.yaml": _extract_helm_chart_yaml,
    "values.yaml": _extract_yaml_assignment_config,
    ".github/workflows/ci.yml": _extract_github_actions,
    ".github/workflows/ci.yaml": _extract_github_actions,
    ".github/workflows/tests.yml": _extract_github_actions,
    ".github/workflows/tests.yaml": _extract_github_actions,
    ".github/workflows/build.yml": _extract_github_actions,
    ".github/workflows/build.yaml": _extract_github_actions,
    "main.tf": _extract_terraform,
    "*.tf": _extract_terraform,
    "*.bicep": _extract_bicep,
    "*.csproj": _extract_xml_config,
    "*.fsproj": _extract_xml_config,
    "*.vbproj": _extract_xml_config,
    "*.props": _extract_xml_config,
    "*.targets": _extract_xml_config,
    "*.tfvars": _extract_assignment_config,
    "*.k8s.yml": _extract_kubernetes_yaml,
    "*.k8s.yaml": _extract_kubernetes_yaml,
    "*.yaml": _extract_kubernetes_yaml,
    "*.yml": _extract_kubernetes_yaml,
    "project.godot": _extract_godot_project,
    "projectsettings/projectversion.txt": _extract_unity_project_version,
    "projectsettings/tagmanager.asset": _extract_unity_tagmanager,
    "*.uproject": _extract_json_config,
}


def get_config_extractor(file_path: str):
    normalized_path = (file_path or "").strip().lower()
    normalized_name = os.path.basename(normalized_path)
    if not normalized_name:
        return None

    if normalized_path in CONFIG_EXTRACTION_SCHEMAS:
        return CONFIG_EXTRACTION_SCHEMAS[normalized_path]

    if normalized_name in CONFIG_EXTRACTION_SCHEMAS:
        return CONFIG_EXTRACTION_SCHEMAS[normalized_name]

    for pattern, extractor in CONFIG_EXTRACTION_SCHEMAS.items():
        if "*" in pattern and (fnmatch(normalized_path, pattern) or fnmatch(normalized_name, pattern)):
            return extractor

    return None

