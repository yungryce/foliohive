"""Cloudfolio consolidated Function App (v0.3.0).

This Function App hosts:
- HTTP API routes (former api-gateway)
- Queue-trigger workers (sync-worker, cache-worker, reconciliation-worker)

Note: merge-worker removed - job completion now handled by cache-worker.

Logical separation is maintained via Azure Functions Blueprints.
"""

from __future__ import annotations

import azure.functions as func

from blueprints.api_gateway import bp as api_gateway_bp
from blueprints.cache_worker import bp as cache_worker_bp
from blueprints.reconciliation_worker import bp as reconciliation_worker_bp
from blueprints.sync_worker import bp as sync_worker_bp

app = func.FunctionApp()

_register = getattr(app, "register_blueprint", None) or getattr(app, "register_functions", None)
if _register is None:  # pragma: no cover
    raise AttributeError("azure.functions.FunctionApp missing blueprint registration method")

_register(api_gateway_bp)
_register(sync_worker_bp)
_register(cache_worker_bp)
_register(reconciliation_worker_bp)
