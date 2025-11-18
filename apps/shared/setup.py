from setuptools import setup, find_packages

setup(
    name="portfolio-shared",
    version="1.0.0",
    packages=find_packages(),
    install_requires=[
        "azure-storage-blob>=12.19.0",
        "azure-identity>=1.15.0",
        "pydantic>=2.5.0",
        "torch>=2.2.2",
        "sentence-transformers>=2.6.1",
        "groq>=0.4.0",
    ],
    python_requires=">=3.11",
)