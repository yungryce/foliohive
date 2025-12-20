"""
Unit tests for FileTypeAnalyzer.

Tests file type categorization and scoring using linguist data:
- categorize_file_type
- analyze_repository_files
- calculate_type_score
"""
import pytest
import yaml
from cloudfolio_shared.ai.type_analyzer import FileTypeAnalyzer


class TestFileTypeAnalyzer:
    """Test suite for FileTypeAnalyzer functionality."""

    def test_init_loads_linguist_data(self, temp_linguist_file):
        """Test that analyzer loads linguist data on initialization."""
        analyzer = FileTypeAnalyzer(linguist_data_path=temp_linguist_file)
        
        assert analyzer.languages_data is not None
        assert 'Python' in analyzer.languages_data
        assert 'JavaScript' in analyzer.languages_data
        
    def test_categorize_python_file(self, temp_linguist_file):
        """Test categorizing Python file extension."""
        analyzer = FileTypeAnalyzer(linguist_data_path=temp_linguist_file)
        
        result = analyzer.categorize_file_type('.py')
        
        assert result == 'programming'
        
    def test_categorize_javascript_file(self, temp_linguist_file):
        """Test categorizing JavaScript file extension."""
        analyzer = FileTypeAnalyzer(linguist_data_path=temp_linguist_file)
        
        result = analyzer.categorize_file_type('.js')
        
        assert result == 'programming'
        
    def test_categorize_json_file(self, temp_linguist_file):
        """Test categorizing JSON file as data type."""
        analyzer = FileTypeAnalyzer(linguist_data_path=temp_linguist_file)
        
        result = analyzer.categorize_file_type('.json')
        
        assert result == 'data'
        
    def test_categorize_markdown_file(self, temp_linguist_file):
        """Test categorizing Markdown file as prose."""
        analyzer = FileTypeAnalyzer(linguist_data_path=temp_linguist_file)
        
        result = analyzer.categorize_file_type('.md')
        
        assert result == 'prose'
        
    def test_categorize_yaml_file(self, temp_linguist_file):
        """Test categorizing YAML file as data type."""
        analyzer = FileTypeAnalyzer(linguist_data_path=temp_linguist_file)
        
        result = analyzer.categorize_file_type('.yml')
        
        assert result == 'data'
        
    def test_categorize_unknown_extension(self, temp_linguist_file):
        """Test that unknown extensions return 'nil'."""
        analyzer = FileTypeAnalyzer(linguist_data_path=temp_linguist_file)
        
        result = analyzer.categorize_file_type('.unknown')
        
        assert result == 'nil'
        
    def test_categorize_extension_without_dot(self, temp_linguist_file):
        """Test categorizing extension without leading dot."""
        analyzer = FileTypeAnalyzer(linguist_data_path=temp_linguist_file)
        
        result = analyzer.categorize_file_type('py')
        
        assert result == 'programming'
        
    def test_categorize_case_insensitive(self, temp_linguist_file):
        """Test that categorization is case-insensitive."""
        analyzer = FileTypeAnalyzer(linguist_data_path=temp_linguist_file)
        
        result1 = analyzer.categorize_file_type('.PY')
        result2 = analyzer.categorize_file_type('.Py')
        result3 = analyzer.categorize_file_type('.py')
        
        assert result1 == result2 == result3 == 'programming'
        
    def test_analyze_repository_files(self, temp_linguist_file, sample_file_extensions):
        """Test analyzing repository file distribution."""
        analyzer = FileTypeAnalyzer(linguist_data_path=temp_linguist_file)
        
        result = analyzer.analyze_repository_files(sample_file_extensions)
        
        # Check structure
        assert 'programming' in result
        assert 'data' in result
        assert 'markup' in result
        assert 'prose' in result
        assert 'nil' in result
        
        # Check counts (from sample_file_extensions fixture)
        # .py: 25 (programming), .js: 10 (programming)
        # .json: 5 (data), .yml: 2 (data)
        # .md: 3 (prose), .txt: 1 (prose)
        assert result['programming'] == 35  # 25 + 10
        assert result['data'] == 7  # 5 + 2
        assert result['prose'] == 4  # 3 + 1
        assert result['markup'] == 0
        assert result['nil'] == 0
        
    def test_analyze_repository_empty_files(self, temp_linguist_file):
        """Test analyzing repository with no files."""
        analyzer = FileTypeAnalyzer(linguist_data_path=temp_linguist_file)
        
        result = analyzer.analyze_repository_files({})
        
        assert all(count == 0 for count in result.values())
        
    def test_analyze_repository_mixed_extensions(self, temp_linguist_file):
        """Test analyzing repository with known and unknown extensions."""
        analyzer = FileTypeAnalyzer(linguist_data_path=temp_linguist_file)
        
        files = {
            '.py': 10,
            '.unknown': 5,
            '.js': 8,
            '.xyz': 3
        }
        
        result = analyzer.analyze_repository_files(files)
        
        assert result['programming'] == 18  # 10 + 8
        assert result['nil'] == 8  # 5 + 3
        
    def test_calculate_type_score_programming_heavy(self, temp_linguist_file):
        """Test score calculation for programming-heavy repository."""
        analyzer = FileTypeAnalyzer(linguist_data_path=temp_linguist_file)
        
        categorized = {
            'programming': 100,
            'data': 10,
            'markup': 5,
            'prose': 5,
            'nil': 0
        }
        
        score = analyzer.calculate_type_score(categorized)
        
        # Should be high score (programming has weight 3)
        assert score > 0.8
        assert score <= 1.0
        
    def test_calculate_type_score_data_heavy(self, temp_linguist_file):
        """Test score calculation for data-heavy repository."""
        analyzer = FileTypeAnalyzer(linguist_data_path=temp_linguist_file)
        
        categorized = {
            'programming': 10,
            'data': 100,
            'markup': 5,
            'prose': 5,
            'nil': 0
        }
        
        score = analyzer.calculate_type_score(categorized)
        
        # Should be moderate score (data has weight 2)
        assert 0.5 < score < 0.8
        
    def test_calculate_type_score_prose_heavy(self, temp_linguist_file):
        """Test score calculation for documentation-heavy repository."""
        analyzer = FileTypeAnalyzer(linguist_data_path=temp_linguist_file)
        
        categorized = {
            'programming': 5,
            'data': 5,
            'markup': 10,
            'prose': 100,
            'nil': 0
        }
        
        score = analyzer.calculate_type_score(categorized)
        
        # Should be lower score (prose has weight 1)
        assert score < 0.5
        
    def test_calculate_type_score_nil_only(self, temp_linguist_file):
        """Test score calculation for repository with only unknown files."""
        analyzer = FileTypeAnalyzer(linguist_data_path=temp_linguist_file)
        
        categorized = {
            'programming': 0,
            'data': 0,
            'markup': 0,
            'prose': 0,
            'nil': 100
        }
        
        score = analyzer.calculate_type_score(categorized)
        
        # Should be zero score (nil has weight 0)
        assert score == 0.0
        
    def test_calculate_type_score_empty_repository(self, temp_linguist_file):
        """Test score calculation for empty repository."""
        analyzer = FileTypeAnalyzer(linguist_data_path=temp_linguist_file)
        
        categorized = {
            'programming': 0,
            'data': 0,
            'markup': 0,
            'prose': 0,
            'nil': 0
        }
        
        score = analyzer.calculate_type_score(categorized)
        
        assert score == 0.0
        
    def test_calculate_type_score_normalized(self, temp_linguist_file):
        """Test that score is always between 0 and 1."""
        analyzer = FileTypeAnalyzer(linguist_data_path=temp_linguist_file)
        
        # Test various distributions
        test_cases = [
            {'programming': 100, 'data': 50, 'markup': 25, 'prose': 10, 'nil': 5},
            {'programming': 10, 'data': 10, 'markup': 10, 'prose': 10, 'nil': 10},
            {'programming': 1000, 'data': 0, 'markup': 0, 'prose': 0, 'nil': 0},
        ]
        
        for categorized in test_cases:
            score = analyzer.calculate_type_score(categorized)
            assert 0.0 <= score <= 1.0
            
    def test_full_workflow(self, temp_linguist_file, sample_file_extensions):
        """Test complete workflow from extensions to score."""
        analyzer = FileTypeAnalyzer(linguist_data_path=temp_linguist_file)
        
        # Step 1: Categorize files
        categorized = analyzer.analyze_repository_files(sample_file_extensions)
        
        # Step 2: Calculate score
        score = analyzer.calculate_type_score(categorized)
        
        # Verify reasonable result
        assert isinstance(categorized, dict)
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0
        
        # Should be high score (mostly programming files)
        assert score > 0.7


@pytest.mark.parametrize("extension,expected_type", [
    ('.py', 'programming'),
    ('.pyw', 'programming'),
    ('.js', 'programming'),
    ('.mjs', 'programming'),
    ('.json', 'data'),
    ('.yml', 'data'),
    ('.yaml', 'data'),
    ('.md', 'prose'),
    ('.markdown', 'prose'),
    ('.txt', 'prose'),
    ('.unknown', 'nil'),
])
def test_categorize_file_type_parametrized(temp_linguist_file, extension, expected_type):
    """Parametrized test for various file extensions."""
    analyzer = FileTypeAnalyzer(linguist_data_path=temp_linguist_file)
    result = analyzer.categorize_file_type(extension)
    assert result == expected_type


def test_analyzer_handles_language_without_type(temp_linguist_file, tmp_path):
    """Test that analyzer handles languages without explicit type."""
    # Create linguist file with language missing 'type' field
    linguist_content = """
NoTypeLanguage:
  extensions:
  - ".notype"
"""
    linguist_file = tmp_path / "test_languages.yml"
    linguist_file.write_text(linguist_content)
    
    analyzer = FileTypeAnalyzer(linguist_data_path=str(linguist_file))
    result = analyzer.categorize_file_type('.notype')
    
    # Should default to 'nil' when type is missing
    assert result == 'nil'
