# Temporary global organization access

Status: active temporary compatibility mode.

All authenticated application users can currently view organizations and use
organization-owned resources, including campaigns and delivery connections,
regardless of their recorded company membership. Destructive organization
management and delivery-credential mutation remain restricted to the existing
admin/owner rules. Legacy jobs and worker-control operations remain
owner-isolated.

This mode exists only for the initial shared-organization rollout. It must not
be treated as the permanent authorization model.

## Removal criteria

Before disabling `TEMPORARY_GLOBAL_ORGANIZATION_ACCESS`:

1. Add an explicit active-organization selector to the authenticated session.
2. Store delivery connections as organization-owned resources instead of
   username-owned resources.
3. Authorize campaign, template, statistics, and delivery operations against
   active organization membership and role.
4. Migrate existing username-owned connections to an organization without
   exposing encrypted secrets.
5. Add negative cross-organization tests and re-enable the scoped expectations
   currently bypassed by the temporary switch.

The switch and its related `TODO(security)` comments are intentionally easy to
search:

```text
TEMPORARY_GLOBAL_ORGANIZATION_ACCESS
TODO(security)
```
