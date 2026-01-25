import { Routes } from '@angular/router';
import { LandingComponent } from './landing/landing.component';
import { ProjectsComponent } from './projects/projects.component';
import { ProjectComponent } from './projects/project/project.component';
import { AiComponent } from './ai/ai.component';
import { DashboardComponent } from './dashboard/dashboard.component';
import { AdminComponent } from './admin/admin.component';
import { MonitoringComponent } from './admin/monitoring/monitoring.component';
import { authGuard } from './guards/auth.guard';
import { adminGuard } from './guards/admin.guard';

export const routes: Routes = [
  { path: '', component: LandingComponent },
  { path: 'projects', component: ProjectsComponent },
  { path: 'projects/:repo', component: ProjectComponent },
  { path: 'ai', component: AiComponent },
  { 
    path: 'dashboard', 
    component: DashboardComponent,
    canActivate: [authGuard]
  },
  {
    path: 'admin',
    component: AdminComponent,
    canActivate: [adminGuard],
    children: [
      { path: '', redirectTo: 'monitoring', pathMatch: 'full' },
      { path: 'monitoring', component: MonitoringComponent },
      // Placeholder routes for future admin components
      { path: 'jobs', component: MonitoringComponent }, // TODO: Create JobListComponent
      { path: 'users', component: MonitoringComponent }, // TODO: Create UsersComponent
      { path: 'api-usage', component: MonitoringComponent } // TODO: Create ApiUsageComponent
    ]
  },
  { path: '**', redirectTo: '' }
];


