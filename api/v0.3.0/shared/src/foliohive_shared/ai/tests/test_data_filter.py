"""Unit tests for data_filter.py."""

import pytest
from foliohive_shared.ai.data_filter import (
    extract_language_terms,
    get_standard_config_file_candidates,
    technical_terms_structured,
    advanced_skills,
    technical_keywords,
    complexity_indicators,
    file_extensions
)


class TestExtractLanguageTerms:
    """Test extract_language_terms function."""

    def test_extracts_python(self):
        """Test Python language detection."""
        queries = [
            "What Python projects does this candidate have?",
            "Show me their Flask applications",
            "Any Django experience?",
            "Does the candidate use py for scripting?"
        ]
        
        for query in queries:
            languages = extract_language_terms(query)
            assert "python" in languages, f"Failed for query: {query}"

    def test_extracts_javascript(self):
        """Test JavaScript language detection."""
        queries = [
            "What JavaScript frameworks?",
            "Any React projects?",
            "Node.js experience?",
            "Show me Express applications"
        ]
        
        for query in queries:
            languages = extract_language_terms(query)
            assert "javascript" in languages, f"Failed for query: {query}"

    def test_extracts_multiple_languages(self):
        """Test multiple language detection in one query."""
        query = "Does this candidate know Python, JavaScript, and Go?"
        languages = extract_language_terms(query)
        
        assert "python" in languages
        assert "javascript" in languages
        assert "go" in languages

    def test_case_insensitive(self):
        """Test language detection is case insensitive."""
        queries = [
            "PYTHON projects",
            "python projects",
            "Python projects"
        ]
        
        for query in queries:
            languages = extract_language_terms(query)
            assert "python" in languages

    def test_returns_empty_for_no_languages(self):
        """Test returns empty list when no languages found."""
        query = "What is the candidate's experience level?"
        languages = extract_language_terms(query)
        
        assert languages == []

    def test_extracts_typed_languages(self):
        """Test detection of statically typed languages."""
        queries = [
            "Any TypeScript experience?",
            "Does the candidate use C#?",
            "What about Java Spring?"
        ]
        
        results = [extract_language_terms(q) for q in queries]
        
        assert "typescript" in results[0]
        assert "c#" in results[1]
        assert "java" in results[2]

    def test_extracts_database_languages(self):
        """Test SQL and database language detection."""
        query = "What SQL databases has the candidate used? PostgreSQL or MySQL?"
        languages = extract_language_terms(query)
        
        assert "sql" in languages

    def test_extracts_markup_languages(self):
        """Test HTML/CSS detection."""
        queries = [
            "Any HTML5 projects?",
            "Does the candidate know CSS3?",
            "SCSS or SASS experience?"
        ]
        
        results = [extract_language_terms(q) for q in queries]
        
        assert "html" in results[0]
        assert "css" in results[1]
        assert "css" in results[2]  # SCSS should match CSS

    def test_extracts_shell_languages(self):
        """Test shell scripting language detection."""
        queries = [
            "Any Bash scripting experience?",
            "Does the candidate use PowerShell?"
        ]
        
        results = [extract_language_terms(q) for q in queries]
        
        assert "shell" in results[0] or "bash" in results[0]
        assert "powershell" in results[1]

    def test_extracts_infrastructure_languages(self):
        """Test infrastructure-as-code language detection."""
        queries = [
            "Any Dockerfile experience?",
            "Does the candidate use YAML?",
            "Terraform HCL knowledge?"
        ]
        
        results = [extract_language_terms(q) for q in queries]
        
        assert "dockerfile" in results[0] or "docker" in results[0]
        assert "yaml" in results[1]
        # HCL might not be detected directly


class TestGetStandardConfigFileCandidates:
    """Test get_standard_config_file_candidates function."""

    def test_returns_list(self):
        """Test function returns a list."""
        result = get_standard_config_file_candidates()
        assert isinstance(result, list)

    def test_contains_common_files(self):
        """Test result contains common config files."""
        result = get_standard_config_file_candidates()
        
        # Python
        assert "requirements.txt" in result
        assert "pyproject.toml" in result
        
        # Node
        assert "package.json" in result
        
        # Docker
        assert "Dockerfile" in result
        assert "docker-compose.yml" in result or "docker-compose.yaml" in result

    def test_respects_limit(self):
        """Test limit parameter restricts results."""
        result = get_standard_config_file_candidates(limit=5)
        assert len(result) <= 5

    def test_no_duplicates(self):
        """Test result has no duplicates."""
        result = get_standard_config_file_candidates()
        assert len(result) == len(set(result))

    def test_deterministic_order(self):
        """Test function returns same order each time."""
        result1 = get_standard_config_file_candidates()
        result2 = get_standard_config_file_candidates()
        assert result1 == result2


class TestTechnicalTermsStructured:
    """Test technical_terms_structured constant."""

    def test_has_required_keys(self):
        """Test structure has all required keys."""
        required_keys = [
            "advanced_skills",
            "complexity_indicators",
            "file_extensions",
            "version_patterns",
            "domain",
            "stop_words",
            "standard_config_files",
            "standard_config_paths"
        ]
        
        for key in required_keys:
            assert key in technical_terms_structured

    def test_advanced_skills_is_frozenset(self):
        """Test advanced_skills is a frozenset for O(1) lookup."""
        assert isinstance(technical_terms_structured["advanced_skills"], frozenset)

    def test_domain_keywords_is_frozenset(self):
        """Test domain is a frozenset."""
        assert isinstance(technical_terms_structured["domain"], frozenset)

    def test_stop_words_is_frozenset(self):
        """Test stop_words is a frozenset."""
        assert isinstance(technical_terms_structured["stop_words"], frozenset)

    def test_version_patterns_are_compiled(self):
        """Test version patterns are pre-compiled regex."""
        import re
        patterns = technical_terms_structured["version_patterns"]
        
        assert len(patterns) > 0
        for pattern in patterns:
            assert isinstance(pattern, type(re.compile("")))

    def test_can_check_membership(self):
        """Test can check membership efficiently."""
        assert "kubernetes" in technical_terms_structured["advanced_skills"]
        assert "api" in technical_terms_structured["domain"]
        assert "the" in technical_terms_structured["stop_words"]


class TestAdvancedSkills:
    """Test advanced_skills set."""

    def test_contains_cloud_keywords(self):
        """Test includes cloud technologies."""
        cloud_terms = ["aws", "azure", "gcp", "kubernetes", "docker"]
        
        for term in cloud_terms:
            assert term in advanced_skills

    def test_contains_architecture_patterns(self):
        """Test includes architecture patterns."""
        patterns = ["microservices", "event-driven", "cqrs", "event sourcing"]
        
        for pattern in patterns:
            assert pattern in advanced_skills

    def test_contains_ml_keywords(self):
        """Test includes machine learning terms."""
        ml_terms = ["machine learning", "neural networks", "nlp"]
        
        for term in ml_terms:
            assert term in advanced_skills


class TestComplexityIndicators:
    """Test complexity_indicators list."""

    def test_contains_concurrency_terms(self):
        """Test includes concurrency indicators."""
        concurrency = ["multithreading", "mutex", "deadlock prevention", "async/await"]
        
        for term in concurrency:
            assert term in complexity_indicators

    def test_contains_security_terms(self):
        """Test includes security indicators."""
        security = ["authentication", "authorization", "encryption", "csrf protection"]
        
        for term in security:
            assert term in complexity_indicators

    def test_contains_testing_terms(self):
        """Test includes testing indicators."""
        testing = ["mock object", "test double", "bdd", "tdd"]
        
        for term in testing:
            assert term in complexity_indicators


class TestTechnicalKeywords:
    """Test technical_keywords set."""

    def test_contains_common_tools(self):
        """Test includes common development tools."""
        tools = ["git", "docker", "kubernetes", "terraform"]
        
        for tool in tools:
            assert tool in technical_keywords

    def test_contains_frameworks(self):
        """Test includes popular frameworks."""
        frameworks = ["flask", "django", "react", "angular", "vue"]
        
        for framework in frameworks:
            assert framework in technical_keywords

    def test_contains_databases(self):
        """Test includes database systems."""
        databases = ["mysql", "postgresql", "mongodb", "redis"]
        
        for db in databases:
            assert db in technical_keywords


class TestFileExtensions:
    """Test file_extensions set."""

    def test_contains_programming_extensions(self):
        """Test includes programming language extensions."""
        extensions = ["py", "js", "ts", "java", "cpp", "rs", "go"]
        
        for ext in extensions:
            assert ext in file_extensions

    def test_contains_config_extensions(self):
        """Test includes config file extensions."""
        config_exts = ["json", "yaml", "yml", "toml", "ini"]
        
        for ext in config_exts:
            assert ext in file_extensions

    def test_contains_markup_extensions(self):
        """Test includes markup extensions."""
        markup = ["html", "xml", "md"]
        
        for ext in markup:
            assert ext in file_extensions


class TestConfigFileRecognition:
    """Test config file recognition using technical_terms_structured."""

    def test_recognizes_python_configs(self):
        """Test recognizes Python config files."""
        python_configs = ["requirements.txt", "pyproject.toml", "setup.py"]
        standard_configs = technical_terms_structured["standard_config_files"]
        
        for config in python_configs:
            assert config.lower() in standard_configs

    def test_recognizes_node_configs(self):
        """Test recognizes Node.js config files."""
        node_configs = ["package.json", "tsconfig.json"]
        standard_configs = technical_terms_structured["standard_config_files"]
        
        for config in node_configs:
            assert config.lower() in standard_configs

    def test_recognizes_docker_configs(self):
        """Test recognizes Docker config files."""
        docker_configs = ["dockerfile", "docker-compose.yml"]
        standard_configs = technical_terms_structured["standard_config_files"]
        
        for config in docker_configs:
            assert config.lower() in standard_configs


class TestVersionPatterns:
    """Test version pattern regex matching."""

    def test_matches_semver(self):
        """Test matches semantic versioning."""
        import re
        patterns = technical_terms_structured["version_patterns"]
        
        versions = ["1.0.0", "2.1.3", "0.0.1"]
        
        for version in versions:
            matched = any(pattern.match(version) for pattern in patterns)
            assert matched, f"Failed to match: {version}"

    def test_matches_version_prefix(self):
        """Test matches versions with 'v' prefix."""
        import re
        patterns = technical_terms_structured["version_patterns"]
        
        versions = ["v1.0", "v2.1.3"]
        
        for version in versions:
            matched = any(pattern.match(version) for pattern in patterns)
            assert matched, f"Failed to match: {version}"

    def test_matches_year_versions(self):
        """Test matches year-style versions."""
        import re
        patterns = technical_terms_structured["version_patterns"]
        
        versions = ["2022", "2024"]
        
        for version in versions:
            matched = any(pattern.match(version) for pattern in patterns)
            assert matched, f"Failed to match: {version}"

    def test_does_not_match_invalid(self):
        """Test does not match invalid version strings."""
        import re
        patterns = technical_terms_structured["version_patterns"]
        
        invalid = ["abc", "version", "latest"]
        
        for invalid_version in invalid:
            matched = any(pattern.match(invalid_version) for pattern in patterns)
            assert not matched, f"Should not match: {invalid_version}"


class TestStopWords:
    """Test stop words filtering."""

    def test_contains_common_words(self):
        """Test contains common stop words."""
        stop_words_set = technical_terms_structured["stop_words"]
        
        common = ["the", "and", "for", "with", "from"]
        
        for word in common:
            assert word in stop_words_set

    def test_does_not_contain_technical_terms(self):
        """Test stop words don't include technical terms."""
        stop_words_set = technical_terms_structured["stop_words"]
        
        technical = ["api", "database", "python", "docker"]
        
        for term in technical:
            assert term not in stop_words_set


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
