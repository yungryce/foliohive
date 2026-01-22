# Azure Table Storage: ACID Compliance & Normalization Analysis

**Date:** 2026-01-21  
**Objective:** Eliminate data duplication, enforce referential integrity, and align with ACID principles  
**Principle:** "Do not duplicate data, reference it instead"

---

## Executive Summary

Current architecture violates multiple normalization principles:
- **Denormalized list fields** stored as JSON in atomic rows (expected_repos, queued_repos, synced_repos, failed_repos)
- **Nested JSON blobs** that should be separate entities (document, metadata, languages, categorized_types)
- **Duplicated foreign keys** across tables without enforced constraints
- **Derived data** stored redundantly (total_repos, completed_repos calculable from RepoSyncStatus)
- **Cross-table data duplication** (username duplicated in multiple tables, job_id stored in multiple locations)

**Severity:** CRITICAL - Current design makes atomicity guarantees impossible for multi-repo operations

---

## Table-by-Table Analysis

### 1. JobMetadataRow (JobMetadata Table)

**PartitionKey:** `username`  
**RowKey:** `job_id`

#### Field Analysis

| Field | Type | Verdict | Issue | Recommendation |
|-------|------|---------|-------|----------------|
| `username` | str | ✅ ATOMIC | Natural key (PartitionKey) | Keep - primary identifier |
| `job_id` | str | ✅ ATOMIC | Natural key (RowKey) | Keep - primary identifier |
| `status` | str | ✅ ATOMIC | Scalar enumeration | Keep - job-level state |
| `total_repos` | int | ❌ DERIVED | Calculable from RepoSyncStatus count | **Remove** - query `COUNT(*) WHERE job_id` |
| `completed_repos` | int | ❌ DERIVED | Calculable from RepoSyncStatus WHERE status='synced' | **Remove** - query `COUNT(*) WHERE status='synced'` |
| `expected_repos` | List[str] | ❌ VIOLATION | List stored in atomic row | **Remove** - should be derived from RepoSyncStatus |
| `queued_repos` | List[str] | ❌ VIOLATION | List stored in atomic row | **Remove** - should be derived from RepoSyncStatus |
| `synced_repos` | List[str] | ❌ VIOLATION | List stored in atomic row | **Remove** - redundant with RepoSyncStatus |
| `failed_repos` | List[str] | ❌ VIOLATION | List stored in atomic row | **Remove** - redundant with RepoSyncStatus |
| `bundle_fingerprint` | str | ✅ ATOMIC | Scalar hash reference to blob | Keep - denormalized for performance |
| `force_refresh` | bool | ✅ ATOMIC | Scalar flag | Keep - job configuration |
| `model_status` | str | ⚠️ DUPLICATED | Duplicates ModelMetadata.status | **Remove** - query ModelMetadata instead |
| `model_fingerprint` | str | ⚠️ FK VIOLATION | Foreign key to ModelMetadata without constraint | **Remove** - query ModelMetadata instead |
| `merge_enqueued_at` | str | ✅ ATOMIC | Timestamp scalar | Keep - workflow tracking |
| `last_requeue_at` | str | ✅ ATOMIC | Timestamp scalar | Keep - workflow tracking |
| `trace_id` | str | ✅ ATOMIC | Correlation ID | Keep - observability |
| `request_id` | str | ✅ ATOMIC | Correlation ID | Keep - observability |
| `session_id` | str | ⚠️ AMBIGUOUS | Should session track jobs or jobs track sessions? | **Remove** - SessionCandidates already tracks this |
| `created_at` | str | ✅ ATOMIC | Timestamp scalar | Keep - audit trail |
| `updated_at` | str | ✅ ATOMIC | Timestamp scalar | Keep - audit trail |

**Summary:**
- **19 total fields**
- **8 fields violate normalization (42%)**
- **4 list fields should be eliminated**
- **4 derived fields should be removed**

#### Critical Issues

1. **Atomicity Violation:** Lists `expected_repos`, `queued_repos`, `synced_repos`, `failed_repos` cannot be updated atomically. When sync_worker updates job progress, it:
   - Reads current lists
   - Modifies in memory
   - Writes back entire row
   - **RACE CONDITION:** Multiple workers processing different repos can overwrite each other's changes

2. **Data Duplication:** RepoSyncStatus table already stores `(job_id, repo_name, status)` triples. These lists are 100% redundant.

3. **Consistency Violation:** No guarantee that `synced_repos` list matches `COUNT(*) FROM RepoSyncStatus WHERE status='synced'`.

#### Recommended Schema (Normalized)

```python
@dataclass
class JobMetadataRow:
    """Job tracking - denormalized aggregates removed."""
    
    # Keys
    username: str  # PartitionKey
    job_id: str    # RowKey
    
    # Job configuration
    status: str = "queued"
    force_refresh: bool = False
    
    # Workflow tracking
    merge_enqueued_at: Optional[str] = None
    last_requeue_at: Optional[str] = None
    
    # References (no duplication)
    bundle_fingerprint: Optional[str] = None  # Blob reference
    model_fingerprint: Optional[str] = None   # FK to ModelMetadata
    
    # Observability
    trace_id: Optional[str] = None
    request_id: Optional[str] = None
    
    # Audit
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
```

**Migration Strategy:**
- Drop 8 fields from JobMetadataRow
- Query RepoSyncStatus for aggregate data:
  ```python
  total_repos = len(table_manager.list_repo_statuses(job_id))
  completed_repos = len([r for r in statuses if r['status'] == 'synced'])
  synced_repos = [r['repo_name'] for r in statuses if r['status'] == 'synced']
  ```

---

### 2. SessionCandidateRow (SessionCandidates Table)

**PartitionKey:** `session_id`  
**RowKey:** `username`

#### Field Analysis

| Field | Type | Verdict | Issue | Recommendation |
|-------|------|---------|-------|----------------|
| `session_id` | str | ✅ ATOMIC | Natural key (PartitionKey) | Keep - session identifier |
| `username` | str | ✅ ATOMIC | Natural key (RowKey) | Keep - user identifier |
| `latest_job_id` | str | ⚠️ FK VIOLATION | Foreign key to JobMetadata without constraint | Keep but add validation |
| `last_viewed_at` | str | ✅ ATOMIC | Timestamp scalar | Keep - analytics |
| `query_count` | int | ✅ ATOMIC | Counter | Keep - analytics |
| `created_at` | str | ✅ ATOMIC | Timestamp scalar | Keep - audit trail |
| `updated_at` | str | ✅ ATOMIC | Timestamp scalar | Keep - audit trail |

**Summary:**
- **7 total fields**
- **1 field has FK concern (14%)**
- **Overall: ACCEPTABLE** - minimal denormalization

#### Issues

1. **Foreign Key Integrity:** `latest_job_id` references JobMetadata but no constraint enforces existence. Could point to deleted jobs.

#### Recommended Schema (No Changes Needed)

This table is **already well-normalized**. Only improvement needed is FK validation:

```python
def upsert_session_candidate(self, session_id: str, username: str, job_id: Optional[str]) -> None:
    # Add FK validation
    if job_id:
        job = self.get_job_metadata(username, job_id)
        if not job:
            raise ValueError(f"Invalid job_id {job_id} for user {username}")
    
    # ... existing logic
```

---

### 3. RepoMetadataRow (RepoMetadata Table)

**PartitionKey:** `username`  
**RowKey:** `repo_name`

#### Field Analysis

| Field | Type | Verdict | Issue | Recommendation |
|-------|------|---------|-------|----------------|
| `username` | str | ✅ ATOMIC | Natural key (PartitionKey) | Keep - user identifier |
| `repo_name` | str | ✅ ATOMIC | Natural key (RowKey) | Keep - repo identifier |
| `fingerprint` | str | ✅ ATOMIC | Content hash | Keep - cache invalidation |
| `job_id` | str | ⚠️ AMBIGUOUS | Which job created this? Or latest job? | **Remove** - RepoSyncStatus table already tracks the (job_id, repo_name) |
| `document` | Dict | ❌ VIOLATION | Nested JSON blob duplicating other fields | **Remove** - redundant with other fields |
| `metadata` | Dict | ❌ VIOLATION | Nested JSON containing repo details | **Normalize** - extract to RepoDetailsRow |
| `content_blob` | str | ✅ ATOMIC | Blob cache key reference | Keep - pointer to full content |
| `languages` | Dict | ❌ VIOLATION | Nested JSON `{language: bytes}` | **Normalize** - extract to RepoLanguagesRow |
| `categorized_types` | Dict | ❌ VIOLATION | Nested JSON file type categorization | **Normalize** - extract to RepoFileTypesRow |
| `has_documentation` | bool | ✅ ATOMIC | Scalar boolean | Keep - derived flag |
| `readme_excerpt` | str | ✅ ATOMIC | Scalar string (truncated) | Keep - preview data |
| `created_at` | str | ✅ ATOMIC | Timestamp scalar | Keep - audit trail |
| `last_synced_at` | str | ✅ ATOMIC | Timestamp scalar | Keep - freshness indicator |
| `updated_at` | str | ✅ ATOMIC | Timestamp scalar | Keep - audit trail |

**Summary:**
- **14 total fields**
- **5 fields violate normalization (36%)**
- **4 nested JSON fields should be normalized**

#### Critical Issues

1. **Nested JSON Blobs:** `document`, `metadata`, `languages`, `categorized_types` are large nested structures (can exceed 32KB Azure limit).

2. **Data Duplication:** `document` field duplicates data already in `metadata`, `fingerprint`, `has_documentation`:
   ```python
   # sync_worker.py
   document = {
       "name": repo_name,  # Already in RowKey
       "fingerprint": ...,  # Already in fingerprint field
       "has_documentation": ...,  # Already in has_documentation field
       "api_usage": ...,  # Should be in RepoAPIUsage table
   }
   ```
 

3. **Queryability:** Cannot query "repos using Python >50%" because `languages` is JSON blob. Must load all repos and parse.

4. **Job_id Ambiguity:** Unclear if this is:
   - FK to job that created this row
   - FK to latest job that synced this repo
   - Stale data after multiple jobs

#### Recommended Schema (Normalized)

**Core Table:**
```python
@dataclass
class RepoMetadataRow:
    """Lightweight repo metadata - no nested JSON."""
    
    # Keys
    username: str        # PartitionKey
    repo_name: str       # RowKey
    
    # Content tracking
    fingerprint: Optional[str]
    content_blob: Optional[str]  # Reference to blob storage
    
    # Simple flags
    has_documentation: Optional[bool]
    readme_excerpt: Optional[str]  # First 4KB only
    
    # Audit
    created_at: Optional[str]
    last_synced_at: Optional[str]
    updated_at: Optional[str]
```

**New Normalized Tables:**

```python
# RepoLanguages Table
@dataclass
class RepoLanguagesRow:
    """Per-repo language statistics."""
    username: str         # PartitionKey
    repo_language_key: str  # RowKey: "{repo_name}#{language}"
    repo_name: str
    language: str
    bytes: int
    percentage: float

# RepoFileTypes Table  
@dataclass
class RepoFileTypesRow:
    """Per-repo file type categorization."""
    username: str      # PartitionKey
    repo_type_key: str  # RowKey: "{repo_name}#{category}"
    repo_name: str
    category: str      # "config" | "docs" | "source" | etc
    file_path: str
    file_type: str

# RepoGitHubMetadata Table
@dataclass
class RepoGitHubMetadataRow:
    """GitHub-specific metadata."""
    username: str      # PartitionKey
    repo_name: str     # RowKey
    full_name: str
    description: Optional[str]
    stars: int
    forks: int
    open_issues: int
    default_branch: str
    created_at: str
    pushed_at: str
    updated_at: str

# RepoAPIUsage Table (NEW)
@dataclass
class RepoAPIUsageRow:
    """GitHub API consumption tracking per sync operation."""
    job_id: str                         # PartitionKey
    operation_key: str                  # RowKey: "{repo_name}#{operation}#{timestamp}"
    repo_name: str
    username: str
    operation: str                      # 'config_discovery', 'metadata_fetch', 'languages_fetch'
    api_calls: int                      # Number of REST API calls made
    rate_limit_remaining: Optional[int] # GitHub rate limit remaining after operation
    rate_limit_reset_at: Optional[str]  # When rate limit resets (ISO8601)
    graphql_cost: Optional[int]         # GraphQL query cost (if applicable)
    execution_time_ms: Optional[int]    # Operation duration in milliseconds
    endpoint: Optional[str]             # GitHub API endpoint called
    mode: Optional[str]                 # 'rest' or 'graphql'
    created_at: str                     # Operation timestamp
```

**Benefits:**
- Each table represents ONE entity type
- No JSON blobs to parse
- Can query: "SELECT * FROM RepoLanguages WHERE language='Python' AND percentage>50"
- Atomic updates per language/file type
- Respects Azure 32KB row limit
- **API Usage Tracking:** Query total GitHub API consumption per job/user for rate limit management and cost optimization

---

### 4. RepoSyncStatusRow (RepoSyncStatus Table)

**PartitionKey:** `job_id`  
**RowKey:** `repo_name`

#### Field Analysis

| Field | Type | Verdict | Issue | Recommendation |
|-------|------|---------|-------|----------------|
| `job_id` | str | ✅ ATOMIC | Natural key (PartitionKey) | Keep - job identifier |
| `repo_name` | str | ✅ ATOMIC | Natural key (RowKey) | Keep - repo identifier |
| `username` | str | ⚠️ DUPLICATED | Duplicates JobMetadata.username | **Consider removing** - query JobMetadata.username |
| `status` | str | ✅ ATOMIC | Scalar enum (synced/failed/pending) | Keep - sync state |
| `message_id` | str | ✅ ATOMIC | Queue message correlation | Keep - idempotency tracking |
| `error` | str | ✅ ATOMIC | Error message if failed | Keep - debugging data |
| `updated_at` | str | ✅ ATOMIC | Timestamp scalar | Keep - audit trail |

**Summary:**
- **7 total fields**
- **1 field duplicated (14%)**
- **Overall: GOOD** - mostly normalized

#### Issues

1. **Username Duplication:** `username` is redundant because:
   - `job_id` already has FK to JobMetadata
   - JobMetadata.PartitionKey = username
   - Can always query: `job = get_job_metadata(?, job_id); username = job.username`

2. **Denormalization Trade-off:** Storing `username` enables querying "all repos synced for user X" without joining JobMetadata. This is **acceptable denormalization** for read performance.

#### Recommended Schema (Minimal Change)

**Option A: Keep username (current design) - RECOMMENDED**
- Accept denormalization for query performance
- Document that username is cached from JobMetadata

**Option B: Remove username (strict normalization)**
```python
@dataclass
class RepoSyncStatusRow:
    """Per-repo sync status - strict normalization."""
    job_id: str
    repo_name: str
    status: str
    message_id: Optional[str]
    error: Optional[str]
    updated_at: Optional[str]
```
**Trade-off:** Requires JOIN to get username, slower queries.

**Verdict:** Keep username field - performance benefit outweighs minor duplication.

---

### 5. ModelMetadataRow (ModelMetadata Table)

**PartitionKey:** `username`  
**RowKey:** `fingerprint`

#### Field Analysis

| Field | Type | Verdict | Issue | Recommendation |
|-------|------|---------|-------|----------------|
| `username` | str | ✅ ATOMIC | Natural key (PartitionKey) | Keep - user identifier |
| `fingerprint` | str | ✅ ATOMIC | Natural key (RowKey) | Keep - model version hash |
| `experiment_name` | str | ✅ ATOMIC | Scalar string | Keep - experiment tracking |
| `status` | str | ✅ ATOMIC | Scalar enum | Keep - training state |
| `artifact_blob` | str | ✅ ATOMIC | Blob storage reference | Keep - model artifact pointer |
| `metadata` | Dict | ❌ VIOLATION | Nested JSON training metrics | **Normalize** - extract to ModelMetricsRow |
| `training_params` | Dict | ⚠️ ACCEPTABLE | Nested JSON config | Keep - small config blob |
| `repos_count` | int | ❌ DERIVED | Calculable from len(repo_names) | **Remove** - redundant |
| `repo_names` | List[str] | ❌ VIOLATION | List stored in atomic row | **Remove** - create ModelReposRow table |
| `trained_at` | str | ✅ ATOMIC | Timestamp scalar | Keep - audit trail |
| `updated_at` | str | ✅ ATOMIC | Timestamp scalar | Keep - audit trail |
| `model_fingerprint` | str | ⚠️ DUPLICATED | Alias for fingerprint field | **Remove** - use fingerprint only |

**Summary:**
- **12 total fields**
- **4 fields violate normalization (33%)**
- **1 list field, 1 derived field, 1 nested metrics blob**

#### Critical Issues

1. **List Field:** `repo_names` suffers same atomicity issues as JobMetadataRow lists. Cannot atomically add/remove repos.

2. **Derived Field:** `repos_count = len(repo_names)` is redundant computation.

3. **Nested Metrics:** `metadata` contains training metrics that should be queryable:
   ```json
   {
     "accuracy": 0.95,
     "loss": 0.05,
     "epochs_completed": 10,
     "training_time_seconds": 3600
   }
   ```
   Cannot query "models with accuracy >0.9" without loading all models.

4. **Fingerprint Duplication:** Both `fingerprint` and `model_fingerprint` point to same value (see `__post_init__` sync logic).

#### Recommended Schema (Normalized)

**Core Table:**
```python
@dataclass
class ModelMetadataRow:
    """Trained model metadata - no lists/metrics."""
    username: str
    fingerprint: str  # Remove model_fingerprint alias
    experiment_name: str = "default"
    status: str = "pending"
    artifact_blob: Optional[str] = None
    training_params: Dict[str, Any] = field(default_factory=dict)  # Keep - small config
    trained_at: Optional[str] = None
    updated_at: Optional[str] = None
```

**New Tables:**
```python
# ModelRepos Table
@dataclass
class ModelReposRow:
    """Many-to-many: models to repos."""
    model_username: str      # PartitionKey
    model_repo_key: str      # RowKey: "{fingerprint}#{repo_name}"
    fingerprint: str
    repo_name: str
    created_at: str

# ModelMetrics Table
@dataclass
class ModelMetricsRow:
    """Training metrics per model."""
    model_username: str  # PartitionKey
    fingerprint: str     # RowKey
    accuracy: float
    loss: float
    precision: float
    recall: float
    f1_score: float
    epochs_completed: int
    training_time_seconds: int
    created_at: str
```

**Benefits:**
- Can query "repos_count" as `COUNT(*)` from ModelRepos
- Can query models by accuracy threshold
- Atomic repo additions to ModelRepos
- No list size limits

---

## Cross-Table Relationship Analysis

### Current Data Flow

```
JobMetadata (username → job_id)
    ├─ session_id → ❌ WRONG DIRECTION (should be SessionCandidates → JobMetadata)
    ├─ synced_repos[] → ❌ DUPLICATE (RepoSyncStatus has this)
    ├─ model_fingerprint → ModelMetadata
    └─ bundle_fingerprint → Blob Storage

RepoMetadata (username → repo_name)
    ├─ job_id → ⚠️ AMBIGUOUS (which job? latest? creator?)
    ├─ document{} → ❌ DUPLICATE (redundant with other fields)
    └─ content_blob → Blob Storage

RepoSyncStatus (job_id → repo_name)
    ├─ username → ⚠️ DUPLICATE (can get from JobMetadata)
    └─ FK to JobMetadata ✅

SessionCandidates (session_id → username)
    └─ latest_job_id → JobMetadata ✅

ModelMetadata (username → fingerprint)
    ├─ repo_names[] → ❌ SHOULD BE ModelRepos TABLE
    └─ artifact_blob → Blob Storage ✅
```

### Violations

1. **Circular Reference:** JobMetadata.session_id ↔ SessionCandidates.latest_job_id
   - **Fix:** Remove JobMetadata.session_id

2. **Ambiguous FK:** RepoMetadata.job_id unclear semantics
   - **Fix:** Remove or clarify (rename to `created_by_job_id` or `last_synced_by_job_id`)

3. **No FK Constraints:** Azure Table Storage doesn't enforce FKs
   - **Fix:** Add application-level validation in upsert methods

---

## Recommended Architecture

### Normalized Schema (Target State)

#### Core Tables (Keep)
1. **JobMetadata** - Job tracking (remove 8 fields)
2. **SessionCandidates** - Session analytics (no changes)
3. **RepoMetadata** - Lightweight repo info (remove 5 fields)
4. **RepoSyncStatus** - Per-repo sync state (keep username for perf)

#### New Tables (Add)
5. **RepoLanguages** - Normalized language stats
6. **RepoFileTypes** - Normalized file categorization  
7. **RepoGitHubMetadata** - Extracted GitHub metadata
8. **RepoAPIUsage** - GitHub API consumption tracking (see detailed section below)  
9. **ModelRepos** - Many-to-many model↔repo 
10. **ModelMetrics** - Queryable training metrics

---

## References

- **Azure Table Storage Limits:** https://learn.microsoft.com/en-us/azure/storage/tables/scalability-targets
- **Database Normalization:** https://en.wikipedia.org/wiki/Database_normalization
- **ACID Properties:** https://en.wikipedia.org/wiki/ACID
- **Code Locations:**
  - Table schemas: `/api/v0.3.0/shared/src/cloudfolio_shared/table/table_manager.py`
  - Job updates: `/api/v0.3.0/function-app/blueprints/sync_worker.py` (line 234-423)
  - Bundle queries: `/api/v0.3.0/function-app/blueprints/api_gateway.py` (line 149-240)
  - Merge logic: `/api/v0.3.0/function-app/blueprints/merge_worker.py` (line 220-280)

---

**Document Version:** 1.0  
**Last Updated:** 2026-01-21  
**Status:** Draft - Awaiting Team Review
