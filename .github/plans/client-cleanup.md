- `ui`: refactor opportunities in `ui/src/app` for clear seperation of concerns and implementation precision and synchronization with `api/v0.3.0/function-app/blueprints/api_gateway.py` data responses. Each service in `foliohive/ui/src/app/services` should serve its component. shared services should be in a shared location


 Critical Path Optimization
Rule: Never block critical content behind non-critical operations.

Data Type	Speed	Critical?	Load Strategy
Repository metadata	Fast (50-200ms)	✅ Critical	Load immediately
Profile metadata	Fast (50-200ms)	✅ Critical	Load immediately
Repository list	Fast (100-300ms)	✅ Critical	Load immediately
AI summaries	Slow (2-10s)	❌ Enhancement	Load progressively
File cache	Async (varies)	❌ Background	Poll/wait as needed