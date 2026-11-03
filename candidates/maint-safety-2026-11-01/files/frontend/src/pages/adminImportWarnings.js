// Admin parts-import result: what the server could NOT apply must be shown, never reported as success.
// (backend/routes/admin.py import_parts_excel returns `warnings` and `unsupported_columns`.)
export function importWarnings(result) {
  if (!result || result.ok === false) return []
  const out = Array.isArray(result.warnings) ? [...result.warnings] : []
  const cols = Array.isArray(result.unsupported_columns) ? result.unsupported_columns : []
  if (cols.length && !out.length) out.push(`עמודות שלא נתמכות ולא יושמו: ${cols.join(', ')}`)
  return out
}
