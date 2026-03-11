import api from './client'

export const partsApi = {
  search: (query, vehicle_id, category, per_type = null, vehicle_manufacturer = null) =>
    api.get('/parts/search', { params: { query, vehicle_id, category, per_type, vehicle_manufacturer } }),
  // Legacy flat search kept for photo-search and URL-triggered flows
  searchFlat: (query, vehicle_id, category, limit = 50, offset = 0, sort_by = 'name', sort_dir = 'asc', vehicle_manufacturer = null) =>
    api.get('/parts/search', { params: { query, vehicle_id, category, vehicle_manufacturer } }),
  categories: () => api.get('/parts/categories'),
  manufacturers: () => api.get('/parts/manufacturers'),
  brands: (params = {}) => api.get('/brands', { params }),
  brandsWithParts: () => api.get('/brands/with-parts'),
  brandParts: (brandName, params = {}) => api.get(`/brands/${encodeURIComponent(brandName)}/parts`, { params }),
  identifyFromImage: (file) => {
    const fd = new FormData()
    fd.append('file', file)
    return api.post('/parts/identify-from-image', fd, { headers: { 'Content-Type': 'multipart/form-data' } })
  },
  searchByVin: (vin, part_query = '', category = null, limit = 50, offset = 0) =>
    api.get('/parts/search-by-vin', { params: { vin, part_query, category, limit, offset } }),
  getById: (id) => api.get(`/parts/${id}`),
  compare: (part_id) => api.post('/parts/compare', null, { params: { part_id } }),
  searchByVehicle: (vehicle_id, category) =>
    api.post('/parts/search-by-vehicle', null, { params: { vehicle_id, category } }),
  autocomplete: (q, limit = 8) =>
    api.get('/parts/autocomplete', { params: { q, limit } }),
}
