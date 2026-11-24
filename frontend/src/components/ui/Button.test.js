import { describe, expect, it } from 'vitest'
import { buttonClassName } from './Button'

describe('buttonClassName', () => {
  it('maps variants and sizes to the shared classes', () => {
    expect(buttonClassName({ variant: 'danger', size: 'sm' })).toContain('btn-danger btn-sm')
    expect(buttonClassName()).toContain('btn-primary btn-md')
    expect(buttonClassName({ variant: 'nope' })).toContain('btn-primary')
  })
  it('never carries a radius utility of its own', () => {
    expect(buttonClassName({ variant: 'secondary' })).not.toMatch(/rounded/)
  })
})
