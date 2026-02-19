#sym:get_standard_config_files is used to retrieve #sym:STANDARD_CONFIG_FILE_CANDIDATES using #sym:discover_repo_files 

We are cleaning up data retrieved and data retrieved for config files can be improved to extract only files needed. for example, dependency fields can be extracted diectly without extracting full file with non relevant detail


------------------------------------------------------------------

Response truncated (hit max_completion_tokens=6000). We are over-feeding context ~45k and under-constraining output ~6k.

| Use Case      | Input     | Notes     | Suggestion       |
| **Repo Summary**    | Single repo README, metadata, config                      | Can allow **more context per repo** since it’s only one repo | Stage 1: Extraction → Stage 2: Interpretation → Stage 3: Presentation. This is effectively a **short pipeline per repo**.                                                      |
| **Profile Summary** | Multiple repos’ metadata, summaries, language/config data | Already compressed via repo summaries. Needs aggregation     | Stage 1: Aggregate repo summaries → Stage 2: Cross-repo interpretation/evaluation → Stage 3: Profile presentation. Don’t reprocess raw repo content; work from repo summaries. |
| **Query Summary**   | User query + profile summary                              | Queries should operate on already compressed data            | Stage 1: Filter/retrieve relevant repo summaries → Stage 2: Query-focused evaluation → Stage 3: Query response presentation.                                                   |

[Repo Summary Pipeline]          <- per repo
Extraction -> Interpretation -> Presentation (optional)
[Profile Summary Pipeline]       <- multi-repo
Input: repo summaries
Aggregation -> Evaluation -> Presentation (HTML / profile card)
[Query Summary Pipeline]         <- user query
Input: profile summary / repo summaries
Filter -> Query evaluation -> Presentation (Answer)

- Repo Summary: single short pipeline per repo (can be parallelized)
- Profile Summary: multi-stage aggregation pipeline consuming repo summaries
- Query Summary: lightweight multi-stage pipeline consuming already compressed data
Important: Early extraction must happen once per repo; all downstream stages work on structured, compressed data.

Recommendations / Best Practices
1. Treat repo summarization as a standalone early-stage process.
    - This prevents recomputation.
    - Allows max input context per repo without blowing up multi-repo pipelines.
2. Profile and query summaries should be multi-stage pipelines that operate on repo summaries.
    - Use aggregation, evaluation, and presentation stages.
3. Token budgets should shrink at each stage.
    - Repo summaries: allow richer input
    - Profile/query: input is compressed, output concise
4. Parallelize repo summaries where possible.
    - Reduces wall-time latency for profile generation
5. Keep presentation separate.
    - Flair, formatting, and recruiter-facing output should happen last.
5. Store/reuse intermediate summaries in Azure Storage for cost and latency efficiency.

Conclusion:
Repo summary = independent process/stage per repo
Profile summary = multi-stage pipeline aggregating repo summaries
Query summary = multi-stage pipeline on compressed data
Multi-stage pipelines start at repo summary for signal extraction and continue downstream for aggregation/evaluation/presentation.


Output Constraints
Maximum defined token budget
Maximum sections allowed
Maximum highlighted repositories
No new inferred skills
No deviation from evaluation output


| Stage           | Target Input | Target Output |
| --------------- | ------------ | ------------- |
| Repo summary    | 10–20k       | 1–2k          |
| Skill inference | 3–6k         | 800–1200      |
| Aggregation     | 5–10k        | 1–2k          |
| HTML            | 5k           | 2–3k          |

| Pipeline        | Input context                                      | Output target | Notes                               |
| --------------- | -------------------------------------------------- | ------------- | ----------------------------------- |
| Repo Summary    | Full README + metadata + configs (~10–12k tokens)  | 1–2k tokens   | More per-repo context allowed       |
| Profile Summary | Compressed repo summaries (~5k per repo × N repos) | 2–3k          | Aggregate, deduplicate, rank skills |
| Query Summary   | Filtered profile summaries (~5k–10k total)         | 1–2k          | Generate query-focused answer       |
Key: Stage 1 Extraction is heavier for repo summaries, lighter for profile/query since input is already compressed.

Early Stage Recommendation
- Always add an early per-repo summary stage for any multi-repo pipeline (profile or query).
- This ensures:
    - Pure signal extraction happens once per repo
    - Cross-repo evaluation works on compact, deterministic summaries
    - Multi-stage token usage is controlled
- Without it, profile or query summarization would need to process full raw repo content each time → costly, high latency, high truncation risk


- Output must be concise and fit within a short profile card.
- Prefer summarization over completeness.
- Never describe every repository individually unless critical.
- The final HTML must not exceed 2000 tokens.
- Limit project descriptions to 3–4 sentences per project.
- Do not repeat information.
- Be concise and structured.
- Maximum total output length: 2500 tokens.
- Maximum 5 sections.
- Maximum 4 repositories mentioned.
- Maximum 3 sentences per repository.
- Use concise bullet lists when possible.
- Do NOT repeat technologies.
- Do NOT explain obvious concepts.
- Do NOT restate input metadata.
- Prefer aggregation over enumeration.
- Only infer a skill if explicit evidence exists in the provided repository data.
If uncertain, lower confidence.
- Based strictly on provided data, list observed architecture patterns



will we benefit from further optimizing STANDARD_CONFIG_FILE_CANDIDATES and extracting file contents 
- target standard files relevant to extract context for a repository across languages, platforms, and tech stacks
- extract only relevant content from files thereby compressing context input data