import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
import { RepoBundleService, RepoBundleResponse, JobStatusResponse } from '../services/repo-bundle.service';
import { CandidateContextService } from '../services/candidate-context.service';
import { CandidateListComponent } from '../shared/candidate-list.component';
import { Observable, map, of, Subject, switchMap, takeUntil, takeWhile, tap, timer } from 'rxjs';

/**
 * Aligned with backend schema from _repo_row_to_bundle_entry in api_gateway.py
 */
interface RepoCardVM {
  name: string;
  description?: string;
  languagesPct: { k: string; pct: number }[];
  updatedAt?: string;
  htmlUrl?: string;
  stars: number;
  forks: number;
  topics: string[];
  isFork: boolean;
  isArchived: boolean;
}

@Component({
  selector: 'app-projects',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterModule, CandidateListComponent],
  templateUrl: './projects.component.html',
  styleUrls: ['./projects.component.css']
})
export class ProjectsComponent implements OnInit, OnDestroy {
  private repoBundleService = inject(RepoBundleService);
  private candidateContext = inject(CandidateContextService);
  private readonly destroy$ = new Subject<void>();
  repoBundle$!: Observable<RepoBundleResponse>;
  filteredRepos$!: Observable<RepoCardVM[]>;
  filterByDocumentation = false; 
  username = '';
  missingCandidate = false;

  // Filter options
  showForks = false;
  selectedLanguage = '';
  selectedTechnology = '';
  searchTerm = '';

  // Sorting
  sortBy = 'updated';
  sortDirection = 'desc';

  // Available filter options
  allLanguages: string[] = [];
  allTechnologies: string[] = [];

  // Loading state
  loading = false;
  loadingMessage = '';

  // Building state
  building = false;
  buildMessage = '';
  bundleEmpty = false;

  ngOnInit(): void {
    this.candidateContext.activeUsername$
      .pipe(takeUntil(this.destroy$))
      .subscribe((username) => {
        if (!username) {
          this.missingCandidate = true;
          this.repoBundle$ = of({ username: '', data: [] });
          this.filteredRepos$ = of([]);
          return;
        }

        this.missingCandidate = false;
        this.username = username;
        this.loadRepoBundle();
      });
  }

  ngOnDestroy(): void {
    this.destroy$.next();
    this.destroy$.complete();
  }

  triggerBuild(): void {
    if (this.building) return;
    this.building = true;
    this.buildMessage = 'Starting build… This may take a few minutes.';
    this.repoBundleService.startBuild(this.username, true).subscribe({
      next: (response) => {
        const jobId = response.job_id;
        if (!jobId) {
          this.building = false;
          this.buildMessage = 'Build started but no job ID returned.';
          return;
        }

        this.candidateContext.upsertCandidate({ username: this.username });

        // Poll job status API for progress updates (up to 120 seconds)
        timer(0, 5000).pipe(
          takeWhile((_, i) => i < 24), // 24*5s = 2 minutes max
          switchMap(() => this.repoBundleService.getJobStatus(this.username, jobId)),
          takeWhile((status) => {
            // Continue polling until completed/failed or null
            if (!status) return false;
            return status.status !== 'completed' && status.status !== 'failed';
          }, true) // inclusive=true to process final completed status
        ).subscribe({
          next: status => {
            if (!status) {
              this.building = false;
              this.buildMessage = 'Job status unavailable.';
              return;
            }

            // Update progress message with detailed breakdown
            const { completed = 0, total = 0, cached = 0, synced = 0, pending = 0, failed = 0 } = status.progress || {};
            this.buildMessage = `Building… ${status.progress?.percentage ?? 0}% (${cached} ready, ${synced} syncing, ${pending} pending, ${failed} failed)`;

            // Load/refresh data progressively as repos become available
            if (status.metadata_ready) {
              console.log('[ProjectsComponent] Loading available repos (status=%s, cached=%d)', status.status, cached);
              this.loadRepoBundle();
            }

            // Stop building state only when fully completed or failed
            if (status.files_ready || status.status === 'completed') {
              this.building = false;
              this.buildMessage = `Build complete! ${cached} repositories ready.`;
              console.log('[ProjectsComponent] Build completed, loading final bundle');
              this.loadRepoBundle(); // Final reload to ensure bundle_fingerprint
            } else if (status.status === 'failed') {
              this.building = false;
              this.buildMessage = `Build failed. ${cached} repositories ready, ${failed} failed.`;
            }
          },
          error: () => {
            this.building = false;
            this.buildMessage = 'Failed to poll job status.';
          },
          complete: () => {
            // Polling timed out or completed
            if (this.building) {
              this.building = false;
              this.buildMessage = 'Build may still be processing. Refresh to check status.';
            }
          }
        });
      },
      error: () => {
        this.building = false;
        this.buildMessage = 'Failed to start build. Please try again.';
      }
    });
  }

  loadRepoBundle(): void {
    this.repoBundle$ = this.repoBundleService.getUserBundle(this.username).pipe(
      tap(bundle => {
        this.bundleEmpty = !(Array.isArray(bundle?.data) && bundle.data.length > 0);
      })
    );
    this.filteredRepos$ = this.repoBundle$.pipe(
      map(bundle => {
        const vms = (bundle?.data ?? [])
          .map(r => this.toCardVM(r))
          .filter((vm): vm is RepoCardVM => vm !== null);
        this.extractFilterOptionsFromVM(vms);
        return this.filterAndSortVMs(vms);
      })
    );
  }

  /**
   * Transform backend bundle entry to card view model.
   * Backend structure (from _repo_row_to_bundle_entry):
   * {
   *   name: string,
   *   languages: {lang: bytes},
   *   languages_top: [{name, pct, bytes}],
   *   urls: {github, homepage},
   *   stats: {stars, forks},
   *   flags: {fork, archived},
   *   timestamps: {pushed_at, updated_at, created_at},
   *   metadata: {description, fingerprint, topics, ...}
   * }
   */
  private toCardVM(r: any): RepoCardVM | null {
    if (!r?.name) return null;

    const langs = r?.languages ?? {};
    const total = Object.values(langs).reduce((a: number, b: any) => a + Number(b), 0) || 1;
    const languagesPct = Object.entries(langs)
      .map(([k, v]) => ({ k, pct: Math.round((Number(v) / total) * 100) }))
      .sort((a, b) => b.pct - a.pct);

    return {
      name: r.name,
      description: r?.metadata?.description ?? 'No description',
      languagesPct,
      updatedAt: r?.timestamps?.updated_at ?? r?.timestamps?.pushed_at,
      htmlUrl: r?.urls?.github,
      stars: r?.stats?.stars ?? 0,
      forks: r?.stats?.forks ?? 0,
      topics: Array.isArray(r?.metadata?.topics) ? r.metadata.topics : [],
      isFork: r?.flags?.fork ?? false,
      isArchived: r?.flags?.archived ?? false,
    };
  }

  private extractFilterOptionsFromVM(vms: RepoCardVM[]): void {
    const languages = new Set<string>();
    const technologies = new Set<string>();
    vms.forEach(vm => {
      (vm.languagesPct ?? []).forEach(l => languages.add(l.k));
      vm.topics.forEach(topic => technologies.add(topic));
    });
    this.allLanguages = Array.from(languages).sort();
    this.allTechnologies = Array.from(technologies).sort();
  }

  private filterAndSortVMs(vms: RepoCardVM[]): RepoCardVM[] {
    let result = vms.filter(vm => {
      if (!this.showForks && vm.isFork) return false;
      if (this.searchTerm) {
        const q = this.searchTerm.toLowerCase();
        const hits =
          vm.name.toLowerCase().includes(q) ||
          (vm.description ?? '').toLowerCase().includes(q) ||
          vm.topics.some(t => t.toLowerCase().includes(q));
        if (!hits) return false;
      }
      if (this.selectedLanguage) {
        const hasLang = (vm.languagesPct || []).some(l => l.k === this.selectedLanguage);
        if (!hasLang) return false;
      }
      if (this.selectedTechnology) {
        const hasTech = vm.topics.some(t => t.toLowerCase().includes(this.selectedTechnology.toLowerCase()));
        if (!hasTech) return false;
      }
      return true;
    });
    result = this.sortVMs(result, this.sortBy, this.sortDirection);
    return result;
  }

  private sortVMs(vms: RepoCardVM[], sortBy: string, direction: string): RepoCardVM[] {
    return [...vms].sort((a, b) => {
      let cmp = 0;
      switch (sortBy) {
        case 'name':
          cmp = a.name.localeCompare(b.name);
          break;
        case 'stars':
          cmp = (a.stars ?? 0) - (b.stars ?? 0);
          break;
        case 'updated':
        default: {
          const at = a.updatedAt ? new Date(a.updatedAt).getTime() : 0;
          const bt = b.updatedAt ? new Date(b.updatedAt).getTime() : 0;
          cmp = at - bt;
          break;
        }
      }
      return direction === 'desc' ? -cmp : cmp;
    });
  }

  applyFilters(): void { this.loadRepoBundle(); }
  resetFilters(): void {
    this.showForks = false;
    this.selectedLanguage = '';
    this.selectedTechnology = '';
    this.searchTerm = '';
    this.sortBy = 'updated';
    this.sortDirection = 'desc';
    this.loadRepoBundle();
  }

  trackByName = (_: number, vm: RepoCardVM) => vm.name;

  removeActiveCandidate(): void {
    if (!this.username) return;
    this.candidateContext.removeCandidate(this.username);
  }
}
