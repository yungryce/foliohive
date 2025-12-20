import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, of } from 'rxjs';
import { environment } from '../../environments/environment';

@Injectable({
  providedIn: 'root'
})
export class ConfigService {
  constructor(private http: HttpClient) {}

  get apiUrl(): string {
    return environment.apiUrl;
  }

  // Load actual configuration if needed
  loadConfig(): Observable<any> {
    console.log('Loading configuration...');
    // Could load from API or external config file
    return of({
      apiUrl: this.apiUrl,
      version: '1.0.0',
      features: ['prefetch', 'caching']
    });
  }
}
