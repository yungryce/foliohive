# Kubernetes Hands-On Learning Guide: AKS Deployment

> **Goal**: Learn Kubernetes fundamentals by deploying a basic orchestration with a web server and monitoring sidecar on AKS.

## 🎯 Learning Objectives

You'll touch on these fundamental Kubernetes concepts:
- **Pods** - Smallest deployable units
- **Deployments** - Managing replicas and updates
- **Services** - Networking & service discovery
- **ConfigMaps & Secrets** - Configuration management
- **Sidecars** - Multi-container pod patterns
- **Resource management** - CPU/memory limits
- **Health checks** - Liveness/readiness probes

## 📋 Prerequisites & Tools

### Tools You'll Need:

1. **Azure CLI** - Manage AKS cluster
2. **kubectl** - Kubernetes command-line tool
3. **k9s** (optional but recommended) - Terminal UI for cluster management
4. **Docker** - Understanding container images

### Install Commands:

```bash
# Azure CLI (if not installed)
curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash

# kubectl
az aks install-cli

# k9s (optional but great for learning)
curl -sS https://webinstall.dev/k9s | bash
```

## 🏗️ Project Structure

```
aks/
├── infra/
│   └── cluster-setup.sh          # AKS cluster creation script
├── manifests/
│   ├── namespace.yaml            # Logical separation
│   ├── server/
│   │   ├── deployment.yaml       # Main application
│   │   ├── service.yaml          # Expose server
│   │   └── configmap.yaml        # Configuration
│   └── monitoring/
│       └── sidecar-deployment.yaml  # Server + sidecar pattern
├── LEARNING-GUIDE.md             # This file
└── README.md                     # Project overview
```

---

## 🚀 Step-by-Step Learning Path

### **Phase 1: Cluster Setup (15 min)**

#### Create AKS Cluster Script

Create `infra/cluster-setup.sh`:

```bash
#!/bin/bash
set -e

RESOURCE_GROUP="rg-aks-learning"
CLUSTER_NAME="aks-learning-cluster"
LOCATION="westeurope"
NODE_COUNT=2

echo "Creating resource group..."
az group create \
  --name $RESOURCE_GROUP \
  --location $LOCATION

echo "Creating AKS cluster (this takes 5-10 minutes)..."
az aks create \
  --resource-group $RESOURCE_GROUP \
  --name $CLUSTER_NAME \
  --node-count $NODE_COUNT \
  --node-vm-size Standard_B2s \
  --enable-managed-identity \
  --generate-ssh-keys \
  --network-plugin azure \
  --network-policy calico

echo "Getting cluster credentials..."
az aks get-credentials \
  --resource-group $RESOURCE_GROUP \
  --name $CLUSTER_NAME

echo "Verifying connection..."
kubectl cluster-info
kubectl get nodes

echo "✅ AKS cluster is ready!"
```

#### Run Setup:

```bash
chmod +x infra/cluster-setup.sh
./infra/cluster-setup.sh
```

#### Key Concepts:
- **Resource Group**: Logical container for Azure resources
- **Node Count**: Worker nodes that run your containers
- **Managed Identity**: Azure handles authentication
- **Network Plugin**: How pods get IP addresses

---

### **Phase 2: Create Namespace (5 min)**

#### Create `manifests/namespace.yaml`:

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: learning-app
  labels:
    environment: learning
    purpose: hands-on
```

#### Apply:

```bash
kubectl apply -f manifests/namespace.yaml
kubectl get namespaces
```

#### Key Concepts:
- **Namespace**: Logical separation within cluster
- **Labels**: Key-value pairs for organization
- Best practice: separate workloads by namespace

---

### **Phase 3: Deploy Simple Server (20 min)**

#### Create `manifests/server/configmap.yaml`:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: nginx-config
  namespace: learning-app
data:
  default.conf: |
    server {
        listen 80;
        location / {
            root /usr/share/nginx/html;
            index index.html;
        }
        location /health {
            access_log off;
            return 200 "healthy\n";
            add_header Content-Type text/plain;
        }
    }
```

#### Create `manifests/server/deployment.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web-server
  namespace: learning-app
  labels:
    app: web-server
spec:
  replicas: 2  # Creates 2 pod instances
  selector:
    matchLabels:
      app: web-server
  template:
    metadata:
      labels:
        app: web-server
    spec:
      containers:
      - name: nginx
        image: nginx:1.25-alpine
        ports:
        - containerPort: 80
          name: http
        resources:
          requests:
            cpu: 100m        # Minimum CPU needed
            memory: 128Mi    # Minimum memory needed
          limits:
            cpu: 200m        # Maximum CPU allowed
            memory: 256Mi    # Maximum memory allowed
        livenessProbe:
          httpGet:
            path: /health
            port: 80
          initialDelaySeconds: 10
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /health
            port: 80
          initialDelaySeconds: 5
          periodSeconds: 5
        volumeMounts:
        - name: config
          mountPath: /etc/nginx/conf.d
      volumes:
      - name: config
        configMap:
          name: nginx-config
```

#### Create `manifests/server/service.yaml`:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: web-server
  namespace: learning-app
spec:
  type: LoadBalancer  # Exposes externally via Azure Load Balancer
  selector:
    app: web-server
  ports:
  - protocol: TCP
    port: 80
    targetPort: 80
    name: http
```

#### Apply:

```bash
kubectl apply -f manifests/server/
kubectl get all -n learning-app
```

#### Key Concepts:
- **Deployment**: Manages pod lifecycle and scaling
- **Replicas**: Number of pod copies running
- **Resources**: CPU/memory requests and limits
- **Probes**: Health checks (liveness = restart if fails, readiness = traffic routing)
- **Service**: Network endpoint to access pods
- **LoadBalancer**: Creates Azure LB with public IP

---

### **Phase 4: Add Monitoring Sidecar (30 min)**

#### Create `manifests/monitoring/sidecar-deployment.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web-server-monitored
  namespace: learning-app
  labels:
    app: web-server-monitored
spec:
  replicas: 1
  selector:
    matchLabels:
      app: web-server-monitored
  template:
    metadata:
      labels:
        app: web-server-monitored
    spec:
      containers:
      # Main application container
      - name: nginx
        image: nginx:1.25-alpine
        ports:
        - containerPort: 80
          name: http
        volumeMounts:
        - name: shared-logs
          mountPath: /var/log/nginx
        resources:
          requests:
            cpu: 100m
            memory: 128Mi
          limits:
            cpu: 200m
            memory: 256Mi
        livenessProbe:
          httpGet:
            path: /
            port: 80
          initialDelaySeconds: 10
          periodSeconds: 10
      
      # Sidecar monitoring container
      - name: log-monitor
        image: busybox:1.36
        command: 
        - /bin/sh
        - -c
        - |
          echo "Starting log monitor sidecar..."
          while true; do
            if [ -f /var/log/nginx/access.log ]; then
              echo "=== Access Log Stats at $(date) ==="
              tail -n 5 /var/log/nginx/access.log
              echo "Request count: $(wc -l < /var/log/nginx/access.log)"
            fi
            sleep 30
          done
        volumeMounts:
        - name: shared-logs
          mountPath: /var/log/nginx
        resources:
          requests:
            cpu: 50m
            memory: 64Mi
          limits:
            cpu: 100m
            memory: 128Mi
      
      volumes:
      - name: shared-logs
        emptyDir: {}  # Temporary volume shared between containers
```

#### Apply:

```bash
kubectl apply -f manifests/monitoring/
kubectl get pods -n learning-app
```

#### Key Concepts:
- **Sidecar Pattern**: Helper container in same pod
- **Shared Volumes**: `emptyDir` for inter-container communication
- **Multi-container Pods**: Containers share network and storage
- Use cases: logging, monitoring, proxies, adapters

---

## 🎓 Learning Exercises

### **Exercise 1: Deploy & Explore**

```bash
# Apply all manifests
kubectl apply -f manifests/

# Watch resources being created
kubectl get all -n learning-app -w

# Check pod details
kubectl describe pod -n learning-app -l app=web-server

# View logs from all replicas
kubectl logs -n learning-app -l app=web-server

# View logs from specific pod
kubectl logs -n learning-app <pod-name>
```

### **Exercise 2: Test Your Server**

```bash
# Get external IP (may take a few minutes)
kubectl get svc -n learning-app web-server -w

# Test the endpoint (replace with actual EXTERNAL-IP)
curl http://<EXTERNAL-IP>

# Test health endpoint
curl http://<EXTERNAL-IP>/health

# Generate some traffic
for i in {1..20}; do curl -s http://<EXTERNAL-IP> > /dev/null; echo "Request $i sent"; done
```

### **Exercise 3: Explore Sidecar Pattern**

```bash
# View both containers in the pod
kubectl get pod -n learning-app -l app=web-server-monitored

# Check main container logs
kubectl logs -n learning-app -l app=web-server-monitored -c nginx

# Check sidecar logs (follow mode)
kubectl logs -n learning-app -l app=web-server-monitored -c log-monitor -f

# Exec into main container
kubectl exec -n learning-app <pod-name> -c nginx -it -- /bin/sh

# Exec into sidecar container
kubectl exec -n learning-app <pod-name> -c log-monitor -it -- /bin/sh

# Inside the sidecar, explore shared volume
ls -la /var/log/nginx/
cat /var/log/nginx/access.log
```

### **Exercise 4: Scale & Watch**

```bash
# Scale up to 4 replicas
kubectl scale deployment/web-server -n learning-app --replicas=4

# Watch pods being created
kubectl get pods -n learning-app -w

# Check which nodes they're running on
kubectl get pods -n learning-app -o wide

# Scale down to 1 replica
kubectl scale deployment/web-server -n learning-app --replicas=1

# Watch pods being terminated
kubectl get pods -n learning-app -w
```

### **Exercise 5: Update Configuration**

```bash
# Edit the ConfigMap
kubectl edit configmap nginx-config -n learning-app

# Rollout restart to pick up changes
kubectl rollout restart deployment/web-server -n learning-app

# Watch the rolling update
kubectl rollout status deployment/web-server -n learning-app
```

### **Exercise 6: Simulate Failures**

```bash
# Delete a pod (watch it get recreated)
kubectl delete pod -n learning-app -l app=web-server --field-selector=status.phase==Running | head -1

# Watch deployment maintain desired state
kubectl get pods -n learning-app -w

# Drain a node (move pods to other nodes)
kubectl get nodes
kubectl drain <node-name> --ignore-daemonsets --delete-emptydir-data

# Uncordon the node
kubectl uncordon <node-name>
```

---

## 📚 Key Concepts Explained

### **1. Pods vs Deployments**
- **Pod**: Smallest unit, one or more containers
- **Deployment**: Manages pods, handles updates, scaling, self-healing
- Pods are ephemeral; Deployments ensure desired state

### **2. Services & Networking**
- **ClusterIP**: Internal only (default)
- **NodePort**: Exposes on each node's IP
- **LoadBalancer**: External load balancer (Azure LB in AKS)
- Services use labels to find pods

### **3. Labels & Selectors**
- **Labels**: Key-value pairs on resources
- **Selectors**: Query resources by labels
- How K8s connects Services → Pods, Deployments → Pods

### **4. Resource Management**
- **Requests**: Minimum guaranteed resources
- **Limits**: Maximum allowed resources
- **m** = millicores (1000m = 1 CPU core)
- **Mi** = Mebibytes (1024 bytes)

### **5. Health Checks**
- **Liveness Probe**: Is container healthy? (restart if fails)
- **Readiness Probe**: Can container accept traffic? (remove from service if fails)
- **Startup Probe**: Has container started? (for slow-starting apps)

### **6. Multi-container Pods**
- **Sidecar**: Helper container (logging, monitoring)
- **Ambassador**: Proxy to external services
- **Adapter**: Standardizes output
- All containers share: network namespace, volumes, lifecycle

### **7. Volumes**
- **emptyDir**: Temporary, exists with pod lifecycle
- **ConfigMap/Secret**: Inject configuration
- **PersistentVolume**: Survives pod restarts

---

## 🔍 Useful Commands Reference

### **Viewing Resources**

```bash
# All resources in namespace
kubectl get all -n learning-app

# Specific resource types
kubectl get pods -n learning-app
kubectl get deployments -n learning-app
kubectl get services -n learning-app
kubectl get configmaps -n learning-app

# With more details
kubectl get pods -n learning-app -o wide
kubectl get pods -n learning-app -o yaml

# Describe for detailed info
kubectl describe pod <pod-name> -n learning-app
kubectl describe deployment web-server -n learning-app
```

### **Logs & Debugging**

```bash
# View logs
kubectl logs <pod-name> -n learning-app
kubectl logs <pod-name> -c <container-name> -n learning-app
kubectl logs -f <pod-name> -n learning-app  # Follow mode
kubectl logs --tail=50 <pod-name> -n learning-app  # Last 50 lines

# Previous container logs (if crashed)
kubectl logs <pod-name> -n learning-app --previous

# Interactive terminal
kubectl exec -it <pod-name> -n learning-app -- /bin/sh
kubectl exec -it <pod-name> -c <container-name> -n learning-app -- /bin/sh
```

### **Port Forwarding**

```bash
# Access service locally without LoadBalancer
kubectl port-forward -n learning-app svc/web-server 8080:80

# Access pod directly
kubectl port-forward -n learning-app <pod-name> 8080:80

# Then access via: http://localhost:8080
```

### **Events & Troubleshooting**

```bash
# View events (sorted by time)
kubectl get events -n learning-app --sort-by='.lastTimestamp'

# View events for specific resource
kubectl describe pod <pod-name> -n learning-app | grep -A 10 Events

# Check node status
kubectl get nodes
kubectl describe node <node-name>
```

### **Resource Usage**

```bash
# Node resource usage (requires metrics-server)
kubectl top nodes

# Pod resource usage
kubectl top pods -n learning-app

# Sort by CPU or memory
kubectl top pods -n learning-app --sort-by=cpu
kubectl top pods -n learning-app --sort-by=memory
```

### **Editing & Updating**

```bash
# Edit resource directly
kubectl edit deployment web-server -n learning-app

# Apply changes from file
kubectl apply -f manifests/server/deployment.yaml

# Rollout management
kubectl rollout status deployment/web-server -n learning-app
kubectl rollout history deployment/web-server -n learning-app
kubectl rollout undo deployment/web-server -n learning-app

# Scale deployment
kubectl scale deployment/web-server -n learning-app --replicas=3
```

### **Context & Namespace**

```bash
# View current context
kubectl config current-context

# List all contexts
kubectl config get-contexts

# Switch context
kubectl config use-context <context-name>

# Set default namespace
kubectl config set-context --current --namespace=learning-app

# Now you can omit -n flag
kubectl get pods
```

---

## 🧹 Cleanup

### **Delete Application Resources**

```bash
# Delete everything in namespace
kubectl delete namespace learning-app

# Or delete specific resources
kubectl delete -f manifests/
```

### **Delete AKS Cluster**

```bash
# Delete cluster (saves costs)
az aks delete \
  --resource-group rg-aks-learning \
  --name aks-learning-cluster \
  --yes \
  --no-wait

# Delete resource group (removes everything)
az group delete \
  --name rg-aks-learning \
  --yes \
  --no-wait

# Check deletion status
az group list --output table
```

---

## 📖 Next Steps & Advanced Topics

Once comfortable with basics, explore:

### **1. Ingress Controllers**
Route HTTP traffic to multiple services:
```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: my-ingress
spec:
  rules:
  - host: myapp.example.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: web-server
            port:
              number: 80
```

### **2. Horizontal Pod Autoscaler (HPA)**
Auto-scale based on CPU/memory:
```bash
kubectl autoscale deployment web-server \
  --cpu-percent=50 \
  --min=2 \
  --max=10 \
  -n learning-app
```

### **3. PersistentVolumes**
For stateful applications:
```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: my-pvc
spec:
  accessModes:
  - ReadWriteOnce
  resources:
    requests:
      storage: 10Gi
  storageClassName: managed-premium
```

### **4. StatefulSets**
For databases and stateful apps:
- Stable network identities
- Ordered deployment and scaling
- Persistent storage per pod

### **5. Helm Charts**
Package manager for Kubernetes:
```bash
helm repo add bitnami https://charts.bitnami.com/bitnami
helm install my-redis bitnami/redis
```

### **6. Monitoring & Logging**
- **Prometheus + Grafana**: Metrics and dashboards
- **ELK Stack**: Centralized logging
- **Azure Monitor**: AKS-native monitoring

### **7. Security**
- **RBAC**: Role-based access control
- **Network Policies**: Pod-to-pod firewall rules
- **Pod Security Standards**: Security contexts
- **Secrets Management**: Azure Key Vault integration

### **8. CI/CD Integration**
- **GitHub Actions**: Build and deploy pipelines
- **Azure DevOps**: Complete ALM solution
- **ArgoCD**: GitOps continuous delivery

---

## 🎯 Learning Checklist

Track your progress:

- [ ] Created AKS cluster successfully
- [ ] Understood kubectl basics
- [ ] Created and applied namespace
- [ ] Deployed application with Deployment
- [ ] Exposed application with Service
- [ ] Used ConfigMap for configuration
- [ ] Implemented health checks (liveness/readiness)
- [ ] Set resource requests and limits
- [ ] Deployed multi-container pod with sidecar
- [ ] Scaled deployment up and down
- [ ] Viewed logs from containers
- [ ] Executed commands inside containers
- [ ] Used port-forward for local access
- [ ] Understood pod lifecycle and self-healing
- [ ] Performed rolling updates
- [ ] Troubleshot with kubectl describe and events
- [ ] Cleaned up resources properly

---

## 📚 Additional Resources

### **Official Documentation**
- [Kubernetes Docs](https://kubernetes.io/docs/)
- [AKS Documentation](https://docs.microsoft.com/azure/aks/)
- [kubectl Cheat Sheet](https://kubernetes.io/docs/reference/kubectl/cheatsheet/)

### **Learning Platforms**
- [Kubernetes By Example](https://kubernetesbyexample.com/)
- [Play with Kubernetes](https://labs.play-with-k8s.com/)
- [Microsoft Learn - AKS](https://docs.microsoft.com/learn/paths/intro-to-kubernetes-on-azure/)

### **Books**
- "Kubernetes Up & Running" by Kelsey Hightower
- "The Kubernetes Book" by Nigel Poulton
- "Kubernetes Patterns" by Bilgin Ibryam

### **Community**
- [Kubernetes Slack](https://slack.k8s.io/)
- [r/kubernetes](https://reddit.com/r/kubernetes)
- [CNCF Webinars](https://www.cncf.io/webinars/)

---

## 🐛 Common Issues & Solutions

### **Issue: Pods stuck in Pending**
```bash
kubectl describe pod <pod-name> -n learning-app
# Look for: insufficient CPU/memory, node selector issues
```

### **Issue: Service has no EXTERNAL-IP**
```bash
# Check service type
kubectl get svc -n learning-app web-server -o yaml

# Verify LoadBalancer provisioning
kubectl describe svc web-server -n learning-app
```

### **Issue: Can't connect to cluster**
```bash
# Re-fetch credentials
az aks get-credentials \
  --resource-group rg-aks-learning \
  --name aks-learning-cluster \
  --overwrite-existing

# Check context
kubectl config current-context
```

### **Issue: Pods keep restarting**
```bash
# Check logs
kubectl logs <pod-name> -n learning-app --previous

# Check events
kubectl get events -n learning-app --sort-by='.lastTimestamp'

# Describe pod for health check failures
kubectl describe pod <pod-name> -n learning-app
```

---

## 💡 Tips for Success

1. **Start small**: Master one concept before moving to the next
2. **Use describe**: `kubectl describe` is your best friend for debugging
3. **Read events**: Events tell you what's happening behind the scenes
4. **Label everything**: Good labels make management easier
5. **Use namespaces**: Separate workloads logically
6. **Set resource limits**: Prevent one app from consuming all resources
7. **Version your manifests**: Use Git to track changes
8. **Document as you go**: Take notes on what works and why
9. **Experiment**: Break things in a learning environment
10. **Clean up**: Delete resources to avoid unnecessary costs

---

**Happy Learning! 🚀**

*Remember: The best way to learn Kubernetes is to get your hands dirty. Don't be afraid to break things—that's how you learn!*
