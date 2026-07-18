with open('/opt/autosparefinder/backend/BACKEND_API_ROUTES.py', 'r') as f:
    content = f.read()

# I will append the health route to the end.

health_route = """
@app.get("/api/health")
async def health_check(
    pii_db: AsyncSession = Depends(get_pii_db),
    cat_db: AsyncSession = Depends(get_db)
):
    try:
        # Check database connection
        await pii_db.execute(text("SELECT 1"))
        await cat_db.execute(text("SELECT 1"))
        return {"status": "healthy"}
    except Exception as e:
        # We can log the error internally
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Database unreachable")
"""

content += health_route

with open('/opt/autosparefinder/backend/BACKEND_API_ROUTES.py', 'w') as f:
    f.write(content)
