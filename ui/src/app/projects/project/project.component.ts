import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, RouterModule } from '@angular/router';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { Observable, map } from 'rxjs';
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import { RepoBundleService, SingleRepoBundleResponse } from '../../services/repo-bundle.service';
import { CandidateContextService } from '../../services/candidate-context.service';

interface RepoDetailVM {
  name: string;
  updatedAt?: string;
  type: string;
  description: string;
  primaryStack: string[];
  languagesPct: { k: string; pct: number }[];
  htmlUrl?: string;
  readme?: string;
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

  repo: any;
  contentType: 'readme' | 'architecture' | 'skills_index' = 'readme';
  contentHtml: SafeHtml = '';

  username = '';
  jobId?: string;
  repoName = '';
  repo$!: Observable<RepoDetailVM | null>;

  readmeHtml: SafeHtml | null = null;
  toc: { text: string; id: string; level: number }[] = [];

  ngOnInit(): void {
    this.repoName = this.route.snapshot.paramMap.get('repo') || '';

    const active = this.candidateContext.activeCandidate;
    this.username = active?.username ?? '';
    this.jobId = active?.jobId;

    this.repo$ = this.repoBundleService
      .getUserSingleRepoBundle(this.username, this.repoName, this.jobId)
      .pipe(
        map((res: SingleRepoBundleResponse | null | undefined) => {
          this.repo = res?.data;
          this.pickRandomContent();
          const vm = this.toVM(this.repo);
          if (vm?.readme) this.renderMarkdown(vm.readme);
          return vm;
        })
      );
  }

  private toVM(r: any | undefined | null): RepoDetailVM | null {
    if (!r) return null;
    const pid = r?.repoContext?.project_identity ?? {};
    const type = r?.repoContext?.type ?? pid?.type ?? 'Unknown';
    const description = r?.repoContext?.description ?? pid?.description ?? 'No description';

    const langs = r?.languages ?? {};
    const total = Object.values(langs).reduce((a: number, b: any) => a + Number(b), 0) || 1;
    const languagesPct = Object.entries(langs)
      .map(([k, v]) => ({ k, pct: Math.round((Number(v) / total) * 100) }))
      .sort((a, b) => b.pct - a.pct);

    return {
      name: r?.name ?? r?.metadata?.name ?? this.repoName,
      updatedAt: r?.metadata?.updated_at ?? r?.metadata?.pushed_at,
      type,
      description,
      primaryStack: r?.repoContext?.tech_stack?.primary ?? [],
      languagesPct,
      htmlUrl: r?.metadata?.html_url, 
      readme: r?.readme
    };
  }

  private renderMarkdown(md: string): void {
    this.toc = [];

    const rawHtml = marked.parse(md, { async: false }) as string;
    const cleanHtml = DOMPurify.sanitize(rawHtml, { USE_PROFILES: { html: true } }) as string;

    const slug = (s: string) =>
      s.toLowerCase().trim().replace(/[^\w\s-]/g, '').replace(/\s+/g, '-');

    const doc = new DOMParser().parseFromString(cleanHtml, 'text/html');
    const headings = doc.body.querySelectorAll('h1,h2,h3,h4,h5,h6');
    headings.forEach(h => {
      const text = (h.textContent || '').trim();
      const level = Number(h.tagName.substring(1));
      const id = slug(text);
      if (!h.id) h.id = id;
      this.toc.push({ text, id, level });
    });

    this.readmeHtml = this.sanitizer.bypassSecurityTrustHtml(doc.body.innerHTML);
  }

  /**
   * Extracts a "## Table of Contents" block and returns:
   *  - stripped markdown (TOC block removed)
   *  - parsed toc items [{ text, id, level }]
   *
   * Rules:
   *  - Finds a heading line containing "Table of Contents" (any level 1-6, case-insensitive).
   *  - The TOC block ends at the next heading (line starting with '#') or end of file.
   *  - Parses list items like "- [Title](#anchor)". Nested indentation => deeper levels.
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

    // List item parser: captures indentation, link text, and anchor.
    // Example matched line: "  - [📖 Overview](#-overview)"
    const liRe = /^(\s*)[-*+]\s+\[(.*?)\]\(#([^)]+)\)\s*$/gmi;

    const toc: { text: string; id: string; level: number }[] = [];
    let m: RegExpExecArray | null;

    while ((m = liRe.exec(block)) !== null) {
      const indent = (m[1] || '').replace(/\t/g, '    '); // normalize tabs to 4 spaces
      const text = (m[2] || '').trim();
      const rawId = (m[3] || '').trim(); // e.g. "-overview"
      // Heuristic: every 2 spaces of indent increases one level (cap at h6)
      const level = Math.min(6, Math.floor(indent.length / 2) + 1);
      // Keep the anchor exactly as authored (your template prepends '#')
      const id = rawId.replace(/^#+/, '');
      toc.push({ text, id, level });
    }

    const stripped = md.slice(0, startMatch.index) + md.slice(endIdx);
    return { stripped, toc };
  }

  pickRandomContent() {
    if (!this.repo) return;

    // Decide which content blob to show
    const options: ('readme' | 'architecture' | 'skills_index')[] = [];
    if (this.repo.readme) options.push('readme');
    if (this.repo.architecture) options.push('architecture');
    if (this.repo.skills_index) options.push('skills_index');
    this.contentType = options[Math.floor(Math.random() * options.length)] || 'readme';

    // Raw markdown (may contain a hand-written TOC)
    let raw = this.repo[this.contentType] || '';

    // 1) Extract TOC and remove it from the markdown body
    const { stripped, toc } = this.extractTocFromMd(raw);
    this.toc = toc;              // <-- navbar data comes from the README’s TOC
    raw = stripped;              // <-- content we will render no longer has the TOC block

    // 2) Extract mermaid blocks, replace with placeholders
    const mermaidBlocks: string[] = [];
    raw = raw.replace(/```mermaid\s*([\s\S]*?)```/g, (_: string, code: string) => {
      mermaidBlocks.push(code.trim());
      return `@@MERMAID_BLOCK_${mermaidBlocks.length - 1}@@`;
    });

    // 3) Parse markdown -> HTML
    let html = marked.parse(raw, { async: false }) as string;

    // 4) Re-insert mermaid blocks as raw <div class="mermaid">...</div>
    mermaidBlocks.forEach((code, i) => {
      html = html.replace(
        `@@MERMAID_BLOCK_${i}@@`,
        `<div class="mermaid">${code}</div>`
      );
    });

    // 5) Sanitize and render
    this.contentHtml = this.sanitizer.bypassSecurityTrustHtml(DOMPurify.sanitize(html));

    // 6) Kick Mermaid
    setTimeout(() => {
      if ((window as any).mermaid) (window as any).mermaid.init();
    }, 0);
  }

  // --- Fallback TOC extraction ---
  // Call this after pickRandomContent if needed
  private fallbackTocFromHeadings(md: string): void {
    if (this.toc.length === 0 && md) {
      const rawHtml = marked.parse(md, { async: false }) as string;
      const cleanHtml = DOMPurify.sanitize(rawHtml, { USE_PROFILES: { html: true } }) as string;
      const doc = new DOMParser().parseFromString(cleanHtml, 'text/html');
      const headings = doc.body.querySelectorAll('h1,h2,h3,h4,h5,h6');
      const slug = (s: string) => s.toLowerCase().trim().replace(/[^\w\s-]/g, '').replace(/\s+/g, '-');
      headings.forEach(h => {
        const text = (h.textContent || '').trim();
        const level = Number(h.tagName.substring(1));
        const id = h.id || slug(text);
        this.toc.push({ text, id, level });
      });
    }
  }
}
