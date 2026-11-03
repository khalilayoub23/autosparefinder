WITH b AS (
    SELECT id, part_id, substr(model, 6) AS m, year_from
    FROM part_vehicle_fitment
    WHERE manufacturer = 'Ford' AND notes = 'extracted:name_he' AND model LIKE 'Ford %'
    ORDER BY id LIMIT 1000
    FOR UPDATE SKIP LOCKED
), dup AS (
    DELETE FROM part_vehicle_fitment p USING b
    WHERE p.id = b.id
      AND EXISTS (SELECT 1 FROM part_vehicle_fitment q
                  WHERE q.part_id = b.part_id AND q.manufacturer = 'Ford' AND q.model = b.m AND q.year_from = b.year_from)
    RETURNING p.id
)
UPDATE part_vehicle_fitment p SET model = b.m, updated_at = NOW()
FROM b WHERE p.id = b.id AND p.id NOT IN (SELECT id FROM dup)
