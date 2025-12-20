import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { ConfigService } from '../services/config.service';

interface SurveyItem {
  name: string;
  theme: 'light' | 'dark' | 'unknown';
  url: string;
  path: string;
}

@Component({
  selector: 'app-home',
  standalone: true,
  imports: [CommonModule, RouterModule],
  templateUrl: './home.component.html',
  styleUrls: ['./home.component.css']
})
export class HomeComponent implements OnInit, OnDestroy {
  title = 'Home';

  private http = inject(HttpClient);
  private config = inject(ConfigService);

  surveys: SurveyItem[] = [];
  theme: 'light' | 'dark' = 'light';
  private mo: MutationObserver | null = null;

  // TrackBy for surveys *ngFor
  trackByPath = (_: number, item: SurveyItem) => item.path || item.url;

  expandedImage: string | null = null;

  ngOnInit(): void {
    this.detectTheme();
    this.loadSurveys();
    // Watch for theme changes on <html>
    this.mo = new MutationObserver(() => {
      const prev = this.theme;
      this.detectTheme();
      if (prev !== this.theme) this.loadSurveys();
    });
    this.mo.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'data-theme'] });
  }

  ngOnDestroy(): void {
    if (this.mo) this.mo.disconnect();
  }

  private detectTheme(): void {
    this.theme = document.documentElement.classList.contains('dark') ? 'dark' : 'light';
  }

  private loadSurveys(): void {
    const url = `${this.config.apiUrl}/surveys?theme=${this.theme}`;
    this.http.get<any>(url).subscribe({
      next: (res) => this.surveys = (res?.items ?? []) as SurveyItem[],
      error: () => this.surveys = []
    });
  }

  openImage(url: string) {
    this.expandedImage = url;
  }

  closeImage() {
    this.expandedImage = null;
  }
}
