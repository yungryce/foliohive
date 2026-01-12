import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterModule } from '@angular/router';
import { RepoBundleService, RepoBundleResponse } from '../services/repo-bundle.service';
import { Observable, map, timer, switchMap, takeWhile, of } from 'rxjs';
import { CandidateContextService } from '../services/candidate-context.service';

interface RepoCardVM {
  name: string;
  updatedAt?: string;
  type: string;
  description: string;
  primaryStack: string[];
  languagesPct: { k: string; pct: number }[];
  htmlUrl?: string;
  isFork?: boolean;
  hasDocumentation: boolean;
}

@Component({
  selector: 'app-projects',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterModule],
  templateUrl: './projects.component.html',
  styleUrls: ['./projects.component.css']
})
export class ProjectsComponent implements OnInit {
  private repoBundleService = inject(RepoBundleService);
  private candidateContext = inject(CandidateContextService);
  repoBundle$!: Observable<RepoBundleResponse>;
  filteredRepos$!: Observable<RepoCardVM[]>;
  filterByDocumentation = false; 
  username = '';
  jobId?: string;
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

  ngOnInit(): void {
    const active = this.candidateContext.activeCandidate;
    if (!active?.username) {
      this.missingCandidate = true;
      this.repoBundle$ = of({ username: '', data: [] });
      this.filteredRepos$ = of([]);
      return;
    }

    this.username = active.username;
    this.jobId = active.jobId;
    this.loadRepoBundle();
  }

  triggerBuild(): void {
    if (this.building) return;
    this.building = true;
    this.buildMessage = 'Starting build… This may take a few minutes.';
    this.repoBundleService.startBuild(this.username, true).subscribe({
      next: (resp) => {
        const nextJobId = resp?.job_id as string | undefined;
        if (nextJobId) {
          this.jobId = nextJobId;
          this.candidateContext.upsertCandidate({ username: this.username, jobId: nextJobId });
        }

        // Begin a light polling loop to refresh the bundle for ~2 minutes
        timer(5000, 5000).pipe(
          takeWhile((_, i) => i < 24), // 24*5s ≈ 2 minutes
          switchMap(() => this.repoBundleService.getUserBundle(this.username, this.jobId, false))
        ).subscribe({
          next: b => {
            // Stop polling when data appears
            if (Array.isArray(b?.data) && b.data.length > 0) {
              this.building = false;
              this.buildMessage = '';
              this.loadRepoBundle();
            } else {
              this.buildMessage = 'Still building… please keep this tab open.';
            }
          },
          error: () => { this.building = false; }
        });
      },
      error: () => {
        this.building = false;
        this.buildMessage = 'Failed to start build. Please try again.';
      }
    });
  }

  loadRepoBundle(): void {
    this.repoBundle$ = this.repoBundleService.getUserBundle(this.username, this.jobId);
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

  private toCardVM(r: any): RepoCardVM | null {
    if (!r?.has_documentation) {
      console.log('Excluding:', r?.name, 'due to missing documentation');
      return null; // Exclude repositories without documentation
    }

    const pid = r?.repoContext?.project_identity ?? {};
    const type = r?.repoContext?.type ?? pid?.type ?? 'Unknown';
    const description = r?.repoContext?.description ?? pid?.description ?? 'No description';
    const langs = r?.languages ?? {};
    const total = Object.values(langs).reduce((a: number, b: any) => a + Number(b), 0) || 1;
    const languagesPct = Object.entries(langs)
      .map(([k, v]) => ({ k, pct: Math.round((Number(v) / total) * 100) }))
      .sort((a, b) => b.pct - a.pct);

    return {
      name: r?.name ?? r?.metadata?.name ?? 'unknown',
      updatedAt: r?.metadata?.updated_at ?? r?.metadata?.pushed_at,
      type,
      description,
      primaryStack: r?.repoContext?.tech_stack?.primary ?? [],
      languagesPct,
      htmlUrl: r?.metadata?.html_url,
      isFork: !!r?.metadata?.fork,
      hasDocumentation: !!r?.has_documentation,
    };
  }

  private extractFilterOptionsFromVM(vms: RepoCardVM[]): void {
    const languages = new Set<string>();
    const technologies = new Set<string>();
    vms.forEach(vm => {
      (vm.languagesPct ?? []).forEach(l => languages.add(l.k));
      (vm.primaryStack ?? []).forEach(tech => technologies.add(tech));
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
          vm.description.toLowerCase().includes(q) ||
          vm.type.toLowerCase().includes(q) ||
          (vm.primaryStack || []).some(t => t.toLowerCase().includes(q));
        if (!hits) return false;
      }
      if (this.selectedLanguage) {
        const hasLang = (vm.languagesPct || []).some(l => l.k === this.selectedLanguage);
        if (!hasLang) return false;
      }
      if (this.selectedTechnology) {
        const hasTech = (vm.primaryStack || []).includes(this.selectedTechnology);
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
        case 'updated':
        default: {
          const at = a.updatedAt ? new Date(a.updatedAt).getTime() : 0;
          const bt = b.updatedAt ? new Date(b.updatedAt).getTime() : 0;
          cmp = at - bt;
          break;
        }
      }
      return direction === 'asc' ? cmp : -cmp;
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
}
