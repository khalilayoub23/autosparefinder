-- #119 fitment data repairs — PREPARED, NOT EXECUTED. Needs explicit owner approval (production write).
-- Run with: docker exec -i autospare_postgres_catalog psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 < REPAIR_FITMENT.sql
-- Not 10:00-14:00 Israel, not inside run_all_tasks (21:30 / 00:15 / 07:15 Israel, ~35 min).
-- Every repaired row is first copied to an audit table; undo = the INSERT/UPDATE at the bottom.
-- Expected (measured read-only 2026-10-31): R1 12,360 rows renamed / 0 deleted, R2 95 rows, R3 644 rows, R4 27 rows.

SET lock_timeout = '5s';
SET statement_timeout = '120s';

-- audit copy (one table, all four repairs)
CREATE TABLE IF NOT EXISTS pvf_repair_119_backup AS
SELECT now() AS backed_up_at, 'R0'::text AS repair, p.* FROM part_vehicle_fitment p WHERE false;

-- R1  Ford rows written as "Ford <model>" (vehicle search never matches them): strip the make.
INSERT INTO pvf_repair_119_backup
SELECT now(), 'R1', p.* FROM part_vehicle_fitment p
WHERE manufacturer = 'Ford' AND notes = 'extracted:name_he' AND model LIKE 'Ford %';
-- repeat the next statement until it reports UPDATE 0 (1,000 rows per run, SKIP LOCKED)
WITH b AS (
    SELECT id, part_id, substr(model, 6) AS m, year_from FROM part_vehicle_fitment
    WHERE manufacturer = 'Ford' AND notes = 'extracted:name_he' AND model LIKE 'Ford %'
    ORDER BY id LIMIT 1000 FOR UPDATE SKIP LOCKED
), dup AS (
    DELETE FROM part_vehicle_fitment p USING b
    WHERE p.id = b.id AND EXISTS (SELECT 1 FROM part_vehicle_fitment q WHERE q.part_id = b.part_id
                                  AND q.manufacturer = 'Ford' AND q.model = b.m AND q.year_from = b.year_from)
    RETURNING p.id
)
UPDATE part_vehicle_fitment p SET model = b.m, updated_at = NOW()
FROM b WHERE p.id = b.id AND p.id NOT IN (SELECT id FROM dup);

-- R2  chassis U25 is the MINI Countryman (2024+), not "BMW X2 Electric".
INSERT INTO pvf_repair_119_backup
SELECT now(), 'R2', p.* FROM part_vehicle_fitment p WHERE notes = 'chassis:U25' AND manufacturer = 'BMW';
UPDATE part_vehicle_fitment p
SET manufacturer = 'MINI', manufacturer_id = '47a433bf-4f6f-4f8f-a686-a8c02f7727a8', model = 'MINI Countryman',
    year_from = 2024, year_to = 2026, updated_at = NOW()
WHERE notes = 'chassis:U25' AND manufacturer = 'BMW'
  AND NOT EXISTS (SELECT 1 FROM part_vehicle_fitment q WHERE q.part_id = p.part_id AND q.manufacturer = 'MINI'
                  AND q.model = 'MINI Countryman' AND q.year_from = 2024);

-- R3  tyre sizes ("235 55 R18 104v") read as BMW motorcycle codes R17/R18/R19: remove those rows.
INSERT INTO pvf_repair_119_backup
SELECT now(), 'R3', p.* FROM part_vehicle_fitment p JOIN parts_catalog pc ON pc.id = p.part_id
WHERE p.notes IN ('chassis:R17', 'chassis:R18', 'chassis:R19')
  AND (pc.name ~* '[0-9]{3}[ /]?[0-9]{2}[ /]?Z?R[ -]?[0-9]{2}' OR pc.name_he ~* '[0-9]{3}[ /]?[0-9]{2}[ /]?Z?R[ -]?[0-9]{2}');
DELETE FROM part_vehicle_fitment p USING pvf_repair_119_backup b WHERE b.repair = 'R3' AND p.id = b.id;

-- R4  Jaguar name-parse rows the corrected rule rejects: 25 merchandise + 2 classic era of 32 (list generated read-only
--     2026-10-31 by jag_r4.py with the corrected _jaguar_fitment_from_name; the 5 genuine rows are kept).
INSERT INTO pvf_repair_119_backup
SELECT now(), 'R4', p.* FROM part_vehicle_fitment p WHERE p.notes = 'name_parse:jaguar' AND p.id IN ('a5c411d7-336b-4bcc-8e5e-d03e15930da9','efa766d6-6984-432d-ad20-2dcce58e9ddd','4120bef7-7fb3-48fd-9a78-2369e2616d27','d8abc5cf-cf7d-4ec4-837d-29afa88d6efc','cd409a41-d229-4eb6-b90b-09641e2b396d','3ecfffe9-855b-407e-ae0c-0bd07b12a656','9cf7bba7-3e96-444a-8b54-dc3dd7b5826c','1db4b512-276e-4684-9c1d-063e5f58a6f3','d22ec75d-1755-45f0-b1ea-7e343ae4f94c','e963f05d-c8fd-4e16-a51d-916e9cb2c054','f64f2b02-3c09-45c8-8e19-742ce59a9753','fd6b5302-b03a-4164-80f5-dbb49b55aafa','8d3c345e-19f9-4b00-828d-038ce4c77f9c','3b7996d1-0131-499e-9232-6ccdee25cab8','59f1ea8f-c2fb-450e-aa6c-8b764b8b3a80','b87ff8c1-d1b7-45e1-8e28-d3f6472aaeab','f9cad8cc-df6b-4080-a748-d96966f3e5ce','06c5d10a-5341-415a-a59e-6ef3633e1ddc','46e26848-2807-4fcf-bf5a-4c6f5b173b00','f7f7d451-f246-4309-84aa-c4702f46c1ff','3b62359c-6b3e-4f7f-b2a0-e113f294fcec','7c58a202-c83d-49ec-b49a-6a7ffaaeb615','d810d82d-2db5-4eda-88bd-f37320ee0341','7e24f978-adc8-4a64-889c-429abf7c0d55','d3d8d73a-e6a2-4930-90a1-bd549046ad17','63eb6ae6-9b60-41aa-bd72-c924334e15b8','b148d5b8-56ef-40eb-a07d-70774dec2f40');
DELETE FROM part_vehicle_fitment p USING pvf_repair_119_backup b WHERE b.repair = 'R4' AND p.id = b.id;

-- the JSON copies (compatible_vehicles) of the repaired parts are refreshed by the next
-- merge_catalog_fitment_from_part_vehicle_fitment pass (it replaces every part_vehicle_fitment-sourced entry).

-- UNDO (per repair):
--   R1/R2: UPDATE part_vehicle_fitment p SET manufacturer=b.manufacturer, manufacturer_id=b.manufacturer_id, model=b.model,
--          year_from=b.year_from, year_to=b.year_to FROM pvf_repair_119_backup b WHERE b.repair IN ('R1','R2') AND p.id=b.id;
--          INSERT INTO part_vehicle_fitment SELECT (b).* ... for R1 rows deleted as duplicates (0 expected)
--   R3/R4: INSERT INTO part_vehicle_fitment SELECT <columns> FROM pvf_repair_119_backup WHERE repair IN ('R3','R4');
