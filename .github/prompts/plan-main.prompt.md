Project

App user: Recruiter

Scope: A GitHub account reviewer tool

Usage: A reviewer would provide the username of a GitHub account. Public Repositories of the provided username is retrieved with their file contents and stored for processing

Current implementation approach: File contents of each repository is retrieved, bundled together and signed per repository and as a single bundle for all repositories 

Response to User (Recruiter): ai/agent provides best fitting response based on user query vs public Repositories of provided username processed to provide relevant information

Knowledge gap:
- ~~How to process user query against username's repositories~~ **SOLVED**: Hybrid search (languages.yml keyword extraction + semantic ranking)
- ~~Best tools to use~~ **SOLVED**: DynamicKeywordExtractor (uses GitHub-maintained languages.yml) + Fine-tuned SentenceTransformer

Core problem: Data is unstructured. There are various types of files, file contents, directory depth and/or counts

**Solution**: Use STANDARD repository metadata (GitHub API + README + Topics) instead of full file contents or custom schemas

Problem to be solved: Efficiently retrieve repositories of provided username, process and transform data against user's query, use user's query against processed data and provide the best fitting response back to User

Goal: Improve data retrieval and design a data processing model that provides a response to the user using ai/agent tools

**Approach**:
1. **Keyword Extraction**: Use `languages.yml` (650+ languages, zero maintenance) to extract technology keywords from queries
2. **Hybrid Search**: Fast keyword pre-filter (100 repos → 15 repos) + semantic ranking (<600ms total)
3. **Standard Files Only**: README + GitHub metadata (no custom .repo-context.json required)

Sample question:
"Does username have cloud skills": check for cloud specific sdks, implementations, file types, configurations, resources, match these to repositories and provide skills and repositories that shows these skills and depth of these skills

Tools: 
- Function app
- Azure storage account

Plan Files:
- .github/prompts/plan-multiAppArchitecture.prompt.md
- .github/prompts/plan-semanticModelTrainingRefactor.prompt.md
- .github/prompts/plan-azureStorageQueuesArchitecture.prompt.md
- .github/prompts/plan-dataRetrievalModel.prompt.md
- .github/prompts/plan-dataProcessingModel.prompt.md

Concerns (ANSWERED):

**Q: When data is retrieved and available in storage, if a chat assistant provides queries from a user based on their usernames, what would be the efficient way to clean up bundled repositories data of that user?**

A: Use TTL-based cache expiration (24-48 hours). Bundle cache automatically invalidates when repositories update (fingerprint mismatch). No manual cleanup needed.

**Q: Is this expensive and/or introduces unnecessary technical debt?**

A: No. Using standard GitHub metadata (languages, topics, README) eliminates custom schema maintenance. Cost: ~$0.50/month for Azure Storage Queues + blob storage.

**Q: How would a relevant repository be determined?**

A: 2-stage hybrid approach:
1. **Keyword Pre-Filter** (<20ms): Match query against GitHub languages, topics, file extensions via languages.yml
2. **Semantic Ranking** (<500ms): Fine-tuned model ranks filtered repos by relevance

**Q: Can I use agents to retrieve relevant Repositories instead?**

A: No need for agents. Rule-based keyword extraction (languages.yml) achieves 90% accuracy with zero infrastructure cost vs LLM agents (95% accuracy, $$$, 2-5s latency).

**Q: Would ETL fit?**

A: No. Real-time query processing (hybrid search) is better suited than batch ETL. Users expect <1s responses, not pre-computed results. 

Guidelines
- Always strive to reduce complexity 
- Do not introduce technical debt unless absolutely necessary. Always strive to remove technical debt and not introduce new ones
- When my query has a question mark, answer the question first 