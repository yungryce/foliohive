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
        """Fingerprint normalized GitHub metadata (TableManager schema).

        Uses the stable fields persisted in RepoGitHubMetadataRow so content
        changes or repo state changes produce a deterministic hash.
        """
        fingerprint_data = {
            "repo_name": repo_metadata.get("repo_name") or repo_metadata.get("name"),
            "fingerprint": repo_metadata.get("fingerprint"),
            "github_updated_at": repo_metadata.get("github_updated_at"),
            "github_pushed_at": repo_metadata.get("github_pushed_at"),
            "github_created_at": repo_metadata.get("github_created_at"),
            "primary_language": repo_metadata.get("primary_language") or repo_metadata.get("language"),
            "stars_count": repo_metadata.get("stars_count"),
            "forks_count": repo_metadata.get("forks_count"),
            "is_fork": bool(repo_metadata.get("is_fork")),
            "is_archived": bool(repo_metadata.get("is_archived")),
            "license_name": repo_metadata.get("license_name"),
        }
        fingerprint_json = json.dumps(fingerprint_data, sort_keys=True)
        return hashlib.md5(fingerprint_json.encode()).hexdigest()

    @staticmethod
    def generate_content_fingerprint(repos_bundle: Union[List[Dict[str, Any]], Dict[str, Any]]) -> str:
        """Fingerprint a bundle of repos.

        Accepts either:
        - List of normalized repo entries with "repo_name" and "fingerprint"
        - Dict with key "repos" containing a list of repo names (fallback)
        """
        normalized: List[Dict[str, Any]] = []

        # Fallback shape used in cache_worker when no fingerprints are present
        if isinstance(repos_bundle, dict) and "repos" in repos_bundle:
            names = [r for r in repos_bundle.get("repos") or [] if r]
            normalized = [{"repo_name": name} for name in names]
        elif isinstance(repos_bundle, list):
            normalized = repos_bundle

        digest_inputs = []
        for repo in normalized:
            digest_inputs.append(
                {
                    "repo_name": repo.get("repo_name") or repo.get("name"),
                    "fingerprint": repo.get("fingerprint"),
                    "github_updated_at": repo.get("github_updated_at"),
                }
            )

        digest_inputs.sort(key=lambda x: x.get("repo_name") or "")
        fingerprint_str = json.dumps(digest_inputs, sort_keys=True)
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

    @staticmethod
    def generate_user_profile_fingerprint(user_profile: Dict[str, Any]) -> str:
        """Fingerprint normalized GitHub user profile state.

        Uses stable fields from the GitHub user profile payload to determine
        whether cached profile data should be refreshed.
        """
        fingerprint_data = {
            "github_id": user_profile.get("id") or user_profile.get("github_id"),
            "github_updated_at": user_profile.get("updated_at") or user_profile.get("github_updated_at"),
            "public_repos": user_profile.get("public_repos"),
            "followers": user_profile.get("followers"),
            "following": user_profile.get("following"),
            "name": user_profile.get("name"),
            "company": user_profile.get("company"),
            "location": user_profile.get("location"),
        }
        fingerprint_json = json.dumps(fingerprint_data, sort_keys=True)
        return hashlib.md5(fingerprint_json.encode()).hexdigest()