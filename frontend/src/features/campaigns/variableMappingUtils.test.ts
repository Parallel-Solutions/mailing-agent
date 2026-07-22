import { describe, expect, it } from 'vitest';
import {
  isColumnValue,
  isLiteralStoredValue,
  mappingToDisplayValues,
  mappingToStorageValues,
  toDisplayValue,
  toStorageValue,
} from '@/features/campaigns/variableMappingUtils';

const columns = ['company', 'contact_name', 'adm_name'];

describe('variableMappingUtils', () => {
  it('detects literal stored values', () => {
    expect(isLiteralStoredValue('=ООО Рога')).toBe(true);
    expect(isLiteralStoredValue('company')).toBe(false);
  });

  it('converts stored literal to display value', () => {
    expect(toDisplayValue('=ООО Рога')).toBe('ООО Рога');
    expect(toDisplayValue('company')).toBe('company');
  });

  it('stores column names in lowercase', () => {
    expect(toStorageValue('Company', columns)).toBe('company');
    expect(toStorageValue('ADM_NAME', columns)).toBe('adm_name');
  });

  it('stores custom text as literal', () => {
    expect(toStorageValue('ООО Рога и копыта', columns)).toBe('=ООО Рога и копыта');
    expect(toStorageValue('=Уже литерал', columns)).toBe('=Уже литерал');
  });

  it('recognizes column values case-insensitively', () => {
    expect(isColumnValue('Company', columns)).toBe(true);
    expect(isColumnValue('Фиксированный текст', columns)).toBe(false);
  });

  it('maps whole objects between display and storage formats', () => {
    const display = mappingToDisplayValues({
      COMPANY: 'company',
      TITLE: '=ООО Рога',
    });
    expect(display).toEqual({
      COMPANY: 'company',
      TITLE: 'ООО Рога',
    });

    const stored = mappingToStorageValues(
      {
        COMPANY: 'Company',
        TITLE: 'ООО Рога',
      },
      columns,
    );
    expect(stored).toEqual({
      COMPANY: 'company',
      TITLE: '=ООО Рога',
    });
  });
});
