export function createManufacturerFilterTransition(nextManufacturer) {
  return {
    manualManufacturer: nextManufacturer,
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
  }
}

export function createModelFilterTransition(nextModel) {
  return {
    manualModel: nextModel,
    manualSubModel: '',
    manualYear: '',
    subModelOptions: [],
    yearOptions: [],
    subModelOptionsKey: '',
    yearOptionsKey: '',
    invalidates: ['categoryMeta', 'subModelOptions', 'yearOptions'],
  }
}

export function createSubModelFilterTransition(nextSubModel) {
  return {
    manualSubModel: nextSubModel,
    manualYear: '',
    yearOptions: [],
    yearOptionsKey: '',
    invalidates: ['categoryMeta', 'yearOptions'],
  }
}

export function createYearFilterTransition(nextYear) {
  return {
    manualYear: nextYear,
    invalidates: ['categoryMeta'],
  }
}

export function createCategoryFilterTransition(nextCategory) {
  return {
    category: nextCategory,
  }
}

export function getSubModelPlaceholder({ loading, hasManufacturer, hasModel, optionCount }) {
  if (!hasManufacturer || !hasModel) return 'בחר דגם תחילה'
  if (!loading && optionCount === 0) return 'אין תת-דגם'
  return 'בחר תת-דגם / גרסה'
}

export function getYearPlaceholder({ loading, hasManufacturer, hasModel, optionCount }) {
  if (!hasManufacturer || !hasModel) return 'בחר דגם תחילה'
  if (!loading && optionCount === 0) return 'אין שנים'
  return 'בחר שנה'
}

export function buildActiveVehicleFilterOrder({
  manualManufacturer,
  effectiveManualModel,
  effectiveManualSubModel,
  effectiveManualYear,
  category,
}) {
  const activeFilters = []

  if (manualManufacturer) activeFilters.push('manufacturer')
  if (effectiveManualModel) activeFilters.push('model')
  if (effectiveManualSubModel) activeFilters.push('submodel')
  if (effectiveManualYear) activeFilters.push('year')
  if (category) activeFilters.push('category')

  return activeFilters
}