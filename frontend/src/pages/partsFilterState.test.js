import { describe, expect, it } from 'vitest'
import {
  buildActiveVehicleFilterOrder,
  createCategoryFilterTransition,
  createManufacturerFilterTransition,
  createModelFilterTransition,
  createSubModelFilterTransition,
  createYearFilterTransition,
  getSubModelPlaceholder,
  getYearPlaceholder,
} from './partsFilterState'

describe('partsFilterState', () => {
  it('resets downstream filters when manufacturer changes', () => {
    expect(createManufacturerFilterTransition('Citroen')).toEqual({
      manualManufacturer: 'Citroen',
      manualModel: '',
      manualSubModel: '',
      manualYear: '',
      modelOptions: [],
      subModelOptions: [],
      yearOptions: [],
      modelOptionsKey: '',
      subModelOptionsKey: '',
      yearOptionsKey: '',
      brandCountInfoOpenFor: '',
      invalidates: ['categoryMeta', 'modelOptions', 'subModelOptions', 'yearOptions'],
    })
  })

  it('resets sub-model and year when model changes', () => {
    expect(createModelFilterTransition('C-CROSSER')).toEqual({
      manualModel: 'C-CROSSER',
      manualSubModel: '',
      manualYear: '',
      subModelOptions: [],
      yearOptions: [],
      subModelOptionsKey: '',
      yearOptionsKey: '',
      invalidates: ['categoryMeta', 'subModelOptions', 'yearOptions'],
    })
  })

  it('resets year when sub-model changes', () => {
    expect(createSubModelFilterTransition('1.6 HDi')).toEqual({
      manualSubModel: '1.6 HDi',
      manualYear: '',
      yearOptions: [],
      yearOptionsKey: '',
      invalidates: ['categoryMeta', 'yearOptions'],
    })
  })

  it('keeps year changes isolated', () => {
    expect(createYearFilterTransition('2018')).toEqual({
      manualYear: '2018',
      invalidates: ['categoryMeta'],
    })
  })

  it('keeps part family changes isolated from vehicle filters', () => {
    expect(createCategoryFilterTransition('filters')).toEqual({
      category: 'filters',
    })
  })

  it('returns active filters in dependency order', () => {
    expect(buildActiveVehicleFilterOrder({
      manualManufacturer: 'Chevrolet',
      effectiveManualModel: 'ASTRO VAN',
      effectiveManualSubModel: 'Cargo',
      effectiveManualYear: '2003',
      category: 'body-exterior',
    })).toEqual(['manufacturer', 'model', 'submodel', 'year', 'category'])
  })

  it('skips empty filters but preserves order', () => {
    expect(buildActiveVehicleFilterOrder({
      manualManufacturer: 'Citroen',
      effectiveManualModel: 'C-CROSSER',
      effectiveManualSubModel: '',
      effectiveManualYear: '',
      category: 'filters',
    })).toEqual(['manufacturer', 'model', 'category'])
  })

  it('supports part family as the only active filter', () => {
    expect(buildActiveVehicleFilterOrder({
      manualManufacturer: '',
      effectiveManualModel: '',
      effectiveManualSubModel: '',
      effectiveManualYear: '',
      category: 'filters',
    })).toEqual(['category'])
  })

  it('shows no sub-model when empty results are loaded', () => {
    expect(getSubModelPlaceholder({
      loading: false,
      hasManufacturer: true,
      hasModel: true,
      optionCount: 0,
    })).toBe('אין תת-דגם')
  })

  it('shows no years when empty results are loaded', () => {
    expect(getYearPlaceholder({
      loading: false,
      hasManufacturer: true,
      hasModel: true,
      optionCount: 0,
    })).toBe('אין שנים')
  })

  it('shows select placeholder while loading (no loading text)', () => {
    expect(getSubModelPlaceholder({
      loading: true,
      hasManufacturer: true,
      hasModel: true,
      optionCount: 0,
    })).toBe('בחר תת-דגם / גרסה')

    expect(getYearPlaceholder({
      loading: true,
      hasManufacturer: true,
      hasModel: true,
      optionCount: 0,
    })).toBe('בחר שנה')
  })
})