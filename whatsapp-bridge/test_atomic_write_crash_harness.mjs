/**
 * Crash-injection child process for atomic_write.js regression tests.
 * NOT run directly by a human — spawned by test_atomic_write.mjs as a
 * separate OS process so it can be SIGKILLed mid-operation without taking
 * down the test runner itself (this is what makes the interruption real,
 * not simulated with a catchable thrown error).
 *
 * Usage: node test_atomic_write_crash_harness.mjs <targetPath> <newContent> <crashPoint>
 *
 * crashPoint one of:
 *   none                        - no injected failure, should succeed normally
 *   before_open                 - die before the temp file is even created
 *   during_write                - write PART of the content, then die
 *   after_close_before_rename   - temp file fully written+closed, die before rename()
 *   after_rename                - rename() has completed, die before the
 *                                 best-effort directory fsync afterward
 *
 * ("during_rename" is deliberately NOT offered — POSIX rename() is a single
 * atomic syscall with no OS-visible partial state to interrupt into; see the
 * crash-safety analysis in the final report for why this is a real
 * guarantee, not an untested gap.)
 */
import { promises as fsp } from 'fs'
import { atomicWriteFile } from './atomic_write.js'

const [, , targetPath, newContent, crashPoint] = process.argv

function die() {
  process.kill(process.pid, 'SIGKILL')
}

const origOpen = fsp.open.bind(fsp)
const origRename = fsp.rename.bind(fsp)

fsp.open = async (...args) => {
  const isTempFile = String(args[0]).includes('.tmp-')
  if (isTempFile && crashPoint === 'before_open') die()
  const fh = await origOpen(...args)
  if (!isTempFile) return fh // only instrument the TEMP file handle, not the directory-fsync handle

  const origWriteFile = fh.writeFile.bind(fh)
  const origSync = fh.sync.bind(fh)
  const origClose = fh.close.bind(fh)

  fh.writeFile = async (data) => {
    if (crashPoint === 'during_write') {
      await origWriteFile(String(data).slice(0, Math.max(1, Math.floor(String(data).length / 2))))
      die()
    }
    return origWriteFile(data)
  }
  fh.close = async (...a) => {
    const r = await origClose(...a)
    if (crashPoint === 'after_close_before_rename') die()
    return r
  }
  // sync() itself is not a crash point here — after_close_before_rename covers
  // "fully durable temp file, not yet installed", which is the meaningful case.
  void origSync
  return fh
}

fsp.rename = async (...args) => {
  const r = await origRename(...args)
  if (crashPoint === 'after_rename') die()
  return r
}

await atomicWriteFile(targetPath, newContent)
process.exit(0)
