"""Phase 1 extraction tests for data_filter.py."""

from foliohive_shared.ai.data_filter import (
    CONFIG_EXTRACTION_SCHEMAS,
    extract_config_content,
    get_config_extractor,
)


class TestConfigExtractionRegistry:
    """Test CONFIG_EXTRACTION_SCHEMAS and dispatcher."""

    def test_registry_contains_priority_extractors(self):
        assert "requirements.txt" in CONFIG_EXTRACTION_SCHEMAS
        assert "pyproject.toml" in CONFIG_EXTRACTION_SCHEMAS
        assert "package.json" in CONFIG_EXTRACTION_SCHEMAS
        assert "docker-compose.yml" in CONFIG_EXTRACTION_SCHEMAS

    def test_get_extractor_returns_callable(self):
        extractor = get_config_extractor("requirements.txt")
        assert extractor is not None
        assert callable(extractor)

    def test_extractor_dispatcher_routes_by_filename(self):
        result = extract_config_content("path/to/package.json", '{"dependencies":{"react":"18"}}')
        assert result is not None
        assert result["dependencies"] == {"react": "18"}

    def test_unknown_file_returns_none_extractor(self):
        assert get_config_extractor("README.md") is None
        assert extract_config_content("README.md", "# docs") is None


class TestPythonExtractors:
    """Test Python config extractors."""

    def test_extract_requirements_txt(self):
        raw = "numpy>=1.21.0\npandas==1.3.0\n-r constraints.txt"
        result = extract_config_content("requirements.txt", raw)

        assert result is not None
        assert result["dependencies"] == ["numpy>=1.21.0", "pandas==1.3.0"]
        assert result["constraints"] == ["-r constraints.txt"]

    def test_extract_pyproject_toml(self):
        raw = """
[project]
dependencies = ["fastapi>=0.110", "uvicorn>=0.27"]

[tool.poetry.dependencies]
python = ">=3.11"
requests = "^2.32"
""".strip()
        result = extract_config_content("pyproject.toml", raw)

        assert result is not None
        assert "fastapi>=0.110" in result["project_dependencies"]
        assert result["poetry_dependencies"].get("requests") == "^2.32"

    def test_extract_pyproject_handles_invalid_toml(self):
        result = extract_config_content("pyproject.toml", "[project\ninvalid")

        assert result is not None
        assert "error" in result
        assert result["error"] == "invalid_toml"
        assert "raw_sample" in result


class TestNodeExtractors:
    """Test Node.js config extractors."""

    def test_extract_package_json(self):
        raw = """
{
  "dependencies": {"express": "^4.19.0"},
  "devDependencies": {"jest": "^29.7.0"},
  "scripts": {"test": "jest"}
}
""".strip()
        result = extract_config_content("package.json", raw)

        assert result is not None
        assert result["dependencies"]["express"] == "^4.19.0"
        assert result["devDependencies"]["jest"] == "^29.7.0"
        assert result["scripts"]["test"] == "jest"

    def test_extract_package_json_handles_invalid_json(self):
        result = extract_config_content("package.json", "{ bad json }")

        assert result is not None
        assert result["error"] == "invalid_json"
        assert "raw_sample" in result


class TestContainerExtractors:
    """Test container config extractors."""

    def test_extract_dockerfile_commands(self):
        raw = """
FROM python:3.12-slim
ENV APP_ENV=prod PORT=8000
EXPOSE 8000 8001
RUN pip install -r requirements.txt
""".strip()
        result = extract_config_content("Dockerfile", raw)

        assert result is not None
        assert result["base_images"] == ["python:3.12-slim"]
        assert result["exposed_ports"] == ["8000", "8001"]
        assert result["env_vars"]["APP_ENV"] == "prod"

    def test_extract_docker_compose_services(self):
        raw = """
services:
  web:
    image: nginx
  api:
    image: app
networks:
  default:
volumes:
  data:
""".strip()
        result = extract_config_content("docker-compose.yml", raw)

        assert result is not None
        assert result["services"] == ["web", "api"]
        assert result["networks"] == ["default"]
        assert result["volumes"] == ["data"]


class TestIaCExtractors:
    """Test IaC config extractors."""

    def test_extract_terraform_resources(self):
        raw = """
provider "azurerm" {}
resource "azurerm_resource_group" "rg" {}
resource "azurerm_storage_account" "sa" {}
""".strip()
        result = extract_config_content("main.tf", raw)

        assert result is not None
        assert result["providers"] == ["azurerm"]
        assert "azurerm_resource_group.rg" in result["resources"]
        assert "azurerm_storage_account.sa" in result["resources"]

    def test_extract_bicep_resources(self):
        raw = """
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {}
module monitoring './modules/monitoring.bicep' = {}
""".strip()
        result = extract_config_content("main.bicep", raw)

        assert result is not None
        assert result["resources"] == ["storage"]
        assert result["modules"] == ["monitoring"]
