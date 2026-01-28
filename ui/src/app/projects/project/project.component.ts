import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, RouterModule } from '@angular/router';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { Observable, combineLatest, map } from 'rxjs';
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import { RepoBundleService, SingleRepoBundleResponse } from '../../services/repo-bundle.service';
import { CandidateContextService } from '../../services/candidate-context.service';

/**
 * Aligned with backend schema from get_repo_files in api_gateway.py
 */
interface RepoDetailVM {
  name: string;
  description?: string;
  updatedAt?: string;
  languagesPct: { k: string; pct: number }[];
  htmlUrl?: string;
  stars: number;
  forks: number;
  topics: string[];
}

@Component({
  selector: 'app-project',
  standalone: true,
  imports: [CommonModule, RouterModule],
  templateUrl: './project.component.html',
  styleUrls: ['./project.component.css']
})
export class ProjectComponent implements OnInit {
  private route = inject(ActivatedRoute);
  private repoBundleService = inject(RepoBundleService);
  private sanitizer = inject(DomSanitizer);
  private candidateContext = inject(CandidateContextService);

  contentType: 'readme' = 'readme';
  contentHtml: SafeHtml = '';

  username = '';
  repoName = '';
  repo$!: Observable<RepoDetailVM | null>;
  toc: { text: string; id: string; level: number }[] = [];

  ngOnInit(): void {
    this.repoName = this.route.snapshot.paramMap.get('repo') || '';

    const active = this.candidateContext.activeCandidate;
    this.username = active?.username ?? '';

    // Fetch files (readme) and metadata (languages, stats) in parallel
    const files$ = this.repoBundleService.getUserSingleRepoBundle(
      this.username,
      this.repoName,
      undefined,
      'readme'
    );
    const bundle$ = this.repoBundleService.getUserBundle(this.username);

    this.repo$ = combineLatest([files$, bundle$]).pipe(
      map(([files, bundle]) => {
        // Find this repo in the bundle for metadata
        const repoData = (bundle?.data || []).find((r: any) => r?.name === this.repoName);
        const vm = this.toVM(repoData);

        // Render primary_readme if available
        if (files?.primary_readme) {
          this.renderMarkdown(files.primary_readme);
        }

        return vm;
      })
    );
  }

  /**
   * Transform backend bundle entry to detail view model.
   * Backend structure (from _repo_row_to_bundle_entry):
   * {
   *   name: string,
   *   languages: {lang: bytes},
   *   urls: {github, homepage},
   *   stats: {stars, forks},
   *   timestamps: {pushed_at, updated_at, created_at},
   *   metadata: {description, fingerprint, topics, ...}
   * }
   */
  private toVM(r: any | null): RepoDetailVM | null {
    if (!r?.name) {
      return {
        name: this.repoName,
        description: 'Repository details',
        languagesPct: [],
        updatedAt: undefined,
        htmlUrl: undefined,
        stars: 0,
        forks: 0,
        topics: [],
      };
    }

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
    };
  }

  private renderMarkdown(md: string): void {
    // Extract table of contents if present
    const { stripped, toc } = this.extractTocFromMd(md);
    this.toc = toc;

    const rawHtml = marked.parse(stripped, { async: false }) as string;
    const cleanHtml = DOMPurify.sanitize(rawHtml, { USE_PROFILES: { html: true } }) as string;

    const slug = (s: string) =>
      s.toLowerCase().trim().replace(/[^\w\s-]/g, '').replace(/\s+/g, '-');

    const doc = new DOMParser().parseFromString(cleanHtml, 'text/html');
    const headings = doc.body.querySelectorAll('h1,h2,h3,h4,h5,h6');
    headings.forEach(h => {
      const text = h.textContent || '';
      const id = slug(text);
      h.id = id;
      // If no TOC was extracted, build fallback TOC from headings
      if (!this.toc.length) {
        const level = parseInt(h.tagName.substring(1), 10);
        this.toc.push({ text, id, level });
      }
    });

    this.contentHtml = this.sanitizer.bypassSecurityTrustHtml(doc.body.innerHTML);
  }

  /**
   * Extracts a "## Table of Contents" block and returns:
   *  - stripped markdown (TOC block removed)
   *  - parsed toc items [{ text, id, level }]
   */
  private extractTocFromMd(md: string): { stripped: string; toc: { text: string; id: string; level: number }[] } {
    const startRe = /^#{1,6}[^\n]*table of contents[^\n]*$/gim;
    const startMatch = startRe.exec(md);
    if (!startMatch) {
      return { stripped: md, toc: [] };
    }

    const afterStart = startMatch.index + startMatch[0].length;

    // Find next heading after the TOC heading
    const nextHeadingRe = /^#{1,6}\s+/gm;
    nextHeadingRe.lastIndex = afterStart;
    const nextHeadingMatch = nextHeadingRe.exec(md);
    const endIdx = nextHeadingMatch ? nextHeadingMatch.index : md.length;

    const block = md.slice(startMatch.index, endIdx);

    // List item parser: captures indentation, link text, and anchor
    const liRe = /^(\s*)[-*+]\s+\[(.*?)\]\(#([^)]+)\)\s*$/gmi;

    const toc: { text: string; id: string; level: number }[] = [];
    let m: RegExpExecArray | null;

    while ((m = liRe.exec(block)) !== null) {
      const indent = (m[1] || '').replace(/\t/g, '    ');
      const text = (m[2] || '').trim();
      const rawId = (m[3] || '').trim();
      const level = Math.min(6, Math.floor(indent.length / 2) + 1);
      const id = rawId.replace(/^#+/, '');
      toc.push({ text, id, level });
    }

    const stripped = md.slice(0, startMatch.index) + md.slice(endIdx);
    return { stripped, toc };
  }
}
