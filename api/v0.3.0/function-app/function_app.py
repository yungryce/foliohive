"""Cloudfolio consolidated Function App (v0.3.0).

This Function App hosts:
- HTTP API routes (former api-gateway)
- Queue-trigger workers (former sync-worker + merge-worker)

Logical separation is maintained via Azure Functions Blueprints.
"""

from __future__ import annotations

import azure.functions as func

from blueprints.api_gateway import bp as api_gateway_bp
from blueprints.merge_worker import bp as merge_worker_bp
from blueprints.sync_worker import bp as sync_worker_bp

app = func.FunctionApp()

_register = getattr(app, "register_blueprint", None) or getattr(app, "register_functions", None)
if _register is None:  # pragma: no cover
	raise AttributeError("azure.functions.FunctionApp missing blueprint registration method")

_register(api_gateway_bp)
_register(sync_worker_bp)
_register(merge_worker_bp)
