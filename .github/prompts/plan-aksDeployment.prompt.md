# AKS Deployment Plan - Future Migration Alternative

**Date**: November 20, 2025  
**Status**: Future Planning Phase (Post-Function App Implementation)  
**Priority**: Alternative Deployment Strategy (Learning & Advanced Configurations)

---

## Executive Summary

This document outlines a **future migration path** to deploy ALL cloudfolio components (API Gateway, Sync Worker, Merge Worker, Training Worker) to Azure Kubernetes Service (AKS) as an **alternative deployment option** after the primary Azure Function Apps implementation is complete and stable.

### Purpose & Positioning

**CRITICAL CLARIFICATION**: This plan represents a **SEPARATE, FUTURE migration** path:
- **PRIMARY Deployment**: Azure Function Apps for API Gateway, Sync Worker, Merge Worker
- **EXCEPTION**: Training Worker is containerized (deployed to ACI/AKS) due to high CPU/GPU requirements (see `plan-semanticModelTrainingRefactor.prompt.md`)
- **THIS PLAN**: Deploy ALL 4 workers to AKS as an alternative after Function App implementation is complete

### Why AKS (After Function Apps Are Working)

| Motivation | Function Apps Limitation | AKS Advantage |
|------------|--------------------------|---------------|
| **Advanced Network Policies** | Basic VNet integration | Calico network policies, pod-to-pod encryption, ingress controllers |
| **Custom Autoscaling** | HTTP-based only | KEDA with 20+ scalers (queue depth, CPU, custom metrics, cron) |
| **GPU Node Pools** | Not available | Dedicated GPU nodes with taints/tolerations for ML workloads |
| **Cost Optimization** | Pay per execution | Spot instances (70% discount) + cluster autoscaler |
| **Portability** | Azure-locked | Deploy to GKE, EKS with minimal changes |
| **Deep Learning** | Limited observability | Prometheus, Grafana, service mesh (Istio/Linkerd) |

### Success Criteria for Triggering This Migration

**DO NOT START** this migration until:
- ✅ Function Apps deployment is stable for 3+ months
- ✅ All 4 workers (API Gateway, Sync, Merge, Training) working in production
- ✅ Azure Storage Queues integration proven (no dead-letter queue issues)
- ✅ Team has capacity for 8-week migration project
- ✅ Business case approved (learning opportunity vs. operational stability)

---

## Current Architecture (Function Apps - PRIMARY Deployment)

### Component Distribution

```
┌─────────────────────────────────────────────────────────────┐
│                 Azure Function Apps (PRIMARY)                │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌─────────────────┐  ┌─────────────────┐                  │
│  │  API Gateway    │  │  Sync Worker    │                  │
│  │  (HTTP Trigger) │  │  (Queue Trigger)│                  │
│  │  ASP Consume   │  │  ASP Consume   │                  │
│  └─────────────────┘  └─────────────────┘                  │
│                                                               │
│  ┌─────────────────┐                                        │
│  │  Merge Worker   │                                        │
│  │  (Queue Trigger)│                                        │
│  │  ASP Consume   │                                        │
│  └─────────────────┘                                        │
└─────────────────────────────────────────────────────────────┘

                              │
                              │ Enqueues to model-training queue
                              ↓
┌─────────────────────────────────────────────────────────────┐
│            Training Worker (CONTAINERIZED - ACI/AKS)         │
│                                                               │
│  - Deployed as container (see plan-semanticModel            │
│    TrainingRefactor.prompt.md)                               │
│  - Uses Azure Storage Queues (model-training queue)         │
│  - 4 vCPU, 16GB RAM (or GPU variant)                        │
│  - Scales to zero when idle                                  │
└─────────────────────────────────────────────────────────────┘

Note: Uses Azure Storage Queues (github-sync, merge-results, 
      model-training) - NOT Redis
```

**Key Points**:
- API Gateway, Sync Worker, Merge Worker → Azure Functions 
- Training Worker → Containerized (ACI or AKS) due to compute requirements
- All workers use Azure Storage Queues (NOT Redis - see `plan-azureStorageQueuesArchitecture.prompt.md`)

---

## Target Architecture (AKS - FUTURE Alternative)

### All Components as Kubernetes Workloads

```
┌───────────────────────────────────────────────────────────────────┐
│                     Azure Kubernetes Service                       │
│                    (FUTURE Alternative Deployment)                 │
├───────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐│
│  │                   Regular Node Pool                          ││
│  │           (Standard_D2s_v5, 2 nodes, auto-scale 1-5)         ││
│  │                                                                ││
│  │  ┌─────────────────┐  ┌─────────────────┐                   ││
│  │  │  API Gateway    │  │  Sync Worker    │                   ││
│  │  │  (Deployment)   │  │  (Deployment)   │                   ││
│  │  │  Port 8000      │  │  Queue Consumer │                   ││
│  │  │  HPA: 2-10 pods │  │  HPA: 0-10 pods │                   ││
│  │  └─────────────────┘  └─────────────────┘                   ││
│  │                                                                ││
│  │  ┌─────────────────┐                                         ││
│  │  │  Merge Worker   │                                         ││
│  │  │  (Deployment)   │                                         ││
│  │  │  Queue Consumer │                                         ││
│  │  │  HPA: 0-5 pods  │                                         ││
│  │  └─────────────────┘                                         ││
│  └──────────────────────────────────────────────────────────────┘│
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐│
│  │                   Spot Node Pool (OPTIONAL)                  ││
│  │          (Standard_NC4as_T4_v3, GPU, 0-2 nodes)              ││
│  │          Taint: workload=spot:NoSchedule                     ││
│  │                                                                ││
│  │  ┌─────────────────┐                                         ││
│  │  │ Training Worker │                                         ││
│  │  │ (Job/CronJob)   │                                         ││
│  │  │ GPU Toleration  │                                         ││
│  │  │ 4 vCPU, 16GB    │                                         ││
│  │  └─────────────────┘                                         ││
│  └──────────────────────────────────────────────────────────────┘│
│                                                                     │
│  Ingress Controller: nginx-ingress                                 │
│  Storage: Azure Storage Queues + Blob (unchanged)                  │
│  Monitoring: Prometheus + Grafana (optional)                       │
└───────────────────────────────────────────────────────────────────┘
```

### Infrastructure Components

**Existing Terraform Configuration** (`infra/terraform/main.tf`):
- AKS Cluster: `cloudfolio-cluster` (already defined)
- System Node Pool: Standard_D2s_v5, 2 nodes
- Spot Node Pool: Standard_NC4as_T4_v3 (GPU), 0-2 nodes with 70% discount
- Network Plugin: Azure CNI with Calico network policy

---

## Clarifications to Existing Plans

### 1. plan-multiAppArchitecture.prompt.md Clarification

**Original Statement (Line 35)**: "Infrastructure: Azure-locked (Durable Functions) → Cloud-agnostic (containers + queues)"

**CLARIFICATION**:
```diff
| Aspect | Current State | Target State | Impact |
|--------|---------------|--------------|--------|
- Infrastructure | Azure-locked | Cloud-agnostic (containers + queues) | Portable to AWS/GCP
+ Infrastructure | Azure-locked | **PRIMARY: Function Apps (ASP)** | Azure-optimized
+               |               | **FUTURE: Containers on AKS** | Portable to AWS/GCP
```

**Updated Section**:
> **PRIMARY Deployment**: Azure Function Apps using Azure Storage Queues
> - API Gateway, Sync Worker, Merge Worker: HTTP/Queue triggers in Function Apps
> - Training Worker: Containerized (ACI/AKS) due to high CPU/GPU requirements
> 
> **FUTURE Alternative**: Full AKS deployment (all 4 workers as containers)
> - Requires stable Function App implementation first (3+ months production)
> - Enables advanced K8s features (network policies, spot instances, custom autoscaling)

---

### 2. plan-azureStorageQueuesArchitecture.prompt.md Clarification

**Original Statement (Line 15)**: "AKS Compatible: Same storage account works with connection string SDK"

**CLARIFICATION**:
```diff
### Key Metrics
- **Delivery Time**: 7 days
- **Infrastructure Changes**: Zero (reuses existing storage account)
- **Code Changes**: ~150 lines (native Azure Functions queue triggers)
+ **Primary Deployment**: Azure Function Apps (Queue Triggers)
+ **AKS Compatibility**: Same Azure Storage Queues work with AKS deployment (future migration)
```

**Updated Section (Line 82)**:
```diff
- **AKS Compatible**: Same storage account works with connection string SDK
+ **AKS Compatible (FUTURE)**: Same Azure Storage Queues work for AKS deployment
+   - Function Apps: Use Managed Identity with @app.queue_trigger()
+   - AKS: Use Connection String with azure.storage.queue.QueueClient
+   - See plan-aksDeployment.prompt.md for AKS migration details
```

---

### 3. plan-semanticModelTrainingRefactor.prompt.md Clarification

**Status**: ✅ **CORRECT** - This plan already shows training-worker as containerized

**Line 15**: "Training Worker: Containerized (see plan-semanticModelTrainingRefactor.prompt.md)"

**Key Section (Line 449-635)**: Shows training worker deployed to Azure Container Instances OR AKS

**NO CHANGES NEEDED** - This is the ONLY component that is containerized in the PRIMARY deployment.

---

## Why AKS Migration (After Function Apps)

### Advanced Kubernetes Configurations

| Feature | Function Apps | AKS | Use Case |
|---------|---------------|-----|----------|
| **Network Policies** | VNet integration only | Calico pod-to-pod rules | Isolate training worker network access |
| **Custom Autoscaling** | HTTP requests only | KEDA (20+ scalers) | Scale on queue depth, CPU, cron schedules |
| **GPU Node Pools** | Not available | Dedicated GPU nodes | ML model training experiments |
| **Spot Instances** | Not available | 70% discount on workloads | Cost optimization for all workers |
| **Service Mesh** | Not available | Istio/Linkerd | mTLS, circuit breaking, retry policies |
| **Ingress Controllers** | Built-in | nginx/Traefik/Envoy | Advanced routing, rate limiting |

### Cost Optimization with Spot Instances

**Function Apps Cost** :
- API Gateway: $15/month (2 avg instances)
- Sync Worker: $20/month (3 avg instances during bursts)
- Merge Worker: $8/month (1 avg instance)
- Training Worker (ACI): $4/month (10 runs × $0.40/run)
- **Total**: $47/month compute

**AKS Cost** (with spot instances):
- Regular Node Pool (2× Standard_D2s_v5): $144/month
- Spot Node Pool (1× NC4as_T4_v3 GPU): $108/month (70% discount from $360/month)
- **Total**: $252/month

**Cost Comparison**: AKS is **$205/month MORE expensive** than Function Apps

**JUSTIFICATION FOR MIGRATION**:
- ❌ NOT for cost savings (Function Apps cheaper)
- ✅ For advanced configurations (network policies, GPU, spot instances)
- ✅ For deep learning in Kubernetes (career growth, portability)
- ✅ For future portability to GKE/EKS (multi-cloud strategy)

---

## Deep Dive Learning in AKS

### Learning Objectives

**Kubernetes Fundamentals**:
- Deployments, StatefulSets, Jobs, CronJobs
- ConfigMaps, Secrets, PersistentVolumeClaims
- Services (ClusterIP, LoadBalancer, NodePort)
- Ingress controllers and routing rules

**Advanced AKS Features**:
- Azure CNI networking with Calico policies
- Managed Identity with Azure Workload Identity
- Private cluster with API server VNet integration
- Cluster autoscaler with spot instances

**Observability**:
- Prometheus for metrics collection
- Grafana for dashboards
- Azure Monitor Container Insights
- Jaeger for distributed tracing

**GitOps**:
- Flux CD for continuous deployment
- Helm charts for application packaging
- ArgoCD for declarative deployments

---

## Future Portability (GKE, EKS)

### Cloud-Agnostic Components

**Already Cloud-Agnostic** (in Function Apps implementation):
- Azure Storage Queues → Can swap with AWS SQS, GCP Pub/Sub
- Azure Blob Storage → Can swap with S3, GCS
- Application code (Python workers) → No Azure-specific dependencies

**AKS-Specific vs. Portable**:

| Component | AKS-Specific | Portable Alternative |
|-----------|--------------|---------------------|
| **Network Plugin** | Azure CNI | Calico (works on GKE/EKS) |
| **Managed Identity** | Azure Workload Identity | IRSA (EKS), Workload Identity (GKE) |
| **Ingress** | Application Gateway Ingress Controller | nginx-ingress (multi-cloud) |
| **Monitoring** | Azure Monitor Container Insights | Prometheus + Grafana (multi-cloud) |
| **Storage** | Azure Disk/Files | PersistentVolumes with CSI drivers |

**Migration Effort** (AKS → GKE/EKS):
- Kubernetes manifests: 90% reusable (change namespace, labels)
- Managed Identity: Swap Azure Workload Identity with IRSA/GKE Workload Identity
- Ingress: Swap Application Gateway with Cloud Load Balancer
- Storage Queues: Swap Azure Storage SDK with AWS SQS/GCP Pub/Sub SDK
- **Estimated Time**: 2-3 weeks for GKE/EKS migration

---

## Side-by-Side Comparison: Function Apps vs. AKS

### Performance

| Metric | Function Apps (ASP) | AKS | Winner |
|--------|---------------------|-----|--------|
| **Cold Start** | 2-5s (HTTP), 1-2s (queue) | 0s (always running pods) | ✅ AKS |
| **Max Throughput** | 100 instances (configurable) | Limited by node pool size | ⚖️ Tie |
| **Scaling Speed** | 10-30s | 5-15s (HPA) | ✅ AKS |
| **Network Latency** | VNet integration | Pod-to-pod in cluster | ✅ AKS |

### Operations

| Metric | Function Apps (ASP) | AKS | Winner |
|--------|---------------------|-----|--------|
| **Deployment Complexity** | Low (1 ZIP file) | High (manifests, Helm, CI/CD) | ✅ Function Apps |
| **Monitoring** | Application Insights | Prometheus + Grafana | ⚖️ Tie |
| **Debugging** | Azure Portal logs | kubectl logs, exec | ⚖️ Tie |
| **Secret Management** | Key Vault references | Sealed Secrets, External Secrets | ⚖️ Tie |

### Cost

| Metric | Function Apps (ASP) | AKS | Winner |
|--------|---------------------|-----|--------|
| **Idle Cost** | $0 (scale to zero) | $144/month (node pool) | ✅ Function Apps |
| **Per-Request Cost** | $0.0004/request | $0 (included in nodes) | ⚖️ Depends on scale |
| **Total (Low Traffic)** | $47/month | $252/month | ✅ Function Apps |
| **Total (High Traffic)** | $120/month | $252/month | ✅ Function Apps |

### Advanced Features

| Feature | Function Apps (ASP) | AKS | Winner |
|---------|---------------------|-----|--------|
| **Network Policies** | ❌ Not available | ✅ Calico policies | ✅ AKS |
| **GPU Support** | ❌ Not available | ✅ Dedicated GPU nodes | ✅ AKS |
| **Custom Autoscaling** | ❌ HTTP only | ✅ KEDA (20+ scalers) | ✅ AKS |
| **Spot Instances** | ❌ Not available | ✅ 70% discount | ✅ AKS |
| **Service Mesh** | ❌ Not available | ✅ Istio/Linkerd | ✅ AKS |

**Recommendation**: 
- **Stick with Function Apps** if cost and simplicity are priorities
- **Migrate to AKS** if you need advanced configurations, GPU, or multi-cloud portability

---

## Migration Phases (8 Weeks)

### Prerequisites (Before Starting)

- ✅ Function Apps deployment stable for 3+ months
- ✅ AKS cluster provisioned (Terraform in `infra/terraform/main.tf`)
- ✅ Team has completed AKS fundamentals training
- ✅ Business case approved (learning vs. operational risk)
- ✅ Rollback plan documented and tested

---

### Phase 1: Infrastructure Setup (Week 1)

**Objective**: Provision AKS cluster and shared infrastructure

**Tasks**:

**1.1 Deploy AKS Cluster (Terraform)**
```bash
cd infra/terraform
terraform init
terraform plan -out=tfplan
terraform apply tfplan
```

**Cluster Configuration** (from `infra/terraform/main.tf`):
- **System Node Pool**: 2× Standard_D2s_v5 (2 vCPU, 8GB RAM)
- **Spot Node Pool**: 0-2× Standard_NC4as_T4_v3 (GPU, 70% discount)
- **Network**: Azure CNI with Calico network policy
- **Kubernetes Version**: 1.28+ (configurable)

**1.2 Configure kubectl Access**
```bash
az aks get-credentials \
  --resource-group rg-cloudfolio \
  --name cloudfolio-cluster
```

**1.3 Install Core Add-ons**
```bash
# NGINX Ingress Controller
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm install nginx-ingress ingress-nginx/ingress-nginx \
  --namespace ingress-nginx --create-namespace

# KEDA (Kubernetes Event-Driven Autoscaling)
helm repo add kedacore https://kedacore.github.io/charts
helm install keda kedacore/keda \
  --namespace keda --create-namespace

# Azure Workload Identity (for Managed Identity)
az aks enable-addons \
  --resource-group rg-cloudfolio \
  --name cloudfolio-cluster \
  --addons azure-workload-identity
```

**1.4 Create Namespaces**
```bash
kubectl create namespace portfolio-api
kubectl create namespace portfolio-workers
kubectl create namespace monitoring
```

**Deliverables**:
- ✅ AKS cluster running with 2 system nodes
- ✅ NGINX ingress controller installed
- ✅ KEDA installed (for queue-based autoscaling)
- ✅ Azure Workload Identity enabled
- ✅ Namespaces created (portfolio-api, portfolio-workers)

---

### Phase 2: Containerize Remaining Workers (Week 2)

**Objective**: Create Docker images for API Gateway, Sync Worker, Merge Worker

**Tasks**:

**2.1 API Gateway Dockerfile**
```dockerfile
# apps/api-gateway/Dockerfile (already exists in Function Apps plan)
FROM python:3.11-slim

WORKDIR /app

# Install shared package
COPY apps/shared /tmp/shared
RUN pip install -e /tmp/shared

# Install gateway dependencies
COPY apps/api-gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY apps/api-gateway/ .

# Health check
HEALTHCHECK --interval=30s --timeout=3s \
  CMD python -c "import requests; requests.get('http://localhost:8000/health')"

# Run FastAPI
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**2.2 Sync Worker Dockerfile**
```dockerfile
# apps/sync-worker/Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install shared package
COPY apps/shared /tmp/shared
RUN pip install -e /tmp/shared

# Install worker dependencies
COPY apps/sync-worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy worker code
COPY apps/sync-worker/ .

# Run worker (polls Azure Storage Queue)
CMD ["python", "worker.py"]
```

**2.3 Merge Worker Dockerfile**
```dockerfile
# apps/merge-worker/Dockerfile (similar structure)
FROM python:3.11-slim
WORKDIR /app
COPY apps/shared /tmp/shared
RUN pip install -e /tmp/shared
COPY apps/merge-worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY apps/merge-worker/ .
CMD ["python", "worker.py"]
```

**2.4 Build and Push Images**
```bash
# Azure Container Registry login
az acr login --name acrportfolio

# Build images
docker build -t acrportfolio.azurecr.io/api-gateway:v1 apps/api-gateway/
docker build -t acrportfolio.azurecr.io/sync-worker:v1 apps/sync-worker/
docker build -t acrportfolio.azurecr.io/merge-worker:v1 apps/merge-worker/

# Push to ACR
docker push acrportfolio.azurecr.io/api-gateway:v1
docker push acrportfolio.azurecr.io/sync-worker:v1
docker push acrportfolio.azurecr.io/merge-worker:v1
```

**Note**: Training worker already containerized (from `plan-semanticModelTrainingRefactor.prompt.md`)

**Deliverables**:
- ✅ Dockerfiles for API Gateway, Sync Worker, Merge Worker
- ✅ Images pushed to Azure Container Registry
- ✅ Local testing with docker-compose (optional)

---

### Phase 3: Deploy API Gateway (Week 3)

**Objective**: Deploy API Gateway as Kubernetes Deployment with LoadBalancer

**Tasks**:

**3.1 Create Kubernetes Manifests**

```yaml
# k8s/api-gateway/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api-gateway
  namespace: portfolio-api
  labels:
    app: api-gateway
spec:
  replicas: 2  # Start with 2 for HA
  selector:
    matchLabels:
      app: api-gateway
  template:
    metadata:
      labels:
        app: api-gateway
        azure.workload.identity/use: "true"  # Enable Workload Identity
    spec:
      serviceAccountName: portfolio-workload-identity
      containers:
      - name: api-gateway
        image: acrportfolio.azurecr.io/api-gateway:v1
        ports:
        - containerPort: 8000
        env:
        - name: AZURE_STORAGE_QUEUE_URL
          value: "https://stgportfolio.queue.core.windows.net"
        - name: AZURE_STORAGE_BLOB_URL
          value: "https://stgportfolio.blob.core.windows.net"
        - name: AZURE_CLIENT_ID
          value: "YOUR_MANAGED_IDENTITY_CLIENT_ID"
        - name: GROQ_API_KEY
          valueFrom:
            secretKeyRef:
              name: api-secrets
              key: groq-api-key
        resources:
          requests:
            memory: "1Gi"
            cpu: "500m"
          limits:
            memory: "2Gi"
            cpu: "1000m"
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 10
          periodSeconds: 30
        readinessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 10
---
apiVersion: v1
kind: Service
metadata:
  name: api-gateway
  namespace: portfolio-api
spec:
  selector:
    app: api-gateway
  ports:
  - protocol: TCP
    port: 80
    targetPort: 8000
  type: LoadBalancer  # Or ClusterIP if using Ingress
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: api-gateway-hpa
  namespace: portfolio-api
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: api-gateway
  minReplicas: 2
  maxReplicas: 10
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
  - type: Resource
    resource:
      name: memory
      target:
        type: Utilization
        averageUtilization: 80
```

**3.2 Configure Azure Workload Identity**
```bash
# Create service account with federated identity
az identity federated-credential create \
  --name portfolio-workload-identity \
  --identity-name uami-portfolio \
  --resource-group rg-portfolio-prod \
  --issuer https://oidc.prod-westeurope.azure.com \
  --subject system:serviceaccount:portfolio-api:portfolio-workload-identity
```

**3.3 Create Kubernetes Secret for API Keys**
```bash
kubectl create secret generic api-secrets \
  --namespace portfolio-api \
  --from-literal=groq-api-key="YOUR_GROQ_API_KEY" \
  --from-literal=github-token="YOUR_GITHUB_TOKEN"
```

**3.4 Deploy to AKS**
```bash
kubectl apply -f k8s/api-gateway/
```

**3.5 Test API Gateway**
```bash
# Get LoadBalancer IP
GATEWAY_IP=$(kubectl get svc api-gateway -n portfolio-api -o jsonpath='{.status.loadBalancer.ingress[0].ip}')

# Test health endpoint
curl http://$GATEWAY_IP/health

# Test refresh endpoint
curl -X POST http://$GATEWAY_IP/bundles/yungryce/refresh
```

**Deliverables**:
- ✅ API Gateway deployed with 2 replicas
- ✅ HPA configured (scale 2-10 based on CPU/memory)
- ✅ LoadBalancer service with public IP
- ✅ Health checks passing
- ✅ Azure Workload Identity configured

---

### Phase 4: Deploy Worker Services (Week 4-5)

**Objective**: Deploy Sync Worker, Merge Worker, Training Worker as Deployments/Jobs

**Tasks**:

**4.1 Sync Worker Deployment with KEDA Autoscaling**

```yaml
# k8s/workers/sync-worker-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: sync-worker
  namespace: portfolio-workers
spec:
  replicas: 1  # KEDA will override this
  selector:
    matchLabels:
      app: sync-worker
  template:
    metadata:
      labels:
        app: sync-worker
        azure.workload.identity/use: "true"
    spec:
      serviceAccountName: portfolio-workload-identity
      containers:
      - name: sync-worker
        image: acrportfolio.azurecr.io/sync-worker:v1
        env:
        - name: AZURE_STORAGE_CONNECTION_STRING
          valueFrom:
            secretKeyRef:
              name: storage-secret
              key: connection-string
        - name: GITHUB_TOKEN
          valueFrom:
            secretKeyRef:
              name: api-secrets
              key: github-token
        resources:
          requests:
            memory: "2Gi"
            cpu: "1000m"
          limits:
            memory: "4Gi"
            cpu: "2000m"
---
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: sync-worker-scaler
  namespace: portfolio-workers
spec:
  scaleTargetRef:
    name: sync-worker
  minReplicaCount: 0  # Scale to zero when queue empty
  maxReplicaCount: 10
  triggers:
  - type: azure-queue
    metadata:
      queueName: github-sync
      queueLength: "5"  # Scale up when >5 messages
      connectionFromEnv: AZURE_STORAGE_CONNECTION_STRING
```

**4.2 Merge Worker Deployment**
```yaml
# k8s/workers/merge-worker-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: merge-worker
  namespace: portfolio-workers
spec:
  replicas: 1
  selector:
    matchLabels:
      app: merge-worker
  template:
    metadata:
      labels:
        app: merge-worker
    spec:
      containers:
      - name: merge-worker
        image: acrportfolio.azurecr.io/merge-worker:v1
        env:
        - name: AZURE_STORAGE_CONNECTION_STRING
          valueFrom:
            secretKeyRef:
              name: storage-secret
              key: connection-string
        resources:
          requests:
            memory: "1Gi"
            cpu: "500m"
---
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: merge-worker-scaler
  namespace: portfolio-workers
spec:
  scaleTargetRef:
    name: merge-worker
  minReplicaCount: 0
  maxReplicaCount: 5
  triggers:
  - type: azure-queue
    metadata:
      queueName: merge-results
      queueLength: "3"
      connectionFromEnv: AZURE_STORAGE_CONNECTION_STRING
```

**4.3 Training Worker Kubernetes Job**

```yaml
# k8s/workers/training-worker-job.yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: training-worker-{{ .Values.timestamp }}
  namespace: portfolio-workers
spec:
  ttlSecondsAfterFinished: 3600  # Auto-cleanup after 1 hour
  backoffLimit: 2  # Retry 2 times on failure
  template:
    metadata:
      labels:
        app: training-worker
    spec:
      restartPolicy: Never
      
      # OPTIONAL: Use spot node pool for cost savings
      tolerations:
      - key: "kubernetes.azure.com/scalesetpriority"
        operator: "Equal"
        value: "spot"
        effect: "NoSchedule"
      
      nodeSelector:
        workload: spot  # Target spot node pool
      
      containers:
      - name: trainer
        image: acrportfolio.azurecr.io/portfolio-trainer:cpu
        env:
        - name: AZURE_STORAGE_CONNECTION_STRING
          valueFrom:
            secretKeyRef:
              name: storage-secret
              key: connection-string
        - name: TRAINING_MODE
          value: "kubernetes"
        resources:
          requests:
            memory: "16Gi"
            cpu: "4"
          limits:
            memory: "16Gi"
            cpu: "4"
```

**4.4 Deploy Workers**
```bash
# Create storage secret
kubectl create secret generic storage-secret \
  --namespace portfolio-workers \
  --from-literal=connection-string="YOUR_STORAGE_CONNECTION_STRING"

# Deploy workers
kubectl apply -f k8s/workers/sync-worker-deployment.yaml
kubectl apply -f k8s/workers/merge-worker-deployment.yaml

# Trigger training job manually (or via CronJob)
kubectl apply -f k8s/workers/training-worker-job.yaml
```

**Deliverables**:
- ✅ Sync Worker deployed with KEDA autoscaling (0-10 replicas)
- ✅ Merge Worker deployed with KEDA autoscaling (0-5 replicas)
- ✅ Training Worker as Kubernetes Job (runs on spot nodes)
- ✅ All workers scale to zero when queues empty
- ✅ KEDA triggers based on Azure Storage Queue depth

---

### Phase 5: Configure Ingress & DNS (Week 6)

**Objective**: Expose API Gateway via Ingress with SSL/TLS

**Tasks**:

**5.1 Install cert-manager for SSL Certificates**
```bash
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.13.0/cert-manager.yaml
```

**5.2 Create ClusterIssuer for Let's Encrypt**
```yaml
# k8s/cert-manager/cluster-issuer.yaml
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    email: your-email@example.com
    privateKeySecretRef:
      name: letsencrypt-prod
    solvers:
    - http01:
        ingress:
          class: nginx
```

**5.3 Create Ingress Resource**
```yaml
# k8s/api-gateway/ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: api-gateway-ingress
  namespace: portfolio-api
  annotations:
    cert-manager.io/cluster-issuer: "letsencrypt-prod"
    nginx.ingress.kubernetes.io/rewrite-target: /
spec:
  ingressClassName: nginx
  tls:
  - hosts:
    - api.portfolio.yungryce.dev
    secretName: api-tls-secret
  rules:
  - host: api.portfolio.yungryce.dev
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: api-gateway
            port:
              number: 80
```

**5.4 Configure DNS**
```bash
# Get NGINX Ingress LoadBalancer IP
INGRESS_IP=$(kubectl get svc nginx-ingress-ingress-nginx-controller \
  -n ingress-nginx -o jsonpath='{.status.loadBalancer.ingress[0].ip}')

# Add DNS A record: api.portfolio.yungryce.dev → $INGRESS_IP
# (Manual step in DNS provider: Azure DNS, Cloudflare, etc.)
```

**5.5 Update Static Web App Backend Link**
```bash
# Update SWA to point to AKS API Gateway
az staticwebapp backends update \
  --name portfolio-frontend \
  --resource-group rg-portfolio-prod \
  --backend-resource-id "" \
  --backend-environment "Production"

# Update frontend environment variable
# src/environments/environment.ts
# apiUrl: 'https://api.portfolio.yungryce.dev'
```

**Deliverables**:
- ✅ NGINX Ingress Controller configured
- ✅ SSL/TLS certificate from Let's Encrypt
- ✅ DNS A record pointing to Ingress IP
- ✅ API accessible at https://api.portfolio.yungryce.dev
- ✅ Static Web App updated to use AKS backend

---

### Phase 6: Monitoring & Observability (Week 7)

**Objective**: Set up Prometheus, Grafana, and Azure Monitor integration

**Tasks**:

**6.1 Install Prometheus & Grafana**
```bash
# Add Prometheus Helm repo
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts

# Install kube-prometheus-stack (Prometheus + Grafana + Alertmanager)
helm install monitoring prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false \
  --set grafana.adminPassword='YOUR_ADMIN_PASSWORD'
```

**6.2 Create ServiceMonitor for API Gateway**
```yaml
# k8s/monitoring/api-gateway-servicemonitor.yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: api-gateway-metrics
  namespace: portfolio-api
spec:
  selector:
    matchLabels:
      app: api-gateway
  endpoints:
  - port: http
    interval: 30s
    path: /metrics  # Expose metrics endpoint in FastAPI
```

**6.3 Configure Azure Monitor Container Insights**
```bash
# Enable Container Insights on AKS
az aks enable-addons \
  --resource-group rg-cloudfolio \
  --name cloudfolio-cluster \
  --addons monitoring \
  --workspace-resource-id /subscriptions/.../resourceGroups/rg-portfolio-prod/providers/Microsoft.OperationalInsights/workspaces/law-portfolio
```

**6.4 Create Grafana Dashboards**
- Import Kubernetes cluster dashboard (ID: 7249)
- Import NGINX Ingress dashboard (ID: 9614)
- Create custom dashboard for queue depth metrics

**6.5 Set Up Alerts**
```yaml
# k8s/monitoring/alerts.yaml
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: portfolio-alerts
  namespace: monitoring
spec:
  groups:
  - name: portfolio
    interval: 30s
    rules:
    - alert: HighQueueDepth
      expr: azure_storage_queue_messages > 100
      for: 5m
      annotations:
        summary: "Queue depth > 100 for 5 minutes"
    
    - alert: PodCrashLooping
      expr: rate(kube_pod_container_status_restarts_total[5m]) > 0.1
      annotations:
        summary: "Pod {{ $labels.pod }} is crash looping"
```

**Deliverables**:
- ✅ Prometheus collecting metrics from all pods
- ✅ Grafana dashboards for cluster, ingress, and custom metrics
- ✅ Azure Monitor Container Insights enabled
- ✅ Alerts configured for queue depth, pod crashes, high CPU
- ✅ Slack/email notifications for critical alerts

---

### Phase 7: Gradual Traffic Migration (Week 8)

**Objective**: Migrate traffic from Function Apps to AKS with rollback capability

**Tasks**:

**7.1 Parallel Deployment (Week 1)**
- Keep Function Apps running (no changes)
- Route 10% of traffic to AKS API Gateway via DNS weighted routing
- Monitor metrics: latency, error rate, queue depth

**7.2 Increase Traffic Gradually**
```
Day 1-2: 10% → AKS, 90% → Function Apps
Day 3-4: 25% → AKS, 75% → Function Apps
Day 5-6: 50% → AKS, 50% → Function Apps
Day 7-8: 75% → AKS, 25% → Function Apps
Day 9-10: 100% → AKS, 0% → Function Apps
```

**7.3 Validation Checklist**
- ✅ API response time <5s (p95)
- ✅ Error rate <0.1%
- ✅ Queue depth stable (<100 messages)
- ✅ Workers scale to 0 when idle
- ✅ Training jobs complete successfully on spot nodes
- ✅ No increase in cost (spot instances offsetting regular nodes)

**7.4 Decommission Function Apps (After 7 Days Stable)**
```bash
# Stop Function Apps (keep for 7 days as rollback window)
az functionapp stop --name fa-portfolio-prod --resource-group rg-portfolio-prod

# After 7 days with no issues, delete
az functionapp delete --name fa-portfolio-prod --resource-group rg-portfolio-prod
az appserviceplan delete --name fp-portfolio-prod --resource-group rg-portfolio-prod
```

**Deliverables**:
- ✅ 100% traffic on AKS API Gateway
- ✅ Function Apps decommissioned
- ✅ Rollback playbook tested (can revert in <15 min)
- ✅ Cost analysis completed (AKS vs Function Apps)

---

## Cost Analysis: AKS vs. Function Apps

### Function Apps (PRIMARY Deployment)

| Component | Configuration | Monthly Cost |
|-----------|---------------|--------------|
| API Gateway | App service Function App, 2 avg instances | $15 |
| Sync Worker | App service Function App, 3 avg instances | $20 |
| Merge Worker | App service Function App, 1 avg instance | $8 |
| Training Worker | ACI (10 runs × $0.40/run) | $4 |
| **Total Compute** | | **$47/month** |

### AKS (FUTURE Deployment)

| Component | Configuration | Monthly Cost |
|-----------|---------------|--------------|
| Regular Node Pool | 2× Standard_D2s_v5 (2 vCPU, 8GB) | $144 |
| Spot Node Pool (OPTIONAL) | 1× NC4as_T4_v3 GPU (70% discount) | $108 |
| Load Balancer | Standard SKU | $18 |
| **Total Compute** | | **$270/month** |

### Cost Comparison

| Metric | Function Apps | AKS | Difference |
|--------|---------------|-----|------------|
| **Monthly Cost (No GPU)** | $47 | $162 | **+$115/month** |
| **Monthly Cost (With GPU)** | $47 | $270 | **+$223/month** |
| **Cost per Request** | $0.0004 | $0 (included) | ⚖️ Depends |
| **Idle Cost** | $0 | $144 | **+$144/month** |

**VERDICT**: **Function Apps are $115-$223/month cheaper**

**Justification for AKS Migration**:
- ❌ NOT for cost savings (Function Apps are cheaper)
- ✅ For advanced K8s features (network policies, custom autoscaling, GPU)
- ✅ For deep learning in AKS (career growth, Kubernetes expertise)
- ✅ For future portability (GKE, EKS migration path)

---

## Risk Assessment

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| **Higher operational complexity** | High | Medium | Invest in team training, create runbooks, automate deployments |
| **Node failures** | Medium | High | Multi-node pools, pod disruption budgets, readiness/liveness probes |
| **Spot instance evictions** | High | Low | Use spot only for training worker, graceful shutdown handlers |
| **Cost overruns** | Medium | High | Set Azure cost alerts, monitor node utilization, use cluster autoscaler |
| **Security vulnerabilities** | Low | High | Network policies, pod security standards, regular image scanning |
| **Rollback complexity** | Medium | High | Maintain Function Apps for 7 days, test rollback procedure |

**Overall Risk**: **MEDIUM-HIGH** (operational complexity increases significantly)

**Recommendation**: Only migrate if you have dedicated DevOps/SRE support and justify advanced K8s features.

---

## Success Metrics

### Performance Targets

| Metric | Function Apps (Baseline) | AKS Target | Measurement |
|--------|--------------------------|-----------|-------------|
| **API Response Time (p95)** | <5s | <3s (pod-to-pod) | Prometheus metrics |
| **Cold Start** | 2-5s | 0s (always running) | HTTP request logs |
| **Worker Scale-to-Zero** | <60s | <30s (KEDA) | KEDA metrics |
| **Queue Processing** | 5-10 msgs/s | 10-20 msgs/s | Azure Storage metrics |

### Cost Targets

| Metric | Target | Measurement |
|--------|--------|-------------|
| **Monthly Infrastructure Cost** | <$300/month | Azure Cost Management |
| **Cost per 1000 Requests** | <$0.50 | Cost / Request Count |
| **Spot Instance Usage** | >50% for training | Node utilization metrics |

### Operational Targets

| Metric | Target | Measurement |
|--------|--------|-------------|
| **Deployment Frequency** | >1 per week | CI/CD pipeline metrics |
| **MTTR (Mean Time to Recovery)** | <30 minutes | Incident logs |
| **Pod Restart Rate** | <5% per day | Kubernetes events |
| **Cluster Availability** | >99.9% | Azure Monitor uptime |

---

## Rollback Plan (Emergency Procedures)

### Trigger Conditions

- API error rate >1% for 10 consecutive minutes
- Pod crash loop rate >10% per hour
- Queue depth >500 messages for 15 minutes
- Cost exceeds $400/month
- Node failures impact availability

### Rollback Steps (Target: <15 minutes)

**1. Switch DNS Back to Function Apps**
```bash
# Update DNS A record: api.portfolio.yungryce.dev → Function App URL
# (Manual step in DNS provider)

# OR: Update Static Web App backend link
az staticwebapp backends update \
  --name portfolio-frontend \
  --resource-group rg-portfolio-prod \
  --backend-resource-id /subscriptions/.../providers/Microsoft.Web/sites/fa-portfolio-prod
```

**2. Start Function Apps (If Stopped)**
```bash
az functionapp start --name fa-portfolio-prod --resource-group rg-portfolio-prod
```

**3. Validate Function App Health**
```bash
# Test endpoints
curl https://fa-portfolio-prod.azurewebsites.net/api/health
curl -X POST https://fa-portfolio-prod.azurewebsites.net/api/bundles/yungryce/refresh
```

**4. Scale Down AKS (Optional)**
```bash
# Scale API Gateway to 0 replicas (stop processing traffic)
kubectl scale deployment api-gateway --replicas=0 -n portfolio-api

# OR: Drain and delete workers
kubectl delete deployment sync-worker merge-worker -n portfolio-workers
```

**5. Post-Rollback Analysis**
- Export AKS logs to Azure Monitor
- Analyze pod restarts, resource usage, queue depth
- Conduct blameless postmortem
- Document learnings and update runbooks

---

## Timeline Summary

| Phase | Duration | Key Deliverable | Blocking Dependencies |
|-------|----------|-----------------|----------------------|
| **Phase 1: Infrastructure** | 1 week | AKS cluster provisioned | Terraform approved |
| **Phase 2: Containerization** | 1 week | Docker images in ACR | Phase 1 complete |
| **Phase 3: API Gateway** | 1 week | API Gateway deployed | Phase 2 complete |
| **Phase 4: Workers** | 2 weeks | All workers deployed with KEDA | Phase 3 complete |
| **Phase 5: Ingress & DNS** | 1 week | SSL/TLS configured | Phase 4 complete |
| **Phase 6: Monitoring** | 1 week | Prometheus + Grafana | Phase 5 complete |
| **Phase 7: Traffic Migration** | 1 week | 100% traffic on AKS | Phase 6 complete |
| **Total** | **8 weeks** | Full AKS deployment | Function Apps stable 3+ months |

**Critical Path**: Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 7

---

## Next Steps (When Ready)

**DO NOT START** until Function Apps are stable for 3+ months.

**When ready to proceed**:

1. **Team Training**: Complete AKS Fundamentals course (Microsoft Learn)
2. **Business Case Approval**: Present cost comparison ($115-$223/month increase)
3. **Terraform Review**: Validate AKS cluster configuration in `infra/terraform/main.tf`
4. **Create Migration Branch**: `feature/aks-migration`
5. **Phase 1 Kickoff**: Provision AKS cluster and install add-ons

**Estimated Start Date**: After Function Apps stable for 3+ months (TBD)  
**Target Completion**: 8 weeks from start date

---

## Appendix: Related Plans

### Plan Dependencies

This plan builds on the following existing plans:

1. **plan-multiAppArchitecture.prompt.md**
   - PRIMARY deployment: Azure Function Apps 
   - THIS PLAN provides alternative AKS deployment

2. **plan-azureStorageQueuesArchitecture.prompt.md**
   - Queue infrastructure (github-sync, merge-results, model-training)
   - Same queues used by both Function Apps and AKS

3. **plan-semanticModelTrainingRefactor.prompt.md**
   - Training worker containerization (ALREADY IMPLEMENTED)
   - Uses same Docker image for ACI and AKS deployment

### Key Differences from Function Apps

| Aspect | Function Apps | AKS (This Plan) |
|--------|---------------|-----------------|
| **Deployment Model** | Serverless | Container orchestration |
| **Scaling** | HTTP trigger autoscaling | KEDA + HPA (20+ scalers) |
| **Networking** | VNet integration | Pod-to-pod, network policies |
| **Cost** | Pay per execution | Pay for nodes (idle cost) |
| **Complexity** | Low (managed service) | High (self-managed K8s) |
| **Portability** | Azure-locked | Multi-cloud (GKE, EKS) |

---

**Document Status**: Draft - Awaiting Function Apps stability milestone  
**Last Updated**: November 20, 2025
