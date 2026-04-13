import fs from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { partFamilySvgMarkup } from '../frontend/src/components/partFamilyVisuals.js'

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(SCRIPT_DIR, '..')
const OUTPUT_DIR = path.join(ROOT, 'frontend', 'public', 'part-family', 'generated')

const FAMILIES = [
  { id: 'filters', label: 'פילטרים', palette: ['#d97706', '#f59e0b'], icon_key: 'filter' },
  { id: 'fluids', label: 'שמנים ונוזלים', palette: ['#0284c7', '#38bdf8'], icon_key: 'fluid' },
  { id: 'belts-chains', label: 'רצועות ושרשראות', palette: ['#7c3aed', '#a78bfa'], icon_key: 'belt' },
  { id: 'service-general', label: 'טיפול וכללי', palette: ['#475569', '#94a3b8'], icon_key: 'service' },
  { id: 'engine', label: 'מנוע', palette: ['#dc2626', '#fb7185'], icon_key: 'engine' },
  { id: 'cooling', label: 'קירור מנוע', palette: ['#0f766e', '#2dd4bf'], icon_key: 'cooling' },
  { id: 'fuel-air', label: 'דלק ויניקה', palette: ['#2563eb', '#60a5fa'], icon_key: 'fuel' },
  { id: 'exhaust', label: 'פליטה ו-EGR', palette: ['#7c2d12', '#fb923c'], icon_key: 'exhaust' },
  { id: 'clutch-drivetrain', label: "קלאץ' והנעה", palette: ['#9333ea', '#c084fc'], icon_key: 'drivetrain' },
  { id: 'gearbox', label: 'גיר ותמסורת', palette: ['#4f46e5', '#818cf8'], icon_key: 'gearbox' },
  { id: 'brakes', label: 'בלמים', palette: ['#b91c1c', '#f87171'], icon_key: 'brake' },
  { id: 'suspension-steering', label: 'מתלה והיגוי', palette: ['#0f766e', '#34d399'], icon_key: 'suspension' },
  { id: 'wheels-bearings', label: 'גלגלים ומיסבים', palette: ['#374151', '#9ca3af'], icon_key: 'wheel' },
  { id: 'body-exterior', label: 'מרכב וחוץ', palette: ['#0891b2', '#67e8f9'], icon_key: 'body' },
  { id: 'lighting', label: 'תאורה', palette: ['#ca8a04', '#fde047'], icon_key: 'lighting' },
  { id: 'electrical-sensors', label: 'חשמל וחיישנים', palette: ['#1d4ed8', '#93c5fd'], icon_key: 'electrical' },
  { id: 'air-conditioning-heating', label: 'מיזוג וחימום', palette: ['#0369a1', '#7dd3fc'], icon_key: 'climate' },
  { id: 'wipers-washers', label: 'מגבים וניקוי שמשות', palette: ['#0f766e', '#99f6e4'], icon_key: 'wiper' },
  { id: 'interior-comfort', label: 'פנים ונוחות', palette: ['#7c3aed', '#ddd6fe'], icon_key: 'interior' },
  { id: 'accessories', label: 'אביזרים וכללי', palette: ['#475569', '#cbd5e1'], icon_key: 'accessories' },
]

async function main() {
  await fs.mkdir(OUTPUT_DIR, { recursive: true })

  for (const family of FAMILIES) {
    const filePath = path.join(OUTPUT_DIR, `${family.id}.svg`)
    await fs.writeFile(filePath, `${partFamilySvgMarkup(family)}\n`, 'utf8')
  }

  console.log(`Generated ${FAMILIES.length} part-family asset(s) in ${path.relative(ROOT, OUTPUT_DIR)}`)
}

main().catch((error) => {
  console.error(error)
  process.exitCode = 1
})