# FolioHive

**AI-Powered GitHub Profile Analysis for Technical Recruiting**

FolioHive is a cloud-native SaaS platform that automatically analyzes candidate GitHub profiles to help recruiters assess technical skills, coding style, and project experience. The system aggregates repository metadata, caches relevant code artifacts, and uses AI to generate contextual summaries and answer recruiter queries.

---

## 🚀 Quick Start

### Prerequisites
- Python 3.12+
- Node.js 18+ and npm
- Azure Functions Core Tools v4
- Azurite (Azure Storage Emulator)
- OpenAI API key

### Local Development

```bash
# Start all development services (Azurite + API + UI)
./run-dev-session.sh --run-e2e -- --python-version 3.12+ --run-tests

# Local settings;
Ensure to update `local.settings.json` with your Github and OpenAI API key and any necessary configuration for Azure Storage connection strings and CORS.
```

Access the application:
- **UI**: http://localhost:4200
- **API**: http://localhost:7071
- **Azurite**: http://localhost:10002 (Table), 10000 (Blob), 10001 (Queue)

---

## 🏗️ Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         Angular UI (SWA)                        │
│  Landing | Profile | Projects | AI Assistant | Admin Dashboard │
└────────────────────────┬────────────────────────────────────────┘
                         │ HTTP/REST
┌────────────────────────┴────────────────────────────────────────┐
│                  Azure Functions (Flex Consumption)             │
│  ┌──────────────┬─────────────┬─────────────┬──────────────┐   │
│  │ API Gateway  │ Sync Worker │Cache Worker │Reconciliation│   │
│  │ (HTTP Routes)│ (Queue)     │ (Queue)     │ (Timer)      │   │
│  └──────────────┴─────────────┴─────────────┴──────────────┘   │
└────────────────────────┬────────────────────────────────────────┘
                         │
┌────────────────────────┴────────────────────────────────────────┐
│                    Azure Storage Account                        │
│  ┌─────────────┬──────────────┬────────────────────────────┐   │
│  │Table Storage│ Blob Storage │    Queue Storage           │   │
│  │(7 Tables)   │(Cached Files)│(sync-jobs, cache-jobs)     │   │
│  └─────────────┴──────────────┴────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                         │
┌────────────────────────┴────────────────────────────────────────┐
│               External Services                                 │
│  GitHub REST/GraphQL API  |  OpenAI GPT API                     │
└─────────────────────────────────────────────────────────────────┘
```

### Key Components

**Function App with Blueprint Pattern** (Modular Monolith)
- **API Gateway**: HTTP endpoints for sync, job polling, AI summaries, queries
- **Sync Worker**: Queue-triggered; fetches GitHub metadata, generates fingerprints
- **Cache Worker**: Queue-triggered; fetches file contents (README, configs)
- **Reconciliation Worker**: Timer-triggered; cleanup and retry logic (3-min interval)

**Shared Modules** (`cloudfolio_shared/`)
- **ai/**: OpenAI integration, context orchestration, token management
- **cache/**: Fingerprint-based caching, blob storage management
- **github/**: REST + GraphQL unified interface
- **table/**: 7-table normalized schema with TableManager
- **queue/**: Message serialization and queue clients

**Data Storage**
- **Table Storage**: 7 normalized tables (JobMetadata, RepoGitHubMetadata, RepoLanguages, etc.)
- **Blob Storage**: Cached README and config files (content-addressable by fingerprint)
- **Queue Storage**: Async job processing (sync-jobs, cache-jobs)

---

## 📊 Key Features

### 1. Automated GitHub Data Collection
- Fetch candidate profiles via GitHub username
- Collect repository metadata (languages, stars, topics, dates)
- Track sync state with job progress monitoring
- Deduplicate requests using fingerprint-based caching

### 2. Intelligent File Caching
- Automatically fetch README files for project context
- Cache language-specific config files (package.json, pyproject.toml, etc.)
- Content-addressable storage (SHA-256 fingerprints)
- Skip unchanged files to minimize API calls

### 3. AI-Powered Summaries
- **Profile Summary**: Holistic candidate overview with skills, experience, patterns
- **Repository Summary**: Individual project analysis with tech stack and architecture
- **Interactive Assistant**: Answer recruiter queries with candidate-specific context

### 4. Asynchronous Processing
- Queue-driven architecture for scalability
- Job state tracking: queued → syncing → metadata_ready → completed
- Repo state tracking: pending → synced → cached
- Automatic retry with reconciliation worker

### 5. Cost-Optimized AI Usage
- Tiered model selection (gpt-5-nano, gpt-4o-mini)
- Token budget management per summary type
- Context chunking to fit within limits
- Response validation and truncation detection

---

## 🛠️ Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Frontend** | Angular 18+ (Standalone Components) | Reactive UI with RxJS |
| **Backend** | Azure Functions (Flex Consumption) | Serverless API + Workers |
| **Language** | Python 3.12+ | Core backend logic |
| **AI** | OpenAI GPT (gpt-5-nano, gpt-4o-mini) | Summaries and queries |
| **Storage** | Azure Table, Blob, Queue Storage | Data persistence |
| **Git Data** | GitHub REST + GraphQL API | Repository metadata |
| **IaC** | Bicep | Infrastructure as Code |
| **CI/CD** | Azure DevOps Pipelines | Automated deployments |
| **Monitoring** | Application Insights | Telemetry and diagnostics |

---

## 📁 Project Structure

```
foliohive/
├── api/v0.3.0/
│   ├── function-app/          # Azure Functions entry point
│   │   ├── function_app.py    # Main app registration
│   │   └── blueprints/        # Worker implementations
│   ├── shared/                # cloudfolio_shared package
│   │   └── src/cloudfolio_shared/
│   │       ├── ai/            # AI integration
│   │       ├── cache/         # Caching logic
│   │       ├── github/        # GitHub API client
│   │       ├── queue/         # Queue messaging
│   │       └── table/         # Table Storage schema
│   └── tests/                 # Pytest test suite
│
├── ui/                        # Angular Static Web App
│   └── src/app/
│       ├── landing/           # Candidate search
│       ├── profile/           # Candidate summary
│       ├── projects/          # Repository list
│       ├── ai/                # AI assistant
│       └── services/          # API clients
│
├── infra/bicep/               # Azure infrastructure
│   ├── main.bicep             # Entry point
│   ├── main.bicepparam        # Parameters
│   └── modules/               # Resource modules
│
└── README.md                  # This file
```

---

## 🔗 Documentation

- **[API Documentation](./api/v0.3.0/README.md)** - Blueprints, workers, shared modules, table schema
- **[UI Documentation](./ui/README.md)** - Components, services, state management
- **[Infrastructure Documentation](./infra/bicep/README.md)** - Bicep modules, deployment, networking
- **[DevOps Documentation](./.ado/README.md)** - Pipelines, CI/CD, variable groups *(coming soon)*

---

## 🧪 Testing

```bash
# Run all tests
cd api/v0.3.0/tests
./run_tests.sh

# Run specific test suite
pytest test_reconciliation_worker.py -v

# Run integration tests
pytest integration/ -v

# Run E2E curl tests
./e2e_curl_tests.sh
```

---

## 🚢 Deployment

### Infrastructure Deployment
```bash
cd infra/bicep

# Deploy with default parameters
az deployment sub create \
  --name foliohive-prod \
  --location eastus \
  --template-file main.bicep \
  --parameters main.bicepparam

# Or use specific parameter file
az deployment sub create \
  --location eastus \
  --template-file main.bicep \
  --parameters @main.bicepparam
```

### Application Deployment
Automated via Azure DevOps pipelines:
- **Functions**: `azure-functions-cd.yml`
- **Static Web App**: `static-web-app-cd.yml`
- **Training Worker**: `training-worker-cd.yml`

---

## 🔐 Security

- **Managed Identity**: No stored credentials for Azure service communication
- **Private Networking**: VNet integration for Function Apps
- **Key Vault**: Secrets management (GitHub tokens, OpenAI keys)
- **CORS**: Restricted to UI origin
- **API Keys**: Optional authentication layer

---

## 💰 Cost Optimization

- **Flex Consumption Plan**: Pay only for execution time
- **Intelligent Caching**: Minimize GitHub API calls with fingerprints
- **AI Token Management**: Budget enforcement per summary type
- **Queue-Based Processing**: Efficient async execution
- **Storage Lifecycle**: TTL-based blob cleanup *(planned)*

---

## 📈 Monitoring

- **Application Insights**: End-to-end telemetry
- **Custom Metrics**: Job success rates, cache hit ratios, AI token usage
- **Structured Logging**: Correlation IDs across workers
- **Queue Metrics**: Message depth, processing time, DLQ counts

---

## 🤝 Contributing

1. Create feature branch: `git checkout -b feature/your-feature`
2. Follow Python coding standards (PEP 8)
3. Add tests for new functionality
4. Update relevant documentation
5. Submit pull request with clear description

---

## 📝 License

Proprietary - All rights reserved

---

## 🆘 Support

- **Issues**: Submit via GitHub Issues
- **Documentation**: Check component-specific READMEs
- **Architecture Questions**: Review [Architecture Decision Records](./docs/adr/) *(coming soon)*

---

**Built with ❤️ for technical recruiters**
