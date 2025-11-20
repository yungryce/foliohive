# Semantic Model Training Refactor Plan

**Date**: November 17, 2025  
**Status**: Design Phase  
**Priority**: Decouple training from Function App for cost optimization and flexibility

---

## Executive Summary

Refactor semantic model training from synchronous Function App activity to independent containerized worker that can run on-demand with high compute resources (CPU/GPU). 

**IMPORTANT**: This is the **ONLY component** in the PRIMARY deployment that requires containerization due to high compute requirements (4 vCPU, 16GB RAM, optional GPU). All other workers (API Gateway, Sync Worker, Merge Worker) use Azure Function Apps.

The training worker will be deployable to both Azure Container Instances (for Function App environments) and AKS (for production), using the same Docker image.

### Key Benefits
- **Cost Reduction**: Zero idle cost (serverless containers vs always-on Function App)
- **Compute Flexibility**: Request GPU instances only when needed for experiments (exceeds Function App 2GB limit)
- **Decoupling**: Training failures don't impact API availability
- **Experiment-Friendly**: Easy to swap base models and hyperparameters
- **Cloud-Agnostic**: Same container runs on Azure Container Instances or AKS

### Deployment Context
- **PRIMARY Deployment**: Azure Container Instances (ACI) for on-demand serverless execution
- **Alternative**: AKS for spot instance cost savings (see `plan-aksDeployment.prompt.md`)
- **Other Workers**: API Gateway, Sync Worker, Merge Worker remain as Function Apps (not containerized)

---

## Current Architecture (Problems)

### Training Flow in Durable Functions

```
merge_worker completes
        ↓
enqueue_training_job (Storage Queue: model-training)
        ↓
training_worker (Queue Trigger in Function App)
        ↓
train_semantic_model_activity
        ↓
SemanticModel.ensure_model_ready()
        ↓
fine_tune_model() [60-90 seconds, CPU-only, 2GB RAM]
        ↓
Upload model to blob storage
```

### Problems

1. **Tied to Function App Runtime**
   - Limited to 2GB RAM (Flex Consumption instance memory)
   - CPU-only (no GPU support for experiments)
   - Shares compute resources with API workers
   - Cold start overhead when scaling

2. **Not Continuously Running (Good!) But...**
   - Queue trigger still runs in Function App context
   - Cannot request GPU instances dynamically
   - Limited control over compute resources

3. **Experimentation is Difficult**
   - Hard-coded base model (`all-MiniLM-L6-v2`)
   - Cannot test different architectures without redeploying Function App
   - No A/B testing capability

4. **Cost Inefficiency**
   - Function App provisioned for peak load (training + API)
   - Training only happens after repo sync (infrequent)
   - Paying for unused capacity

---

## Target Architecture: Containerized Training Worker

### Design Principles

1. **Stateless Container**: No persistent state, reads from queue, writes to blob
2. **On-Demand Execution**: Container spins up when queue has messages, terminates after completion
3. **Dual Deployment**: Same Docker image runs on Azure Container Instances or AKS Jobs
4. **GPU-Capable**: Can request GPU instances for experiments (via container spec)
5. **Experiment Registry**: Track multiple model configurations without code changes

---

## Architecture Diagram

### Serverless Container (Azure Container Instances)

```
┌─────────────────────────────────────────────────┐
│         Azure Storage Queue                      │
│         queue: model-training                    │
│   message: {username, repos_bundle, params}     │
└────────────────┬────────────────────────────────┘
                 │
                 │ (queue has messages)
                 ↓
┌─────────────────────────────────────────────────┐
│    Azure Container Instances (on-demand)        │
│                                                  │
│  Container Spec:                                 │
│    - Image: portfolio-trainer:cpu                │
│    - CPU: 4 cores, RAM: 16GB                     │
│    - GPU: Optional (NVIDIA T4 for experiments)   │
│    - Restart Policy: Never (one-time job)        │
│                                                  │
│  Process:                                        │
│    1. Poll queue (receive 1 message)             │
│    2. Download repos_bundle from message         │
│    3. Train model (PyTorch)                      │
│    4. Upload model.zip to blob storage           │
│    5. Delete message from queue                  │
│    6. Terminate (container exits)                │
└────────────────┬────────────────────────────────┘
                 │
                 ↓
┌─────────────────────────────────────────────────┐
│         Azure Blob Storage                       │
│         container: github-cache                  │
│   - model_{fingerprint}.zip                      │
│   - model_metadata_{fingerprint}.json            │
└─────────────────────────────────────────────────┘
```

**Cost**: ~$0.40 per training run (4 vCPU × 60 min), $0 when idle

**Note**: Uses Azure Storage Queues for message delivery. Queue polling implemented in training worker.

---

### Kubernetes Job (AKS)

```
┌─────────────────────────────────────────────────┐
│         Azure Storage Queue                      │
│         queue: model-training                    │
└────────────────┬────────────────────────────────┘
                 │
                 │ (CronJob or event trigger)
                 ↓
┌─────────────────────────────────────────────────┐
│    Kubernetes Job (AKS)                          │
│                                                  │
│  Job Spec:                                       │
│    - Image: portfolio-trainer:cpu                │
│    - Resources:                                  │
│        requests: {cpu: 4, memory: 16Gi}          │
│        limits: {cpu: 4, memory: 16Gi}            │
│    - RestartPolicy: Never                        │
│    - TTL: 3600s (auto-cleanup after completion)  │
│                                                  │
│  Optional GPU:                                   │
│    - NodeSelector: {accelerator: nvidia-t4}      │
│    - Tolerations: nvidia.com/gpu                 │
│                                                  │
│  Process: Same as Container Instances            │
└────────────────┬────────────────────────────────┘
                 │
                 ↓
┌─────────────────────────────────────────────────┐
│         Azure Blob Storage (same)                │
└─────────────────────────────────────────────────┘
```

**Cost**: ~$0.15 per training run (shared node pool), or $72/month baseline (dedicated nodes)

---

## Implementation Design

### 1. Directory Structure

```
api/
  workers/
    training/
      Dockerfile                    # Multi-stage build (CPU/GPU variants)
      requirements.txt              # PyTorch, sentence-transformers, azure-storage
      train_worker.py               # Main training orchestrator
      models/
        __init__.py
        semantic_model.py           # Extracted from fine_tuning.py (standalone)
        model_registry.py           # Experiment configurations
      tests/
        test_semantic_model.py
        test_train_worker.py
```

---

### 2. Refactored Semantic Model (Standalone)

**Extract from `fine_tuning.py` into `workers/training/models/semantic_model.py`**

Key changes:
- Remove `cache_manager` dependency (use direct Azure Blob SDK)
- Remove Function App-specific imports (`azure.functions`)
- Add experiment tracking (model versions, hyperparameters)
- Support multiple base models (configurable)

```python
# workers/training/models/semantic_model.py
from sentence_transformers import SentenceTransformer, InputExample, losses
from torch.utils.data import DataLoader
from azure.storage.blob import BlobServiceClient
import logging
import os
import zipfile
import tempfile
import shutil

logger = logging.getLogger(__name__)

class SemanticModel:
    """
    Standalone semantic model trainer (no Function App dependencies).
    Supports multiple base models and experiment tracking.
    """
    
    def __init__(self, base_model: str = 'all-MiniLM-L6-v2', blob_connection_string: str = None):
        """
        Initialize semantic model.
        
        Args:
            base_model: HuggingFace model identifier (e.g., 'all-MiniLM-L6-v2', 'all-mpnet-base-v2')
            blob_connection_string: Azure Storage connection string for model persistence
        """
        self.base_model_name = base_model
        self.model = None
        self._whiten_kernel = None
        self._whiten_bias = None
        
        # Azure Blob Storage client (for model upload/download)
        if blob_connection_string:
            self.blob_service = BlobServiceClient.from_connection_string(blob_connection_string)
        else:
            self.blob_service = None
    
    def _ensure_base_model(self):
        """Load base model if not already loaded."""
        if self.model is None:
            try:
                logger.info(f"Loading base model: {self.base_model_name}")
                self.model = SentenceTransformer(self.base_model_name)
            except Exception as e:
                logger.error(f"Failed to load base model: {e}", exc_info=True)
                self.model = None
    
    def train_from_repositories(self, repos_bundle: list, output_path: str, 
                                training_params: dict = None) -> bool:
        """
        Train semantic model from repository bundles.
        
        Args:
            repos_bundle: List of repository context bundles
            output_path: Local directory path to save trained model
            training_params: Training hyperparameters (batch_size, epochs, etc.)
            
        Returns:
            bool: True if training succeeded
        """
        logger.info(f"Training model from {len(repos_bundle)} repositories")
        
        # Default parameters
        params = {
            'batch_size': 8,
            'max_pairs': 150,
            'epochs': 2,
            'warmup_steps': 50,
            'use_mnrl': True
        }
        if training_params:
            params.update(training_params)
        
        # Generate training pairs
        training_pairs = []
        for repo in repos_bundle:
            pairs = self._generate_training_pairs(repo)
            training_pairs.extend(pairs)
        
        if not training_pairs:
            logger.warning("No training pairs generated")
            return False
        
        # Sample pairs if too many (memory optimization)
        if len(training_pairs) > params['max_pairs']:
            import random
            random.seed(42)
            training_pairs = random.sample(training_pairs, params['max_pairs'])
        
        # Fine-tune model
        self._ensure_base_model()
        if self.model is None:
            return False
        
        try:
            if params['use_mnrl']:
                # Multiple Negatives Ranking Loss (in-batch negatives)
                pos_pairs = [(q, c) for (q, c, y) in training_pairs if y >= 0.8]
                train_examples = [InputExample(texts=[q, c]) for (q, c) in pos_pairs]
                train_dataloader = DataLoader(train_examples, shuffle=True, batch_size=params['batch_size'])
                train_loss = losses.MultipleNegativesRankingLoss(self.model)
            else:
                # Cosine Similarity Loss
                train_examples = [InputExample(texts=[q, c], label=y) for (q, c, y) in training_pairs]
                train_dataloader = DataLoader(train_examples, shuffle=True, batch_size=params['batch_size'])
                train_loss = losses.CosineSimilarityLoss(self.model)
            
            # Train
            self.model.fit(
                train_objectives=[(train_dataloader, train_loss)],
                epochs=params['epochs'],
                warmup_steps=params['warmup_steps'],
                show_progress_bar=True
            )
            
            # Save model locally
            os.makedirs(output_path, exist_ok=True)
            self.model.save(output_path)
            logger.info(f"Model saved to {output_path}")
            
            return True
            
        except Exception as e:
            logger.error(f"Training failed: {e}", exc_info=True)
            return False
    
    def upload_to_blob(self, local_model_path: str, blob_name: str, container_name: str = 'github-cache'):
        """
        Upload trained model to Azure Blob Storage.
        
        Args:
            local_model_path: Local directory containing saved model
            blob_name: Blob name (e.g., 'model_abc123.zip')
            container_name: Blob container name
        """
        if not self.blob_service:
            raise ValueError("Blob service not configured")
        
        # Zip model
        temp_dir = tempfile.mkdtemp()
        zip_path = os.path.join(temp_dir, blob_name)
        
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, _, files in os.walk(local_model_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, local_model_path)
                    zipf.write(file_path, arcname)
        
        # Upload
        container_client = self.blob_service.get_container_client(container_name)
        blob_client = container_client.get_blob_client(blob_name)
        
        with open(zip_path, 'rb') as data:
            blob_client.upload_blob(data, overwrite=True)
        
        shutil.rmtree(temp_dir)
        logger.info(f"Model uploaded to {container_name}/{blob_name}")
    
    def _generate_training_pairs(self, repo_bundle: dict) -> list:
        """Generate (query, context, score) training pairs from repository."""
        pairs = []
        
        identity = repo_bundle.get('repoContext', {}).get('project_identity', {})
        tech_stack = repo_bundle.get('repoContext', {}).get('tech_stack', {})
        
        if identity.get('name'):
            pairs.append((f"What is {identity['name']}?", identity.get('description', ''), 1.0))
        
        if tech_stack.get('primary'):
            pairs.append(("Which technologies are used?", ", ".join(tech_stack['primary']), 1.0))
        
        # Add more pairs from README, skills, etc.
        readme = repo_bundle.get('readme', '')
        if readme:
            pairs.append(("Summarize the README", readme[:500], 1.0))
        
        return pairs
```

---

### 3. Model Registry (Experiment Tracking)

```python
# workers/training/models/model_registry.py
"""
Central registry for model experiments.
Add new configurations here without modifying training code.
"""

MODEL_CONFIGS = {
    'default': {
        'base_model': 'all-MiniLM-L6-v2',
        'description': 'Fast, lightweight (80MB), good for production',
        'params': {
            'batch_size': 8,
            'epochs': 2,
            'max_pairs': 150,
            'use_mnrl': True
        }
    },
    
    'large': {
        'base_model': 'all-mpnet-base-v2',
        'description': 'Larger model (420MB), more accurate',
        'params': {
            'batch_size': 4,
            'epochs': 3,
            'max_pairs': 200,
            'use_mnrl': True
        }
    },
    
    'fast': {
        'base_model': 'all-MiniLM-L12-v2',
        'description': 'Faster training, good for testing',
        'params': {
            'batch_size': 16,
            'epochs': 1,
            'max_pairs': 100,
            'use_mnrl': False
        }
    },
    
    'experimental-bge': {
        'base_model': 'BAAI/bge-small-en-v1.5',
        'description': 'State-of-the-art (133MB), requires GPU',
        'params': {
            'batch_size': 8,
            'epochs': 2,
            'max_pairs': 150,
            'use_mnrl': True
        }
    }
}

def get_model_config(experiment_name: str) -> dict:
    """
    Get model configuration by experiment name.
    
    Args:
        experiment_name: Name of experiment (e.g., 'default', 'large', 'experimental-bge')
        
    Returns:
        dict: Configuration with 'base_model' and 'params'
    """
    config = MODEL_CONFIGS.get(experiment_name, MODEL_CONFIGS['default'])
    logger.info(f"Using model config '{experiment_name}': {config['description']}")
    return config
```

---

### 4. Training Worker (Main Orchestrator)

```python
# workers/training/train_worker.py
"""
Standalone training worker that polls Azure Storage Queue for training jobs.
Can run as:
  - Azure Container Instance (serverless)
  - Kubernetes Job (AKS)
  - Docker container (local testing)
"""

import os
import json
import logging
import time
from datetime import datetime
from azure.storage.queue import QueueClient
from azure.storage.blob import BlobServiceClient
from models.semantic_model import SemanticModel
from models.model_registry import get_model_config

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class TrainingWorker:
    """
    Processes training jobs from Azure Storage Queue.
    """
    
    def __init__(self):
        # Azure Storage connection
        connection_string = os.getenv('AZURE_STORAGE_CONNECTION_STRING')
        if not connection_string:
            raise ValueError("AZURE_STORAGE_CONNECTION_STRING not set")
        
        self.queue_client = QueueClient.from_connection_string(
            conn_str=connection_string,
            queue_name='model-training'
        )
        
        self.blob_service = BlobServiceClient.from_connection_string(connection_string)
        self.container_name = 'github-cache'
        
        # Training mode: 'serverless' (exit after one job) or 'kubernetes' (continuous polling)
        self.training_mode = os.getenv('TRAINING_MODE', 'serverless')
        
        logger.info(f"Training worker initialized (mode: {self.training_mode})")
    
    def process_training_job(self, message: dict) -> bool:
        """
        Process a single training job.
        
        Args:
            message: Queue message with {username, repos_bundle, training_params, experiment_name}
            
        Returns:
            bool: True if training succeeded
        """
        username = message['username']
        repos_bundle = message['repos_bundle']
        experiment_name = message.get('experiment_name', 'default')
        custom_params = message.get('training_params', {})
        
        logger.info(f"Starting training for {username}, experiment: {experiment_name}")
        
        # Filter documented repositories
        documented_repos = [r for r in repos_bundle if r.get('has_documentation')]
        if len(documented_repos) < 3:
            logger.warning(f"Not enough documented repos ({len(documented_repos)}), skipping")
            return False
        
        # Get model configuration
        config = get_model_config(experiment_name)
        base_model = config['base_model']
        training_params = config['params'].copy()
        training_params.update(custom_params)  # Allow message to override
        
        # Generate fingerprint
        fingerprint = self._generate_fingerprint(documented_repos)
        model_path = f"/tmp/model_{fingerprint}"
        
        # Train model
        semantic_model = SemanticModel(
            base_model=base_model,
            blob_connection_string=os.getenv('AZURE_STORAGE_CONNECTION_STRING')
        )
        
        success = semantic_model.train_from_repositories(
            repos_bundle=documented_repos,
            output_path=model_path,
            training_params=training_params
        )
        
        if not success:
            logger.error("Training failed")
            return False
        
        # Upload model to blob storage
        blob_name = f"model_{fingerprint}.zip"
        semantic_model.upload_to_blob(model_path, blob_name, self.container_name)
        
        # Save metadata
        self._save_metadata(fingerprint, username, experiment_name, training_params, documented_repos)
        
        logger.info(f"Training completed successfully: {fingerprint[:8]}")
        return True
    
    def _generate_fingerprint(self, repos_bundle: list) -> str:
        """Generate content fingerprint for repos bundle."""
        import hashlib
        content = json.dumps(repos_bundle, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()
    
    def _save_metadata(self, fingerprint: str, username: str, experiment_name: str, 
                      training_params: dict, repos_bundle: list):
        """Save training metadata for tracking."""
        metadata = {
            'fingerprint': fingerprint,
            'username': username,
            'experiment_name': experiment_name,
            'training_params': training_params,
            'timestamp': datetime.now().isoformat(),
            'repos_count': len(repos_bundle),
            'repo_names': [r.get('name', 'Unknown') for r in repos_bundle]
        }
        
        # Upload metadata as JSON blob
        container_client = self.blob_service.get_container_client(self.container_name)
        blob_client = container_client.get_blob_client(f"model_metadata_{fingerprint}.json")
        blob_client.upload_blob(json.dumps(metadata, indent=2), overwrite=True)
        
        logger.info(f"Metadata saved for fingerprint: {fingerprint[:8]}")
    
    def run(self):
        """
        Main worker loop.
        In 'serverless' mode: process one job and exit.
        In 'kubernetes' mode: continuously poll queue.
        """
        logger.info(f"Worker started, polling queue (mode: {self.training_mode})...")
        
        while True:
            messages = self.queue_client.receive_messages(
                max_messages=1,
                visibility_timeout=600  # 10 minutes
            )
            
            for message in messages:
                try:
                    job_data = json.loads(message.content)
                    logger.info(f"Received training job: {job_data.get('username')}")
                    
                    # Process training
                    success = self.process_training_job(job_data)
                    
                    if success:
                        # Delete message from queue
                        self.queue_client.delete_message(message)
                        logger.info("Training job completed, message deleted")
                    else:
                        logger.error("Training failed, message will be retried")
                    
                    # In serverless mode, exit after processing one job
                    if self.training_mode == 'serverless':
                        logger.info("Serverless mode: exiting after job completion")
                        return
                    
                except Exception as e:
                    logger.error(f"Error processing training job: {e}", exc_info=True)
                    # Leave message in queue for retry
            
            # If no messages and in serverless mode, exit
            if self.training_mode == 'serverless':
                logger.info("No messages in queue, exiting")
                return
            
            # In kubernetes mode, wait before next poll
            time.sleep(30)


if __name__ == '__main__':
    worker = TrainingWorker()
    worker.run()
```

---

### 5. Dockerfile (Multi-Stage Build)

```dockerfile
# workers/training/Dockerfile

# ============================================
# Stage 1: Base image with Python + PyTorch CPU
# ============================================
FROM python:3.11-slim AS base

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ============================================
# Stage 2: GPU variant (NVIDIA CUDA + cuDNN)
# ============================================
FROM nvidia/cuda:11.8.0-cudnn8-runtime-ubuntu22.04 AS gpu-base

# Install Python 3.11
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 \
    python3-pip \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install PyTorch with CUDA support
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt && \
    pip3 install torch==2.2.2+cu118 torchvision==0.17.2+cu118 -f https://download.pytorch.org/whl/torch_stable.html

# ============================================
# Stage 3: Application code (shared by CPU and GPU)
# ============================================
FROM base AS app

# Copy application code
COPY models/ ./models/
COPY train_worker.py .

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV TRAINING_MODE=serverless

# Health check (optional, for Kubernetes liveness probes)
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import sys; sys.exit(0)"

# Run training worker
CMD ["python", "train_worker.py"]
```

**Build commands**:
```bash
# CPU version (default, for Azure Container Instances and AKS)
docker build -t acr.azurecr.io/portfolio-trainer:cpu --target app .

# GPU version (for experiments)
docker build -t acr.azurecr.io/portfolio-trainer:gpu --target gpu-base -f Dockerfile .
```

---

### 6. Requirements File

```txt
# workers/training/requirements.txt

# Core ML libraries
torch==2.2.2
sentence-transformers==2.6.1
transformers==4.38.0
scikit-learn==1.4.0
numpy==1.26.4

# Azure SDK
azure-storage-queue==12.9.0
azure-storage-blob==12.19.0
azure-identity==1.15.0

# Utilities
tqdm==4.66.1
```

---

## Deployment Strategies

### Option A: Azure Container Instances (Function App Environment)

**Trigger**: Launch container from `merge_worker` after bundle is ready

```python
# api/config/container_launcher.py (NEW FILE)
from azure.mgmt.containerinstance import ContainerInstanceManagementClient
from azure.identity import DefaultAzureCredential
import os
import time
import logging

logger = logging.getLogger('portfolio.api')

class ContainerLauncher:
    """
    Launch on-demand training containers using Azure Container Instances.
    """
    
    def __init__(self):
        self.credential = DefaultAzureCredential()
        self.subscription_id = os.getenv('AZURE_SUBSCRIPTION_ID')
        self.resource_group = os.getenv('AZURE_RESOURCE_GROUP')
        self.location = os.getenv('AZURE_LOCATION', 'westeurope')
        
        self.client = ContainerInstanceManagementClient(
            credential=self.credential,
            subscription_id=self.subscription_id
        )
    
    def launch_training_job(self, username: str, gpu: bool = False, experiment_name: str = 'default'):
        """
        Launch a container instance to process training queue.
        
        Args:
            username: GitHub username (for logging/naming)
            gpu: Whether to request GPU instance
            experiment_name: Model experiment to run
        """
        container_group_name = f"trainer-{username}-{int(time.time())}"
        
        # Container resource requests
        container_resources = {
            'cpu': 4.0,
            'memory_in_gb': 16.0
        }
        
        if gpu:
            container_resources['gpu'] = {
                'count': 1,
                'sku': 'K80'  # Options: K80, P100, V100
            }
        
        # Container group specification
        container_group = {
            'location': self.location,
            'containers': [{
                'name': 'trainer',
                'image': f'{os.getenv("ACR_REGISTRY")}/portfolio-trainer:{"gpu" if gpu else "cpu"}',
                'resources': {
                    'requests': container_resources
                },
                'environment_variables': [
                    {
                        'name': 'AZURE_STORAGE_CONNECTION_STRING',
                        'secure_value': os.getenv('AZURE_STORAGE_CONNECTION_STRING')
                    },
                    {'name': 'TRAINING_MODE', 'value': 'serverless'},
                    {'name': 'EXPERIMENT_NAME', 'value': experiment_name}
                ]
            }],
            'os_type': 'Linux',
            'restart_policy': 'Never',  # One-time job
            'identity': {
                'type': 'UserAssigned',
                'user_assigned_identities': {
                    os.getenv('UAMI_RESOURCE_ID'): {}
                }
            }
        }
        
        try:
            # Launch container asynchronously
            poller = self.client.container_groups.begin_create_or_update(
                self.resource_group,
                container_group_name,
                container_group
            )
            
            logger.info(f"Launched training container: {container_group_name} (GPU: {gpu})")
            return container_group_name
            
        except Exception as e:
            logger.error(f"Failed to launch container: {e}", exc_info=True)
            return None
```

**Modified `merge_worker` in `function_app.py`**:
```python
@app.queue_trigger(arg_name="msg", queue_name="merge-results", connection="AzureWebJobsStorage")
def merge_worker(msg: func.QueueMessage):
    # ... existing merge logic ...
    
    # CHANGED: Instead of enqueuing training job, launch container
    try:
        from config.container_launcher import ContainerLauncher
        
        launcher = ContainerLauncher()
        container_name = launcher.launch_training_job(
            username=username,
            gpu=False,  # Set to True for GPU experiments
            experiment_name='default'  # Or get from job metadata
        )
        
        if container_name:
            logger.info(f"Training container launched: {container_name}")
        else:
            logger.warning("Failed to launch training container")
            
    except Exception as e:
        logger.error(f"Container launch error: {e}", exc_info=True)
```

---

### Option B: Kubernetes Job (AKS Environment)

**Kubernetes Job Manifest**:
```yaml
# k8s/training-job.yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: model-training-{{ username }}-{{ timestamp }}
  labels:
    app: portfolio-trainer
    username: {{ username }}
    experiment: {{ experiment_name }}
spec:
  # Auto-cleanup after 1 hour
  ttlSecondsAfterFinished: 3600
  
  # Retry policy
  backoffLimit: 2
  
  template:
    metadata:
      labels:
        app: portfolio-trainer
    spec:
      restartPolicy: Never
      
      containers:
      - name: trainer
        image: {{ ACR_REGISTRY }}/portfolio-trainer:cpu
        
        resources:
          requests:
            memory: "16Gi"
            cpu: "4"
          limits:
            memory: "16Gi"
            cpu: "4"
        
        env:
        - name: AZURE_STORAGE_CONNECTION_STRING
          valueFrom:
            secretKeyRef:
              name: storage-secret
              key: connection-string
        
        - name: TRAINING_MODE
          value: "kubernetes"
        
        - name: EXPERIMENT_NAME
          value: "{{ experiment_name }}"
      
      # Optional: Use GPU node pool
      # Uncomment when running experiments with GPU
      # nodeSelector:
      #   accelerator: nvidia-tesla-t4
      # 
      # tolerations:
      # - key: "nvidia.com/gpu"
      #   operator: "Exists"
      #   effect: "NoSchedule"
```

**Python Launcher for AKS**:
```python
# api/workers/k8s_job_launcher.py
from kubernetes import client, config
import os
import time
import logging

logger = logging.getLogger(__name__)

class K8sJobLauncher:
    """
    Launch Kubernetes Jobs for model training.
    """
    
    def __init__(self):
        # Load in-cluster config when running inside AKS
        try:
            config.load_incluster_config()
        except:
            # Fallback to local kubeconfig for development
            config.load_kube_config()
        
        self.batch_api = client.BatchV1Api()
        self.namespace = os.getenv('K8S_NAMESPACE', 'default')
    
    def launch_training_job(self, username: str, gpu: bool = False, experiment_name: str = 'default'):
        """
        Create a Kubernetes Job for model training.
        """
        job_name = f"trainer-{username}-{int(time.time())}"
        
        # Container specification
        container = client.V1Container(
            name='trainer',
            image=f'{os.getenv("ACR_REGISTRY")}/portfolio-trainer:{"gpu" if gpu else "cpu"}',
            resources=client.V1ResourceRequirements(
                requests={'memory': '16Gi', 'cpu': '4'},
                limits={'memory': '16Gi', 'cpu': '4'}
            ),
            env=[
                client.V1EnvVar(
                    name='AZURE_STORAGE_CONNECTION_STRING',
                    value_from=client.V1EnvVarSource(
                        secret_key_ref=client.V1SecretKeySelector(
                            name='storage-secret',
                            key='connection-string'
                        )
                    )
                ),
                client.V1EnvVar(name='TRAINING_MODE', value='kubernetes'),
                client.V1EnvVar(name='EXPERIMENT_NAME', value=experiment_name)
            ]
        )
        
        # Pod template
        pod_spec = client.V1PodSpec(
            restart_policy='Never',
            containers=[container]
        )
        
        # Add GPU node selector if requested
        if gpu:
            pod_spec.node_selector = {'accelerator': 'nvidia-tesla-t4'}
            pod_spec.tolerations = [
                client.V1Toleration(
                    key='nvidia.com/gpu',
                    operator='Exists',
                    effect='NoSchedule'
                )
            ]
        
        # Job specification
        job = client.V1Job(
            api_version='batch/v1',
            kind='Job',
            metadata=client.V1ObjectMeta(
                name=job_name,
                labels={
                    'app': 'portfolio-trainer',
                    'username': username,
                    'experiment': experiment_name
                }
            ),
            spec=client.V1JobSpec(
                ttl_seconds_after_finished=3600,
                backoff_limit=2,
                template=client.V1PodTemplateSpec(spec=pod_spec)
            )
        )
        
        try:
            self.batch_api.create_namespaced_job(namespace=self.namespace, body=job)
            logger.info(f"Launched K8s training job: {job_name} (GPU: {gpu})")
            return job_name
        except Exception as e:
            logger.error(f"Failed to create K8s job: {e}", exc_info=True)
            return None
```

---

## Cost Comparison

### Azure Container Instances (Serverless)

| Configuration | vCPU | Memory | GPU | Cost per Hour | Cost per Training Run (60 min) |
|---------------|------|--------|-----|---------------|--------------------------------|
| **CPU Standard** | 4 | 16GB | No | $0.40/hr | **$0.40** |
| **CPU High-Memory** | 4 | 32GB | No | $0.80/hr | $0.80 |
| **GPU (K80)** | 4 | 16GB | 1× K80 | $1.50/hr | **$1.50** |
| **GPU (T4)** | 4 | 16GB | 1× T4 | $2.00/hr | $2.00 |

**Idle Cost**: $0 (containers terminated after job completion)

---

### AKS (Shared Node Pool)

| Configuration | Node Type | vCPU | Memory | GPU | Monthly Cost | Cost per Run (amortized) |
|---------------|-----------|------|--------|-----|--------------|--------------------------|
| **CPU Node Pool** | Standard_D4s_v3 | 4 | 16GB | No | $144/month | **$0.20** (if 30 runs/month) |
| **GPU Node Pool** | Standard_NC4as_T4_v3 | 4 | 28GB | 1× T4 | $360/month | **$1.00** (if 15 runs/month) |
| **Spot GPU** | NC4as_T4_v3 (spot) | 4 | 28GB | 1× T4 | $108/month | **$0.30** (70% discount) |

**Idle Cost**: Full node cost (unless using scale-to-zero with virtual nodes)

---

## Experiment Workflow

### Running Different Model Experiments

1. **Add new configuration** to `model_registry.py`:
```python
MODEL_CONFIGS['my-experiment'] = {
    'base_model': 'BAAI/bge-large-en-v1.5',
    'description': 'Testing larger BGE model',
    'params': {
        'batch_size': 4,
        'epochs': 3,
        'use_mnrl': True
    }
}
```

2. **Queue training job with experiment name**:
```python
# In merge_worker or API endpoint
queue_manager.enqueue_training_job(
    username=username,
    repos_bundle=merged_results,
    training_params={
        'experiment_name': 'my-experiment'  # Use new config
    }
)
```

3. **Launch GPU container for experiment** (optional):
```python
launcher = ContainerLauncher()
launcher.launch_training_job(
    username=username,
    gpu=True,  # Request GPU for large model
    experiment_name='my-experiment'
)
```

4. **Compare results**: Models saved with different fingerprints, metadata tracks experiment name

---

## Migration Path

### Phase 1: Parallel Deployment (Week 1-2)

- Deploy training container to Azure Container Registry
- Keep existing `train_semantic_model_activity` (no changes)
- Add `container_launcher.py` with feature flag `ENABLE_CONTAINER_TRAINING=false`
- Test container training manually (outside queue)

### Phase 2: Gradual Rollout (Week 3-4)

- Enable `ENABLE_CONTAINER_TRAINING=true` for 10% of training jobs
- Monitor: Container startup time, training duration, cost
- Compare: Container Instances vs Function App activity performance
- Increase to 50%, then 100%

### Phase 3: Deprecate Function App Training (Week 5-6)

- Remove `train_semantic_model_activity` from `function_app.py`
- Remove `fine_tuning.py` imports from Function App
- Update documentation

---

## Success Metrics

### Performance Targets

| Metric | Current (Function App) | Target (Container) | Improvement |
|--------|------------------------|-------------------|-------------|
| **Training Time** | 60-90 seconds | 45-60 seconds | 25% faster (more CPU) |
| **Memory Usage** | Limited to 2GB | 16GB available | 8× capacity |
| **GPU Support** | Not available | Optional (experiments) | ✅ Enabled |
| **Idle Cost** | $30/month (shared) | $0/month | 100% reduction |
| **Experiment Flexibility** | Requires redeploy | Config change only | ✅ Instant |

### Week 1 Post-Deployment

- ✅ Training container processes jobs successfully
- ✅ Model artifacts uploaded to blob storage
- ✅ Metadata tracking works (experiment names, fingerprints)
- ✅ Container terminates after job completion (zero idle cost)

### Week 2-4 Optimization

- ✅ Average training time < 60 seconds
- ✅ Zero failed training jobs (retry logic works)
- ✅ At least 2 model experiments tested (different base models)

---

## Risk Assessment

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| **Container cold start** | High | Low | 30-60s startup acceptable (non-blocking) |
| **Training failures** | Medium | Medium | Retry logic (3 attempts), dead-letter queue |
| **Cost overrun (GPU)** | Low | High | Set resource quotas, monitor spend, use spot instances |
| **Model download failures** | Low | Medium | Retry with exponential backoff |
| **AKS cluster unavailable** | Low | High | Fallback to Azure Container Instances |

**Overall Risk**: **LOW** (training is non-critical, failures don't impact API)

---

## Summary

### Recommended Approach

1. **Start with Azure Container Instances** (simplest, zero infrastructure)
2. **Same Docker image for both Function App and AKS** (portability)
3. **Experiment tracking via model registry** (no code changes needed)
4. **On-demand GPU for experiments** (cost-effective, flexible)

### Key Benefits

- ✅ **Cost**: $0 idle cost (vs $30/month Function App overhead)
- ✅ **Performance**: 4 vCPU, 16GB RAM (vs 2GB Function App limit)
- ✅ **Flexibility**: Swap base models without redeploying Function App
- ✅ **GPU Support**: Request GPU instances on-demand for experiments
- ✅ **Decoupling**: Training failures don't impact API availability
- ✅ **Portability**: Same container runs on Container Instances or AKS

### Next Steps

1. Create `workers/training/` directory structure
2. Extract `SemanticModel` from `fine_tuning.py` (remove Function App dependencies)
3. Implement `train_worker.py` with queue polling
4. Build Docker image (CPU variant first)
5. Test locally with Azurite queues
6. Deploy to Azure Container Registry
7. Test Container Instances launch from `merge_worker`
8. Monitor costs and performance for 1 week
9. Deprecate Function App training activity

---

**Estimated Implementation Time**: 2-3 weeks (parallel with storage queue migration)

