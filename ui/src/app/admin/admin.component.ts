import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterOutlet, RouterLink, RouterLinkActive } from '@angular/router';
import { AuthService } from '../services/auth.service';

@Component({
  selector: 'app-admin',
  standalone: true,
  imports: [CommonModule, RouterOutlet, RouterLink, RouterLinkActive],
  templateUrl: './admin.component.html',
  styleUrls: ['./admin.component.css']
})
export class AdminComponent {
  public username: string = 'Admin';

  constructor(private authService: AuthService) {
    const user = this.authService.getCurrentUser();
    this.username = user?.username || 'Admin';
  }

  logout(): void {
    this.authService.logout('/');
  }
}
