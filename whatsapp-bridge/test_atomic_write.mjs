/**
 * Regression tests — atomic_write.js (2026-09-12).
 *
 * Proves the creds.json 0-byte vulnerability (see the forensic report) is
 * closed: an interrupted write can never leave the previously-valid target
 * file truncated, empty, or partially written.
 *
 * Uses a REAL directory on the SAME filesystem as production `auth_info/`
 * (a sibling directory under whatsapp-bridge/, never touching auth_info/
 * itself) so the atomicity guarantee under test — temp file and target must
 * share a filesystem for rename() to be atomic — is exercised for real, not
 * assumed. Crash-point tests spawn a REAL child process and SIGKILL it at
 * precise instrumented points (test_atomic_write_crash_harness.mjs) so the
 * "interruption" is an actual abrupt process death, not a caught exception.
 *
 * Run: node whatsapp-bridge/test_atomic_write.mjs
 */
import { promises as fsp } from 'fs'
import path from 'path'
import { fileURLToPath } from 'url'
import { spawn } from 'child_process'
import { atomicWriteFile, _pendingWriteCount } from './atomic_write.js'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const TEST_DIR = path.join(__dirname, '_test_tmp_authdir') // same filesystem as auth_info/, never auth_info/ itself
const HARNESS = path.join(__dirname, 'test_atomic_write_crash_harness.mjs')

let passed = 0
let failed = 0

function assert(label, actual, expected) {
  const ok = actual === expected
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${label}`)
  if (!ok) {
    console.log(`        expected: ${JSON.stringify(expected)}`)
    console.log(`        actual:   ${JSON.stringify(actual)}`)
  }
  ok ? passed++ : failed++
}

function section(name) {
  console.log(`\n${name}`)
}

async function resetTestDir() {
  await fsp.rm(TEST_DIR, { recursive: true, force: true })
  await fsp.mkdir(TEST_DIR, { recursive: true })
}

async function readFileOrNull(p) {
  try {
    return await fsp.readFile(p, 'utf-8')
  } catch {
    return null
  }
}

async function listTempFiles(dir, base) {
  const entries = await fsp.readdir(dir)
  return entries.filter((e) => e.startsWith(`.${base}.tmp-`))
}

function runCrashHarness(targetPath, content, crashPoint) {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [HARNESS, targetPath, content, crashPoint], {
      stdio: 'ignore',
    })
    child.on('exit', (code, signal) => resolve({ code, signal }))
  })
}

async function main() {
  // ═══════════════════════════════════════════════════════════════════════
  section('1. Normal atomic write — full replace, valid JSON')
  await resetTestDir()
  {
    const target = path.join(TEST_DIR, 'creds.json')
    const oldContent = JSON.stringify({ me: { id: 'old' } })
    await fsp.writeFile(target, oldContent)
    const newContent = JSON.stringify({ me: { id: 'new' }, registered: true })
    await atomicWriteFile(target, newContent)
    const result = await readFileOrNull(target)
    assert('final file content is the new complete JSON', result, newContent)
    assert('final file parses as valid JSON', (() => { try { JSON.parse(result); return true } catch { return false } })(), true)
  }

  // ═══════════════════════════════════════════════════════════════════════
  section('2. Failure BEFORE any temp file is created (e.g. directory unwritable) — original file untouched')
  await resetTestDir()
  {
    const target = path.join(TEST_DIR, 'creds.json')
    const VALID_OLD_CREDS = JSON.stringify({ me: { id: 'valid-old' } })
    await fsp.writeFile(target, VALID_OLD_CREDS)

    // Make the directory read-only so opening a NEW temp file fails with EACCES —
    // a real OS-level failure, not a mock. (Running as root would bypass this,
    // so this test degrades to a soft check rather than a hard requirement.)
    let permissionTestApplicable = true
    await fsp.chmod(TEST_DIR, 0o555)
    try {
      await atomicWriteFile(target, JSON.stringify({ me: { id: 'should-not-land' } }))
      permissionTestApplicable = false // write unexpectedly succeeded (e.g. running as root)
    } catch (err) {
      assert('atomicWriteFile() rejects when the temp file cannot be created', err instanceof Error, true)
    } finally {
      await fsp.chmod(TEST_DIR, 0o755) // restore so cleanup/next tests work
    }
    if (permissionTestApplicable) {
      const result = await readFileOrNull(target)
      assert('original creds.json is UNCHANGED after a failed write', result, VALID_OLD_CREDS)
    } else {
      console.log('  SKIP  (running as root — permission-based failure injection not applicable in this environment)')
    }
  }

  // ═══════════════════════════════════════════════════════════════════════
  section('3. Write interruption via injected mid-write exception — target untouched, temp file cleaned up')
  await resetTestDir()
  {
    const target = path.join(TEST_DIR, 'creds.json')
    const VALID_OLD_CREDS = JSON.stringify({ me: { id: 'valid-old-3' } })
    await fsp.writeFile(target, VALID_OLD_CREDS)

    // Monkey-patch fs.promises.open just for this test to return a FileHandle
    // whose writeFile() throws AFTER the temp file exists but before it's complete.
    const origOpen = fsp.open.bind(fsp)
    fsp.open = async (...args) => {
      const fh = await origOpen(...args)
      const origWriteFile = fh.writeFile.bind(fh)
      fh.writeFile = async () => {
        throw new Error('injected write failure')
      }
      return fh
    }
    let threw = null
    try {
      await atomicWriteFile(target, JSON.stringify({ me: { id: 'should-not-land-3' } }))
    } catch (err) {
      threw = err
    } finally {
      fsp.open = origOpen
    }
    assert('atomicWriteFile() propagates the injected failure', threw?.message, 'injected write failure')
    const result = await readFileOrNull(target)
    assert('creds.json still equals the previous valid content', result, VALID_OLD_CREDS)
    const leftoverTemps = await listTempFiles(TEST_DIR, 'creds.json')
    assert('no leftover temp file after a caught write failure', leftoverTemps.length, 0)
  }

  // ═══════════════════════════════════════════════════════════════════════
  section('4. Existing 0-BYTE file (production symptom, reproduced in isolation) — recoverable with valid new content')
  await resetTestDir()
  {
    const target = path.join(TEST_DIR, 'creds.json')
    await fsp.writeFile(target, '') // exactly reproduces the production symptom, in an isolated test dir
    assert('setup: file starts at 0 bytes', (await fsp.stat(target)).size, 0)

    const freshCreds = JSON.stringify({ me: { id: 'freshly-authenticated' }, registered: true })
    await atomicWriteFile(target, freshCreds)
    const result = await readFileOrNull(target)
    assert('0-byte file is replaced with the complete new content', result, freshCreds)
    assert('result is valid JSON', (() => { try { JSON.parse(result); return true } catch { return false } })(), true)
  }

  // ═══════════════════════════════════════════════════════════════════════
  section('5. Concurrent overlapping writes to the SAME path — no corruption, no temp-file collision')
  await resetTestDir()
  {
    const target = path.join(TEST_DIR, 'creds.json')
    await fsp.writeFile(target, JSON.stringify({ me: { id: 'concurrent-seed' } }))

    const N = 12
    const contents = Array.from({ length: N }, (_, i) => JSON.stringify({ me: { id: `concurrent-${i}` }, seq: i }))
    // Fire all N WITHOUT awaiting individually first — genuine overlap, exactly
    // how multiple creds.update events firing close together would behave.
    await Promise.all(contents.map((c) => atomicWriteFile(target, c)))

    const result = await readFileOrNull(target)
    let parsed = null
    try { parsed = JSON.parse(result) } catch {}
    assert('final file is valid, parseable JSON after concurrent writes', parsed !== null, true)
    assert('final content is EXACTLY one of the attempted writes (no interleaving/corruption)',
           contents.includes(result), true)
    const leftoverTemps = await listTempFiles(TEST_DIR, 'creds.json')
    assert('no leftover/colliding temp files after concurrent writes', leftoverTemps.length, 0)
  }

  // ═══════════════════════════════════════════════════════════════════════
  section('6. Existing valid file survives a deliberately-failing write (explicit VALID_OLD_CREDS / NEW_CREDS scenario)')
  await resetTestDir()
  {
    const target = path.join(TEST_DIR, 'creds.json')
    const VALID_OLD_CREDS = JSON.stringify({ me: { id: 'VALID_OLD_CREDS' }, registered: true, keyMaterial: 'xyz123' })
    await fsp.writeFile(target, VALID_OLD_CREDS)

    const origOpen = fsp.open.bind(fsp)
    fsp.open = async (...args) => {
      const fh = await origOpen(...args)
      fh.sync = async () => { throw new Error('injected fsync failure') }
      return fh
    }
    let threw = null
    try {
      await atomicWriteFile(target, JSON.stringify({ me: { id: 'NEW_CREDS' } }))
    } catch (err) {
      threw = err
    } finally {
      fsp.open = origOpen
    }
    // Note: fsync failures are caught as best-effort inside atomic_write.js
    // and do NOT abort the write (documented durability tradeoff) — so this
    // specific failure mode is expected to SUCCEED. This test proves that
    // choice is deliberate and the resulting file is still fully valid, not
    // an unexpected/unhandled crash.
    assert('an fsync failure does not throw (documented best-effort durability)', threw, null)
    const result = await readFileOrNull(target)
    let parsed = null
    try { parsed = JSON.parse(result) } catch {}
    assert('result is valid JSON even when fsync itself failed', parsed !== null, true)
  }

  // ═══════════════════════════════════════════════════════════════════════
  section('7. Temp-file cleanup — normal operation never accumulates temp files')
  await resetTestDir()
  {
    const target = path.join(TEST_DIR, 'creds.json')
    await fsp.writeFile(target, JSON.stringify({ me: { id: 'seed-7' } }))
    for (let i = 0; i < 20; i++) {
      await atomicWriteFile(target, JSON.stringify({ me: { id: `iter-${i}` } }))
    }
    const leftovers = await listTempFiles(TEST_DIR, 'creds.json')
    assert('20 successful sequential writes leave zero temp files behind', leftovers.length, 0)
    assert('internal write-chain map does not grow per-call (bounded by distinct paths)', _pendingWriteCount() <= 2, true)
  }

  // ═══════════════════════════════════════════════════════════════════════
  section('8. Crash-point analysis — REAL process SIGKILL at each meaningful stage (Phase 5)')
  await resetTestDir()
  {
    const target = path.join(TEST_DIR, 'creds.json')
    const VALID_OLD_CREDS = JSON.stringify({ me: { id: 'crash-baseline' } })
    const NEW_CREDS = JSON.stringify({ me: { id: 'crash-new' }, registered: true })

    // 8a. before_open — die before the temp file exists at all
    await fsp.writeFile(target, VALID_OLD_CREDS)
    let r = await runCrashHarness(target, NEW_CREDS, 'before_open')
    assert('8a. harness was actually killed (SIGKILL)', r.signal, 'SIGKILL')
    assert('8a. target unchanged after crash before temp creation', await readFileOrNull(target), VALID_OLD_CREDS)
    assert('8a. no temp file left behind', (await listTempFiles(TEST_DIR, 'creds.json')).length, 0)

    // 8b. during_write — temp file exists but is only PARTIALLY written when killed
    await fsp.writeFile(target, VALID_OLD_CREDS)
    r = await runCrashHarness(target, NEW_CREDS, 'during_write')
    assert('8b. harness was actually killed (SIGKILL)', r.signal, 'SIGKILL')
    assert('8b. target UNCHANGED despite a partially-written temp file existing', await readFileOrNull(target), VALID_OLD_CREDS)
    {
      const temps = await listTempFiles(TEST_DIR, 'creds.json')
      console.log(`        (informational) leftover partial temp file(s) after SIGKILL: ${temps.length}`)
      // This IS the documented cleanup gap: a hard kill mid-write cannot run our
      // own catch-block unlink(). The INVARIANT under test — the target itself —
      // still holds. See report Phase 5/7 for the startup-cleanup discussion.
    }

    // 8c. after_close_before_rename — temp file fully written+durable, killed
    // before the rename that would have installed it.
    await resetTestDir()
    await fsp.writeFile(target, VALID_OLD_CREDS)
    r = await runCrashHarness(target, NEW_CREDS, 'after_close_before_rename')
    assert('8c. harness was actually killed (SIGKILL)', r.signal, 'SIGKILL')
    assert('8c. target STILL the old content — new data never got installed', await readFileOrNull(target), VALID_OLD_CREDS)
    {
      const temps = await listTempFiles(TEST_DIR, 'creds.json')
      assert('8c. the complete (but not-yet-renamed) temp file is left on disk (recoverable, not corrupted)', temps.length, 1)
    }

    // 8d. after_rename — rename() already completed (atomic, irreversible at
    // this point); killed only before the EXTRA best-effort directory fsync.
    await resetTestDir()
    await fsp.writeFile(target, VALID_OLD_CREDS)
    r = await runCrashHarness(target, NEW_CREDS, 'after_rename')
    assert('8d. harness was actually killed (SIGKILL)', r.signal, 'SIGKILL')
    assert('8d. target correctly shows the NEW content — rename already committed', await readFileOrNull(target), NEW_CREDS)

    // 8e. none — full run inside the same harness process, sanity check the
    // harness itself introduces no behavioral difference when nothing crashes.
    await resetTestDir()
    await fsp.writeFile(target, VALID_OLD_CREDS)
    r = await runCrashHarness(target, NEW_CREDS, 'none')
    assert('8e. harness exits cleanly with no crash point selected', r.code, 0)
    assert('8e. target correctly updated to new content', await readFileOrNull(target), NEW_CREDS)
  }

  await fsp.rm(TEST_DIR, { recursive: true, force: true })

  console.log(`\n${passed} passed, ${failed} failed`)
  if (failed > 0) process.exit(1)
}

main()
