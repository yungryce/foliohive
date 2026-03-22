import { Component, OnInit, inject } from '@angular/core';
import { RouterOutlet, RouterLink, RouterLinkActive } from '@angular/router';
import { CommonModule } from '@angular/common';
import { combineLatest } from 'rxjs';
import { map } from 'rxjs/operators';
import { CandidateContextService, CandidateContext } from './services/candidate-context.service';
import { JobStatusBadgeComponent } from './shared/job-status-badge.component';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterOutlet, RouterLink, RouterLinkActive, CommonModule, JobStatusBadgeComponent],
  templateUrl: './app.component.html',
  styleUrl: './app.component.css'
})
export class AppComponent implements OnInit {
  title = 'foliohive';
  theme: 'light' | 'dark' = 'light';

  private readonly candidateContext = inject(CandidateContextService);

  readonly activeBuild$ = combineLatest([
    this.candidateContext.candidates$,
    this.candidateContext.activeUsername$,
  ]).pipe(
    map(([candidates, activeUsername]): CandidateContext | null => {
      if (!activeUsername) return null;
      const active = candidates.find(c => c.username === activeUsername);
      if (!active || active.buildStatus !== 'building') return null;
      return active;
    })
  );

  ngOnInit(): void {
    this.initTheme();
  }

  private initTheme(): void {
    const saved = (localStorage.getItem('theme') as 'light' | 'dark') || null;
    this.theme = saved ?? (window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    this.applyTheme();
  }

  toggleTheme(): void {
    this.theme = this.theme === 'dark' ? 'light' : 'dark';
    localStorage.setItem('theme', this.theme);
    this.applyTheme();
  }

  private applyTheme(): void {
    const root = document.documentElement; // <html>
    if (this.theme === 'dark') root.classList.add('dark');
    else root.classList.remove('dark');
    root.setAttribute('data-theme', this.theme);
  }
}
