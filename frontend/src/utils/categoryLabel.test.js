import { describe, expect, it } from 'vitest'
import { CATEGORY_DISPLAY, categoryLabel } from './categoryLabel'

describe('categoryLabel', () => {
  it('localises the stored slug and never shows it raw', () => {
    expect(categoryLabel('brakes', 'he')).toBe('בלמים')
    expect(categoryLabel('brakes', 'ar')).toBe('الفرامل')
    expect(categoryLabel('brakes', 'en')).toBe('Brakes')
    expect(categoryLabel('suspension-steering')).toBe('מתלים והיגוי')
  })
  it('falls back safely', () => {
    expect(categoryLabel('totally-unknown-slug', 'he')).toBe('כללי')
    expect(categoryLabel(undefined)).toBe('כללי')
    expect(categoryLabel('', 'en')).toBe('General')
    expect(categoryLabel('brakes', 'xx')).toBe('בלמים')
  })
  it('keeps values that are already Hebrew/Arabic', () => {
    expect(categoryLabel('בלמים')).toBe('בלמים')
    expect(categoryLabel('כללי')).toBe('כללי')
  })
  it('covers every canonical backend slug (category_map.DISPLAY)', () => {
    expect(Object.keys(CATEGORY_DISPLAY)).toHaveLength(26)
  })
})
