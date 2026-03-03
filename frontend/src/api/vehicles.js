import api from './client'

export const vehiclesApi = {
  identify: (license_plate) => api.post('/vehicles/identify', { license_plate }),
  myVehicles: () => api.get('/vehicles/my-vehicles'),
  addVehicle: (license_plate, nickname) => {
    const fd = new FormData()
    fd.append('license_plate', license_plate)
    if (nickname) fd.append('nickname', nickname)
    return api.post('/vehicles/my-vehicles', fd, { headers: { 'Content-Type': 'multipart/form-data' } })
  },
  updateVehicle: (id, data) => api.put(`/vehicles/my-vehicles/${id}`, null, { params: data }),
  deleteVehicle: (id) => api.delete(`/vehicles/my-vehicles/${id}`),
  setPrimary: (id) => api.post('/vehicles/my-vehicles/set-primary', null, { params: { vehicle_id: id } }),
  compatibleParts: (id, category) =>
    api.get(`/vehicles/${id}/compatible-parts`, { params: { category } }),
}
