// Run: node --test src/pages/adminImportWarnings.node-test.mjs   (no dependencies)
import test from 'node:test'
import assert from 'node:assert/strict'
import { importWarnings } from './adminImportWarnings.js'

test('a price column that was not applied is surfaced', () => {
  const w = importWarnings({ ok: true, created: 1, updated: 1, unsupported_columns: ['base_price'], warnings: ['x not applied'] })
  assert.deepEqual(w, ['x not applied'])
})
test('unsupported column without a server message still warns', () => {
  assert.equal(importWarnings({ ok: true, unsupported_columns: ['base_price'] }).length, 1)
})
test('clean import and failed import produce no warnings', () => {
  assert.deepEqual(importWarnings({ ok: true, created: 2, updated: 0 }), [])
  assert.deepEqual(importWarnings({ ok: false, msg: 'err' }), [])
  assert.deepEqual(importWarnings(null), [])
})
