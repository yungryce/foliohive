from typing import Dict, Any, Union, List
import yaml
import re
import logging

logger = logging.getLogger('portfolio.api')

class FileTypeAnalyzer:
    """
    Analyzes and categorizes file types in a repository using linguist/languages.yml.
    """
    def __init__(self, linguist_data_path: str = "linguist/languages.yml"):
        with open(linguist_data_path, 'r') as f:
            self.languages_data = yaml.safe_load(f)

    def categorize_file_type(self, extension: str) -> str:
        # Use regex to ensure exact match for extension (with or without leading dot)
        ext_pattern = re.compile(rf"^\.?{re.escape(extension.lstrip('.'))}$", re.IGNORECASE)
        for lang_name, lang_data in self.languages_data.items():
            if 'extensions' in lang_data:
                for ext in lang_data['extensions']:
                    if ext_pattern.match(ext):
                        return lang_data.get('type', 'nil')
        return 'nil'

    def analyze_repository_files(self, file_extensions: Dict[str, int]) -> Dict[str, int]:
        # Categorize extensions by type, using optimized extension matching
        categorized = {'programming': 0, 'data': 0, 'markup': 0, 'prose': 0, 'nil': 0}
        for ext, count in file_extensions.items():
            file_type = self.categorize_file_type(ext)
            categorized[file_type] += count
        return categorized

    def calculate_type_score(self, categorized_files: Dict[str, int]) -> float:
        # Weighted scoring: programming > data > markup > prose > nil
        weights = {'programming': 3, 'data': 2, 'markup': 1.5, 'prose': 1, 'nil': 0}
        raw_score = sum(categorized_files[t] * weights[t] for t in categorized_files)
        max_possible = sum(categorized_files.values()) * max(weights.values()) if categorized_files else 1
        if max_possible == 0:
            return 0.0
        type_score = min(raw_score / max_possible, 1.0)
        return type_score