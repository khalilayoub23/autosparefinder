import yaml
with open('docker-compose.yml', 'r') as f:
    text = f.read()

# I will write a regex or just replace the backup section and logging headers.
