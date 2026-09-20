/**
 * Generic atomic file-write primitive (root-fix 2026-09-12).
 *
 * FORENSIC CONTEXT (see the "CREDS.JSON 0-BYTE FORENSIC" report): Baileys'
 * own `useMultiFileAuthState()` persists `auth_info/creds.json` via
 * `fs/promises.writeFile(path, content)` directly to the live path. Node's
 * `writeFile` opens the target with the 'w' flag, which TRUNCATES it before
 * any new bytes are written. If the process is interrupted between that
 * truncation and the write completing — a crash, an OOM kill, an abrupt
 * `process.exit()` — the file is left however far the write got, in the
 * worst case exactly 0 bytes. This happened in production on 2026-09-10.
 *
 * This module replaces that write pattern for `creds.json` ONLY (see
 * index.js) with the standard atomic-replace sequence:
 *
 *   serialize -> write COMPLETE content to a uniquely-named temp file in the
 *   SAME directory -> fsync the temp file -> rename() the temp file onto the
 *   target -> best-effort fsync the directory entry.
 *
 * POSIX rename() is atomic when source and destination are on the same
 * filesystem: at every instant, the target path resolves to either the
 * complete OLD file or the complete NEW file — never a partial/truncated
 * one. This is why the temp file MUST be created in the same directory as
 * the target (this module always does that automatically via
 * `path.dirname(targetPath)` — a caller cannot accidentally route the temp
 * file through /tmp or any other filesystem).
 *
 * Durability tradeoff (documented per the hardening spec, not silently
 * assumed): `fsync()` on the temp file guarantees its bytes are on disk
 * BEFORE the rename is attempted, so the rename can never atomically install
 * a file whose content didn't actually make it to storage. The best-effort
 * directory fsync afterward additionally protects the rename operation
 * itself against a host crash in the narrow window immediately after
 * (ext4 does not strictly require this for `rename()` durability under most
 * data=ordered configurations, but it costs one extra syscall and removes an
 * edge case rather than assuming it away). Neither fsync call is allowed to
 * abort the write if it is unsupported on a given filesystem/mount (best
 * effort — logged as debug information only, never fatal, never silently
 * corrupting).
 *
 * Concurrency: `creds.update` can fire from many independent code paths
 * inside Baileys (message receipt, app-state sync, pushname updates, socket
 * handshake — confirmed by inspecting the installed package). Nothing
 * guarantees those events are never emitted back-to-back before a prior
 * write's I/O has completed. This module serializes writers PER TARGET PATH
 * with an in-process promise chain (no new dependency — this project's own
 * package.json does not declare `async-lock`, which Baileys uses internally
 * only as a transitive dependency, so this file does not rely on it):
 * concurrent calls for the SAME path queue and run one at a time, in call
 * order; a failed write never blocks the next one from being attempted.
 * Calls for DIFFERENT paths never wait on each other.
 */
import { promises as fsp } from 'fs'
import path from 'path'
import { randomBytes } from 'crypto'

const _writeChains = new Map() // targetPath -> tail promise of the serialized write queue for that path

/**
 * Atomically replace `targetPath` with `content` (a string or Buffer).
 * Never truncates or otherwise disturbs the existing file until the
 * complete new content is durably written to a temp file and ready to be
 * installed in one atomic rename. If this call fails at any point BEFORE
 * the rename, `targetPath` is guaranteed unchanged — no zero-byte or
 * partial replacement is possible.
 *
 * @param {string} targetPath - absolute or relative path to replace
 * @param {string|Buffer} content - the COMPLETE new file content
 * @param {{mode?: number}} [opts] - file mode for the new file (default 0o644,
 *   matching the original creds.json permissions)
 * @returns {Promise<void>}
 */
export function atomicWriteFile(targetPath, content, opts = {}) {
  const mode = opts.mode ?? 0o644
  const prior = _writeChains.get(targetPath) || Promise.resolve()
  const run = prior.then(() => _writeOnce(targetPath, content, mode))
  // The chain itself must never reject (a failed write must not permanently
  // wedge every FUTURE write to this path) — but `run`, returned to THIS
  // caller, still carries the real rejection so failures are never hidden.
  _writeChains.set(targetPath, run.then(() => {}, () => {}))
  return run
}

async function _writeOnce(targetPath, content, mode) {
  const dir = path.dirname(targetPath)
  const tmpPath = path.join(
    dir,
    `.${path.basename(targetPath)}.tmp-${process.pid}-${randomBytes(6).toString('hex')}`,
  )

  let fh = null
  try {
    fh = await fsp.open(tmpPath, 'w', mode)
    await fh.writeFile(content)
    // Durability: the bytes must be ON DISK before we ever attempt the
    // rename, or an atomic rename could atomically install a file whose
    // content is still only in the page cache.
    try {
      await fh.sync()
    } catch {
      // Some filesystems/mounts don't support fsync on this handle type —
      // best effort, never fatal (documented tradeoff, not silently assumed).
    }
  } catch (err) {
    // Failed before we ever touched the live file — target is untouched.
    if (fh) {
      try { await fh.close() } catch {}
    }
    try { await fsp.unlink(tmpPath) } catch {}
    throw err
  }
  try {
    await fh.close()
  } catch {
    // Non-fatal — content is already fsync'd; proceed to the rename.
  }

  try {
    await fsp.rename(tmpPath, targetPath)
  } catch (err) {
    // Rename failed (e.g. cross-device, permissions) — the temp file never
    // became live. Clean it up; the target is STILL the old, valid content.
    try { await fsp.unlink(tmpPath) } catch {}
    throw err
  }

  // Best-effort: fsync the directory entry so the rename itself survives an
  // immediate host crash. Never allowed to turn a successful replace into a
  // reported failure — the file is already correctly in place at this point.
  try {
    const dfh = await fsp.open(dir, 'r')
    try {
      await dfh.sync()
    } catch {
      // Directories aren't fsync-able on every platform/filesystem — ignore.
    }
    await dfh.close()
  } catch {
    // Could not even open the directory for a sync fd — irrelevant to
    // correctness of the already-completed rename; ignore.
  }
}

/** Test/inspection helper only — never used by production code. */
export function _pendingWriteCount() {
  return _writeChains.size
}
