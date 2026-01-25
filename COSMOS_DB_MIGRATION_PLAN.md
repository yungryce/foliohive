# Azure Table Storage → Cosmos DB Migration Plan

**Version**: 1.0  
**Date**: January 25, 2026  
**Status**: Planning Phase  
**Owner**: Cloudfolio Engineering

---

## Executive Summary

This document outlines the migration strategy for transitioning Cloudfolio's metadata storage from **Azure Table Storage** to **Azure Cosmos DB for NoSQL**. The migration will enable:

- **Sub-10ms latency** for admin dashboard queries
- **Server-side aggregation** reducing client-side data processing
- **Change feed** for real-time monitoring updates
- **Vector search** for AI model metadata (future capability)
- **Multi-region writes** for global admin access (optional)

**Migration Approach**: Dual-write pattern with staged rollout (no downtime)

---

## 1. Current State Analysis

### 1.1 Existing Table Storage Schema

| Table Name | PartitionKey | RowKey | Size Estimate | Query Patterns |
|------------|--------------|--------|---------------|----------------|
| **JobMetadata** | `username` | `job_id` | ~100KB/user | By user, by status, by date |
| **RepoSyncStatus** | `job_id` | `repo_name` | ~50KB/job | By job (all repos) |
| **RepoAPIUsage** | `username` | `operation_key` | ~10MB/user/month | By user, by operation, by job |
| **SessionCandidates** | `session_id` | `username` | ~5KB/session | By session (recent activity) |
| **RepoMetadata** | `username` | `repo_name` | ~500KB/user | By user, by repo list |
| **RepoLanguages** | `username` | `repo#language` | ~20KB/repo | By user+repo |
| **RepoGitHubMetadata** | `username` | `repo_name` | ~30KB/repo | By user+repo |
| **ModelMetadata** | `username` | `fingerprint` | ~100KB/model | By user, by fingerprint |

**Total Storage**: ~50-100 GB (est. for 10K users with 100 repos each)

### 1.2 Current Query Limitations

**Table Storage Pain Points**:
1. **No server-side aggregation**: Must fetch all jobs and count statuses client-side
2. **No full-text search**: Cannot search job errors or repo descriptions
3. **Limited filtering**: Only partition key + row key range queries
4. **No computed properties**: Must recalculate metrics on every query
5. **No change notifications**: Polling required for real-time updates

**Example: Dashboard Job Stats Query**
```python
# Current approach (Table Storage)
jobs = table_manager.list_jobs_metadata_by_status(["queued", "syncing", "metadata_ready"])
# Client-side grouping required
status_counts = defaultdict(int)
for job in jobs:
    status_counts[job["status"]] += 1
```

---

## 2. Cosmos DB Architecture Design

### 2.1 Container Strategy

**Option A: One Container per Table** (Recommended for MVP)
- Simplest migration path (1:1 mapping)
- Partition key remains unchanged
- Minimal code changes to `table_manager.py`

**Option B: Consolidated Containers** (Recommended for Scale)
- Combine related entities (e.g., `JobMetadata` + `RepoSyncStatus`)
- Better for distributed queries across job + repo data
- Enables hierarchical partition keys

**Recommendation**: Start with **Option A**, migrate to **Option B** post-production validation.

### 2.2 Partition Key Design

| Container | Partition Key | Rationale | Max Size |
|-----------|---------------|-----------|----------|
| **JobMetadata** | `/username` | User-level isolation, matches Table Storage | 20GB (unlimited with HPK) |
| **RepoSyncStatus** | `/jobId` | Job-level isolation, efficient repo listing | 20GB |
| **RepoAPIUsage** | `/username` | User-level tracking, time-series data | 20GB (HPK: `/username/yearMonth`) |
| **RepoMetadata** | `/username` | User-owned repos, matches Table Storage | 20GB |

**Hierarchical Partition Key (HPK) Opportunities**:
- `RepoAPIUsage`: Use `/username/yearMonth` to overcome 20GB limit per user
- `JobMetadata`: Use `/username/status` for efficient status filtering

### 2.3 Indexing Strategy

**Default Policy**: Index everything except large blobs
```json
{
  "indexingMode": "consistent",
  "automatic": true,
  "includedPaths": [
    { "path": "/*" }
  ],
  "excludedPaths": [
    { "path": "/content_blob/?"},
    { "path": "/artifact_blob/?" }
  ]
}
```

**Composite Indexes** (for dashboard queries):
```json
{
  "compositeIndexes": [
    [
      { "path": "/status", "order": "ascending" },
      { "path": "/updated_at", "order": "descending" }
    ],
    [
      { "path": "/username", "order": "ascending" },
      { "path": "/created_at", "order": "descending" }
    ]
  ]
}
```

### 2.4 Throughput Provisioning

**Initial RU/s Allocation** (Autoscale mode):
| Container | Min RU/s | Max RU/s | Rationale |
|-----------|----------|----------|-----------|
| JobMetadata | 400 | 4,000 | High read volume (dashboard polling) |
| RepoSyncStatus | 400 | 2,000 | Moderate writes (job updates) |
| RepoAPIUsage | 400 | 1,000 | Append-only, low read volume |
| RepoMetadata | 400 | 2,000 | Moderate reads (portfolio views) |

**Cost Estimate** (autoscale):
- **Idle state**: ~$25/month (400 RU/s × 4 containers)
- **Peak load**: ~$250/month (9,000 RU/s total)
- **Storage**: ~$0.25/GB/month ($12.50 for 50GB)

**Total**: ~$40-$280/month depending on traffic (vs. $10-$50 for Table Storage)

---

## 3. Data Model Changes

### 3.1 Schema Enhancements (Cosmos DB Benefits)

**Add Computed Properties** (reduce client-side logic):
```python
# JobMetadata document example
{
  "id": "job_id",  # Cosmos DB requires 'id' field
  "username": "octocat",
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "created_at": "2026-01-25T10:00:00Z",
  "updated_at": "2026-01-25T10:15:00Z",
  
  # NEW: Computed fields (updated via triggers or change feed processor)
  "duration_seconds": 900,
  "repo_count": 42,
  "failed_repos": ["repo1", "repo2"],
  "success_rate": 95.2,
  
  # Cosmos DB metadata
  "_ts": 1737804900,  # Auto-populated timestamp
  "ttl": -1  # Never expire (-1 = disabled)
}
```

**Add Vector Embeddings** (future AI capability):
```python
# ModelMetadata document
{
  "id": "model_fingerprint",
  "username": "octocat",
  "model_fingerprint": "abc123",
  "embedding": [0.1, 0.2, ..., 0.768],  # 768-dim vector for semantic search
  "trained_at": "2026-01-25T10:00:00Z"
}
```

### 3.2 Breaking Changes vs. Compatibility

**Field Name Changes**:
| Table Storage | Cosmos DB | Reason |
|---------------|-----------|--------|
| `PartitionKey` | `username` (or `jobId`) | Clearer semantics |
| `RowKey` | `id` | Cosmos DB convention |
| N/A | `_ts` | Auto-populated timestamp |

**Backward Compatibility Strategy**:
- Keep both `PartitionKey` and `username` during migration
- Gradually deprecate `PartitionKey` in client code
- Use views/projections to hide internal fields from API responses

---

## 4. Migration Strategy

### 4.1 Phases

**Phase 1: Dual-Write Setup** (Week 1-2)
1. Create Cosmos DB account + containers
2. Update `table_manager.py` to write to **both** Table Storage and Cosmos DB
3. Add feature flag: `COSMOS_DB_ENABLED=write_only`
4. Deploy to staging, validate dual-write consistency

**Phase 2: Read Migration** (Week 3-4)
1. Update dashboard endpoints to read from Cosmos DB
2. Feature flag: `COSMOS_DB_ENABLED=read_admin` (admin endpoints only)
3. Monitor latency, RU consumption, error rates
4. Gradually expand to public endpoints: `COSMOS_DB_ENABLED=read_all`

**Phase 3: Backfill Historical Data** (Week 5)
1. Run backfill script to copy existing Table Storage data to Cosmos DB
2. Use Azure Data Factory or custom Python script
3. Validate data integrity (checksums, row counts)

**Phase 4: Decommission Table Storage** (Week 6)
1. Stop writes to Table Storage
2. Feature flag: `COSMOS_DB_ENABLED=true` (Cosmos only)
3. Archive Table Storage data to Blob Storage (cold tier)
4. Delete Table Storage tables after 30-day retention

### 4.2 Dual-Write Implementation

**Update `table_manager.py`**:
```python
class TableManager:
    def __init__(self, ...):
        self._table_service = ...  # Existing Table Storage client
        self._cosmos_client = None
        
        # Initialize Cosmos DB if enabled
        if os.getenv("COSMOS_DB_ENABLED") in ("write_only", "read_admin", "read_all", "true"):
            from azure.cosmos import CosmosClient
            self._cosmos_client = CosmosClient(
                url=os.getenv("COSMOS_DB_ENDPOINT"),
                credential=DefaultAzureCredential()
            )
            self._cosmos_db = self._cosmos_client.get_database_client("cloudfolio")
    
    def upsert_job_metadata(self, row: JobMetadataRow) -> None:
        # Write to Table Storage (existing logic)
        self._upsert_to_table_storage(row)
        
        # Dual-write to Cosmos DB
        if self._cosmos_client:
            self._upsert_to_cosmos_db(row)
    
    def _upsert_to_cosmos_db(self, row: JobMetadataRow) -> None:
        container = self._cosmos_db.get_container_client("JobMetadata")
        doc = {
            "id": row.job_id,
            "username": row.username,
            "job_id": row.job_id,
            "status": row.status,
            "bundle_fingerprint": row.bundle_fingerprint,
            # ... other fields
        }
        try:
            container.upsert_item(doc)
            logger.info("[COSMOS_UPSERT] job=%s", row.job_id)
        except Exception as exc:
            logger.error("[COSMOS_UPSERT_FAILED] job=%s error=%s", row.job_id, exc)
            # Don't fail entire operation - Table Storage is source of truth during migration
```

**Read Path with Feature Flag**:
```python
def get_job_metadata(self, username: str, job_id: str) -> Optional[Dict[str, Any]]:
    mode = os.getenv("COSMOS_DB_ENABLED", "false")
    
    if mode in ("read_admin", "read_all", "true"):
        # Read from Cosmos DB
        return self._get_job_from_cosmos(username, job_id)
    else:
        # Read from Table Storage (existing)
        return self._get_job_from_table(username, job_id)
```

### 4.3 Backfill Script

**`scripts/backfill_cosmos.py`**:
```python
#!/usr/bin/env python3
"""Backfill historical Table Storage data to Cosmos DB."""

from azure.data.tables import TableServiceClient
from azure.cosmos import CosmosClient
from datetime import datetime, timezone
import logging
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def backfill_table(table_name: str, cosmos_container_name: str):
    """Copy all entities from Table Storage to Cosmos DB."""
    
    # Source: Table Storage
    table_client = TableServiceClient.from_connection_string(
        os.getenv("AzureWebJobsStorage")
    ).get_table_client(table_name)
    
    # Destination: Cosmos DB
    cosmos_client = CosmosClient(
        url=os.getenv("COSMOS_DB_ENDPOINT"),
        credential=os.getenv("COSMOS_DB_KEY")
    )
    container = cosmos_client.get_database_client("cloudfolio").get_container_client(cosmos_container_name)
    
    # Batch process
    entities = table_client.list_entities()
    batch_size = 100
    batch = []
    total_migrated = 0
    
    for entity in entities:
        # Transform entity (PartitionKey/RowKey → Cosmos format)
        doc = transform_entity(entity, table_name)
        batch.append(doc)
        
        if len(batch) >= batch_size:
            # Bulk upsert
            for doc in batch:
                try:
                    container.upsert_item(doc)
                    total_migrated += 1
                except Exception as exc:
                    logger.error("Failed to migrate doc id=%s: %s", doc.get("id"), exc)
            
            logger.info("Migrated %d/%d...", total_migrated, total_migrated)
            batch = []
    
    # Final batch
    for doc in batch:
        container.upsert_item(doc)
        total_migrated += 1
    
    logger.info("Migration complete: %d entities", total_migrated)

def transform_entity(entity: dict, table_name: str) -> dict:
    """Transform Table Storage entity to Cosmos DB document."""
    
    if table_name == "JobMetadata":
        return {
            "id": entity["RowKey"],  # job_id
            "username": entity["PartitionKey"],
            "job_id": entity["RowKey"],
            "status": entity.get("status", "unknown"),
            "created_at": entity.get("created_at"),
            # ... other fields
        }
    
    # Add transforms for other tables...
    raise NotImplementedError(f"Transform not implemented for {table_name}")

if __name__ == "__main__":
    tables = [
        ("JobMetadata", "JobMetadata"),
        ("RepoSyncStatus", "RepoSyncStatus"),
        ("RepoAPIUsage", "RepoAPIUsage"),
        # ... other tables
    ]
    
    for table_name, container_name in tables:
        logger.info("Starting migration: %s → %s", table_name, container_name)
        backfill_table(table_name, container_name)
```

---

## 5. Dashboard Query Optimizations

### 5.1 Server-Side Aggregation

**Before (Table Storage)**:
```python
# Admin endpoint - fetch all jobs and aggregate client-side
jobs = table_manager.list_jobs_metadata_by_status(["queued", "syncing", "metadata_ready"])
status_counts = defaultdict(int)
for job in jobs:
    status_counts[job["status"]] += 1

# Transfers ~1MB for 1000 jobs
```

**After (Cosmos DB with SQL API)**:
```python
# Admin endpoint - aggregate server-side
container = cosmos_db.get_container_client("JobMetadata")
query = """
    SELECT 
        j.status,
        COUNT(1) AS count,
        AVG(DATEDIFF(second, j.created_at, j.updated_at)) AS avg_duration
    FROM jobs j
    WHERE j.status IN ('queued', 'syncing', 'metadata_ready', 'completed', 'failed')
    GROUP BY j.status
"""
results = list(container.query_items(query, enable_cross_partition_query=True))

# Returns ~200 bytes (just aggregates)
# Example: [{"status": "completed", "count": 500, "avg_duration": 120}, ...]
```

**RU Cost**: ~5-10 RU (vs. 100+ RU for full table scan)

### 5.2 Change Feed for Real-Time Updates

**Use Case**: Admin dashboard auto-refresh without polling

**Implementation**:
```python
# Change feed processor (runs as Azure Function or container)
from azure.cosmos import CosmosClient, PartitionKey
from azure.cosmos.aio import CosmosClient as AsyncCosmosClient

async def process_changes(changes):
    """Process Cosmos DB change feed events."""
    for change in changes:
        if change.get("status") == "completed":
            # Push notification to admin dashboard via SignalR
            await signalr_client.send_to_group("admin", "job_completed", {
                "job_id": change["job_id"],
                "username": change["username"],
                "repo_count": change.get("repo_count", 0)
            })

# Register change feed processor
async with AsyncCosmosClient(url, credential) as client:
    database = client.get_database_client("cloudfolio")
    container = database.get_container_client("JobMetadata")
    
    await container.query_items_change_feed(
        processor=process_changes,
        start_time="Now"
    )
```

**Benefit**: Dashboard updates in real-time (sub-second latency) vs. 30s polling interval

---

## 6. Performance Benchmarks

### 6.1 Expected Latency Improvements

| Query | Table Storage | Cosmos DB | Improvement |
|-------|---------------|-----------|-------------|
| Get single job | 50-100ms | 5-10ms | **10x faster** |
| List jobs by status | 200-500ms | 20-50ms | **10x faster** |
| Aggregate job stats | 1-2s (client-side) | 50-100ms (server-side) | **20x faster** |
| API usage by user | 300-800ms | 30-80ms | **10x faster** |

### 6.2 Cost Comparison

**Scenario**: 10,000 users, 100 jobs/user/month, 50 repos/user

| Service | Table Storage | Cosmos DB | Difference |
|---------|---------------|-----------|------------|
| **Storage (50GB)** | $5/month | $12.50/month | +$7.50 |
| **Operations** | $10/month | $150/month (avg) | +$140 |
| **Egress** | $5/month | $5/month | $0 |
| **Total** | **$20/month** | **$167.50/month** | **+$147.50** |

**ROI Justification**:
- Reduced development time (no client-side aggregation logic)
- Better user experience (real-time updates, faster queries)
- Scalability headroom (multi-region, vector search, change feed)

**Cost Optimization**:
- Use **shared throughput** across containers (400 RU/s shared = $24/month)
- Enable **serverless mode** for dev/staging (<100 RU/s avg)
- Archive old data to Blob Storage (jobs >90 days old)

---

## 7. Risk Mitigation

### 7.1 Potential Risks

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| **Data inconsistency during dual-write** | Medium | High | Implement reconciliation job, checksums |
| **RU/s throttling (429 errors)** | Medium | Medium | Use autoscale, implement retry logic |
| **Migration script failure** | Low | High | Run backfill in batches, save progress state |
| **Higher than expected costs** | Medium | Medium | Start with shared throughput, monitor daily |
| **Query performance regression** | Low | High | A/B test with Table Storage fallback |

### 7.2 Rollback Strategy

**If migration fails**:
1. Set feature flag: `COSMOS_DB_ENABLED=false`
2. All reads/writes revert to Table Storage immediately
3. No data loss (dual-write ensures both stores have data)
4. Investigate Cosmos DB issues offline
5. Re-attempt migration after fixes

**Data Retention**:
- Keep Table Storage for **30 days** after full migration
- Archive Table Storage snapshots to Blob Storage (AzCopy)
- Enable Cosmos DB **continuous backup** (point-in-time restore)

---

## 8. Testing Strategy

### 8.1 Integration Tests

**New test suite**: `tests/integration/test_cosmos_migration.py`

```python
def test_dual_write_consistency():
    """Verify writes go to both Table Storage and Cosmos DB."""
    job = JobMetadataRow(username="test", job_id="123", status="queued")
    
    with feature_flag("COSMOS_DB_ENABLED", "write_only"):
        table_manager.upsert_job_metadata(job)
    
    # Verify Table Storage
    table_result = table_client.get_entity("test", "123")
    assert table_result["status"] == "queued"
    
    # Verify Cosmos DB
    cosmos_result = cosmos_container.read_item("123", partition_key="test")
    assert cosmos_result["status"] == "queued"

def test_read_path_with_cosmos():
    """Verify reads use Cosmos DB when enabled."""
    with feature_flag("COSMOS_DB_ENABLED", "read_all"):
        result = table_manager.get_job_metadata("test", "123")
    
    # Should come from Cosmos (verify via logs or mock)
    assert result["status"] == "queued"
```

### 8.2 Performance Testing

**Load test**: Simulate 1000 concurrent admin dashboard queries

```bash
# Use Apache JMeter or k6
k6 run --vus 100 --duration 60s load_test.js

# Measure:
# - p50, p95, p99 latency
# - RU/s consumption
# - Error rate (429 throttling)
```

---

## 9. Monitoring & Observability

### 9.1 Metrics to Track

**Cosmos DB Metrics** (Azure Monitor):
- Request Units consumed (RU/s)
- Storage used (GB)
- Throttling rate (429 errors)
- Latency (p50, p95, p99)
- Availability (99.99% SLA)

**Application Insights**:
- Dual-write success/failure rate
- Read path latency (Table vs. Cosmos)
- Migration job progress
- Feature flag adoption (% of requests using Cosmos)

### 9.2 Alerts

```yaml
# Azure Monitor alert rules
alerts:
  - name: "High RU/s Consumption"
    metric: "TotalRequestUnits"
    threshold: 8000  # 80% of max autoscale
    window: 5m
    action: "Notify engineering team"
  
  - name: "Cosmos DB Throttling"
    metric: "TotalRequests"
    filter: "StatusCode == 429"
    threshold: 10
    window: 1m
    action: "Auto-scale RU/s"
  
  - name: "Dual-Write Failure"
    metric: "CustomMetric:CosmosWriteFailure"
    threshold: 5
    window: 5m
    action: "Page on-call engineer"
```

---

## 10. Post-Migration Optimization

### 10.1 Hierarchical Partition Keys

**Upgrade `RepoAPIUsage` to use HPK**:
```json
{
  "partitionKeyPaths": ["/username", "/yearMonth"],
  "kind": "MultiHash"
}
```

**Benefit**: Overcome 20GB limit per user, better query performance

### 10.2 Vector Search for AI Models

**Enable vector indexing** on `ModelMetadata`:
```python
# Index policy with vector index
{
  "vectorIndexes": [
    {
      "path": "/embedding",
      "type": "quantizedFlat"
    }
  ]
}

# Query similar models
query = """
    SELECT TOP 10 m.model_fingerprint, VectorDistance(m.embedding, @target_embedding) AS similarity
    FROM models m
    ORDER BY VectorDistance(m.embedding, @target_embedding)
"""
```

**Use Case**: "Find similar trained models" for transfer learning

### 10.3 Time-to-Live (TTL) for Auto-Cleanup

**Auto-delete old API usage records**:
```python
# RepoAPIUsage document
{
  "id": "freshness#2026-01-25#repo1",
  "username": "octocat",
  "operation": "freshness_check",
  "created_at": "2026-01-25T10:00:00Z",
  "ttl": 2592000  # 30 days in seconds (auto-delete after 30 days)
}
```

**Benefit**: Automatic data retention without manual cleanup jobs

---

## 11. Timeline & Milestones

| Week | Milestone | Deliverables | Risk |
|------|-----------|--------------|------|
| **1** | Cosmos DB setup | Create account, containers, indexes | Low |
| **2** | Dual-write implementation | Update `table_manager.py`, deploy to staging | Medium |
| **3** | Admin dashboard migration | Update admin endpoints to read from Cosmos | Medium |
| **4** | Public endpoint migration | Gradually expand Cosmos reads | High |
| **5** | Historical data backfill | Run backfill script, validate integrity | Medium |
| **6** | Table Storage decommission | Stop writes, archive data, delete tables | Low |

**Total Duration**: 6 weeks  
**Team Size**: 2 engineers (1 backend, 1 DevOps)

---

## 12. Success Criteria

✅ **Migration Complete When**:
1. All reads/writes use Cosmos DB exclusively
2. Table Storage tables archived to Blob Storage
3. Admin dashboard latency <100ms (p95)
4. RU/s consumption <5000 (avg)
5. Zero data loss or inconsistencies
6. Cost within $200/month budget

✅ **Quality Gates**:
- [ ] 100% integration test coverage for Cosmos paths
- [ ] Load test passes (1000 concurrent users, <200ms p95)
- [ ] Security review passed (RBAC, encryption at rest)
- [ ] Runbook documented (rollback, troubleshooting)
- [ ] Stakeholder sign-off (product, engineering, finance)

---

## 13. Appendix

### A. Cosmos DB vs. Table Storage Feature Matrix

| Feature | Table Storage | Cosmos DB |
|---------|---------------|-----------|
| **Latency** | 50-500ms | 5-10ms (single-digit) |
| **Throughput** | 20K ops/sec | Unlimited (autoscale) |
| **Consistency** | Strong (single region) | 5 levels (tunable) |
| **Indexing** | PartitionKey + RowKey only | Automatic, all fields |
| **Queries** | OData (limited) | SQL, Gremlin, MongoDB API |
| **Aggregation** | None (client-side only) | Server-side (GROUP BY, COUNT) |
| **Change Feed** | No | Yes (real-time notifications) |
| **Vector Search** | No | Yes (preview) |
| **Multi-region** | Read replicas only | Multi-master writes |
| **Cost** | $0.05/GB + $0.10/10K ops | $0.25/GB + RU/s pricing |

### B. Recommended Azure Cosmos DB Resources

- **Well-Architected Framework**: https://learn.microsoft.com/azure/well-architected/service-guides/cosmos-db
- **Partitioning Best Practices**: https://learn.microsoft.com/azure/cosmos-db/partitioning-overview
- **Change Feed**: https://learn.microsoft.com/azure/cosmos-db/change-feed
- **Vector Search**: https://learn.microsoft.com/azure/cosmos-db/vector-search

### C. Migration Script Repository

**GitHub**: `cloudfolio/cosmos-migration` (private repo)
- `scripts/backfill_cosmos.py` - Historical data migration
- `scripts/validate_consistency.py` - Data integrity checks
- `scripts/rollback.sh` - Emergency rollback procedure
- `tests/migration_tests.py` - Integration tests

---

**Document Version History**:
- v1.0 (2026-01-25): Initial migration plan
- Future revisions: Update based on PoC results, stakeholder feedback

**Next Steps**:
1. Review this plan with engineering team
2. Create Cosmos DB PoC (1 container, 1000 test docs)
3. Benchmark query performance vs. Table Storage
4. Get budget approval from finance
5. Schedule migration kickoff (target: Q1 2026)
