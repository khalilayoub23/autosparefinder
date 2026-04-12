import fs from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(SCRIPT_DIR, '..')
const TARGET_DIR = path.join(ROOT, 'frontend', 'public', 'part-family')
const IMAGE_EXTENSIONS = new Set(['.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg'])

function looksLikeSvg(buffer) {
  const head = buffer.toString('utf8', 0, Math.min(buffer.length, 512)).trimStart().toLowerCase()
  return head.startsWith('<svg') || head.startsWith('<?xml')
}

function detectType(buffer) {
  if (buffer.length >= 3 && buffer[0] === 0xff && buffer[1] === 0xd8 && buffer[2] === 0xff) {
    return 'jpeg'
  }
  if (
    buffer.length >= 8 &&
    buffer[0] === 0x89 &&
    buffer[1] === 0x50 &&
    buffer[2] === 0x4e &&
    buffer[3] === 0x47 &&
    buffer[4] === 0x0d &&
    buffer[5] === 0x0a &&
    buffer[6] === 0x1a &&
    buffer[7] === 0x0a
  ) {
    return 'png'
  }
  if (buffer.length >= 6) {
    const signature = buffer.toString('ascii', 0, 6)
    if (signature === 'GIF87a' || signature === 'GIF89a') {
      return 'gif'
    }
  }
  if (buffer.length >= 12 && buffer.toString('ascii', 0, 4) === 'RIFF' && buffer.toString('ascii', 8, 12) === 'WEBP') {
    return 'webp'
  }
  if (looksLikeSvg(buffer)) {
    return 'svg'
  }

  const head = buffer.toString('utf8', 0, Math.min(buffer.length, 512)).trimStart().toLowerCase()
  if (head.startsWith('<!doctype html') || head.startsWith('<html') || head.includes('<title>wikimedia error')) {
    return 'html'
  }

  return 'unknown'
}

function expectedTypesForExtension(extension) {
  switch (extension) {
    case '.jpg':
    case '.jpeg':
      return new Set(['jpeg'])
    case '.png':
      return new Set(['png'])
    case '.gif':
      return new Set(['gif'])
    case '.webp':
      return new Set(['webp'])
    case '.svg':
      return new Set(['svg'])
    default:
      return new Set()
  }
}

async function main() {
  const failures = []
  let checked = 0

  async function visit(dirPath) {
    const entries = await fs.readdir(dirPath, { withFileTypes: true })

    for (const entry of entries) {
      const filePath = path.join(dirPath, entry.name)
      if (entry.isDirectory()) {
        await visit(filePath)
        continue
      }
      if (!entry.isFile()) continue
      const extension = path.extname(entry.name).toLowerCase()
      if (!IMAGE_EXTENSIONS.has(extension)) continue

      const buffer = await fs.readFile(filePath)
      const actualType = detectType(buffer)
      const expectedTypes = expectedTypesForExtension(extension)
      checked += 1

      if (!expectedTypes.has(actualType)) {
        failures.push({
          file: path.relative(ROOT, filePath),
          extension,
          actualType,
        })
      }
    }
  }

  await visit(TARGET_DIR)

  if (failures.length > 0) {
    console.error('Invalid part-family assets detected:')
    for (const failure of failures) {
      console.error(`- ${failure.file}: extension ${failure.extension} does not match detected type ${failure.actualType}`)
    }
    process.exitCode = 1
    return
  }

  console.log(`Validated ${checked} part-family asset(s) in frontend/public/part-family`)
}

main().catch((error) => {
  console.error(error)
  process.exitCode = 1
})