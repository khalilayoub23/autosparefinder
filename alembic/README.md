Alembic scaffold for AutoSpareFinder

How to create the first migration (run locally):

1. Ensure `DATABASE_URL` is set in your environment (or in alembic.ini).
2. Install dependencies: `pip install -r backend/requirements.txt`.
3. Generate an autogenerate revision:
   `alembic revision --autogenerate -m "init schema"`
4. Review the generated migration in `alembic/versions/` and then apply:
   `alembic upgrade head`

If your models live in `src.config.database.Base`, Alembic is preconfigured to use `target_metadata` from there.
