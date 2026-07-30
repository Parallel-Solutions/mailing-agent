import { describe, expect, it } from 'vitest';
import { PROFILE_TABS } from './ProfilePage';

describe('profile tabs', () => {
  it('does not duplicate the global onboarding launcher in profile', () => {
    expect(PROFILE_TABS).not.toContain('onboarding');
  });
});
