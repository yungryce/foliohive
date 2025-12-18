import { Routes } from '@angular/router';
import { LandingComponent } from './landing/landing.component';
import { ProjectsComponent } from './projects/projects.component';
import { ProjectComponent } from './projects/project/project.component';
import { AiComponent } from './ai/ai.component';

export const routes: Routes = [
  { path: '', component: LandingComponent },
  { path: 'projects', component: ProjectsComponent },
  { path: 'projects/:repo', component: ProjectComponent },
  { path: 'ai', component: AiComponent },
  { path: '**', redirectTo: '' }
];

