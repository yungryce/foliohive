Goal: 

- `ui`: refactor opportunities in `ui/src/app` for clear seperation of concerns and implementation precision and synchronization with `api/v0.3.0/function-app/blueprints/api_gateway.py` data responses. repeated methods collocated in a shared view/services
- `api`: refactor opportunities in `api/v0.3.0/function-app/blueprints/api_gateway.py` for clear seperation of concerns, optimized data retrieval for client views, reduced code duplication, and improved maintainability.


Context:

Client has 4 views that retrives and displays data from the server. These views are:
- `ui/src/app/profile`: This expects 2 separate data responses. metadat
    - Metadata-`api/v0.3.0/shared/src/foliohive_shared/table/table_manager.py`: candidate aggregated data returned via `api_gateway.get_profiles()`
    - Summary-`api/v0.3.0/shared/src/foliohive_shared/ai/summary_manager.py`:  ai summary returned via `api_gateway.get_profile_summary()`
- `ui/src/app/projects`: This expects 1 data response
    - Metadata-`api/v0.3.0/shared/src/foliohive_shared/table/table_manager.py`: candidate per repo metadata used to display repo cards `api_gateway.get_candidate_repos_metadata()`.
- `ui/src/app/projects/project`: This expects 2 data responses
    - Metadata-`api/v0.3.0/shared/src/foliohive_shared/table/table_manager.py`: candidate per repo metadata used to display repo details `api_gateway.get_candidate_repo_metadata()`. This is a missing gap that has not been implemented
    - Summary-`api/v0.3.0/shared/src/foliohive_shared/ai/summary_manager.py`: ai summary of repo details via `api_gateway.get_repo_summary()`
- `ui/src/app/ai`: This expects 1 data response
    - Summary-`api/v0.3.0/shared/src/foliohive_shared/ai/summary_manager.py`: ai summary provided based on user query. All of candidate's data is used as context for query via `api_gateway.portfolio_query()`

Status Checks: 
- `getJobStatus`: This works alongside `api_gateway.get_job_status()`
- `getCacheStatus`: This function checks the cache status for a given candidate. It retrieves the candidate's metadata and summary from the API gateway and determines if the data is still being processed or if it is ready for use. The function returns an object containing the cache status, which can be used to inform the user about the availability of the data.



Is a api-gateway optimized for these data retrieval or there are room for improvments?
Is polling a good design pattern for our use case?
Service structuring and function file locations, does this signify clear seperation of concerns. 
Are there redundant operations in api-gateway that should be optimized. 
are there repeating operations across several functions that can be abstracted into a single function to reduce code duplication and improve maintainability?