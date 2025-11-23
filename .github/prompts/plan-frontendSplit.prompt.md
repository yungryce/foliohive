# Frontend Architecture Split & New Cloudfolio UI Plan

**Date**: November 20, 2025  
**Status**: Planning Phase  
**Objective**: Split portfolio showcase from recruiter tool, build new Cloudfolio UI with chat interface for GitHub candidate evaluation

---

## Executive Summary

Separate the current monolithic Angular application into two distinct Static Web Apps:
1. **Portfolio SWA** (`portfolio.yungryce.dev`) - Personal showcase for developers
2. **Cloudfolio UI** (`cloudfolio.app`) - Recruiter tool for evaluating GitHub candidates

### Key Transformations

| Aspect | Current State | Target State | Impact |
|--------|---------------|--------------|--------|
| **Architecture** | Single Angular app (portfolio/) | 2 independent Angular apps | Clear separation of concerns |
| **Primary User** | Developer (yungryce) | Recruiter evaluating candidates | Different UX paradigms |
| **Input Model** | Hardcoded username 'yungryce' | Dynamic username input per search | Multi-candidate support |
| **Authentication** | None | Anonymous sessions + optional OAuth | Usage tracking, zero friction |
| **Interface** | Project listing + details | Username search + AI chat | Conversational candidate evaluation |
| **Deployment** | 1 SWA linked to Function App | 2 SWAs, different API routes | Independent updates |
| **Cost** | $0 (SWA free tier) | $0 (both SWAs on free tier) | No cost increase |

**Design Philosophy**: 
- **Portfolio**: Static showcase (developer's personal brand)
- **Cloudfolio**: Interactive tool (recruiter's research assistant)

---

## Problem Statement

### Current Architecture Issues

**1. Mixed Concerns**
- Portfolio components (`home.component`, `projects.component`, `project.component`) serve developer showcase
- Assistant component (`assistant.component`) provides a recruiter-facing chat UI but currently uses a hardcoded default username ('yungryce') and should be parameterized to accept arbitrary usernames for recruiter workflows.
- Same Angular app tries to serve two different personas (developer vs recruiter)

**2. Hardcoded Username Limitation**
```typescript
// portfolio/src/app/projects/projects.component.ts:37
username = 'yungryce';  // CRITICAL: Cannot evaluate other candidates

// portfolio/src/app/projects/project/project.component.ts:37
username = 'yungryce';  // CRITICAL: Same issue
```

**3. No Recruiter Workflow**
- No UI to input arbitrary GitHub usernames; the repository includes an `assistant.component` chat UI, but it currently uses a hardcoded default ('yungryce') rather than accepting arbitrary usernames for recruiter use.
- Chat interface exists in the repo (`assistant.component`) but needs adaptation for multi-user workflows and integration with session management.
- No session management (can't track which candidates recruiter reviewed)
- No multi-candidate comparison features

**4. Single Deployment Constraint**
- Portfolio updates require full app redeployment
- Cannot A/B test Cloudfolio features without affecting portfolio
- Different update cadences (portfolio: monthly, Cloudfolio: weekly)

---

## Target Architecture

### High-Level Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         User Traffic                                 │
└────────┬─────────────────────────────────────┬────────────────────────┘
         │                                     │
         │ portfolio.yungryce.dev              │ cloudfolio.app
         │ (Developer showcase)                │ (Recruiter tool)
         ▼                                     ▼
┌─────────────────────┐            ┌──────────────────────────┐
│   Portfolio SWA     │            │   Cloudfolio UI SWA      │
│  (Angular, static)  │            │   (Angular, dynamic)     │
│                     │            │                          │
│  - Home (bio)       │            │  - Username search       │
│  - Projects list    │            │  - Job status polling    │
│  - Project details  │            │  - Chat interface        │
│  - Skills table     │            │  - Session management    │
│  - Support surveys  │            │  - Analytics tracking    │
└──────────┬──────────┘            └─────────────┬────────────┘
           │                                     │
           │ /api/bundles/yungryce               │ /api/bundles/{username}
           │ (single user, cached)               │ (multi-user, dynamic)
           │                                     │
           └─────────────────┬───────────────────┘
                             │
                    ┌────────▼────────┐
                    │  API Gateway    │
                    │  (Function App) │
                    │                 │
                    │  HTTP Triggers: │
                    │  /bundles/*     │
                    │  /chat/*        │
                    │  /status/*      │
                    └─────────────────┘
```

---

## Phase 1: Extract Portfolio SWA (Week 1) 📦

**Objective**: Isolate existing portfolio components into dedicated Static Web App

### Directory Structure

```
portfolio/                           # REMAINS: Developer showcase only
├── angular.json
├── package.json
├── staticwebapp.config.json         # UPDATED: New proxy rules
├── src/
│   ├── app/
│   │   ├── home/                    # KEEP: Personal bio, skills, surveys
│   │   │   ├── home.component.ts
│   │   │   ├── home.component.html
│   │   │   └── home.component.css
│   │   ├── projects/                # KEEP: Project listing
│   │   │   ├── projects.component.ts
│   │   │   ├── projects.component.html
│   │   │   └── project/             # KEEP: Project details
│   │   │       ├── project.component.ts
│   │   │       └── project.component.html
│   │   ├── services/
│   │   │   ├── repo-bundle.service.ts    # KEEP: Fetch portfolio data
│   │   │   └── config.service.ts         # KEEP: API URL config
│   │   └── app.routes.ts            # UPDATED: Remove assistant route
│   └── environments/
│       ├── environment.ts           # apiUrl: /api (SWA proxy)
│       └── environment.development.ts

portfolio/infra/                     # Azure deployment
└── main.bicep                       # UPDATED: Deploy portfolio SWA
```

### Tasks

**1.1 Remove Recruiter Features**

Remove `assistant` component (moved to Cloudfolio UI):
```bash
rm -rf portfolio/src/app/assistant/
```

Update `app.routes.ts`:
```typescript
// portfolio/src/app/app.routes.ts
export const routes: Routes = [
  { path: '', component: HomeComponent },
  { path: 'projects', component: ProjectsComponent },
  { path: 'projects/:repo', component: ProjectComponent },
  { path: '**', redirectTo: '' }
  // REMOVED: { path: 'assistant', component: AssistantComponent }
];
```

**1.2 Lock Username to 'yungryce'**

Ensure portfolio only fetches yungryce's data (no dynamic username):
```typescript
// portfolio/src/app/services/repo-bundle.service.ts
export class RepoBundleService {
  private readonly PORTFOLIO_USERNAME = 'yungryce';  // Hardcoded for portfolio
  
  getUserBundle(): Observable<RepoBundleResponse> {
    return this.http.get<RepoBundleResponse>(
      `${this.config.apiUrl}/bundles/${this.PORTFOLIO_USERNAME}`
    );
  }
  
  // Remove startBuild() - not needed for static portfolio
}
```

**1.3 Update Static Web App Configuration**

```json
// portfolio/staticwebapp.config.json
{
  "routes": [
    {
      "route": "/api/bundles/yungryce",
      "rewrite": "/api/bundles/yungryce",
      "allowedRoles": ["anonymous"]
    },
    {
      "route": "/api/surveys",
      "rewrite": "/api/surveys",
      "allowedRoles": ["anonymous"]
    }
  ],
  "navigationFallback": {
    "rewrite": "/index.html",
    "exclude": ["/api/*"]
  },
  "globalHeaders": {
    "Cache-Control": "public, max-age=3600"
  },
  "responseOverrides": {
    "404": {
      "rewrite": "/index.html"
    }
  }
}
```

**1.4 Deploy Portfolio SWA**

```bicep
// portfolio/infra/portfolio-swa.bicep
resource portfolioSwa 'Microsoft.Web/staticSites@2023-01-01' = {
  name: 'swa-portfolio-yungryce'
  location: location
  sku: {
    name: 'Free'
    tier: 'Free'
  }
  properties: {
    repositoryUrl: 'https://github.com/yungryce/cloudfolio'
    branch: 'main'
    buildProperties: {
      appLocation: '/portfolio'
      apiLocation: ''  // No API (uses Function App)
      outputLocation: 'dist/portfolio/browser'
    }
    enterpriseGradeCdnStatus: 'Disabled'
  }
}

// Link to API Gateway Function App
resource portfolioBackend 'Microsoft.Web/staticSites/linkedBackends@2023-01-01' = {
  parent: portfolioSwa
  name: 'portfolio-backend'
  properties: {
    backendResourceId: functionAppGateway.id
    region: location
  }
}

output portfolioUrl string = portfolioSwa.properties.defaultHostname
```

**Deliverables:**
- ✅ Portfolio SWA isolated (only yungryce's data)
- ✅ Assistant component removed
- ✅ Static caching enabled (1 hour TTL)
- ✅ Custom domain: portfolio.yungryce.dev
- ✅ Deployment: Azure DevOps pipeline (azure-pipelines-portfolio.yml)

**Time Estimate**: 8 hours

---

## Phase 2: Create Cloudfolio UI Scaffold (Week 2) 🏗️

**Objective**: Bootstrap new Angular application for recruiter tool

### Directory Structure

```
cloudfolio-ui/                       # NEW: Recruiter tool
├── angular.json
├── package.json
├── staticwebapp.config.json
├── README.md
├── src/
│   ├── app/
│   │   ├── app.component.ts
│   │   ├── app.config.ts
│   │   ├── app.routes.ts
│   │   ├── search/                  # NEW: Username search
│   │   │   ├── search.component.ts
│   │   │   ├── search.component.html
│   │   │   └── search.component.css
│   │   ├── chat/                    # NEW: AI chat interface
│   │   │   ├── chat.component.ts
│   │   │   ├── chat.component.html
│   │   │   └── chat.component.css
│   │   ├── services/
│   │   │   ├── cloudfolio-api.service.ts     # NEW: Multi-username API
│   │   │   ├── session.service.ts            # NEW: Session tracking
│   │   │   ├── analytics.service.ts          # NEW: Application Insights
│   │   │   └── config.service.ts
│   │   └── models/
│   │       ├── candidate.model.ts
│   │       ├── chat-message.model.ts
│   │       └── session.model.ts
│   └── environments/
│       ├── environment.ts
│       └── environment.development.ts
│
└── infra/
    └── cloudfolio-swa.bicep         # NEW: Cloudfolio SWA deployment

.github/workflows/
└── deploy-cloudfolio-ui.yml         # NEW: CI/CD pipeline
```

### Tasks

**2.1 Create Angular Workspace**

```bash
cd /home/juk/cloudfolio
npx @angular/cli@latest new cloudfolio-ui --routing --style=css --ssr=false --standalone
cd cloudfolio-ui

# Install dependencies
npm install @azure/monitor-web
npm install marked dompurify  # For rendering candidate READMEs
```

**2.2 Create Search Component**

```typescript
// cloudfolio-ui/src/app/search/search.component.ts
import { Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { CloudfolioApiService } from '../services/cloudfolio-api.service';
import { SessionService } from '../services/session.service';

@Component({
  selector: 'app-search',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './search.component.html',
  styleUrls: ['./search.component.css']
})
export class SearchComponent {
  private api = inject(CloudfolioApiService);
  private session = inject(SessionService);
  private router = inject(Router);

  username = '';
  loading = false;
  error = '';
  recentSearches: string[] = [];

  ngOnInit(): void {
    this.recentSearches = this.session.getRecentSearches();
  }

  searchCandidate(): void {
    if (!this.username.trim()) {
      this.error = 'Please enter a GitHub username';
      return;
    }

    // Validate username format (alphanumeric + hyphens)
    if (!/^[a-zA-Z0-9-]+$/.test(this.username)) {
      this.error = 'Invalid GitHub username format';
      return;
    }

    this.loading = true;
    this.error = '';

    // Start bundle build job
    this.api.startBundleBuild(this.username, true).subscribe({
      next: (response) => {
        this.session.addRecentSearch(this.username);
        this.router.navigate(['/chat', this.username]);
      },
      error: (err) => {
        this.loading = false;
        this.error = err.error?.message || 'Failed to start analysis. Please try again.';
      }
    });
  }

  selectRecent(username: string): void {
    this.username = username;
    this.searchCandidate();
  }
}
```

**2.3 Create CloudfolioApiService**

```typescript
// cloudfolio-ui/src/app/services/cloudfolio-api.service.ts
import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { ConfigService } from './config.service';

export interface BundleBuildResponse {
  status: 'accepted' | 'processing' | 'completed' | 'failed';
  jobId: string;
  username: string;
  estimatedTime?: number;  // seconds
}

export interface JobStatusResponse {
  status: 'queued' | 'processing' | 'completed' | 'failed';
  progress?: number;  // 0-100
  message?: string;
  data?: any;
}

@Injectable({ providedIn: 'root' })
export class CloudfolioApiService {
  private http = inject(HttpClient);
  private config = inject(ConfigService);

  /**
   * Start bundle build for a GitHub username
   * Returns jobId for polling status
   */
  startBundleBuild(username: string, force: boolean = false): Observable<BundleBuildResponse> {
    const params = new HttpParams().set('force', force.toString());
    return this.http.post<BundleBuildResponse>(
      `${this.config.apiUrl}/bundles/${username}/refresh`,
      {},
      { params }
    );
  }

  /**
   * Get bundle build job status
   * Poll this endpoint until status is 'completed' or 'failed'
   */
  getJobStatus(jobId: string): Observable<JobStatusResponse> {
    return this.http.get<JobStatusResponse>(
      `${this.config.apiUrl}/status/${jobId}`
    );
  }

  /**
   * Get candidate bundle (cached result)
   */
  getCandidateBundle(username: string): Observable<any> {
    return this.http.get(`${this.config.apiUrl}/bundles/${username}`);
  }

  /**
   * Get single repository details
   */
  getCandidateRepo(username: string, repo: string): Observable<any> {
    return this.http.get(`${this.config.apiUrl}/bundles/${username}/${repo}`);
  }

  /**
   * Send chat message to AI assistant
   */
  sendChatMessage(username: string, message: string): Observable<any> {
    return this.http.post(`${this.config.apiUrl}/chat/${username}`, { message });
  }
}
```

**2.4 Create SessionService**

```typescript
// cloudfolio-ui/src/app/services/session.service.ts
import { Injectable } from '@angular/core';

interface SessionData {
  sessionId: string;
  recentSearches: string[];  // Last 10 usernames
  favorites: string[];       // Starred candidates
  createdAt: number;
}

@Injectable({ providedIn: 'root' })
export class SessionService {
  private readonly STORAGE_KEY = 'cloudfolio_session';
  private readonly MAX_RECENT = 10;
  private session: SessionData;

  constructor() {
    this.session = this.loadSession();
  }

  private loadSession(): SessionData {
    const stored = localStorage.getItem(this.STORAGE_KEY);
    if (stored) {
      try {
        return JSON.parse(stored);
      } catch {
        // Invalid JSON, create new session
      }
    }
    
    // Create new session
    return {
      sessionId: crypto.randomUUID(),
      recentSearches: [],
      favorites: [],
      createdAt: Date.now()
    };
  }

  private saveSession(): void {
    localStorage.setItem(this.STORAGE_KEY, JSON.stringify(this.session));
  }

  getSessionId(): string {
    return this.session.sessionId;
  }

  getRecentSearches(): string[] {
    return [...this.session.recentSearches];
  }

  addRecentSearch(username: string): void {
    // Remove if already exists
    this.session.recentSearches = this.session.recentSearches.filter(u => u !== username);
    
    // Add to front
    this.session.recentSearches.unshift(username);
    
    // Keep only MAX_RECENT
    this.session.recentSearches = this.session.recentSearches.slice(0, this.MAX_RECENT);
    
    this.saveSession();
  }

  addFavorite(username: string): void {
    if (!this.session.favorites.includes(username)) {
      this.session.favorites.push(username);
      this.saveSession();
    }
  }

  removeFavorite(username: string): void {
    this.session.favorites = this.session.favorites.filter(u => u !== username);
    this.saveSession();
  }

  isFavorite(username: string): boolean {
    return this.session.favorites.includes(username);
  }
}
```

**Deliverables:**
- ✅ Angular 18 workspace created (cloudfolio-ui/)
- ✅ Search component with username input
- ✅ CloudfolioApiService with multi-username support
- ✅ SessionService for localStorage tracking
- ✅ Responsive UI with Tailwind CSS
- ✅ Development server running (ng serve)

**Time Estimate**: 12 hours

---

## Phase 3: Implement Chat Interface (Week 3) 💬

**Objective**: Build conversational UI for querying candidate skills

### Design

```
┌─────────────────────────────────────────────────────────┐
│  Chat with @octocat                          ⭐ Favorite │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  👤 You:                                                 │
│  What are the candidate's top 3 Python projects?        │
│                                                          │
│  🤖 Assistant:                                           │
│  Based on @octocat's portfolio, the top 3 Python        │
│  projects are:                                           │
│                                                          │
│  1. **hello-world** (⭐ 1.2k) - Flask web application   │
│     with Docker deployment                               │
│  2. **data-pipeline** (⭐ 340) - ETL system using        │
│     Pandas and Airflow                                   │
│  3. **ml-toolkit** (⭐ 180) - Scikit-learn wrapper       │
│     with custom preprocessing                            │
│                                                          │
│  Would you like details on any specific project?        │
│                                                          │
├─────────────────────────────────────────────────────────┤
│  [Type your question...]                      [Send] 📤 │
└─────────────────────────────────────────────────────────┘

Suggested prompts:
• Summarize candidate's experience
• Compare skills: Python vs JavaScript
• What are their DevOps skills?
```

### Tasks

**3.1 Create Chat Component**

```typescript
// cloudfolio-ui/src/app/chat/chat.component.ts
import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute } from '@angular/router';
import { CloudfolioApiService } from '../services/cloudfolio-api.service';
import { SessionService } from '../services/session.service';
import { AnalyticsService } from '../services/analytics.service';
import { timer, switchMap, takeWhile } from 'rxjs';

interface ChatMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: number;
}

@Component({
  selector: 'app-chat',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './chat.component.html',
  styleUrls: ['./chat.component.css']
})
export class ChatComponent implements OnInit {
  private route = inject(ActivatedRoute);
  private api = inject(CloudfolioApiService);
  private session = inject(SessionService);
  private analytics = inject(AnalyticsService);

  username = '';
  messages: ChatMessage[] = [];
  inputMessage = '';
  loading = false;
  bundleReady = false;
  buildProgress = 0;
  candidateData: any = null;

  suggestedPrompts = [
    'Summarize the candidate\'s experience',
    'What are their top 3 projects?',
    'Compare Python vs JavaScript skills',
    'List DevOps and cloud skills',
    'Any red flags in their profile?'
  ];

  ngOnInit(): void {
    this.username = this.route.snapshot.paramMap.get('username') || '';
    this.analytics.trackSearch(this.username);
    
    // Add system message
    this.messages.push({
      role: 'system',
      content: `Analyzing @${this.username}'s GitHub profile...`,
      timestamp: Date.now()
    });

    this.pollBundleStatus();
  }

  /**
   * Poll job status until bundle is ready
   * Pattern from plan-multiAppArchitecture.prompt.md Phase 6
   */
  private pollBundleStatus(): void {
    // Start polling every 5 seconds for up to 5 minutes
    timer(0, 5000).pipe(
      takeWhile(() => !this.bundleReady && this.buildProgress < 100, true),
      switchMap(() => this.api.getCandidateBundle(this.username))
    ).subscribe({
      next: (response) => {
        if (response?.data) {
          this.bundleReady = true;
          this.buildProgress = 100;
          this.candidateData = response.data;
          
          // Update system message
          this.messages[0].content = `✅ @${this.username}'s profile is ready! Ask me anything about their skills and projects.`;
        } else {
          // Still building, estimate progress
          this.buildProgress = Math.min(this.buildProgress + 10, 90);
          this.messages[0].content = `⏳ Analyzing repositories... ${this.buildProgress}%`;
        }
      },
      error: (err) => {
        this.messages[0].content = `❌ Failed to analyze @${this.username}. Please try another username.`;
      }
    });
  }

  sendMessage(prompt?: string): void {
    const message = prompt || this.inputMessage.trim();
    if (!message || !this.bundleReady) return;

    // Add user message
    this.messages.push({
      role: 'user',
      content: message,
      timestamp: Date.now()
    });

    this.inputMessage = '';
    this.loading = true;

    // Track chat query
    this.analytics.trackChatQuery(this.username, message);

    // Send to API
    this.api.sendChatMessage(this.username, message).subscribe({
      next: (response) => {
        this.messages.push({
          role: 'assistant',
          content: response.response || response.message || 'No response',
          timestamp: Date.now()
        });
        this.loading = false;
      },
      error: (err) => {
        this.messages.push({
          role: 'assistant',
          content: 'Sorry, I encountered an error. Please try again.',
          timestamp: Date.now()
        });
        this.loading = false;
      }
    });
  }

  toggleFavorite(): void {
    if (this.session.isFavorite(this.username)) {
      this.session.removeFavorite(this.username);
    } else {
      this.session.addFavorite(this.username);
      this.analytics.trackEvent('favorite_added', { username: this.username });
    }
  }

  isFavorite(): boolean {
    return this.session.isFavorite(this.username);
  }
}
```

**3.2 Create Chat Template**

```html
<!-- cloudfolio-ui/src/app/chat/chat.component.html -->
<div class="flex flex-col h-screen bg-[var(--bg)]">
  <!-- Header -->
  <header class="border-b border-[var(--border)] bg-[var(--card)] p-4 flex items-center justify-between">
    <div class="flex items-center gap-3">
      <a routerLink="/" class="text-[var(--muted)] hover:text-[var(--fg)]">
        ← Back to search
      </a>
      <h1 class="text-xl font-bold">Chat with @{{ username }}</h1>
    </div>
    <button 
      (click)="toggleFavorite()"
      [class.text-yellow-500]="isFavorite()"
      class="px-3 py-2 rounded hover:bg-[var(--soft)]"
      [attr.aria-label]="isFavorite() ? 'Remove from favorites' : 'Add to favorites'"
    >
      {{ isFavorite() ? '⭐' : '☆' }} Favorite
    </button>
  </header>

  <!-- Progress bar (while building) -->
  <div *ngIf="!bundleReady" class="bg-[var(--soft)] h-1">
    <div 
      class="bg-[var(--primary)] h-full transition-all duration-500"
      [style.width.%]="buildProgress"
    ></div>
  </div>

  <!-- Messages -->
  <main class="flex-1 overflow-y-auto p-4 space-y-4">
    <div *ngFor="let msg of messages" 
         [ngClass]="{
           'flex justify-end': msg.role === 'user',
           'flex justify-start': msg.role !== 'user'
         }">
      <div [ngClass]="{
             'bg-[var(--primary)] text-[var(--primary-foreground)]': msg.role === 'user',
             'bg-[var(--card)] border border-[var(--border)]': msg.role === 'assistant',
             'bg-[var(--soft)] text-[var(--muted)] italic': msg.role === 'system'
           }"
           class="rounded-lg p-3 max-w-[80%]">
        <div class="text-xs mb-1 opacity-70">
          {{ msg.role === 'user' ? 'You' : msg.role === 'assistant' ? '🤖 Assistant' : 'System' }}
        </div>
        <div [innerHTML]="msg.content | markdown"></div>
      </div>
    </div>

    <!-- Loading indicator -->
    <div *ngIf="loading" class="flex justify-start">
      <div class="bg-[var(--card)] border border-[var(--border)] rounded-lg p-3">
        <div class="flex items-center gap-2">
          <div class="animate-pulse">🤖 Assistant</div>
          <div class="flex gap-1">
            <span class="animate-bounce">.</span>
            <span class="animate-bounce delay-100">.</span>
            <span class="animate-bounce delay-200">.</span>
          </div>
        </div>
      </div>
    </div>
  </main>

  <!-- Suggested prompts (only show when ready and no messages) -->
  <aside *ngIf="bundleReady && messages.length === 1" 
         class="p-4 border-t border-[var(--border)] bg-[var(--card)]">
    <p class="text-sm text-[var(--muted)] mb-2">Suggested questions:</p>
    <div class="flex flex-wrap gap-2">
      <button 
        *ngFor="let prompt of suggestedPrompts"
        (click)="sendMessage(prompt)"
        class="px-3 py-2 text-sm rounded border border-[var(--border)] hover:bg-[var(--soft)]"
      >
        {{ prompt }}
      </button>
    </div>
  </aside>

  <!-- Input -->
  <footer class="border-t border-[var(--border)] bg-[var(--card)] p-4">
    <form (ngSubmit)="sendMessage()" class="flex gap-2">
      <input 
        type="text"
        [(ngModel)]="inputMessage"
        name="message"
        [disabled]="!bundleReady || loading"
        placeholder="Ask about skills, projects, experience..."
        class="flex-1 px-4 py-2 rounded border border-[var(--border)] bg-[var(--bg)] focus:outline-none focus:ring-2 focus:ring-[var(--ring)]"
      />
      <button 
        type="submit"
        [disabled]="!bundleReady || loading || !inputMessage.trim()"
        class="px-6 py-2 rounded bg-[var(--primary)] text-[var(--primary-foreground)] hover:brightness-95 disabled:opacity-50"
      >
        Send 📤
      </button>
    </form>
  </footer>
</div>
```

**3.3 Create AnalyticsService**

```typescript
// cloudfolio-ui/src/app/services/analytics.service.ts
import { Injectable, inject } from '@angular/core';
import { ApplicationInsights } from '@azure/monitor-web';
import { SessionService } from './session.service';
import { ConfigService } from './config.service';

@Injectable({ providedIn: 'root' })
export class AnalyticsService {
  private session = inject(SessionService);
  private config = inject(ConfigService);
  private appInsights: ApplicationInsights;

  constructor() {
    // Initialize Application Insights
    this.appInsights = new ApplicationInsights({
      config: {
        connectionString: this.config.appInsightsConnectionString,
        enableAutoRouteTracking: true
      }
    });
    this.appInsights.loadAppInsights();
    
    // Set user context (anonymous session ID)
    this.appInsights.setAuthenticatedUserContext(
      this.session.getSessionId(),
      undefined,  // No account ID (anonymous)
      true        // Store in cookie
    );
  }

  trackSearch(username: string): void {
    this.appInsights.trackEvent({
      name: 'candidate_search',
      properties: {
        sessionId: this.session.getSessionId(),
        username: username,
        timestamp: Date.now()
      }
    });
  }

  trackChatQuery(username: string, query: string): void {
    this.appInsights.trackEvent({
      name: 'chat_query',
      properties: {
        sessionId: this.session.getSessionId(),
        username: username,
        queryLength: query.length,
        timestamp: Date.now()
      }
    });
  }

  trackEvent(name: string, properties?: { [key: string]: any }): void {
    this.appInsights.trackEvent({
      name,
      properties: {
        sessionId: this.session.getSessionId(),
        ...properties
      }
    });
  }

  trackPageView(name?: string, uri?: string): void {
    this.appInsights.trackPageView({ name, uri });
  }
}
```

**Deliverables:**
- ✅ Chat component with message history
- ✅ Real-time typing indicator (loading state)
- ✅ Suggested prompts for quick questions
- ✅ Bundle build polling (Phase 6 pattern)
- ✅ Favorite candidates (localStorage)
- ✅ Application Insights tracking (anonymous sessions)
- ✅ Markdown rendering for assistant responses

**Time Estimate**: 16 hours

---

## Phase 4: Anonymous Session Tracking (Week 4) 📊

**Objective**: Implement usage analytics without authentication friction

### Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Cloudfolio UI                         │
│  ┌────────────────────────────────────────────────┐    │
│  │  localStorage (client-only)                     │    │
│  │  - sessionId: UUID                              │    │
│  │  - recentSearches: [username1, username2, ...]  │    │
│  │  - favorites: [username3, username5, ...]       │    │
│  │  - createdAt: timestamp                         │    │
│  └────────────────────────────────────────────────┘    │
│                         │                                │
│                         │ Every API call includes        │
│                         │ X-Session-Id header            │
│                         ▼                                │
└─────────────────────────────────────────────────────────┘
                          │
                ┌─────────▼─────────┐
                │   API Gateway     │
                │  (Function App)   │
                │                   │
                │  Logs sessionId   │
                │  to App Insights  │
                └─────────┬─────────┘
                          │
                ┌─────────▼──────────────────────────┐
                │   Application Insights             │
                │                                     │
                │  Custom Events:                    │
                │  - candidate_search                │
                │  - chat_query                      │
                │  - bundle_build_started            │
                │  - favorite_added                  │
                │                                     │
                │  Dimensions:                       │
                │  - sessionId (anonymized UUID)     │
                │  - username (candidate searched)   │
                │  - timestamp                       │
                │  - queryLength (chat messages)     │
                └────────────────────────────────────┘
```

### Tasks

**4.1 Add Session Header Interceptor**

```typescript
// cloudfolio-ui/src/app/app.config.ts
import { ApplicationConfig, provideZoneChangeDetection } from '@angular/core';
import { provideRouter } from '@angular/router';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { routes } from './app.routes';
import { sessionInterceptor } from './interceptors/session.interceptor';

export const appConfig: ApplicationConfig = {
  providers: [
    provideZoneChangeDetection({ eventCoalescing: true }),
    provideRouter(routes),
    provideHttpClient(
      withInterceptors([sessionInterceptor])
    )
  ]
};
```

```typescript
// cloudfolio-ui/src/app/interceptors/session.interceptor.ts
import { HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { SessionService } from '../services/session.service';

export const sessionInterceptor: HttpInterceptorFn = (req, next) => {
  const session = inject(SessionService);
  
  // Add session ID header to all API requests
  const clonedReq = req.clone({
    setHeaders: {
      'X-Session-Id': session.getSessionId()
    }
  });
  
  return next(clonedReq);
};
```

**4.2 Update API Gateway to Log Sessions**

```python
# apps/api-gateway/function_app.py
import azure.functions as func
from azure.monitor.opentelemetry import configure_azure_monitor
from opentelemetry import trace

# Configure Application Insights
configure_azure_monitor()
tracer = trace.get_tracer(__name__)

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

@app.route(route="bundles/{username}/refresh", methods=["POST"])
def refresh_bundle(req: func.HttpRequest) -> func.HttpResponse:
    username = req.route_params.get('username')
    session_id = req.headers.get('X-Session-Id', 'unknown')
    
    # Log custom event to Application Insights
    with tracer.start_as_current_span("candidate_search") as span:
        span.set_attribute("session_id", session_id)
        span.set_attribute("username", username)
        span.set_attribute("force_refresh", req.params.get('force', 'false'))
    
    # ... rest of implementation
```

**4.3 Create Analytics Dashboard Queries**

```kusto
// Azure Monitor Workbook: Cloudfolio Usage Analytics

// Daily Active Sessions
customEvents
| where name == "candidate_search"
| where timestamp > ago(30d)
| summarize dau = dcount(tostring(customDimensions.sessionId)) by bin(timestamp, 1d)
| render timechart

// Top Searched Candidates
customEvents
| where name == "candidate_search"
| where timestamp > ago(7d)
| summarize searches = count() by username = tostring(customDimensions.username)
| top 20 by searches desc

// Chat Engagement Rate
let searches = customEvents
    | where name == "candidate_search"
    | summarize searchCount = count() by sessionId = tostring(customDimensions.sessionId);
let chats = customEvents
    | where name == "chat_query"
    | summarize chatCount = count() by sessionId = tostring(customDimensions.sessionId);
searches
| join kind=leftouter (chats) on sessionId
| extend engagementRate = todouble(chatCount) / todouble(searchCount) * 100
| summarize avgEngagement = avg(engagementRate)

// Returning Users (by sessionId)
customEvents
| where name == "candidate_search"
| where timestamp > ago(30d)
| summarize 
    firstSeen = min(timestamp),
    lastSeen = max(timestamp),
    totalSearches = count()
    by sessionId = tostring(customDimensions.sessionId)
| extend daysSinceFirstVisit = datetime_diff('day', lastSeen, firstSeen)
| where daysSinceFirstVisit > 0
| summarize returningUsers = count()
```

**Deliverables:**
- ✅ Session ID generated on first visit (UUID in localStorage)
- ✅ Session header interceptor (X-Session-Id on all requests)
- ✅ Application Insights custom events logged
- ✅ API Gateway logs sessionId for correlation
- ✅ Analytics dashboard queries (DAU, top candidates, engagement)
- ✅ Zero authentication friction (100% anonymous)

**Time Estimate**: 8 hours

---

## Phase 5: Deploy Cloudfolio UI (Week 5) 🚀

**Objective**: Deploy Cloudfolio Static Web App with separate API routes

### Tasks

**5.1 Create Cloudfolio SWA Bicep**

```bicep
// cloudfolio-ui/infra/cloudfolio-swa.bicep
param location string = resourceGroup().location
param functionAppGatewayId string  // Link to API Gateway

resource cloudfolioSwa 'Microsoft.Web/staticSites@2023-01-01' = {
  name: 'swa-cloudfolio-prod'
  location: location
  sku: {
    name: 'Free'
    tier: 'Free'
  }
  properties: {
    repositoryUrl: 'https://github.com/yungryce/cloudfolio'
    branch: 'main'
    buildProperties: {
      appLocation: '/cloudfolio-ui'
      apiLocation: ''  // No API (uses Function App)
      outputLocation: 'dist/cloudfolio-ui/browser'
    }
    enterpriseGradeCdnStatus: 'Disabled'
  }
}

// Link to API Gateway Function App
resource cloudfolioBackend 'Microsoft.Web/staticSites/linkedBackends@2023-01-01' = {
  parent: cloudfolioSwa
  name: 'cloudfolio-backend'
  properties: {
    backendResourceId: functionAppGatewayId
    region: location
  }
}

// Custom domain (optional)
resource customDomain 'Microsoft.Web/staticSites/customDomains@2023-01-01' = {
  parent: cloudfolioSwa
  name: 'cloudfolio.app'
  properties: {
    validationMethod: 'cname-delegation'
  }
}

output cloudfolioUrl string = cloudfolioSwa.properties.defaultHostname
output customDomainUrl string = 'cloudfolio.app'
```

**5.2 Update Main Bicep to Deploy Both SWAs**

```bicep
// infra/main.bicep
module portfolioSwa './portfolio-swa.bicep' = {
  name: 'portfolio-swa-deployment'
  params: {
    location: location
    functionAppGatewayId: functionAppGateway.outputs.id
  }
}

module cloudfolioSwa './cloudfolio-swa.bicep' = {
  name: 'cloudfolio-swa-deployment'
  params: {
    location: location
    functionAppGatewayId: functionAppGateway.outputs.id
  }
}

output portfolioUrl string = portfolioSwa.outputs.portfolioUrl
output cloudfolioUrl string = cloudfolioSwa.outputs.cloudfolioUrl
```

**5.3 Create CI/CD Pipeline**

```yaml
# .github/workflows/deploy-cloudfolio-ui.yml
name: Deploy Cloudfolio UI

on:
  push:
    branches:
      - main
    paths:
      - 'cloudfolio-ui/**'
      - '.github/workflows/deploy-cloudfolio-ui.yml'
  workflow_dispatch:

jobs:
  build-and-deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          submodules: true

      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'npm'
          cache-dependency-path: cloudfolio-ui/package-lock.json

      - name: Install dependencies
        run: |
          cd cloudfolio-ui
          npm ci

      - name: Build Angular app
        run: |
          cd cloudfolio-ui
          npm run build -- --configuration production

      - name: Deploy to Azure Static Web Apps
        uses: Azure/static-web-apps-deploy@v1
        with:
          azure_static_web_apps_api_token: ${{ secrets.AZURE_STATIC_WEB_APPS_API_TOKEN_CLOUDFOLIO }}
          repo_token: ${{ secrets.GITHUB_TOKEN }}
          action: 'upload'
          app_location: '/cloudfolio-ui'
          output_location: 'dist/cloudfolio-ui/browser'
          skip_api_build: true
```

**5.4 Configure Static Web App**

```json
// cloudfolio-ui/staticwebapp.config.json
{
  "routes": [
    {
      "route": "/api/bundles/*",
      "allowedRoles": ["anonymous"]
    },
    {
      "route": "/api/chat/*",
      "allowedRoles": ["anonymous"]
    },
    {
      "route": "/api/status/*",
      "allowedRoles": ["anonymous"]
    }
  ],
  "navigationFallback": {
    "rewrite": "/index.html",
    "exclude": ["/api/*", "/assets/*"]
  },
  "globalHeaders": {
    "Cache-Control": "no-cache, no-store, must-revalidate"
  },
  "responseOverrides": {
    "404": {
      "rewrite": "/index.html"
    }
  },
  "mimeTypes": {
    ".json": "application/json",
    ".woff2": "font/woff2"
  }
}
```

**Deliverables:**
- ✅ Cloudfolio SWA deployed (cloudfolio.app)
- ✅ Portfolio SWA deployed (portfolio.yungryce.dev)
- ✅ Both SWAs linked to same API Gateway
- ✅ GitHub Actions CI/CD pipelines
- ✅ Custom domains configured
- ✅ Zero-downtime deployment

**Time Estimate**: 8 hours

---

## Phase 6: Optional OAuth Enhancement (Week 6+) 🔐

**Objective**: Add optional Google OAuth for premium features (deferred to v2)

### Features Unlocked by OAuth

| Feature | Anonymous User | Authenticated User |
|---------|----------------|-------------------|
| Search candidates | ✅ | ✅ |
| Chat with AI | ✅ | ✅ |
| Recent searches | ✅ (localStorage only) | ✅ (cross-device sync) |
| Favorite candidates | ✅ (localStorage only) | ✅ (cross-device sync) |
| Chat history | ❌ (session only) | ✅ (7-day retention) |
| Export reports | ❌ | ✅ (PDF download) |
| Email notifications | ❌ | ✅ (profile updates) |
| Usage limits | 10 searches/day (IP-based) | 100 searches/day |

### Implementation (Deferred)

```json
// cloudfolio-ui/staticwebapp.config.json (when adding auth)
{
  "auth": {
    "identityProviders": {
      "google": {
        "registration": {
          "clientIdSettingName": "GOOGLE_CLIENT_ID",
          "clientSecretSettingName": "GOOGLE_CLIENT_SECRET"
        }
      }
    }
  },
  "routes": [
    {
      "route": "/.auth/login/google",
      "allowedRoles": ["anonymous"]
    },
    {
      "route": "/api/favorites",
      "allowedRoles": ["authenticated"]
    },
    {
      "route": "/api/export/*",
      "allowedRoles": ["authenticated"]
    }
  ]
}
```

**Cost**: $0 (SWA built-in auth, no Azure AD B2C needed)

**Time Estimate**: 12 hours (when needed)

---

## Cost Analysis

### Current State (Portfolio Only)

| Resource | Configuration | Monthly Cost |
|----------|--------------|--------------|
| Static Web App (Portfolio) | Free tier | $0 |
| Function App (API) | 2GB RAM, 100 max instances | $45 |
| Azure Storage | 10GB blob, 1M operations | $25 |
| Application Insights | 5GB ingestion | $15 |
| **Total** | | **$85/month** |

### Target State (Portfolio + Cloudfolio)

| Resource | Configuration | Monthly Cost |
|----------|--------------|--------------|
| Static Web App (Portfolio) | Free tier | $0 |
| Static Web App (Cloudfolio) | Free tier | $0 |
| Function App (API Gateway) | Flex Consumption, 1GB RAM | $12 |
| Function App (Sync Worker) | Flex Consumption, 2GB RAM | $15 |
| Function App (Merge Worker) | Flex Consumption, 1GB RAM | $8 |
| Training Worker (ACI) | 4 vCPU, 16GB, on-demand | $0.40/run × 10 runs = $4 |
| Azure Storage (Queues + Blob) | 1M queue ops, 10GB blob | $25.50 |
| Application Insights | 8GB ingestion (+ analytics) | $20 |
| **Total** | | **$84.50/month** |

**Cost Impact**: +$0 (SWA free tier absorbs both frontends)  
**Bandwidth**: Cloudfolio UI ~500KB initial load, portfolio ~800KB (images)

---

## Success Metrics

### Phase 1-2 (Portfolio Split)

| Metric | Baseline | Target | Measurement |
|--------|----------|--------|-------------|
| **Portfolio Load Time** | 2.5s | <2s | Lighthouse performance score |
| **Bundle Size** | 800KB | <600KB | After removing assistant component |
| **Deployment Frequency** | 1x/month | 2x/week (Cloudfolio) | GitHub Actions logs |

### Phase 3-4 (Chat & Analytics)

| Metric | Baseline | Target | Measurement |
|--------|----------|--------|-------------|
| **Daily Active Sessions** | 0 (no recruiter UI) | 10+ sessions/day | Application Insights |
| **Chat Engagement Rate** | N/A | >60% (users send ≥1 message) | Custom event correlation |
| **Search → Chat Conversion** | N/A | >80% | Search events vs chat events |
| **Average Queries per Session** | N/A | 5-10 messages | Application Insights |

### Phase 5 (Deployment)

| Metric | Baseline | Target | Measurement |
|--------|----------|--------|-------------|
| **SWA Deployment Time** | N/A | <5 min | GitHub Actions duration |
| **Zero-Downtime Deployment** | N/A | 100% uptime | Azure Monitor |
| **CDN Cache Hit Rate** | N/A | >90% | SWA metrics |

---

## Risk Assessment

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| **Portfolio downtime during split** | Low | Medium | Deploy new SWA before removing old routes |
| **Chat API rate limiting** | Medium | High | Implement queue depth alerts, auto-scale Function Apps |
| **Anonymous session tracking insufficient** | Medium | Low | Add optional OAuth in Phase 6 if needed |
| **Bundle build timeout (>5 min)** | Medium | High | Show progress bar, allow background processing |
| **localStorage data loss (incognito)** | High | Low | Display warning for incognito mode users |
| **CORS issues with dual SWAs** | Low | Medium | Test cross-origin requests during Phase 5 |

**Overall Risk**: **LOW** (incremental rollout, no breaking changes to existing portfolio)

---

## Phase 6: Portfolio API Integration (Week 6) 🔗

**Objective**: Update Angular Portfolio app to use queue-based API with polling

**Scope**: This phase covers the Portfolio SWA (portfolio.yungryce.dev) only. The Cloudfolio UI app has its own integration work covered in Phases 2-5.

### Context

This phase aligns with `.github/prompts/plan-multiAppArchitecture.prompt.md` Phase 6, which migrates the backend from Durable Functions to a queue-based architecture with Function Apps. The Portfolio SWA needs to:
1. Adopt the new polling pattern (POST to start job, GET to poll status)
2. Handle async bundle builds with loading states
3. Maintain backward compatibility during migration

### Tasks

**6.1 Update RepoBundleService**
```typescript
// portfolio/src/app/services/repo-bundle.service.ts
import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, throwError, of, timer } from 'rxjs';
import { switchMap, map, catchError, filter, take, retry, tap } from 'rxjs/operators';
import { ConfigService } from './config.service';

interface JobStatus {
  status: 'queued' | 'processing' | 'syncing' | 'completed' | 'failed';
  message?: string;
  progress?: number;
  data?: any;
}

@Injectable({ providedIn: 'root' })
export class RepoBundleService {
  private http = inject(HttpClient);
  private config = inject(ConfigService);
  
  private apiUrl = this.config.apiUrl;

  /**
   * Get user bundle (tries cache first, triggers refresh if needed)
   */
  getUserBundle(username: string, forceRefresh = false): Observable<any> {
    if (!forceRefresh) {
      // Try cache first
      return this.http.get(`${this.apiUrl}/bundles/${username}`).pipe(
        catchError(err => {
          if (err.status === 404) {
            // Cache miss, trigger refresh
            return this.startRefreshAndPoll(username);
          }
          return throwError(() => err);
        })
      );
    }
    
    return this.startRefreshAndPoll(username);
  }

  /**
   * Start refresh job and poll until complete
   */
  private startRefreshAndPoll(username: string): Observable<any> {
    return this.http.post<{ job_id: string }>(`${this.apiUrl}/bundles/${username}/refresh`, {}).pipe(
      switchMap(({ job_id }) => this.pollJobStatus(job_id)),
      map(status => status.data)
    );
  }

  /**
   * Poll job status until completed or failed
   */
  private pollJobStatus(jobId: string): Observable<JobStatus> {
    return timer(0, 2000).pipe(  // Poll every 2 seconds
      switchMap(() => this.http.get<JobStatus>(`${this.apiUrl}/status/${jobId}`)),
      filter(status => status.status === 'completed' || status.status === 'failed'),
      take(1),  // Stop after first completed/failed
      retry({ count: 3, delay: 1000 })  // Retry on network errors
    );
  }

  /**
   * Get single repository bundle
   */
  getUserSingleRepoBundle(username: string, repo: string): Observable<any> {
    return this.http.get(`${this.apiUrl}/bundles/${username}/${repo}`);
  }
}
```

**6.2 Update Components with Loading States**
```typescript
// portfolio/src/app/projects/projects.component.ts
import { Component, OnInit, inject } from '@angular/core';
import { RepoBundleService } from '../services/repo-bundle.service';
import { tap, catchError } from 'rxjs/operators';
import { of } from 'rxjs';

@Component({
  selector: 'app-projects',
  templateUrl: './projects.component.html'
})
export class ProjectsComponent implements OnInit {
  private repoService = inject(RepoBundleService);
  
  username = 'yungryce';  // Hardcoded for Portfolio SWA
  loading = false;
  loadingMessage = '';
  repoBundle$ = of(null);

  ngOnInit() {
    this.loading = true;
    this.loadingMessage = 'Loading repositories...';
    
    this.repoBundle$ = this.repoService.getUserBundle(this.username).pipe(
      tap(() => {
        this.loading = false;
        this.loadingMessage = '';
      }),
      catchError(err => {
        this.loading = false;
        this.loadingMessage = 'Failed to load repositories';
        console.error('Error loading bundle:', err);
        return of(null);
      })
    );
  }
}
```

**6.3 Update Static Web App Proxy (Cutover)**
```json
// portfolio/staticwebapp.config.json
{
  "routes": [
    {
      "route": "/api/*",
      "rewrite": "/api/*",
      "allowedRoles": ["anonymous"]
    }
  ],
  "navigationFallback": {
    "rewrite": "/index.html"
  },
  "mimeTypes": {
    ".json": "application/json"
  },
  "globalHeaders": {
    "Cache-Control": "no-cache, no-store, must-revalidate"
  },
  "responseOverrides": {
    "404": {
      "rewrite": "/index.html"
    }
  }
}
```

**Note**: During cutover, update Azure Static Web App backend link from old Durable Functions app to new Function App Gateway URL

**Deliverables:**
- ✅ RepoBundleService uses polling pattern
- ✅ Components show loading states during async operations
- ✅ Loading states show poll progress
- ✅ Static Web App proxy configured for Function App Gateway
- ✅ Backward compatible (works with both sync and async APIs during migration)
- ✅ Username remains hardcoded to 'yungryce' (Portfolio-specific)

**Time Estimate**: 8 hours

---

## Phase 7: Optional OAuth Enhancement (Deferred) 🔐

**Objective**: Add optional Google OAuth for premium features (deferred to v2)

### Features Unlocked by OAuth

| Feature | Anonymous User | Authenticated User |
|---------|----------------|-------------------|
| Search candidates | ✅ | ✅ |
| Chat with AI | ✅ | ✅ |
| Recent searches | ✅ (localStorage only) | ✅ (cross-device sync) |
| Favorite candidates | ✅ (localStorage only) | ✅ (cross-device sync) |
| Chat history | ❌ (session only) | ✅ (7-day retention) |
| Export reports | ❌ | ✅ (PDF download) |
| Email notifications | ❌ | ✅ (profile updates) |
| Usage limits | 10 searches/day (IP-based) | 100 searches/day |

### Implementation (Deferred)

```json
// cloudfolio-ui/staticwebapp.config.json (when adding auth)
{
  "auth": {
    "identityProviders": {
      "google": {
        "registration": {
          "clientIdSettingName": "GOOGLE_CLIENT_ID",
          "clientSecretSettingName": "GOOGLE_CLIENT_SECRET"
        }
      }
    }
  },
  "routes": [
    {
      "route": "/api/export/*",
      "allowedRoles": ["authenticated"]
    },
    {
      "route": "/api/history/*",
      "allowedRoles": ["authenticated"]
    }
  ]
}
```

**Cost**: $0 (SWA built-in auth, no Azure AD B2C needed)

**Time Estimate**: 12 hours (when needed)

---

## Timeline Summary

| Phase | Duration | Key Deliverable | Blocking Dependencies |
|-------|----------|-----------------|----------------------|
| **Phase 1: Extract Portfolio** | 1 week | Portfolio SWA isolated | None |
| **Phase 2: Cloudfolio Scaffold** | 1 week | Angular app + search UI | None (parallel with Phase 1) |
| **Phase 3: Chat Interface** | 1 week | AI chat with polling | Phase 2 complete |
| **Phase 4: Analytics** | 1 week | Session tracking | Phase 2 complete |
| **Phase 5: Deployment** | 1 week | Both SWAs live | Phase 1, 3, 4 complete |
| **Phase 6: Portfolio API Integration** | 1 week | Polling pattern adopted | Backend migration (multi-app plan Phase 4-5) |
| **Phase 7: OAuth (Optional)** | Deferred | Premium features | User demand validated |
| **Total** | **6 weeks** | Portfolio + Cloudfolio live | |

**Critical Path**: Phase 1 → Phase 3 → Phase 5 → Phase 6

**Note**: Phase 6 depends on backend migration completion from `.github/prompts/plan-multiAppArchitecture.prompt.md`

---

## Next Steps

1. **Approve this plan** - Review frontend split strategy, confirm anonymous-first approach
2. **Set up repositories** - Create `cloudfolio-ui/` directory, copy Angular scaffold
3. **Phase 1 execution** - Extract portfolio components, update routes, deploy portfolio SWA
4. **Phase 2 execution** - Build Cloudfolio search component, implement SessionService
5. **Phase 3 execution** - Create chat interface, integrate with API Gateway `/chat/{username}`
6. **Phase 4 execution** - Add Application Insights tracking, create analytics dashboard
7. **Phase 5 execution** - Deploy Cloudfolio SWA, configure custom domain, test end-to-end

**Estimated Total Time**: 5 weeks (1 developer, part-time)

---

## Appendix

### A. API Gateway Routes (Reference)

```
POST /api/bundles/{username}/refresh    # Start bundle build job
GET  /api/bundles/{username}            # Get cached bundle
GET  /api/bundles/{username}/{repo}     # Get single repo details
POST /api/chat/{username}               # Send chat message
GET  /api/status/{job_id}               # Poll job status
GET  /api/surveys                       # Portfolio support surveys (legacy)
```

### B. Session Storage Schema

```typescript
interface SessionData {
  sessionId: string;          // UUID v4
  recentSearches: string[];   // Last 10 usernames
  favorites: string[];        // Starred candidates
  createdAt: number;          // Unix timestamp
}
```

### C. Application Insights Events

```typescript
// Custom events logged
interface CustomEvent {
  name: 'candidate_search' | 'chat_query' | 'favorite_added' | 'bundle_build_started';
  properties: {
    sessionId: string;
    username?: string;
    queryLength?: number;
    timestamp: number;
  };
}
```

### D. Related Plans

- **Backend Migration**: `plan-multiAppArchitecture.prompt.md` (Function Apps + Azure Storage Queues)
- **AKS Deployment**: `plan-aksDeployment.prompt.md` (Future containerized deployment)
- **Queue Architecture**: `plan-azureStorageQueuesArchitecture.prompt.md` (Worker communication)
- **Training Worker**: `plan-semanticModelTrainingRefactor.prompt.md` (Containerized GPU training)

---

**Status**: Ready for implementation  
**Next Review**: After Phase 1 completion  
**Last Updated**: November 20, 2025
