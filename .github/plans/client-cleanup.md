raw profile → extract signals → summarize signals → render HTML


MAX_OUTPUT_BY_TASK = {
    "initial_summary": 1200,
    "repo_summary": 2000,
    "skill_extract": 1200,
    "profile_html": 3000,      # important
    "deep_analysis": 8000,
}


Output must be concise and fit within a short profile card.
Prefer summarization over completeness.
Never describe every repository individually unless critical.