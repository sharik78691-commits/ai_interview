import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { NavigationEnd, Router, RouterLink, RouterOutlet } from '@angular/router';
import { Subscription, filter } from 'rxjs';
import { AuthService } from './core/auth/auth.service';
import { InterviewWsService } from './core/services/interview-ws.service';
import { InterviewerAudioService } from './core/services/interviewer-audio.service';
import { ResumeService } from './core/services/resume.service';
import { SeoConfig, SeoService } from './core/services/seo.service';
import { TranscriptionService } from './core/services/transcription.service';
import { NavbarComponent } from './shared/components/navbar/navbar.component';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterOutlet, RouterLink, NavbarComponent],
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App implements OnInit, OnDestroy {
  private auth = inject(AuthService);
  private router = inject(Router);
  private seo = inject(SeoService);
  private resumeService = inject(ResumeService);
  private interviewWs = inject(InterviewWsService);
  private interviewerAudio = inject(InterviewerAudioService);
  private transcription = inject(TranscriptionService);
  private navSub?: Subscription;
  private logoutSub?: Subscription;

  ngOnInit(): void {
    // Restore the session from the HttpOnly cookie on startup so guards and
    // the navbar know the current user without storing tokens client-side.
    this.auth.checkSession().subscribe();

    // Sign-out (here or in another tab) must stop live capture and remove the
    // previous user's resume, job description and answers from this browser.
    this.logoutSub = this.auth.loggedOut$.subscribe(() => {
      this.transcription.stop();
      void this.interviewerAudio.stop();
      this.interviewWs.clearSession();
      this.resumeService.clearStoredData();
      void this.router.navigate(['/login'], { replaceUrl: true });
    });

    // Apply per-route SEO metadata (title, description, canonical, robots) on
    // every navigation so each page is indexed with its own content.
    this.navSub = this.router.events
      .pipe(filter((e): e is NavigationEnd => e instanceof NavigationEnd))
      .subscribe(() => this.applySeo());
    // Handle the very first load, where NavigationEnd may already have fired.
    this.applySeo();
  }

  ngOnDestroy(): void {
    this.navSub?.unsubscribe();
    this.logoutSub?.unsubscribe();
  }

  /** Read the active route's `seo` data and push it into the document head. */
  private applySeo(): void {
    let route = this.router.routerState.snapshot.root;
    while (route.firstChild) route = route.firstChild;
    const config = route.data['seo'] as SeoConfig | undefined;
    if (config) this.seo.apply(config);
  }
}
