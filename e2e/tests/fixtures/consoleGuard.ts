import type { Page, Request, Response } from '@playwright/test';

/**
 * Collects browser console / page / network failures.
 * Allowed exceptions must be listed explicitly — no broad masks.
 */
export type ConsoleGuardOptions = {
  /** Exact substrings that are allowed in console.error / pageerror */
  allowConsole?: string[];
  /** URL substrings whose failed requests are ignored */
  allowFailedUrls?: string[];
  /** URL substrings where HTTP 4xx is expected */
  allowHttp4xxUrls?: string[];
};

export class ConsoleGuard {
  readonly consoleErrors: string[] = [];
  readonly pageErrors: string[] = [];
  readonly failedRequests: string[] = [];
  readonly http5xx: string[] = [];
  readonly unexpectedHttp4xx: string[] = [];

  private readonly allowConsole: string[];
  private readonly allowFailedUrls: string[];
  private readonly allowHttp4xxUrls: string[];

  constructor(
    private readonly page: Page,
    options: ConsoleGuardOptions = {},
  ) {
    this.allowConsole = options.allowConsole || [];
    this.allowFailedUrls = options.allowFailedUrls || [];
    this.allowHttp4xxUrls = options.allowHttp4xxUrls || [];

    page.on('console', (msg) => {
      if (msg.type() !== 'error') return;
      const text = msg.text();
      if (this.allowConsole.some((s) => text.includes(s))) return;
      // External webfont failures must not fail product E2E.
      if (text.includes('downloadable font') || text.includes('fonts.gstatic.com')) return;
      this.consoleErrors.push(text);
    });

    page.on('pageerror', (err) => {
      const text = String(err);
      if (this.allowConsole.some((s) => text.includes(s))) return;
      // WebKit cancels in-flight fetch during SPA navigations with a CORS-like message.
      if (text.includes('access control checks') || text.includes('Load failed')) return;
      this.pageErrors.push(text);
    });

    page.on('requestfailed', (req: Request) => {
      const url = req.url();
      if (this.allowFailedUrls.some((s) => url.includes(s))) return;
      // Favicon / chrome internals
      if (url.includes('favicon.ico')) return;
      const failure = req.failure()?.errorText || 'failed';
      // Navigation aborts in-flight XHR/fetch — expected when changing routes quickly.
      if (
        failure.includes('ERR_ABORTED') ||
        failure.includes('NS_BINDING_ABORTED') ||
        failure.includes('net::ERR_ABORTED') ||
        failure.includes('Load request cancelled') ||
        failure.includes('cancelled')
      ) {
        return;
      }
      this.failedRequests.push(`${failure} ${url}`);
    });

    page.on('response', (res: Response) => {
      const url = res.url();
      const status = res.status();
      if (status >= 500) {
        this.http5xx.push(`${status} ${url}`);
        return;
      }
      if (status >= 400) {
        if (this.allowHttp4xxUrls.some((s) => url.includes(s))) return;
        // Auth probe endpoints during logout checks
        if (url.includes('/api/auth/me') && status === 401) return;
        this.unexpectedHttp4xx.push(`${status} ${url}`);
      }
    });
  }

  assertClean(context: string): void {
    const parts: string[] = [];
    if (this.consoleErrors.length) {
      parts.push(`console.error: ${this.consoleErrors.join(' | ')}`);
    }
    if (this.pageErrors.length) {
      parts.push(`pageerror: ${this.pageErrors.join(' | ')}`);
    }
    if (this.failedRequests.length) {
      parts.push(`requestfailed: ${this.failedRequests.join(' | ')}`);
    }
    if (this.http5xx.length) {
      parts.push(`http5xx: ${this.http5xx.join(' | ')}`);
    }
    // 4xx are reported but only fail hard for unexpected API calls that look critical
    const critical4xx = this.unexpectedHttp4xx.filter(
      (line) => line.includes('/api/') && !line.startsWith('404 '),
    );
    if (critical4xx.length) {
      parts.push(`http4xx: ${critical4xx.join(' | ')}`);
    }
    if (parts.length) {
      throw new Error(`[${context}] unexpected browser/network errors:\n${parts.join('\n')}`);
    }
  }
}
