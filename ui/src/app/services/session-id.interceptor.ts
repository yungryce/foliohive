import { inject } from '@angular/core';
import { HttpInterceptorFn } from '@angular/common/http';
import { SessionIdService } from './session-id.service';

export const sessionIdInterceptor: HttpInterceptorFn = (req, next) => {
  const sessionId = inject(SessionIdService).getOrCreate();
  return next(
    req.clone({
      setHeaders: {
        'X-Session-Id': sessionId,
      },
    })
  );
};
