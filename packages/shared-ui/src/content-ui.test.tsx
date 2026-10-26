import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ConfirmDialog } from './auth-controls';
import { translationLanguage, withTranslation } from './translations';

describe('translation language (Phase 05.1)', () => {
  it.each([
    [['en', 'bn'], 'en', { code: 'bn', name: 'Bangla' }],
    [['bn', 'en'], 'bn', { code: 'en', name: 'English' }],
    [['en', 'bn'], 'bn', { code: 'en', name: 'English' }],
    [['en'], 'en', null],
    [['bn'], 'bn', null],
  ] as const)('languages %j with default %s', (languages, defaultLanguage, expected) => {
    expect(translationLanguage(languages, defaultLanguage)).toEqual(expected);
  });

  it('saves one language without losing other translations or fields', () => {
    const stored = { en: { tagline: 'Old', description: 'Kept' }, fr: { tagline: 'Gardé' } };
    const saved = withTranslation(stored, 'en', { tagline: 'Fresh daily' });
    expect(saved).toEqual({
      en: { tagline: 'Fresh daily', description: 'Kept' },
      fr: { tagline: 'Gardé' },
    });
    expect(stored.en.tagline).toBe('Old'); // the loaded value is not mutated
    expect(withTranslation({}, 'bn', { title: 'আমাদের কথা' })).toEqual({
      bn: { title: 'আমাদের কথা' },
    });
  });
});

describe('confirming permanent deletion (Phase 05.1)', () => {
  function setup() {
    const remove = vi.fn(async () => undefined);
    const cancel = vi.fn();
    render(
      <ConfirmDialog title="Delete this page?" onConfirm={remove} onCancel={cancel}>
        “Shipping” will be deleted permanently.
      </ConfirmDialog>,
    );
    return { remove, cancel };
  }

  it('names the item and explains it is permanent', () => {
    setup();
    expect(screen.getByRole('heading', { name: 'Delete this page?' })).toBeInTheDocument();
    expect(screen.getByText(/“Shipping” will be deleted permanently/)).toBeInTheDocument();
  });

  it('does nothing when cancelled', async () => {
    const { remove, cancel } = setup();
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await userEvent.click(screen.getByRole('button', { name: 'Close dialog' }));
    expect(cancel).toHaveBeenCalledTimes(2);
    expect(remove).not.toHaveBeenCalled();
  });

  it('deletes once when confirmed', async () => {
    const { remove, cancel } = setup();
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }));
    expect(remove).toHaveBeenCalledTimes(1);
    expect(cancel).not.toHaveBeenCalled();
  });
});
