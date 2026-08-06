# Organization access isolation

Status: temporary global mode removed on 2026-08-03.

The compatibility switch `TEMPORARY_GLOBAL_ORGANIZATION_ACCESS` previously
allowed every authenticated user to view organizations and owner-scoped
resources. It was removed after the account-isolation review because a newly
registered account could see data owned by unrelated accounts.

The active authorization model is:

1. Regular users can access only resources owned by their username.
2. Company administrators can access resources owned by members of their
   company.
3. Application administrators can access resources across all owners.
4. The organization directory is available only to application administrators.
5. Delivery connections and credentials remain owner-scoped; only application
   administrators have global visibility.

Core campaign, chain, audience, and template endpoints have negative
cross-account tests for list, read, and update operations. Their service-layer
defaults are owner-scoped so an omitted visibility argument cannot silently
become global access.

The longer-term organization-owned connection model and active-organization
selector remain separate product improvements. They are not prerequisites for
account isolation.
