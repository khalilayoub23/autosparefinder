import yaml
with open('docker-compose.yml', 'r') as f:
    data = yaml.safe_load(f)

# Use python urllib
code = "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health').getcode() == 200 else 1)"
data['services']['backend']['healthcheck']['test'] = ["CMD", "python3", "-c", code]

with open('docker-compose.yml', 'w') as f:
    yaml.dump(data, f, sort_keys=False, default_flow_style=False)
