import yaml
import sys

def main():
    try:
        with open('docker-compose.yml', 'r') as f:
            data = yaml.safe_load(f)
    except Exception as e:
        print(f"Error reading: {e}")
        return

    if 'services' in data:
        if 'postgres_backup' in data['services']:
            data['services']['postgres_backup']['networks'] = ['internal']
        if 'postgres_backup_catalog' in data['services']:
            data['services']['postgres_backup_catalog']['networks'] = ['internal']
            
    try:
        with open('docker-compose.yml', 'w') as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
        print("Success")
    except Exception as e:
        print(f"Error writing: {e}")

if __name__ == "__main__":
    main()
