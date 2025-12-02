from typing import Dict, Any, Union, List, Optional
import logging
from importlib import resources

import yaml

logger = logging.getLogger(__name__)

class FileTypeAnalyzer:
    """
    Analyzes and categorizes file types in a repository using linguist/languages.yml.
    """
    def __init__(self, linguist_data_path: Optional[str] = None) -> None:
        """Load linguist metadata once and cache extension lookups."""
        self.languages_data = self._load_linguist_data(linguist_data_path)
        self.extension_type_map = self._build_extension_type_map(self.languages_data)

    @staticmethod
    def _load_linguist_data(linguist_data_path: Optional[str]) -> Dict[str, Any]:
        if linguist_data_path:
            with open(linguist_data_path, 'r', encoding='utf-8') as handle:
                return yaml.safe_load(handle)
        with resources.open_text('cloudfolio_shared.linguist', 'languages.yml', encoding='utf-8') as handle:
            return yaml.safe_load(handle)

    @staticmethod
    def _build_extension_type_map(languages_data: Dict[str, Any]) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        for lang_data in languages_data.values():
            lang_type = lang_data.get('type', 'nil')
            for ext in lang_data.get('extensions', []) or []:
                normalized = ext.lower()
                mapping[normalized] = lang_type
                if normalized.startswith('.'):
                    mapping[normalized.lstrip('.')] = lang_type
        return mapping

    def categorize_file_type(self, extension: str) -> str:
        normalized = extension.lower().lstrip('.')
        dot_prefixed = f'.{normalized}'
        return self.extension_type_map.get(dot_prefixed) or self.extension_type_map.get(normalized, 'nil')

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