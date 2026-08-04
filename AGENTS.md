# Repository agent rules

## Keep Docker containers current

- After every repository change, before reporting the task as complete, rebuild and recreate all affected long-running Docker Compose services. Never leave an active stack running code from an older image.
- For changes that can affect application code, dependencies, frontend assets, or runtime configuration, rebuild both `app` and `worker`. If the E2E stack is running, refresh its affected services as well.
- Use non-destructive Compose updates such as `up -d --build`; never use `down -v` or reset persistent volumes unless the user explicitly requests it.
- After recreation, verify Compose status and the relevant health/readiness endpoints. Do not claim completion while an affected service is unhealthy or still running a stale image.
- Follow `.cursor/rules/local-vs-test-containers.mdc` for the exact local and E2E commands and data-safety constraints.
