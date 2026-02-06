import { inject } from '@angular/core';
import { Router, CanActivateFn } from '@angular/router';
import { AuthService } from '../services/auth.service';

/**
 * Authentication guard - requires any authenticated user
 * 
 * Usage in routes:
 * {
 *   path: 'dashboard',
 *   component: DashboardComponent,
 *   canActivate: [authGuard]
 * }
 */
export const authGuard: CanActivateFn = (route, state) => {
  const authService = inject(AuthService);
  const router = inject(Router);

  // Check if user is authenticated
  if (authService.isUserAuthenticated()) {
    return true;
  }

  // Not authenticated - redirect to login
  authService.login('google', state.url); // Redirect back after login
  return false;
};
