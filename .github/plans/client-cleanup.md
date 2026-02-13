- `ui`: refactor opportunities in `ui/src/app` for clear seperation of concerns and implementation precision and synchronization with `api/v0.3.0/function-app/blueprints/api_gateway.py` data responses. Each service in `foliohive/ui/src/app/services` should serve its component. shared services should be in a shared location
- `api`: refactor opportunities in `api/v0.3.0/function-app/blueprints/api_gateway.py` for clear seperation of concerns, optimized data retrieval for client views, reduced code duplication, and improved maintainability.


Is a api-gateway optimized for these data retrieval or there are room for improvments?
Is polling a good design pattern for job and cache status use case? is status functions optimized?
Are there redundant operations in api-gateway that should be optimized.?
are there repeating operations across several functions that can be abstracted into a single function to reduce code duplication and improve maintainability?

Design goals:
- No repeated operations across functions 