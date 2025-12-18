import { TestBed } from '@angular/core/testing';

import { RepoBundleService } from './repo-bundle.service';

describe('RepoBundleService', () => {
  let service: RepoBundleService;

  beforeEach(() => {
    TestBed.configureTestingModule({});
    service = TestBed.inject(RepoBundleService);
  });

  it('should be created', () => {
    expect(service).toBeTruthy();
  });
});
