
async def lookup_oem_spec(db: AsyncSession, limit: int = 100) -> Dict[str, Any]:
    """
    Uses HF / GPT-4o to find OEM numbers for inactive parts with needs_oem_lookup=TRUE.
    After finding OEM: clears needs_oem_lookup so enrich_pending_parts can process them.
    """
    from sqlalchemy import text as _text
    report = {
        "task": "lookup_oem_spec",
        "scanned": 0,
        "oem_found": 0,
        "oem_not_found": 0,
        "errors": 0,
    }

    result = await db.execute(_text("""
        SELECT id, sku, name, name_he, category, part_type, manufacturer
        FROM parts_catalog
        WHERE is_active = FALSE
          AND needs_oem_lookup = TRUE
          AND name IS NOT NULL
          AND name != ''
        ORDER BY created_at ASC
        LIMIT :lim
    """), {"lim": limit})
    rows = result.fetchall()
    report["scanned"] = len(rows)

    if not rows:
        return report

    for row in rows:
        try:
            prompt = (
                f'You are an automotive parts specialist. '
                f'Given this auto part, return the most likely OEM part number.\n\n'
                f'Part name (Hebrew): "{row.name}"\n'
                f'English name hint: "{row.name_he or ""}"\n'
                f'Vehicle manufacturer: "{row.manufacturer}"\n'
                f'Category: "{row.category or "unknown"}"\n'
                f'Part type: "{row.part_type or "unknown"}"\n\n'
                f'Return ONLY valid JSON, no explanation:\n'
                f'{{"oem_number": "EXACT_OEM_NUMBER_OR_NULL", '
                f'"confidence": "high|medium|low", '
                f'"canonical_name_en": "2-5 word English name"}}'
            )

            raw = await hf_text(prompt, system=SYSTEM_PROMPT, timeout=30.0)
            if not raw:
                report["oem_not_found"] += 1
                continue

            raw = raw.strip()
            raw = re.sub(r'^```(?:json)?\s*', '', raw)
            raw = re.sub(r'```\s*$', '', raw)
            
            s, e = raw.find("{"), raw.rfind("}") + 1
            data = json.loads(raw[s:e]) if s >= 0 and e > s else {}

            oem = (data.get("oem_number") or "").strip()
            confidence = str(data.get("confidence", "low")).lower()
            name_en = (data.get("canonical_name_en") or "").strip()

            if oem and oem.upper() != "NULL" and confidence in ("high", "medium"):
                await db.execute(_text("""
                    UPDATE parts_catalog
                    SET oem_number        = :oem,
                        needs_oem_lookup  = FALSE,
                        name_he           = COALESCE(NULLIF(name_he, ''), :name_en),
                        updated_at        = NOW()
                    WHERE id = :part_id
                """), {
                    "oem": oem,
                    "name_en": name_en,
                    "part_id": str(row.id),
                })
                await db.commit()
                report["oem_found"] += 1
            else:
                report["oem_not_found"] += 1

        except Exception as exc:
            report["errors"] += 1
            print(f"    [ERR] lookup_oem_spec for {row.sku}: {exc}")
            try: await db.rollback()
            except: pass

    return report
