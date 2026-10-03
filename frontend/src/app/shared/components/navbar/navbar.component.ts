import { Component, OnInit, inject } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';
import { ResumeService } from '../../../core/services/resume.service';

@Component({
  selector: 'app-navbar',
  standalone: true,
  imports: [RouterLink, RouterLinkActive],
  templateUrl: './navbar.component.html',
  styleUrl: './navbar.component.css',
})
export class NavbarComponent implements OnInit {
  private resumeService = inject(ResumeService);
  mode: 'demo' | 'live' | 'offline' | 'checking' = 'checking';

  ngOnInit(): void {
    this.resumeService.checkHealth().subscribe({
      next: (h) => (this.mode = h.llm_configured ? 'live' : 'demo'),
      error: () => (this.mode = 'offline'),
    });
  }
}
