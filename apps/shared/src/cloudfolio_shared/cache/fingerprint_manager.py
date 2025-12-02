import hashlib
import json
from typing import Dict, Any, List, Optional, Union

class FingerprintManager:
    """
    Centralized fingerprinting utilities for detecting changes in repositories
    and repository collections.
    """

    @staticmethod
    def generate_metadata_fingerprint(repo_metadata: Dict[str, Any]) -> str:
        """
        Generate a fingerprint for a repository based on its metadata.
        Used for cache invalidation of individual repositories.
        
        Args:
            repo_metadata: Repository metadata dictionary
            
        Returns:
            A string hash that uniquely identifies the repository metadata state
        """
        fingerprint_data = {
            'id': repo_metadata.get('id'),
            'updated_at': repo_metadata.get('updated_at'),
            'pushed_at': repo_metadata.get('pushed_at'),
            'size': repo_metadata.get('size'),
            'default_branch': repo_metadata.get('default_branch'),
            'language': repo_metadata.get('language')
        }
        
        fingerprint_json = json.dumps(fingerprint_data, sort_keys=True)
        return hashlib.md5(fingerprint_json.encode()).hexdigest()

    @staticmethod
    def generate_content_fingerprint(repos_bundle: List[Dict[str, Any]]) -> str:
        """
        Generate a fingerprint for a collection of repositories based on their content.
        Used for determining if model retraining is needed.
        """
        fingerprint_data = []
        for repo in repos_bundle:
            if not repo.get("has_documentation", False):
                continue
                
            repo_data = {
                "name": repo.get("name", ""),
                "last_modified": repo.get("last_updated", ""),
                "readme_hash": hashlib.md5(repo.get("readme", "").encode()).hexdigest()[:16],
                "skills_hash": hashlib.md5(repo.get("skills_index", "").encode()).hexdigest()[:16],
                "arch_hash": hashlib.md5(repo.get("architecture", "").encode()).hexdigest()[:16]
            }
            fingerprint_data.append(repo_data)
        
        # Sort to ensure consistent order
        fingerprint_data.sort(key=lambda x: x["name"])
        
        fingerprint_str = json.dumps(fingerprint_data, sort_keys=True)
        return hashlib.md5(fingerprint_str.encode()).hexdigest()

    @staticmethod
    def generate_bundle_fingerprint(repo_fingerprints: List[str]) -> str:
        """
        Generate a fingerprint for a bundle of repositories based on their individual fingerprints.
        
        Args:
            repo_fingerprints: List of repository fingerprints
            
        Returns:
            A string hash representing the collection of repositories
        """
        fingerprint_str = json.dumps(sorted(repo_fingerprints))
        return hashlib.md5(fingerprint_str.encode()).hexdigest()