import os

# List all XLS files in the project directory
def find_xls_files():
    xls_files = []
    for root, dirs, files in os.walk('.'):
        for file in files:
            if file.endswith(('.xls', '.xlsx')):
                xls_files.append(os.path.join(root, file))
    return xls_files

# Print found XLS files
xls_files = find_xls_files()
print("Found XLS files:", xls_files)
