import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { AuthService } from '../services/auth.service';
import { RepoBundleService } from '../services/repo-bundle.service';

interface UserStats {
  totalJobs: number;
  completedJobs: number;
  totalRepos: number;
  lastActivity: string | null;
}

interface RecentJob {
  job_id: string;
  status: string;
  created_at: string;
  repo_count: number;
}

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './dashboard.component.html',
  styleUrls: ['./dashboard.component.css']
})
export class DashboardComponent implements OnInit {
  // Reactive state
  public readonly stats = signal<UserStats>({
    totalJobs: 0,
    completedJobs: 0,
    totalRepos: 0,
    lastActivity: null
  });
  
  public readonly recentJobs = signal<RecentJob[]>([]);
  public readonly loading = signal<boolean>(true);
  public readonly error = signal<string | null>(null);
  
  public readonly username = signal<string | null>(null);

  constructor(
    private authService: AuthService,
    private repoBundleService: RepoBundleService
  ) {}

  ngOnInit(): void {
    const user = this.authService.getCurrentUser();
    if (user) {
      this.username.set(user.email.split('@')[0]); // Extract username from email
      this.loadDashboardData();
    } else {
      this.error.set('User not authenticated');
      this.loading.set(false);
    }
  }

  /**
   * Load user-specific dashboard data
   */
  private loadDashboardData(): void {
    this.loading.set(true);
    this.error.set(null);

    // TODO: Replace with actual API endpoint when implemented
    // For now, use mock data
    setTimeout(() => {
      this.stats.set({
        totalJobs: 12,
        completedJobs: 8,
        totalRepos: 156,
        lastActivity: new Date().toISOString()
      });
      
      this.recentJobs.set([
        {
          job_id: '123e4567-e89b-12d3-a456-426614174000',
          status: 'completed',
          created_at: new Date(Date.now() - 3600000).toISOString(),
          repo_count: 42
        },
        {
          job_id: '223e4567-e89b-12d3-a456-426614174001',
          status: 'metadata_ready',
          created_at: new Date(Date.now() - 7200000).toISOString(),
          repo_count: 38
        },
        {
          job_id: '323e4567-e89b-12d3-a456-426614174002',
          status: 'syncing',
          created_at: new Date(Date.now() - 10800000).toISOString(),
          repo_count: 50
        }
      ]);
      
      this.loading.set(false);
    }, 1000);
  }

  /**
   * Refresh dashboard data
   */
  refresh(): void {
    this.loadDashboardData();
  }

  /**
   * Navigate to projects view
   */
  viewProjects(): void {
    window.location.href = '/projects';
  }

  /**
   * Get status badge color
   */
  getStatusColor(status: string): string {
    switch (status) {
      case 'completed': return 'green';
      case 'metadata_ready': return 'blue';
      case 'syncing': return 'yellow';
      case 'failed': return 'red';
      default: return 'gray';
    }
  }

  /**
   * Format date to relative time
   */
  formatRelativeTime(isoDate: string): string {
    const date = new Date(isoDate);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    
    if (diffMins < 1) return 'just now';
    if (diffMins < 60) return `${diffMins}m ago`;
    
    const diffHours = Math.floor(diffMins / 60);
    if (diffHours < 24) return `${diffHours}h ago`;
    
    const diffDays = Math.floor(diffHours / 24);
    return `${diffDays}d ago`;
  }
}
