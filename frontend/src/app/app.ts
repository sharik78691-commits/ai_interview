import { Component, OnInit, inject } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { AuthService } from './core/auth/auth.service';
import { NavbarComponent } from './shared/components/navbar/navbar.component';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterOutlet, NavbarComponent],
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App implements OnInit {
  private auth = inject(AuthService);

  ngOnInit(): void {
    // Restore the session from the HttpOnly cookie on startup so guards and
    // the navbar know the current user without storing tokens client-side.
    this.auth.checkSession().subscribe();
  }
}
