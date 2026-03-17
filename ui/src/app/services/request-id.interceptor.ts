import { HttpInterceptorFn } from '@angular/common/http';

function generateRequestId(): string {
  try {
    if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
      return crypto.randomUUID();
    }
  } catch {
    // ignore
  }
  // Fallback: not cryptographically strong, but sufficient for local correlation.
  return `req_${Math.random().toString(16).slice(2)}_${Date.now().toString(16)}`;
}

export const requestIdInterceptor: HttpInterceptorFn = (req, next) => {
  // Always generate a new request id per outgoing HTTP call.
  // If the caller already set one explicitly, preserve it.
  const requestId = req.headers.get('X-Request-Id') || generateRequestId();

  // Trace id is used for cross-hop correlation (API -> queues -> workers).
  // Default it to the request id unless a caller provided a different trace id.
  const traceId = req.headers.get('X-Trace-Id') || requestId;

  return next(
    req.clone({
      setHeaders: {
        'X-Request-Id': requestId,
        'X-Trace-Id': traceId,
      },
    })
  );
};
