import { Component, inject } from '@angular/core';
import { Router, RouterLink } from '@angular/router';

@Component({
  selector: 'app-landing',
  standalone: true,
  imports: [RouterLink],
  templateUrl: './landing.component.html',
  styleUrl: './landing.component.css',
})
export class LandingComponent {
  private router = inject(Router);

  tryDemo(): void {
    localStorage.setItem('aia_demo', '1');
    if (!localStorage.getItem('aia_resumeText')) localStorage.setItem('aia_resumeText', 'Demo candidate — full-stack engineer');
    if (!localStorage.getItem('aia_jd')) localStorage.setItem('aia_jd', 'Demo role — senior backend engineer');
    this.router.navigate(['/interview']);
  }
}
