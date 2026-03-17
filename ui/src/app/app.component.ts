import { Component, OnInit } from '@angular/core';
import { RouterOutlet, RouterLink, RouterLinkActive } from '@angular/router';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  templateUrl: './app.component.html',
  styleUrl: './app.component.css'
})
export class AppComponent implements OnInit {
  title = 'foliohive';
  theme: 'light' | 'dark' = 'light';

  ngOnInit(): void {
    this.initTheme();
  }

  private initTheme(): void {
    const saved = (localStorage.getItem('theme') as 'light' | 'dark') || null;
    this.theme = saved ?? (window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    this.applyTheme();
  }

  toggleTheme(): void {
    this.theme = this.theme === 'dark' ? 'light' : 'dark';
    localStorage.setItem('theme', this.theme);
    this.applyTheme();
  }

  private applyTheme(): void {
    const root = document.documentElement; // <html>
    if (this.theme === 'dark') root.classList.add('dark');
    else root.classList.remove('dark');
    root.setAttribute('data-theme', this.theme);
  }
}
