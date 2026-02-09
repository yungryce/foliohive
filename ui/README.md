# FolioHive UI

**Angular 18+ Static Web App Frontend**

The FolioHive UI is a modern, reactive Angular application deployed to Azure Static Web Apps. It provides recruiters with an intuitive interface to search candidates, view AI-generated summaries, explore repositories, and interact with an intelligent AI assistant.

---

## 📋 Table of Contents

- [Architecture Overview](#architecture-overview)
- [Component Structure](#component-structure)
- [Service Layer](#service-layer)
- [State Management](#state-management)
- [Routing](#routing)
- [Local Development](#local-development)
- [Configuration](#configuration)
- [Build and Deployment](#build-and-deployment)
- [Testing](#testing)

---

## 🏗️ Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                       Angular App                            │
│                  (Standalone Components)                     │
└────────────┬───────────────────────────────────┬─────────────┘
             │                                   │
   ┌─────────┴──────────┐              ┌────────┴─────────┐
   │   Component Tree   │              │  Service Layer   │
   │                    │              │                  │
   │  ├─ Landing        │              │  ├─ API Client   │
   │  ├─ Profile        │              │  ├─ Auth Guard   │
   │  ├─ Projects       │◄────────────►│  ├─ Session Mgr  │
   │  ├─ AI Assistant   │              │  └─ State Svc    │
   │  ├─ Admin          │              └──────────────────┘
   │  └─ Dashboard      │                        │
   └────────────────────┘                        │
                                        ┌─────────┴──────────┐
                                        │  Backend API       │
                                        │  (Azure Functions) │
                                        └────────────────────┘
```

### Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Framework** | Angular 18+ | Reactive UI with signals and RxJS |
| **Language** | TypeScript 5.x | Type-safe development |
| **Styling** | Tailwind CSS | Utility-first responsive design |
| **HTTP** | Angular HttpClient | API communication |
| **State** | RxJS BehaviorSubjects | Reactive state management |
| **Routing** | Angular Router | SPA navigation with guards |
| **Deployment** | Azure Static Web Apps | Global CDN with auto-deployment |

---

## 🧩 Component Structure

### Application Components

Located in `src/app/`:

```
app/
├── landing/              # Candidate search
│   ├── landing.component.ts
│   ├── landing.component.html
│   └── landing.component.css
│
├── profile/              # Candidate summary
│   ├── profile.component.ts
│   ├── profile.component.html
│   └── profile.component.css
│
├── projects/             # Repository list
│   ├── projects.component.ts
│   ├── projects.component.html
│   └── projects.component.css
│
├── ai/                   # Repository detail with AI summary
│   ├── ai.component.ts
│   ├── ai.component.html
│   └── ai.component.css
│
├── assistant/            # AI assistant chat
│   ├── assistant.component.ts
│   ├── assistant.component.html
│   └── assistant.component.css
│
├── admin/                # Admin dashboard
│   ├── admin.component.ts
│   ├── admin.component.html
│   └── admin.component.css
│
├── dashboard/            # Analytics dashboard
│   ├── dashboard.component.ts
│   ├── dashboard.component.html
│   └── dashboard.component.css
│
└── shared/               # Reusable UI components
    ├── loading-spinner/
    ├── error-message/
    └── progress-bar/
```

### Component Responsibilities

#### 1. Landing Component (`landing/`)

**Purpose**: Entry point for candidate search

**Features**:
- GitHub username input with validation
- Trigger candidate refresh via API
- Display recent searches (localStorage)
- Link to candidate summary pages

**Key Methods**:
```typescript
triggerBuild(username: string): void
  → POST /trigger_candidate_refresh
  → Store job_id and username in localStorage
  → Navigate to projects view with polling
```

**User Flow**:
```
1. Enter username → Validate format
2. Click "Analyze" → API call with correlation_id
3. Receive job_id → Store in session
4. Navigate to /projects → Begin polling
```

#### 2. Profile Component (`profile/`)

**Purpose**: Candidate overview with AI-generated summary

**Features**:
- Display user metadata (name, bio, location, company)
- Show aggregate statistics (total repos, languages, stars)
- Render AI-generated profile summary (HTML)
- Link to GitHub profile
- Navigate to repository list

**Key Methods**:
```typescript
loadProfile(username: string): void
  → GET /get_profile with username
  → Display metadata + repo aggregates
  → POST AI summary generation
  → Render HTML summary with [innerHTML]
```

**Data Displayed**:
- Avatar, name, bio, location, company
- Total repos, languages, stars
- AI summary: Skills, experience, patterns, strengths

#### 3. Projects Component (`projects/`)

**Purpose**: Repository list with metadata cards

**Features**:
- Poll job status until metadata_ready or completed
- Display repository cards with:
  - Name, description, stars
  - Language breakdown (pie chart or bars)
  - Topics/tags
  - Last updated date
  - Repo state (pending/synced/cached)
- Filter by language, topics, state
- Sort by stars, updated date, name
- Navigate to individual repo detail

**Key Methods**:
```typescript
pollJobStatus(jobId: string): void
  → Interval polling GET /get_job_status
  → Update progress bar (synced_repos / total_repos)
  → Stop when state === 'metadata_ready' or 'completed'

loadCandidateRepos(username: string): void
  → GET /get_candidate with username
  → Map repos to card components
  → Apply filters and sorting
```

**User Flow**:
```
1. Navigate from landing → Start polling
2. Display progress: "Syncing 35/50 repos..."
3. Metadata ready → Load full repo list
4. Click repo card → Navigate to AI summary
```

#### 4. AI Component (`ai/`)

**Purpose**: Repository detail with AI-generated README summary

**Features**:
- Display repository metadata (description, stars, topics)
- Show language breakdown
- Render AI-generated repository summary (HTML)
- Link to GitHub repo
- Navigate back to projects list

**Key Methods**:
```typescript
loadRepoSummary(username: string, repoName: string): void
  → POST /get_repo_readme_summary
  → Display repo metadata
  → Render AI summary with [innerHTML]
```

**Summary Sections**:
- Project overview
- Key features
- Tech stack
- Architecture patterns
- Code quality indicators

#### 5. Assistant Component (`assistant/`)

**Purpose**: Interactive AI chat for recruiter queries

**Features**:
- Chat interface with message history
- Query input with textarea
- Display user and AI messages
- Typing indicator while processing
- Session persistence (localStorage)

**Key Methods**:
```typescript
sendQuery(query: string): void
  → Append user message to chat
  → POST /portfolio_query with username + query
  → Display typing indicator
  → Append AI response to chat
  → Store session in localStorage
```

**Example Queries**:
- "What backend frameworks does this candidate use?"
- "Does this candidate have experience with microservices?"
- "What's their strongest programming language?"
- "Show me their most impressive project"

#### 6. Admin Component (`admin/`)

**Purpose**: Administrative dashboard for monitoring

**Features**:
- View recent sync jobs
- Monitor job success/failure rates
- View queue depths
- Check cache statistics
- Manually trigger reconciliation

**Key Methods**:
```typescript
loadAdminMetrics(): void
  → GET /admin/metrics (if implemented)
  → Display job stats, queue depths, cache ratios
```

#### 7. Dashboard Component (`dashboard/`)

**Purpose**: Analytics and usage metrics

**Features**:
- Total candidates analyzed
- Total repositories cached
- AI queries processed
- Usage trends (charts)

---

## 🔧 Service Layer

Located in `src/app/services/`:

### 1. API Service (`api.service.ts`)

**Purpose**: Centralized HTTP client for backend communication

**Key Methods**:
```typescript
// Candidate sync
triggerCandidateRefresh(username: string, correlationId: string): Observable<JobResponse>
getJobStatus(jobId: string): Observable<JobStatus>
getCandidate(username: string): Observable<CandidateData>

// AI summaries
getProfileSummary(username: string): Observable<SummaryResponse>
getRepoSummary(username: string, repoName: string): Observable<SummaryResponse>
portfolioQuery(username: string, query: string): Observable<QueryResponse>
```

**Configuration**:
- Base URL from environment config
- Correlation ID header injection
- Error handling with retry logic
- Request/response logging

**Example**:
```typescript
export class ApiService {
  private apiUrl = environment.apiBaseUrl;

  constructor(private http: HttpClient) {}

  triggerCandidateRefresh(username: string, correlationId: string): Observable<any> {
    return this.http.post(`${this.apiUrl}/trigger_candidate_refresh`, 
      { username, correlation_id: correlationId },
      { headers: { 'X-Correlation-ID': correlationId } }
    );
  }
}
```

### 2. Session Service (`session.service.ts`)

**Purpose**: Manage user session and correlation IDs

**Key Methods**:
```typescript
getCorrelationId(): string
  → Retrieve from localStorage or generate new UUID

setCurrentUsername(username: string): void
  → Store in session for context

getCurrentUsername(): string | null
  → Retrieve active username

clearSession(): void
  → Clear localStorage on logout
```

**Storage Keys**:
- `correlation_id`: Session identifier (UUID)
- `current_username`: Active candidate
- `recent_searches`: Array of recent usernames
- `job_id`: Current sync job identifier

### 3. State Service (`state.service.ts`)

**Purpose**: Reactive state management with RxJS

**State**:
```typescript
private currentUserSubject = new BehaviorSubject<string | null>(null);
public currentUser$ = this.currentUserSubject.asObservable();

private jobStatusSubject = new BehaviorSubject<JobStatus | null>(null);
public jobStatus$ = this.jobStatusSubject.asObservable();
```

**Methods**:
```typescript
setCurrentUser(username: string): void
getJobStatus(): Observable<JobStatus | null>
updateJobStatus(status: JobStatus): void
```

**Usage in Components**:
```typescript
constructor(private stateService: StateService) {}

ngOnInit() {
  this.stateService.currentUser$.subscribe(username => {
    if (username) {
      this.loadProfile(username);
    }
  });
}
```

### 4. Auth Guard (`auth.guard.ts`)

**Purpose**: Route protection for authenticated features

**Logic**:
```typescript
canActivate(): boolean {
  const username = this.sessionService.getCurrentUsername();
  if (!username) {
    this.router.navigate(['/landing']);
    return false;
  }
  return true;
}
```

**Protected Routes**:
- `/profile`
- `/projects`
- `/ai`
- `/assistant`
- `/admin`

---

## 🗺️ Routing

Defined in `app.routes.ts`:

```typescript
export const routes: Routes = [
  { path: '', redirectTo: '/landing', pathMatch: 'full' },
  { path: 'landing', component: LandingComponent },
  { 
    path: 'profile/:username', 
    component: ProfileComponent,
    canActivate: [AuthGuard]
  },
  { 
    path: 'projects/:username', 
    component: ProjectsComponent,
    canActivate: [AuthGuard]
  },
  { 
    path: 'ai/:username/:repo', 
    component: AiComponent,
    canActivate: [AuthGuard]
  },
  { 
    path: 'assistant/:username', 
    component: AssistantComponent,
    canActivate: [AuthGuard]
  },
  { 
    path: 'admin', 
    component: AdminComponent,
    canActivate: [AuthGuard]
  },
  { 
    path: 'dashboard', 
    component: DashboardComponent,
    canActivate: [AuthGuard]
  },
  { path: '**', redirectTo: '/landing' }
];
```

### Navigation Flow

```
Landing (/landing)
  │
  ├─→ Enter username → Trigger sync
  │
  └─→ Projects (/:username/projects)
        │
        ├─→ Click repo → AI Summary (/:username/ai/:repo)
        │
        ├─→ Profile (/:username/profile)
        │
        └─→ Assistant (/:username/assistant)
```

---

## 🛠️ Local Development

### Prerequisites

- Node.js 18+ and npm 9+
- Angular CLI (`npm install -g @angular/cli`)
- Running backend API (see [API README](../api/v0.3.0/README.md))

### Setup Steps

#### 1. Install Dependencies

```bash
cd ui
npm install
```

#### 2. Start Development Server

**Option A: Full Stack** (Recommended)
```bash
# From repo root
./run-dev-session.sh
```

This starts:
- Azurite (storage emulator)
- Azure Functions backend
- Angular dev server

**Option B: UI Only**
```bash
cd ui
npm start
```

By default, connects to `http://localhost:7071/api`

#### 3. Access Application

Open browser: `http://localhost:4200`

### Development Commands

```bash
# Start dev server with hot reload
npm start

# Build for development
npm run build

# Build for production
npm run build:prod

# Run linter
npm run lint

# Run tests
npm test

# Run tests with coverage
npm test -- --coverage

# Serve production build locally
npm run serve:prod
```

### File Watching

Angular CLI watches for file changes and auto-reloads:
- Component TypeScript/HTML/CSS
- Services and guards
- Routes and configuration
- Environment files

---

## ⚙️ Configuration

### Environment Files

Located in `src/environments/`:

#### `environment.development.ts` (Local Dev)
```typescript
export const environment = {
  production: false,
  apiBaseUrl: 'http://localhost:7071/api',
  enableDebugLogging: true,
  pollingIntervalMs: 2000,
  maxPollingAttempts: 60
};
```

#### `environment.ts` (Production)
```typescript
export const environment = {
  production: true,
  apiBaseUrl: 'https://foliohive-api.azurewebsites.net/api',
  enableDebugLogging: false,
  pollingIntervalMs: 3000,
  maxPollingAttempts: 40
};
```

### Angular Configuration

`angular.json`:
- **outputPath**: `dist/foliohive-ui`
- **baseHref**: `/`
- **budgets**: 2MB initial, 1MB per chunk
- **styles**: Tailwind CSS with purge
- **scripts**: None (no jQuery, etc.)

### Static Web App Configuration

`staticwebapp.config.json`:
```json
{
  "routes": [
    {
      "route": "/api/*",
      "allowedRoles": ["anonymous"]
    }
  ],
  "navigationFallback": {
    "rewrite": "/index.html",
    "exclude": ["/api/*", "/*.{css,scss,js,png,jpg,gif,ico,svg}"]
  },
  "globalHeaders": {
    "content-security-policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline';"
  },
  "mimeTypes": {
    ".json": "application/json"
  }
}
```

---

## 🚢 Build and Deployment

### Production Build

```bash
cd ui
npm run build:prod
```

Output: `dist/foliohive-ui/` or `dist/browser/`

### Deployment to Azure Static Web Apps

Automated via Azure DevOps pipelines:
- **CI**: `.ado/ci-swa.yml`
- **CD**: `.ado/cd-swa.yml`

**Build artifact**: `ui/dist/browser/`

**Manual Deployment** (using SWA CLI):
```bash
# Install SWA CLI
npm install -g @azure/static-web-apps-cli

# Deploy
swa deploy \
  --app-location ./ui \
  --output-location dist/browser \
  --deployment-token $SWA_DEPLOYMENT_TOKEN
```

### Build Optimization

- **Tree Shaking**: Remove unused code
- **Minification**: Uglify JS/CSS
- **Code Splitting**: Lazy-loaded routes
- **AOT Compilation**: Ahead-of-time template compilation
- **Service Worker**: Offline support (future)

### Deployment Checklist
- [ ] Environment variables configured
- [ ] API base URL points to production backend
- [ ] CORS configured on backend for SWA domain
- [ ] Custom domain configured (if applicable)
- [ ] CDN caching rules set
- [ ] Application Insights enabled

---

## 🧪 Testing

### Unit Tests (Jasmine + Karma)

```bash
# Run all tests
npm test

# Run with coverage
npm test -- --coverage

# Run specific component
npm test -- --include='**/landing.component.spec.ts'

# Watch mode
npm test -- --watch
```

**Test Structure**:
```typescript
describe('LandingComponent', () => {
  let component: LandingComponent;
  let fixture: ComponentFixture<LandingComponent>;
  let apiService: jasmine.SpyObj<ApiService>;

  beforeEach(() => {
    const apiSpy = jasmine.createSpyObj('ApiService', ['triggerCandidateRefresh']);
    
    TestBed.configureTestingModule({
      imports: [LandingComponent],
      providers: [{ provide: ApiService, useValue: apiSpy }]
    });

    fixture = TestBed.createComponent(LandingComponent);
    component = fixture.componentInstance;
    apiService = TestBed.inject(ApiService) as jasmine.SpyObj<ApiService>;
  });

  it('should trigger candidate refresh', () => {
    apiService.triggerCandidateRefresh.and.returnValue(of({ job_id: '123' }));
    component.triggerBuild('torvalds');
    expect(apiService.triggerCandidateRefresh).toHaveBeenCalledWith('torvalds', jasmine.any(String));
  });
});
```

### E2E Tests (Playwright - Future)

```bash
# Install Playwright
npm install -D @playwright/test

# Run E2E tests
npx playwright test

# Run with UI
npx playwright test --ui
```

### Test Coverage Goals

- **Components**: 80%+ coverage
- **Services**: 90%+ coverage
- **Guards**: 100% coverage

---

## 🎨 Styling

### Tailwind CSS

Utility-first CSS framework configured in `tailwind.config.js`:

```javascript
module.exports = {
  content: ['./src/**/*.{html,ts}'],
  theme: {
    extend: {
      colors: {
        primary: '#3b82f6',
        secondary: '#8b5cf6',
        success: '#10b981',
        warning: '#f59e0b',
        danger: '#ef4444'
      }
    }
  },
  plugins: []
};
```

**Usage**:
```html
<div class="flex flex-col gap-4 p-6 bg-white rounded-lg shadow-md">
  <h2 class="text-2xl font-bold text-gray-800">Profile Summary</h2>
  <p class="text-gray-600">{{ profile.bio }}</p>
</div>
```

### Responsive Design

- **Mobile-first**: Base styles for mobile, scale up
- **Breakpoints**: `sm` (640px), `md` (768px), `lg` (1024px), `xl` (1280px)
- **Flexbox/Grid**: Modern layout techniques

---

## 🔍 Troubleshooting

### Common Issues

**Issue**: "Cannot connect to backend API"  
**Solution**: Ensure backend is running on `http://localhost:7071`. Check `environment.development.ts` for correct API URL.

**Issue**: "CORS error when calling API"  
**Solution**: Backend must allow `http://localhost:4200` in CORS settings. Check `function_app.py` or Azure portal.

**Issue**: "Polling never completes"  
**Solution**: Check browser console for errors. Verify job_id is stored in localStorage. Check backend logs for job processing errors.

**Issue**: "AI summary not rendering"  
**Solution**: Check if `summary_html` is returned from API. Verify `[innerHTML]` binding is used, not `{{ }}` interpolation.

### Debug Mode

Enable verbose logging in `environment.development.ts`:
```typescript
export const environment = {
  enableDebugLogging: true,
  // ... other config
};
```

Then in services:
```typescript
if (environment.enableDebugLogging) {
  console.log('API Request:', url, body);
}
```

---

## 📖 Additional Resources

- [Angular Documentation](https://angular.dev)
- [Tailwind CSS Documentation](https://tailwindcss.com/docs)
- [Azure Static Web Apps Documentation](https://learn.microsoft.com/azure/static-web-apps/)
- [RxJS Documentation](https://rxjs.dev/)

---

**Questions or Issues?** Check the [root README](../README.md) or submit a GitHub issue.
