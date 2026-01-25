import { Injectable, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, BehaviorSubject, of } from 'rxjs';
import { tap, catchError, map } from 'rxjs/operators';

export interface ClientPrincipal {
  identityProvider: string;
  userId: string;
  userDetails: string;
  userRoles: string[];
}

export interface AuthResponse {
  clientPrincipal: ClientPrincipal | null;
}

export interface UserProfile {
  username: string;
  email: string;
  provider: string;
  roles: string[];
  isAuthenticated: boolean;
  isAdmin: boolean;
}

@Injectable({
  providedIn: 'root'
})
export class AuthService {
  private readonly AUTH_ME_ENDPOINT = '/.auth/me';
  
  // Reactive state using Angular signals
  public readonly user = signal<UserProfile | null>(null);
  public readonly isAuthenticated = signal<boolean>(false);
  public readonly isAdmin = signal<boolean>(false);
  
  // Legacy BehaviorSubject for backward compatibility
  private userSubject = new BehaviorSubject<UserProfile | null>(null);
  public user$ = this.userSubject.asObservable();

  constructor(private http: HttpClient) {}

  /**
   * Initialize authentication by fetching user identity from /.auth/me
   * Call this once during app initialization
   */
  initializeAuth(): Observable<UserProfile | null> {
    return this.http.get<AuthResponse>(this.AUTH_ME_ENDPOINT).pipe(
      map(response => {
        if (response.clientPrincipal) {
          const profile: UserProfile = {
            username: response.clientPrincipal.userDetails,
            email: response.clientPrincipal.userDetails, // Google uses email as userDetails
            provider: response.clientPrincipal.identityProvider,
            roles: response.clientPrincipal.userRoles || [],
            isAuthenticated: true,
            isAdmin: this.checkAdminRole(response.clientPrincipal.userRoles)
          };
          
          this.updateUserState(profile);
          return profile;
        }
        
        // Not authenticated (anonymous user)
        this.updateUserState(null);
        return null;
      }),
      catchError(error => {
        console.error('[AuthService] Failed to fetch user identity:', error);
        this.updateUserState(null);
        return of(null);
      })
    );
  }

  /**
   * Update user state across all reactive patterns
   */
  private updateUserState(profile: UserProfile | null): void {
    this.user.set(profile);
    this.isAuthenticated.set(!!profile);
    this.isAdmin.set(profile?.isAdmin || false);
    this.userSubject.next(profile);
  }

  /**
   * Check if user has admin role
   */
  private checkAdminRole(roles: string[]): boolean {
    return roles.includes('admin');
  }

  /**
   * Check if user has specific role
   */
  hasRole(role: string): boolean {
    const currentUser = this.user();
    return currentUser?.roles.includes(role) || false;
  }

  /**
   * Check if user is authenticated (has any role beyond 'anonymous')
   */
  isUserAuthenticated(): boolean {
    return this.isAuthenticated();
  }

  /**
   * Check if user is admin
   */
  isUserAdmin(): boolean {
    return this.isAdmin();
  }

  /**
   * Navigate to login page
   */
  login(provider: 'google' = 'google', redirect?: string): void {
    const redirectUrl = redirect || window.location.pathname;
    window.location.href = `/.auth/login/${provider}?post_login_redirect_uri=${encodeURIComponent(redirectUrl)}`;
  }

  /**
   * Navigate to logout page
   */
  logout(redirect?: string): void {
    const redirectUrl = redirect || '/';
    window.location.href = `/.auth/logout?post_logout_redirect_uri=${encodeURIComponent(redirectUrl)}`;
  }

  /**
   * Get current user profile (signal value)
   */
  getCurrentUser(): UserProfile | null {
    return this.user();
  }

  /**
   * Refresh user identity (re-fetch from /.auth/me)
   */
  refreshAuth(): Observable<UserProfile | null> {
    return this.initializeAuth();
  }
}
