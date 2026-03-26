import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterModule } from '@angular/router';
import { RepoBundleService } from '../services/repo-bundle.service';
import { CandidateContextService } from '../services/candidate-context.service';
import { JobPollingService } from '../services/job-polling.service';
import { CandidateListComponent } from '../shared/candidate-list.component';
import { EMPTY, Subject, takeUntil, switchMap, catchError, of } from 'rxjs';

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
  private route = inject(ActivatedRoute);
  private repoBundleService = inject(RepoBundleService);
  private candidateContext = inject(CandidateContextService);
  private jobPollingService = inject(JobPollingService);
  private readonly destroy$ = new Subject<void>();
  username = '';
  missingCandidate = false;

  // Filter state
  showForks = false;
  selectedLanguage = '';
  selectedTechnology = '';
  searchTerm = '';

  // Sorting
  sortBy = 'updated';
  sortDirection = 'desc';

  // Loading state
  loading = false;
  bundleEmpty = false;
  totalRepoCount = 0;

  // Data
  allLanguages: string[] = [];
  allTechnologies: string[] = [];
  filteredRepos: RepoCardVM[] = [];
  private allVMs: RepoCardVM[] = [];

  ngOnInit(): void {
    const requestedUsername = this.route.snapshot.queryParamMap.get('username')?.trim() ?? null;
    const requestedJobId = this.route.snapshot.queryParamMap.get('job_id')?.trim() ?? null;

    if (requestedUsername) {
      // Use setActive so activeUsername$ emits exactly once for this navigation.
      // Store the jobId separately so it doesn't trigger an extra activeUsername$ emission.
      this.candidateContext.setActive(requestedUsername);
      if (requestedJobId) {
        this.candidateContext.updateProgress(requestedUsername, { jobId: requestedJobId });
      }
    }

    this.candidateContext.activeUsername$
      .pipe(
        takeUntil(this.destroy$),
        switchMap((username) => {
          if (!username) {
            this.missingCandidate = true;
            this._clearData();
            return EMPTY;
          }

          this.missingCandidate = false;
          this.username = username;
          this.loading = true;

          const activeCandidate = this.candidateContext.activeCandidate;
          const resolvedJobId = activeCandidate?.jobId ?? null;

          if (resolvedJobId) {
            this.startJobIfNeeded(username, resolvedJobId);
          }

          // switchMap ensures this inner Observable is cancelled if username changes
          // before whenMetadataReady emits, so we never load stale data.
          return this.jobPollingService.whenMetadataReady(username).pipe(
            switchMap(() => {
              const jobId = this.candidateContext.activeCandidate?.jobId ?? undefined;
              const useCache = !this.jobPollingService.isPolling;
              return this.repoBundleService.getCandidateMetadata(username, jobId, useCache);
            }),
            catchError(() => of({ username, data: [] as any[] }))
          );
        })
      )
      .subscribe(bundle => {
        const data = bundle?.data ?? [];
        this.allVMs = data
          .map((r: any) => this.toCardVM(r))
          .filter((vm): vm is RepoCardVM => vm !== null);
        this.totalRepoCount = this.allVMs.length;
        this.bundleEmpty = this.allVMs.length === 0;
        this.allLanguages = this._deriveLanguages(this.allVMs);
        this.allTechnologies = this._deriveTechnologies(this.allVMs);
        this._applyFilters();
        this.loading = false;
      });
  }

  ngOnDestroy(): void {
    this.destroy$.next();
    this.destroy$.complete(); 
  }

  private _clearData(): void {
    this.allVMs = [];
    this.filteredRepos = [];
    this.allLanguages = [];
    this.allTechnologies = [];
    this.totalRepoCount = 0;
    this.bundleEmpty = false;
    this.loading = false;
  }

  private _applyFilters(): void {
    this.filteredRepos = this.filterAndSortVMs(this.allVMs);
  }

  private _deriveLanguages(vms: RepoCardVM[]): string[] {
    const languages = new Set<string>();
    vms.forEach(vm => (vm.languagesPct ?? []).forEach(l => languages.add(l.k)));
    return Array.from(languages).sort();
  }

  private _deriveTechnologies(vms: RepoCardVM[]): string[] {
    const technologies = new Set<string>();
    vms.forEach(vm => vm.topics.forEach(topic => technologies.add(topic)));
    return Array.from(technologies).sort();
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

  applyFilters(): void {
    this._applyFilters();
  }

  resetFilters(): void {
    this.showForks = true;
    this.selectedLanguage = '';
    this.selectedTechnology = '';
    this.searchTerm = '';
    this.sortBy = 'updated';
    this.sortDirection = 'desc';
    this._applyFilters();
  }

  trackByName = (_: number, vm: RepoCardVM) => vm.name;

  private startJobIfNeeded(username: string, jobId: string): void {
    const current = this.jobPollingService.currentStatus;
    if (current?.job_id === jobId) return;
    if (this.jobPollingService.isPolling) return;
    const stored = this.candidateContext.activeCandidate;
    if (stored?.buildStatus === 'ready' || stored?.buildStatus === 'failed') return;
    this.jobPollingService.startJob(username, jobId);
  }
}
