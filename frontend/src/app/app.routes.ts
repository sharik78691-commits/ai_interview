import { Routes } from '@angular/router';
import { interviewGuard } from './core/guards/interview.guard';
import { DashboardComponent } from './features/dashboard/dashboard.component';
import { InterviewComponent } from './features/interview/interview.component';
import { LandingComponent } from './features/landing/landing.component';
import { SettingsComponent } from './features/settings/settings.component';

export const routes: Routes = [
  { path: '', component: LandingComponent },
  { path: 'dashboard', component: DashboardComponent },
  { path: 'interview', component: InterviewComponent, canActivate: [interviewGuard] },
  { path: 'settings', component: SettingsComponent },
  { path: '**', redirectTo: '' },
];
