import { inject } from '@angular/core';
import { Router, CanActivateFn } from '@angular/router';
import { AuthService } from '../services/auth.service';

/**
 * Admin route guard - requires 'admin' role
 * 
 * Usage in routes:
 * {
 *   path: 'admin',
 *   component: AdminComponent,
 *   canActivate: [adminGuard]
 * }
 */
export const adminGuard: CanActivateFn = (route, state) => {
  const authService = inject(AuthService);
  const router = inject(Router);

  // Check if user is authenticated and has admin role
  if (authService.isUserAdmin()) {
    return true;
  }

  // Not authenticated - redirect to login
  if (!authService.isUserAuthenticated()) {
    authService.login('google', state.url); // Redirect back after login
    return false;
  }

  // Authenticated but not admin - redirect to home with error
  router.navigate(['/'], { 
    queryParams: { error: 'insufficient_permissions' }
  });
  return false;
};
