diff --git a/.github/TABLE_NORMALIZATION_ANALYSIS.md b/.github/TABLE_NORMALIZATION_ANALYSIS.md
index f203db2a..c04c64d4 100644
--- a/.github/TABLE_NORMALIZATION_ANALYSIS.md
+++ b/.github/TABLE_NORMALIZATION_ANALYSIS.md
@@ -22,6 +22,7 @@ Current architecture violates multiple normalization principles:
 ## Table-by-Table Analysis
 
 ### 1. JobMetadataRow (JobMetadata Table)
+This table has been **normalized**
 
 **PartitionKey:** `username`  
 **RowKey:** `job_id`
@@ -113,6 +114,7 @@ class JobMetadataRow:
 ---
 
 ### 2. SessionCandidateRow (SessionCandidates Table)
+This table has been **normalized**
 
 **PartitionKey:** `session_id`  
 **RowKey:** `username`
@@ -140,8 +142,6 @@ class JobMetadataRow:
 
 #### Recommended Schema (No Changes Needed)
 
-This table is **already well-normalized**. Only improvement needed is FK validation:
-
 ```python
 def upsert_session_candidate(self, session_id: str, username: str, job_id: Optional[str]) -> None:
     # Add FK validation
@@ -156,6 +156,7 @@ def upsert_session_candidate(self, session_id: str, username: str, job_id: Optio
 ---
 
 ### 3. RepoMetadataRow (RepoMetadata Table)
+This table has been **normalized**
 
 **PartitionKey:** `username`  
 **RowKey:** `repo_name`
@@ -304,6 +305,7 @@ class RepoAPIUsageRow:
 ---
 
 ### 4. RepoSyncStatusRow (RepoSyncStatus Table)
+This table is **accepted normalized**
 
 **PartitionKey:** `job_id`  
 **RowKey:** `repo_name`
@@ -359,6 +361,7 @@ class RepoSyncStatusRow:
 ---
 
 ### 5. ModelMetadataRow (ModelMetadata Table)
+**Skip this table**
 
 **PartitionKey:** `username`  
 **RowKey:** `fingerprint`
diff --git a/api/v0.3.0/shared/src/cloudfolio_shared/table/table_manager.py b/api/v0.3.0/shared/src/cloudfolio_shared/table/table_manager.py
index 9ca1875a..05c8cb73 100644
--- a/api/v0.3.0/shared/src/cloudfolio_shared/table/table_manager.py
+++ b/api/v0.3.0/shared/src/cloudfolio_shared/table/table_manager.py
@@ -309,6 +309,18 @@ class TableManager:
         if not session_id or not username:
             return
 
+        # Validate FK: if job_id provided, verify it exists in JobMetadata
+        if job_id:
+            job = self.get_job_metadata(username, job_id)
+            if not job:
+                raise ValueError(f"Invalid job_id '{job_id}' for user '{username}' - job not found in JobMetadata")
+            logger.info(
+                "[TABLE_VALIDATE_SESSION_FK] session=%s user=%s job=%s - FK validation passed",
+                session_id,
+                username,
+                job_id,
+            )
+
         now = _utcnow_iso()
         existing_count = 0
         created_at = now
@@ -329,6 +341,13 @@ class TableManager:
             "updated_at": now,
         }
         table.upsert_entity(entity, mode=UpdateMode.MERGE)
+        logger.info(
+            "[TABLE_UPSERT_SESSION_CANDIDATE] session=%s user=%s job=%s query_count=%d",
+            session_id,
+            username,
+            job_id or "<none>",
+            existing_count + 1,
+        )
 
     def list_session_candidates(self, session_id: str, *, limit: int = 10) -> List[Dict[str, Any]]:
         table = self._get_table_client(self.table_names.session_candidates)
