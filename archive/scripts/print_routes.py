from BACKEND_API_ROUTES import app
for route in app.routes:
    print(route.path)
