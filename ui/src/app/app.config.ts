import { ApplicationConfig, provideZoneChangeDetection, importProvidersFrom, provideAppInitializer, inject } from '@angular/core';
import { provideRouter } from '@angular/router';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { MarkdownModule } from 'ngx-markdown';
import { ConfigService } from './services/config.service';
import { AuthService } from './services/auth.service';
import { firstValueFrom } from 'rxjs';
import { sessionIdInterceptor } from './services/session-id.interceptor';
import { requestIdInterceptor } from './services/request-id.interceptor';

import { routes } from './app.routes';

export const appConfig: ApplicationConfig = {
  providers: [
    provideZoneChangeDetection({ eventCoalescing: true }), 
    provideRouter(routes),
    provideHttpClient(withInterceptors([sessionIdInterceptor, requestIdInterceptor])),
    importProvidersFrom(
      MarkdownModule.forRoot()
    ),
    provideAppInitializer(() => {
      const authService = inject(AuthService);
      
      // Initialize authentication on app startup
      // This fetches user identity from /.auth/me endpoint
      return firstValueFrom(authService.initializeAuth());
    }),
    provideAppInitializer(() => {
      const configService = inject(ConfigService);
      // const preFetchService = inject(PreFetchService);
      
      // // Load config first, then initialize prefetching
      // return firstValueFrom(configService.loadConfig()).then(() => {
      //   preFetchService.initialize();
      // });
    })
  ]
};

