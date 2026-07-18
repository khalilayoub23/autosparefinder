import yaml
with open('docker-compose.yml', 'r') as f:
    data = yaml.safe_load(f)

# Backend Healthcheck
data['services']['backend']['healthcheck'] = {
    'test': ["CMD", "curl", "-f", "http://localhost:8000/api/health"],
    'interval': '30s',
    'timeout': '10s',
    'retries': 3
}

with open('docker-compose.yml', 'w') as f:
    yaml.dump(data, f, sort_keys=False, default_flow_style=False)
