import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { Router, RouterLink, RouterLinkActive } from '@angular/router';
import { Subscription } from 'rxjs';
import { AuthService } from '../../../core/auth/auth.service';
import { AuthUser } from '../../../core/models/auth.models';
import { ResumeService } from '../../../core/services/resume.service';
import { SettingsService, ThemeName } from '../../../core/services/settings.service';

@Component({
  selector: 'app-navbar',
  standalone: true,
  imports: [RouterLink, RouterLinkActive],
  templateUrl: './navbar.component.html',
  styleUrl: './navbar.component.css',
})
export class NavbarComponent implements OnInit, OnDestroy {
  private resumeService = inject(ResumeService);
  private settings = inject(SettingsService);
  private auth = inject(AuthService);
  private router = inject(Router);
  mode: 'demo' | 'live' | 'offline' | 'checking' = 'checking';
  /** Current theme, kept in sync with SettingsService. */
  theme: ThemeName = 'dark';
  /** Current authenticated user (null when signed out). */
  user: AuthUser | null = null;
  private sub: Subscription | null = null;
  private userSub: Subscription | null = null;

  ngOnInit(): void {
    this.theme = this.settings.theme;
    this.sub = this.settings.settings$.subscribe((s) => {
      this.theme = s.theme === 'light' ? 'light' : 'dark';
    });
    this.userSub = this.auth.user$.subscribe((u) => (this.user = u));
    this.resumeService.checkHealth().subscribe({
      next: (h) => (this.mode = h.llm_configured ? 'live' : 'demo'),
      error: () => (this.mode = 'offline'),
    });
  }

  ngOnDestroy(): void {
    this.sub?.unsubscribe();
    this.userSub?.unsubscribe();
  }

  /** Dark <-> white mode toggle. Persists via SettingsService. */
  toggleTheme(): void {
    this.theme = this.settings.toggleTheme();
  }

  logout(): void {
    // Clear locally stored resume / job description / demo data so the next
    // user on this browser starts with an empty dashboard.
    this.resumeService.clearStoredData();
    this.auth.logout().subscribe({
      next: () => this.router.navigate(['/login']),
      error: () => this.router.navigate(['/login']),
    });
  }
}
