"""
Setup configuration for cloudfolio-shared package.

This package is installed in development mode during local testing
and can be published to PyPI or Azure Artifacts for production.
"""

from setuptools import setup, find_packages
from pathlib import Path

# Read the long description from README
readme_file = Path(__file__).parent / "README.md"
long_description = readme_file.read_text(encoding="utf-8") if readme_file.exists() else ""

setup(
    name="cloudfolio-shared",
    version="0.1.0",
    description="Shared utilities for Cloudfolio portfolio analyzer (GitHub, AI, caching, queues)",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Your Name",
    author_email="your.email@example.com",
    url="https://github.com/yourusername/cloudfolio",
    license="MIT",
    
    # Package discovery
    packages=find_packages(exclude=["tests", "*.tests", "tests.*"]),
    package_data={
        "apps.shared.linguist": ["languages.yml"],
    },
    include_package_data=True,
    
    # Python version requirement
    python_requires=">=3.11",
    
    # Core dependencies (minimal set)
    install_requires=[
        # Azure Storage
        "azure-storage-blob>=12.27.0",
        "azure-storage-queue>=12.14.0",
        "azure-identity>=1.25.0",
        
        # GitHub API
        "requests>=2.32.5",
        
        # AI/ML
        "openai>=2.8.1",  # For Groq via OpenAI client
        
        # Data processing
        "pyyaml>=6.0.3",  # For languages.yml parsing
    ],
    
    # Optional dependencies for development/testing
    extras_require={
        "dev": [
            "pytest>=9.0.0",
            "pytest-cov>=7.0.0",
            "pytest-timeout>=2.4.0",
            "pytest-asyncio>=1.3.0",
            "pytest-mock>=3.15.1",
            "black>=25.11.0",
            "ruff>=0.14.0",
            "mypy>=1.18.0",
        ],
        "prod": [
            # Add any production-only dependencies here
        ],
    },
    
    # Entry points (if needed for CLI tools)
    entry_points={
        # "console_scripts": [
        #     "cloudfolio=apps.shared.cli:main",
        # ],
    },
)

# Local testing with Azurite
azure-storage-blob>=12.27.0
azure-storage-queue>=12.14.0
azure-identity>=1.25.0

# HTTP mocking (for GitHub API tests)
responses>=0.25.0