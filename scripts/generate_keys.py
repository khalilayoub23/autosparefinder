import secrets, base64

if __name__ == '__main__':
    print(f"JWT_SECRET_KEY={base64.b64encode(secrets.token_bytes(32)).decode()}")
    print(f"JWT_REFRESH_SECRET_KEY={base64.b64encode(secrets.token_bytes(32)).decode()}")
    print(f"ENCRYPTION_KEY={base64.b64encode(secrets.token_bytes(32)).decode()}")
