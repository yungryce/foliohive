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

lets add add a destroy method to #file:table_manager.py that deletes all tables
we would alos add a destroy method to #file:cache_manager.py that deletes all container (_DEFAULT_CONTAINER = "github-cache")
This is for debugging purposes toease starting afresh ( i currently have to manually delete tables and contsiners)

we would add an api trigger to #file:api_gateway.py and a service to #file:repo-bundle.service.ts that deletes tables and container 

add a button to #file:landing that calls service and trigger