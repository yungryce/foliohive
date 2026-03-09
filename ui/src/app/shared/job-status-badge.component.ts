import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';

interface StatusConfig {
  label: string;
  color: string;
  icon: string;
}

const STATUS_LABELS: Record<string, StatusConfig> = {
  'queued': {
    label: 'Queued',
    color: 'bg-yellow-100 text-yellow-800',
    icon: '⏳'
  },
  'syncing': {
    label: 'Syncing repositories',
    color: 'bg-blue-100 text-blue-800',
    icon: '🔄'
  },
  'metadata_ready': {
    label: 'Processing files',
    color: 'bg-blue-100 text-blue-800',
    icon: '⚙️'
  },
  'caching_started': {
    label: 'Caching files',
    color: 'bg-blue-100 text-blue-800',
    icon: '💾'
  },
  'completed': {
    label: 'Ready',
    color: 'bg-green-100 text-green-800',
    icon: '✓'
  },
  'failed': {
    label: 'Failed',
    color: 'bg-red-100 text-red-800',
    icon: '✘'
  }
};

@Component({
  selector: 'app-job-status-badge',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div *ngIf="statusConfig || loading" class="inline-flex items-center gap-2 text-xs font-medium px-2 py-1 rounded" [ngClass]="statusConfig?.color">
      <span *ngIf="loading" class="inline-block w-2 h-2 rounded-full bg-current animate-pulse"></span>
      <span *ngIf="!loading" class="inline-block">{{ statusConfig?.icon }}</span>
      <span>{{ statusConfig?.label }}</span>
    </div>
  `,
  styles: []
})
export class JobStatusBadgeComponent {
  @Input() status: string | null = null;
  @Input() loading = false;

  get statusConfig(): StatusConfig | undefined {
    if (!this.status || !(this.status in STATUS_LABELS)) {
      return undefined;
    }
    return STATUS_LABELS[this.status];
  }
}
