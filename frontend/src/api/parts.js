import api from './client'

export const partsApi = {
  search: (query, vehicle_id, category, limit = 20) =>
    api.get('/parts/search', { params: { query, vehicle_id, category, limit } }),
  categories: () => api.get('/parts/categories'),
  manufacturers: () => api.get('/parts/manufacturers'),
  getById: (id) => api.get(`/parts/${id}`),
  compare: (part_id) => api.post('/parts/compare', null, { params: { part_id } }),
  searchByVehicle: (vehicle_id, category) =>
    api.post('/parts/search-by-vehicle', null, { params: { vehicle_id, category } }),
  identifyFromImage: (file) => {
    const fd = new FormData()
    fd.append('file', file)
    return api.post('/parts/identify-from-image', fd, { headers: { 'Content-Type': 'multipart/form-data' } })
  },
}
