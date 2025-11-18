from typing import Tuple, List, Dict, Any, Optional
from config.cache_manager import cache_manager
from config.fingerprint_manager import FingerprintManager
from sentence_transformers import SentenceTransformer, InputExample, losses
from torch.utils.data import DataLoader
import logging
import datetime
import os
import tempfile
import zipfile
import shutil
from azure.storage.blob import BlobServiceClient, ContentSettings
import numpy as np
from sklearn.decomposition import PCA

logger = logging.getLogger('portfolio.api')


def keyword_overlap_score(query: str, context_str: str) -> float:
    """
    Compute keyword overlap between query and context using regex tokenization.
    Returns ratio of overlapping unique words (case-insensitive, ignores punctuation).
    """
    query_tokens = set(re.findall(r'\b\w+\b', query.lower()))
    context_tokens = set(re.findall(r'\b\w+\b', context_str.lower()))
    overlap = query_tokens & context_tokens
    if not query_tokens:
        return 0.0
    return len(overlap) / len(query_tokens)
class SemanticModel:
    """
    A class to handle semantic scoring using a fine-tuned Sentence Transformer model.
    """
    def __init__(self):
        """
        Initializes the SemanticModel with a pretrained Sentence Transformer model.
        """
        logger.info(f"Initializing SemanticModel")
        self.model = None
        # For embedding whitening (to reduce anisotropy)
        self._whiten_kernel = None  # PCA whitening kernel (d x k)
        self._whiten_bias = None    # Whitening bias (k,)

    def _ensure_base_model(self):
        """
        Lazily loads the base sentence transformer if not already loaded.
        """
        if self.model is None:
            try:
                self.model = SentenceTransformer("all-MiniLM-L6-v2")
            except Exception as e:
                logger.error(f"Failed to load base model: {e}", exc_info=True)
                self.model = None

    def get_model(self) -> SentenceTransformer:
        """
        Returns the current model (tuned or base). If no model is loaded, initializes the base model.
        
        Returns:
            SentenceTransformer: The current model instance.
        """
        if self.model is None:
            self._ensure_base_model()
        return self.model

    def encode(self, texts: List[str], apply_whitening: bool = True, normalize: bool = True, batch_size: int = 32) -> np.ndarray:
        """
        Encodes texts to embeddings with optional whitening and L2 normalization.
        """
        self._ensure_base_model()
        if self.model is None:
            return np.zeros((len(texts), 0))
            
        # First get raw embeddings
        emb = self.model.encode(
            texts,
            batch_size=batch_size,
            convert_to_numpy=True,
            normalize_embeddings=False  # defer normalization until after whitening
        )
        
        # Apply whitening if available and requested
        if apply_whitening and self._whiten_kernel is not None and self._whiten_bias is not None and emb.size:
            emb = emb @ self._whiten_kernel + self._whiten_bias
            
        # Apply L2 normalization if requested
        if normalize and emb.size:
            norms = np.linalg.norm(emb, axis=1, keepdims=True) + 1e-12
            emb = emb / norms
            
        return emb

    def _flatten_repo_bundle_for_training(self, repo_bundle: Dict) -> str:
        """
        Creates a natural language representation of repository context for training.
        """
        # Existing implementation
        rc = repo_bundle.get("repoContext", {}) or {}
        identity = rc.get("project_identity", {}) or {}
        tech = rc.get("tech_stack", {}) or {}
        lines: List[str] = []
        if identity.get("name"):
            lines.append(f"Project Name: {identity['name']}.")
        if identity.get("description"):
            lines.append(f"Description: {identity['description']}.")
        if identity.get("type"):
            lines.append(f"Type: {identity['type']}.")
        if identity.get("scope"):
            lines.append(f"Scope: {identity['scope']}.")
        if tech.get("primary"):
            lines.append(f"Primary technologies include {', '.join(tech['primary'])}.")
        if tech.get("secondary"):
            lines.append(f"Secondary tools include {', '.join(tech['secondary'])}.")
        if tech.get("key_libraries"):
            lines.append(f"Key libraries: {', '.join(tech['key_libraries'])}.")
        topics = rc.get("topics") or repo_bundle.get("topics") or []
        if topics:
            lines.append(f"Topics: {', '.join(topics)}.")
        skills = rc.get("skill_manifest", {}) or {}
        if isinstance(skills.get("technical"), list) and skills["technical"]:
            lines.append(f"Technical skills: {', '.join(skills['technical'])}.")
        if isinstance(skills.get("domain"), list) and skills["domain"]:
            lines.append(f"Domain: {', '.join(skills['domain'])}.")
        return "\n".join(lines).strip()

    def _fit_embedding_whitener(self, corpus_texts: List[str], n_components: int = None):
        """
        Fits a PCA whitening transform on corpus embeddings to reduce anisotropy.
        This helps spread out embeddings for better similarity discrimination.
        """
        if not corpus_texts:
            return
            
        self._ensure_base_model()
        if self.model is None:
            return
            
        # Get raw embeddings from the model
        emb = self.model.encode(corpus_texts, convert_to_numpy=True, normalize_embeddings=False)
        if emb.size == 0:
            return
            
        # Compute mean and center the data
        mu = emb.mean(axis=0, keepdims=True)
        X = emb - mu
        
        # Calculate max possible components based on data
        max_components = min(X.shape[0], X.shape[1]) - 1  # Subtract 1 for numerical stability
        
        # Use either specified n_components or 90% of max possible components
        if n_components is None:
            n_components = min(int(0.9 * max_components), max_components)
        else:
            n_components = min(n_components, max_components)
        
        logger.info(f"Fitting whitening with {n_components} components (max possible: {max_components})")
        
        try:
            # Fit PCA for whitening
            pca = PCA(n_components=n_components, svd_solver="auto", whiten=False, random_state=42)
            pca.fit(X)
            
            # Compute whitening transform: components_.T / sqrt(variance)
            kernel = pca.components_.T / (np.sqrt(pca.explained_variance_ + 1e-12))
            bias = (-mu) @ kernel
            
            self._whiten_kernel = kernel  # shape (d, k)
            self._whiten_bias = bias.ravel()  # shape (k,)
            
            explained_variance_ratio = sum(pca.explained_variance_ratio_) * 100
            logger.info(f"Fitted embedding whitener: dims {X.shape[1]} → {n_components} " 
                        f"(explains {explained_variance_ratio:.1f}% of variance)")
        except Exception as e:
            logger.warning(f"Whitening fit failed: {e}")
            self._whiten_kernel = None
            self._whiten_bias = None

    def ensure_model_ready(self, all_repos_bundle: List[Dict], train_if_missing: bool = True, training_params: Dict = None) -> bool:
        """
        Ensures a fine-tuned model is ready for use. Checks cache first, loads existing model,
        or trains a new one if necessary and if train_if_missing is True.
        
        Args:
            all_repos_bundle: List of repository bundles
            train_if_missing: If True, train a new model when none exists; if False, just load existing
            training_params: Dictionary of parameters to customize training behavior
            
        Returns:
            bool: True if a fine-tuned model is ready, False otherwise
        """
        # Filter documented repositories
        documented_repos = [repo for repo in all_repos_bundle if repo.get("has_documentation", False)]
        if not documented_repos:
            logger.warning("No documented repositories available for training. Using base model.")
            # Still ensure base model and fit whitener from whatever we have
            self._ensure_base_model()
            corpus = [self._flatten_repo_bundle_for_training(rb) for rb in all_repos_bundle if rb]
            self._fit_embedding_whitener([c for c in corpus if c])
            return False

        # Generate fingerprint of current repositories
        fingerprint = FingerprintManager.generate_content_fingerprint(documented_repos)
        logger.info(f"Generated repository fingerprint: {fingerprint[:8]}...")
        
        # Check local disk cache first
        local_cache_dir = os.path.join(tempfile.gettempdir(), "semantic_models")
        local_model_path = os.path.join(local_cache_dir, f"model_{fingerprint}")
        
        if os.path.exists(local_model_path):
            try:
                logger.info(f"Found matching model in local disk cache: {fingerprint[:8]}")
                self.model = SentenceTransformer(local_model_path)
                # Fit whitening on current documented contexts
                corpus = [self._flatten_repo_bundle_for_training(rb) for rb in documented_repos if rb]
                self._fit_embedding_whitener([c for c in corpus if c])
                return True
            except Exception as e:
                logger.warning(f"Failed to load model from disk cache: {str(e)}")

        # Check global cache for the model metadata
        cache_key = cache_manager.generate_cache_key(kind='model', fingerprint=None)
        cache_result = cache_manager.get(cache_key)

        if cache_result['status'] == 'valid' and cache_result['data']:
            model_metadata = cache_result['data']

            # Check if we have a model matching the current fingerprint
            if model_metadata.get("fingerprint") == fingerprint:
                logger.info("Found model with matching repository fingerprint")
                
                load_success = self.load_model_from_storage(model_metadata, local_cache_dir)
                if load_success:
                    corpus = [self._flatten_repo_bundle_for_training(rb) for rb in documented_repos if rb]
                    self._fit_embedding_whitener([c for c in corpus if c])
                    return True
        
        # No matching model found
        if not train_if_missing:
            logger.info("No matching model found and train_if_missing=False. Using base model.")
            self._ensure_base_model()
            corpus = [self._flatten_repo_bundle_for_training(rb) for rb in all_repos_bundle if rb]
            self._fit_embedding_whitener([c for c in corpus if c])
            return False
        
        # No matching model found - train new model
        logger.info(f"No model found with matching fingerprint. Training with {len(documented_repos)} repositories")

        # Train model using the documented repositories
        model_path = f"model_{fingerprint}"
        success = self.train_from_repositories(documented_repos, model_path, local_cache_dir, training_params)
        if success:
            # Fit whitening on documented contexts after training
            corpus = [self._flatten_repo_bundle_for_training(rb) for rb in documented_repos if rb]
            self._fit_embedding_whitener([c for c in corpus if c])
            
            # Save model metadata to cache if training was successful
            model_info = {
                "storage_type": "blob",
                "container": cache_manager.container_name,
                "blob_name": f"{model_path}.zip",
                "fingerprint": fingerprint,
                "training_timestamp": datetime.datetime.now().isoformat(),
                "training_repos_count": len(documented_repos),
                "repo_names": [repo.get("name", "Unknown") for repo in documented_repos]
            }
            
            cache_manager.save(
                cache_key, 
                model_info,
                ttl=None,  # No expiration - models valid until repos change
                fingerprint=fingerprint
            )
            logger.info("Fine-tuned model metadata saved to cache.")
            return True
        return False

    def load_model_from_storage(self, model_info: Dict[str, Any], local_cache_dir: Optional[str] = None) -> bool:
        """
        Loads a fine-tuned model from Azure Blob Storage.
        
        Args:
            model_info: Dictionary containing model storage information
            
        Returns:
            bool: True if model was successfully loaded, False otherwise
        """        
        try:
            # Extract model storage information
            container = model_info.get("container", "ai-models")
            blob_name = model_info.get("blob_name")
            fingerprint = model_info.get("fingerprint", "")
            if not blob_name:
                logger.error("Missing blob_name in model_info")
                return False
            
            try:
                ensure = getattr(cache_manager, "_ensure_initialized", None)
                if callable(ensure):
                    ensure()
            except Exception as e:
                logger.warning(f"Failed to ensure cache_manager init: {e}")

            if not cache_manager.blob_service_client:
                logger.error("Blob service client not available from cache_manager")
                return False
            
            container_client = cache_manager.blob_service_client.get_container_client(container)
            blob_client = container_client.get_blob_client(blob_name)
            
            # Local cache short-circuit
            if local_cache_dir and fingerprint:
                local_model_path = os.path.join(local_cache_dir, f"model_{fingerprint}")
                if os.path.exists(local_model_path):
                    try:
                        logger.info(f"Loading model from local cache: {local_model_path}")
                        self.model = SentenceTransformer(local_model_path)
                        return True
                    except Exception as local_e:
                        logger.warning(f"Failed to load from local cache: {str(local_e)}")
            
            # Download to temp, extract, load
            temp_dir = tempfile.mkdtemp()
            zip_path = os.path.join(temp_dir, "model.zip")
            try:
                with open(zip_path, "wb") as download_file:
                    download_file.write(blob_client.download_blob().readall())
                
                model_dir = os.path.join(temp_dir, "model")
                with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                    zip_ref.extractall(model_dir)
                
                self.model = SentenceTransformer(model_dir)

                if local_cache_dir and fingerprint:
                    os.makedirs(local_cache_dir, exist_ok=True)
                    local_model_path = os.path.join(local_cache_dir, f"model_{fingerprint}")
                    if os.path.exists(local_model_path):
                        shutil.rmtree(local_model_path)
                    shutil.copytree(model_dir, local_model_path)
                    logger.info(f"Model saved to local cache: {local_model_path}")
            except Exception as e:
                logger.error(f"Error processing downloaded model: {e}", exc_info=True)
                return False
            finally:
                shutil.rmtree(temp_dir)
                
            logger.info(f"Successfully loaded model from {container}/{blob_name}")
            return True
        except Exception as e:
            logger.error(f"Error loading model from storage: {str(e)}", exc_info=True)
            return False

    def train_from_repositories(self, repos_bundle: List[Dict], output_path: str, 
                            local_cache_dir: Optional[str] = None,
                            training_params: Dict = None) -> bool:
        """
        Trains a semantic model using a list of repository contexts and persists it to Azure Blob Storage.
        Now includes fallback to embedding whitening if fine-tuning fails.
        
        Args:
            repos_bundle: List of repository bundles with context
            output_path: Path where the model should be saved
            local_cache_dir: Directory for local caching
            training_params: Dictionary of parameters to customize training behavior
            
        Returns:
            bool: True if training succeeded, False otherwise
        """
        logger.info(f"Training semantic model from {len(repos_bundle)} repositories")
        
        # Default training parameters
        params = {
            'batch_size': 8,
            'max_pairs': 150,
            'epochs': 2,
            'warmup_steps': 50,
            'use_mnrl': True
        }
        
        # Update with custom parameters if provided
        if training_params:
            params.update(training_params)
        
        # Generate training pairs from all repositories
        training_pairs = []
        for repo_bundle in repos_bundle:
            repo_name = repo_bundle.get('name', 'Unknown')
            logger.info(f"Generating training pairs for repository: {repo_name}")
            try:
                pairs = self.generate_semantic_training_pairs(repo_bundle)
                training_pairs.extend(pairs)
                logger.info(f"Generated {len(pairs)} training pairs for {repo_name}")
            except Exception as e:
                logger.error(f"Error generating training pairs for {repo_name}: {str(e)}")
                continue
        
        # Fine-tune the model if we have training pairs
        if not training_pairs:
            logger.warning("No training pairs generated, skipping fine-tuning")
            return False
        
        try:
            # Create a temporary directory for the model
            temp_dir = tempfile.mkdtemp()
            temp_model_path = os.path.join(temp_dir, "model")
            
            # Fine-tune the model and save to temp location
            training_success = self.fine_tune_model(
                training_pairs, 
                temp_model_path, 
                use_mnrl=params['use_mnrl'],
                batch_size=params['batch_size'],
                max_pairs=params['max_pairs'],
                epochs=params['epochs'],
                warmup_steps=params['warmup_steps']
            )
            
            if not training_success:
                logger.warning("Fine-tuning failed. Falling back to base model with whitening only.")
                self._ensure_base_model()
                self.model.save(temp_model_path)
                
                # Load corpus for whitening and apply
                corpus = []
                for repo_bundle in repos_bundle:
                    context = self._flatten_repo_bundle_for_training(repo_bundle)
                    if context:
                        corpus.append(context)
                self._fit_embedding_whitener(corpus)
            else:
                # Load the trained model into self.model for immediate use
                self.model = SentenceTransformer(temp_model_path)
                logger.info("Trained model loaded into memory for immediate use.")
                
            # After training (successful or fallback), save to local cache if requested
            if local_cache_dir:
                os.makedirs(local_cache_dir, exist_ok=True)
                local_model_path = os.path.join(local_cache_dir, output_path)
                
                if os.path.exists(local_model_path):
                    shutil.rmtree(local_model_path)
                shutil.copytree(temp_model_path, local_model_path)
                logger.info(f"Model saved to local cache: {local_model_path}")
        
            # Create a zip file of the model
            model_id = os.path.basename(output_path)
            zip_path = os.path.join(temp_dir, f"{model_id}.zip")
            
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(temp_model_path):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, temp_model_path)
                        zipf.write(file_path, arcname)
        
            # Upload zip to Azure Blob Storage using the same blob service client as cache_manager
            try:
                ensure = getattr(cache_manager, "_ensure_initialized", None)
                if callable(ensure):
                    ensure()
            except Exception:
                pass

            if not cache_manager.blob_service_client:
                logger.error("Blob service client not available")
                return False

            container_name = cache_manager.container_name
            blob_name = f"{model_id}.zip"

            # Prefer container client from cache_manager to ensure init and MI path
            try:
                container_client = cache_manager.get_container_client(container_name)
                if not container_client.exists():
                    logger.info(f"Container {container_name} does not exist, creating it...")
                    cache_manager.blob_service_client.create_container(container_name)
                    logger.info(f"Container {container_name} created successfully")
            except Exception as e:
                logger.error(f"Error checking/creating container {container_name}: {e}")
                return False

            blob_client = container_client.get_blob_client(blob_name)
            
            with open(zip_path, "rb") as data:
                try:
                    blob_client.upload_blob(
                        data, 
                        overwrite=True,
                        content_settings=ContentSettings(
                            content_type='application/zip',
                            content_encoding='utf-8'
                        )
                    )
                except Exception as e:
                    logger.error(f"Failed to upload model to blob storage: {e}", exc_info=True)
                    return False
                        
            # Cleanup temporary files 
            shutil.rmtree(temp_dir)
            return True
        except Exception as e:
            logger.error(f"Error during model training and storage: {str(e)}", exc_info=True)
            return False
            
    def fine_tune_model(self, training_pairs: List[Tuple[str, str, float]], output_path: str, 
                    use_mnrl: bool = True, batch_size: int = 8, max_pairs: int = 150,
                    epochs: int = 2, warmup_steps: int = 50):
        """
        Fine-tunes the Sentence Transformer model with memory-optimized settings.
        
        Args:
            training_pairs: List of (query, context, score) training examples
            output_path: Path to save the trained model
            use_mnrl: Whether to use MultipleNegativesRankingLoss (vs CosineSimilarityLoss)
            batch_size: Batch size for training
            max_pairs: Maximum number of training pairs to use (prevents OOM errors)
            epochs: Number of training epochs
            warmup_steps: Number of warmup steps
            
        Returns:
            bool: True if training succeeded, False otherwise
        """
        logger.info(f"Starting fine-tuning process with {len(training_pairs)} training pairs.")
        self._ensure_base_model()
        if self.model is None:
            logger.error("Cannot fine-tune: base model failed to load.")
            return False
        if not training_pairs:
            logger.warning("No training pairs provided; skipping fine-tune.")
            return False
        try:
            # Memory optimizations
            show_progress_bar = False  # Disable progress bar to save memory
            
            if use_mnrl:
                # Keep only strong positives; in-batch acts as negatives
                pos_pairs = [(q, c) for (q, c, y) in training_pairs if y >= 0.8]
                if not pos_pairs:
                    logger.warning("No positive pairs for MNRL; falling back to cosine loss.")
                    return self.fine_tune_model(
                        training_pairs, output_path, use_mnrl=False,
                        batch_size=batch_size, max_pairs=max_pairs,
                        epochs=epochs, warmup_steps=warmup_steps
                    )
                    
                # Sample if too many pairs (optional)
                if len(pos_pairs) > max_pairs:
                    import random
                    random.seed(42)
                    pos_pairs = random.sample(pos_pairs, max_pairs)
                    logger.info(f"Sampled training pairs to {max_pairs} to reduce memory usage")
                    
                train_examples = [InputExample(texts=[q, c]) for (q, c) in pos_pairs]
                train_dataloader = DataLoader(train_examples, shuffle=True, batch_size=batch_size)
                train_loss = losses.MultipleNegativesRankingLoss(self.model)
            else:
                # Sample if too many pairs (optional)
                if len(training_pairs) > max_pairs:
                    import random
                    random.seed(42)
                    training_pairs = random.sample(training_pairs, max_pairs)
                    logger.info(f"Sampled training pairs to {max_pairs} to reduce memory usage")
                    
                train_examples = [InputExample(texts=[q, c], label=y) for (q, c, y) in training_pairs]
                train_dataloader = DataLoader(train_examples, shuffle=True, batch_size=batch_size)
                train_loss = losses.CosineSimilarityLoss(self.model)

            # Lower epochs and add garbage collection
            import gc
            self.model.fit(
                train_objectives=[(train_dataloader, train_loss)], 
                epochs=epochs,
                warmup_steps=warmup_steps,
                show_progress_bar=show_progress_bar
            )
            gc.collect()  # Force garbage collection
            
            self.model.save(output_path)
            return True
        except Exception as e:
            logger.error(f"Error during fine-tuning: {str(e)}", exc_info=True)
            return False

    def generate_semantic_training_pairs(self, repo_bundle: Dict) -> List[Tuple[str, str, float]]:
        """
        Generates (query, context, label) training pairs for semantic model fine-tuning
        using the repository context bundle.

        Args:
            repo_bundle (Dict): Repository context bundle.

        Returns:
            List[Tuple[str, str, float]]: List of (query, context, similarity_score) tuples.
        """
        logger.info("Generating semantic training pairs from repository context.")
        pairs = []

        # Extract relevant fields
        identity = repo_bundle.get("repoContext", {}).get("project_identity", {})
        tech_stack = repo_bundle.get("repoContext", {}).get("tech_stack", {})
        skills = repo_bundle.get("repoContext", {}).get("skill_manifest", {})
        outcomes = repo_bundle.get("repoContext", {}).get("outcomes", {})
        readme = repo_bundle.get("readme", "")
        skills_index = repo_bundle.get("skills_index", "")
        architecture = repo_bundle.get("architecture", "")

        # Generate pairs based on schema fields
        if identity.get("name"):
            pairs.append((f"What is {identity['name']}?", identity.get("description", ""), 1.0))
        if identity.get("description"):
            pairs.append(("Summarize this project.", identity["description"], 1.0))

        if tech_stack.get("primary"):
            pairs.append(("Which technologies are used in this project?", ", ".join(tech_stack["primary"]), 1.0))
        if tech_stack.get("secondary"):
            pairs.append(("What supporting tools are used?", ", ".join(tech_stack["secondary"]), 0.8))

        if skills.get("technical"):
            pairs.append(("What technical skills are demonstrated?", ", ".join(skills["technical"]), 1.0))
        if skills.get("domain"):
            pairs.append(("What domain-specific knowledge areas are covered?", ", ".join(skills["domain"]), 1.0))

        if outcomes.get("deliverables"):
            pairs.append(("What are the deliverables of this project?", ", ".join(outcomes["deliverables"]), 1.0))
        if outcomes.get("skills_acquired"):
            pairs.append(("What skills were acquired?", ", ".join(outcomes["skills_acquired"]), 1.0))

        # Include README, SKILLS-INDEX, and ARCHITECTURE content
        if readme:
            pairs.append(("Summarize the README content.", readme, 1.0))
        if skills_index:
            pairs.append(("List the core skills demonstrated in this project.", skills_index, 1.0))
        if architecture:
            pairs.append(("Describe the architecture of this project.", architecture, 1.0))

        logger.info(f"Generated {len(pairs)} training pairs.")
        return pairs