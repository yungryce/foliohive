import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';

interface JobStats {
  queued: number;
  syncing: number;
  metadata_ready: number;
  completed: number;
  failed: number;
}

interface APIUsageStats {
  total_rest_calls: number;
  total_graphql_calls: number;
  cache_hit_rate: number;
  rate_limit_remaining: number;
}

@Component({
  selector: 'app-monitoring',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './monitoring.component.html',
  styleUrls: ['./monitoring.component.css']
})
export class MonitoringComponent implements OnInit {
  public readonly jobStats = signal<JobStats>({
    queued: 0,
    syncing: 0,
    metadata_ready: 0,
    completed: 0,
    failed: 0
  });

  public readonly apiUsageStats = signal<APIUsageStats>({
    total_rest_calls: 0,
    total_graphql_calls: 0,
    cache_hit_rate: 0,
    rate_limit_remaining: 5000
  });

  public readonly loading = signal<boolean>(true);
  public readonly lastRefresh = signal<Date>(new Date());

  ngOnInit(): void {
    this.loadMonitoringData();
    
    // Auto-refresh every 30 seconds
    setInterval(() => {
      this.loadMonitoringData();
    }, 30000);
  }

  private loadMonitoringData(): void {
    this.loading.set(true);

    // TODO: Replace with actual API endpoint
    // GET /api/admin/metrics/job-stats
    // GET /api/admin/metrics/api-usage
    
    setTimeout(() => {
      this.jobStats.set({
        queued: 12,
        syncing: 5,
        metadata_ready: 8,
        completed: 342,
        failed: 3
      });

      this.apiUsageStats.set({
        total_rest_calls: 1248,
        total_graphql_calls: 52,
        cache_hit_rate: 68.5,
        rate_limit_remaining: 4821
      });

      this.lastRefresh.set(new Date());
      this.loading.set(false);
    }, 1000);
  }

  refresh(): void {
    this.loadMonitoringData();
  }

  getTotalJobs(): number {
    const stats = this.jobStats();
    return stats.queued + stats.syncing + stats.metadata_ready + stats.completed + stats.failed;
  }

  getSuccessRate(): number {
    const stats = this.jobStats();
    const total = this.getTotalJobs();
    return total > 0 ? (stats.completed / total) * 100 : 0;
  }
}
