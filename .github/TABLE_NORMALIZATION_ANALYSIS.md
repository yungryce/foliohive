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
| `model_status` | str | ⚠️ DUPLICATED | Duplicates ModelMetadata.status | **Consider removing** - query ModelMetadata instead |
| `model_fingerprint` | str | ⚠️ FK VIOLATION | Foreign key to ModelMetadata without constraint | Keep but add validation logic |
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
| `job_id` | str | ⚠️ AMBIGUOUS | Which job created this? Or latest job? | **Clarify semantics** - consider removing |
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
   document = {
       "name": repo_name,  # Already in RowKey
       "fingerprint": ...,  # Already in fingerprint field
       "has_documentation": ...,  # Already in has_documentation field
       "api_usage": ...,  # Should be separate
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
```

**Benefits:**
- Each table represents ONE entity type
- No JSON blobs to parse
- Can query: "SELECT * FROM RepoLanguages WHERE language='Python' AND percentage>50"
- Atomic updates per language/file type
- Respects Azure 32KB row limit

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
8. **ModelRepos** - Many-to-many model↔repo
9. **ModelMetrics** - Queryable training metrics

### Migration Plan

#### Phase 1: Remove Derived Fields (Low Risk)
- Drop `total_repos`, `completed_repos`, `repos_count` from JobMetadata/ModelMetadata
- Update code to calculate from RepoSyncStatus/ModelRepos queries
- **Estimated effort:** 2 hours

#### Phase 2: Remove List Fields (Medium Risk)
- Drop `expected_repos`, `queued_repos`, `synced_repos`, `failed_repos` from JobMetadata
- Drop `repo_names` from ModelMetadata
- Create ModelRepos table
- Update sync_worker.py to query instead of read lists
- **Estimated effort:** 1 day

#### Phase 3: Normalize Nested JSON (High Risk)
- Create RepoLanguages, RepoFileTypes, RepoGitHubMetadata tables
- Migrate existing `languages`, `categorized_types`, `metadata` blobs
- Update merge_worker.py and API gateway queries
- **Estimated effort:** 2-3 days

#### Phase 4: Add FK Validation (Low Risk)
- Add validation in upsert methods to check FK existence
- Add unit tests for FK violations
- **Estimated effort:** 4 hours

---

## Impact Analysis

### Code Changes Required

#### api_gateway.py
```python
# BEFORE
candidate_job = _fetch_candidate_jobs(username)
synced_repos = candidate_job.get("synced_repos", [])

# AFTER
candidate_job = _fetch_candidate_jobs(username)
job_id = candidate_job.get("job_id")
statuses = table_manager.list_repo_statuses(job_id)
synced_repos = [s["repo_name"] for s in statuses if s["status"] == "synced"]
```

#### sync_worker.py
```python
# BEFORE
job_info["synced_repos"].append(repo_name)
table_manager.upsert_job_metadata(job_info)

# AFTER
# RepoSyncStatus update is atomic - no list manipulation needed
table_manager.upsert_repo_status(RepoSyncStatusRow(
    job_id=job_id,
    repo_name=repo_name,
    status="synced",
    ...
))
```

#### merge_worker.py
```python
# BEFORE
fresh_repos = payload.get("fresh_repos")  # Embedded list
cached_bundle = payload.get("cached_bundle")  # Embedded list

# AFTER
job_id = payload.get("job_id")
statuses = table_manager.list_repo_statuses(job_id)
synced_repo_names = [s["repo_name"] for s in statuses if s["status"] == "synced"]
fresh_repos = table_manager.query_repo_metadata(username, repo_names=synced_repo_names)
```

### Performance Impact

#### Queries Become More Expensive
| Operation | Before | After | Impact |
|-----------|--------|-------|--------|
| Get synced repos | 1 point read | 1 query (list) | +10-50ms depending on repo count |
| Get total repos | 1 point read | 1 query + count | +10-50ms |
| Get model repos | 1 point read | 1 query (list) | +10-50ms |

#### Writes Become Safer
| Operation | Before | After | Benefit |
|-----------|--------|-------|---------|
| Update repo status | Read+Modify+Write (race condition) | Single upsert | Atomic - no race |
| Add model repo | Read list+Append+Write (race) | Single insert | Atomic - no race |

**Verdict:** Slight read latency increase (10-50ms) acceptable for ACID guarantees.

### Storage Impact

#### Before Normalization
- JobMetadata: ~2KB per row (with 4 lists)
- RepoMetadata: ~10-50KB per row (nested JSON)
- ModelMetadata: ~5KB per row (with repo_names list)

#### After Normalization
- JobMetadata: ~500 bytes per row (no lists)
- RepoMetadata: ~1KB per row (no JSON)
- ModelMetadata: ~500 bytes per row (no lists)
- +RepoLanguages: ~200 bytes × languages per repo
- +RepoFileTypes: ~300 bytes × file types per repo
- +ModelRepos: ~150 bytes × repos per model
- +ModelMetrics: ~500 bytes per model

**Net Result:** Slight increase in total storage (~10-20%) but data is queryable and atomic.

---

## Compliance Scorecard

### ACID Principles

| Principle | Current State | After Normalization |
|-----------|---------------|---------------------|
| **Atomicity** | ❌ VIOLATED - list updates have race conditions | ✅ FIXED - each row is atomic unit |
| **Consistency** | ❌ VIOLATED - synced_repos list can diverge from RepoSyncStatus | ✅ FIXED - single source of truth |
| **Isolation** | ⚠️ PARTIAL - Azure Table optimistic concurrency | ⚠️ PARTIAL - unchanged (Azure limitation) |
| **Durability** | ✅ COMPLIANT - Azure Table persistence | ✅ COMPLIANT - unchanged |

### Normal Forms

| Normal Form | Current State | After Normalization |
|-------------|---------------|---------------------|
| **1NF** (Atomic values) | ❌ VIOLATED - lists and nested JSON | ✅ COMPLIANT - all scalar values |
| **2NF** (No partial deps) | ✅ COMPLIANT - composite keys used properly | ✅ COMPLIANT - maintained |
| **3NF** (No transitive deps) | ❌ VIOLATED - derived fields (total_repos from synced_repos) | ✅ COMPLIANT - no derived data |
| **BCNF** (Every determinant is key) | ⚠️ PARTIAL - username determines job_id in multiple tables | ⚠️ PARTIAL - acceptable for NoSQL |

---

## Decision: Accept or Normalize?

### Option A: Keep Current Design (Denormalized)
**Pros:**
- No code changes required
- Faster reads (1 query vs multiple)
- Simpler mental model

**Cons:**
- Race conditions in production (already observed)
- Data consistency bugs (synced_repos ≠ RepoSyncStatus)
- Cannot query by language/file type
- Violates ACID atomicity

**Verdict:** ❌ NOT RECOMMENDED - Tech debt will compound

### Option B: Full Normalization (Recommended)
**Pros:**
- Eliminates race conditions
- Single source of truth
- Queryable data (languages, metrics)
- ACID compliant
- Scalable architecture

**Cons:**
- 2-3 days migration effort
- Slightly slower reads (+10-50ms)
- More complex queries

**Verdict:** ✅ RECOMMENDED - Short-term pain, long-term gain

### Option C: Hybrid Approach (Pragmatic)
**Phase 1 (Must Fix):**
- Remove list fields from JobMetadata (eliminates race conditions)
- Remove repo_names from ModelMetadata

**Phase 2 (Should Fix):**
- Normalize RepoMetadata nested JSON

**Phase 3 (Nice to Have):**
- Add FK validation
- Create ModelMetrics table

**Verdict:** ⚠️ ACCEPTABLE - Prioritize safety over purity

---

## Action Items

### Immediate (Week 1)
- [ ] Review this analysis with team
- [ ] Decide: Full normalization vs Hybrid
- [ ] Create feature branch: `normalize-table-schema`
- [ ] Write failing tests demonstrating race conditions

### Short-term (Week 2-3)
- [ ] Phase 1: Remove list fields from JobMetadata
- [ ] Update sync_worker.py to query RepoSyncStatus
- [ ] Update api_gateway.py aggregate queries
- [ ] Add FK validation to upsert methods

### Medium-term (Month 2)
- [ ] Phase 2: Normalize RepoMetadata JSON blobs
- [ ] Create migration script for existing data
- [ ] Update merge_worker.py queries
- [ ] Performance testing

### Long-term (Month 3+)
- [ ] Phase 3: Create ModelMetrics table
- [ ] Add advanced querying capabilities
- [ ] Documentation updates
- [ ] Monitoring/alerting for FK violations

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
