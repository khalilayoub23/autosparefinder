import hashlib
import os
from pathlib import Path

def scan_duplicates(root_dir: str) -> dict:
    hash_map = {}
    
    for filepath in Path(root_dir).rglob('*'):
        if filepath.is_file():
            file_hash = hashlib.md5(filepath.read_bytes()).hexdigest()
            if file_hash in hash_map:
                hash_map[file_hash].append(filepath)
            else:
                hash_map[file_hash] = [filepath]
                
    return {k: v for k, v in hash_map.items() if len(v) > 1}

def remove_duplicates(duplicate_map: dict) -> None:
    for hash_val, file_list in duplicate_map.items():
        # Keep the first occurrence, remove others
        for duplicate in file_list[1:]:
            duplicate.unlink()
