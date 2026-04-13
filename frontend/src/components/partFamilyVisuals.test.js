import { describe, expect, it } from 'vitest'
import { partFamilyImageSrc, partFamilySvgMarkup } from './partFamilyVisuals'

describe('partFamilyVisuals', () => {
  it('returns a stable asset path for the same known family', () => {
    const family = {
      id: 'filters',
      label: 'Filters',
      palette: ['#2563eb', '#7dd3fc'],
      icon_key: 'filter',
    }

    const firstUri = partFamilyImageSrc(family)
    const secondUri = partFamilyImageSrc(family)

    expect(firstUri).toBe(secondUri)
    expect(firstUri).toContain('/part-family/real/filters.jpg')
  })

  it('returns a real photo path for known families', () => {
    const uri = partFamilyImageSrc({
      id: 'body-exterior',
      label: 'Body',
      palette: ['#f97316', '#fdba74'],
      icon_key: 'body',
    })

    expect(uri).toContain('/part-family/real/body-exterior.jpg')
  })

  it('generates fallback svg markup with gradient background and label', () => {
    const svg = partFamilySvgMarkup(null)

    expect(svg).toContain("aria-label='Parts'")
    expect(svg).toContain("width='160'")
    expect(svg).toContain("height='96'")
    expect(svg).toContain("id='bg'")
    expect(svg).toContain('fill=\'white\'')
  })

  it('includes the requested part family label and uses a colored gradient background', () => {
    const svg = partFamilySvgMarkup({
      id: 'body-exterior',
      label: 'Body',
      palette: ['#f97316', '#fdba74'],
      icon_key: 'body',
    })

    expect(svg).toContain("aria-label='Body'")
    // New style: gradient 'bg' driven by the family's palette
    expect(svg).toContain("id='bg'")
    expect(svg).toContain("stop-color='#f97316'")
    expect(svg).toContain("stop-color='#fdba74'")
    // Dark label tray at bottom
    expect(svg).toContain('rgba(0,0,0,0.52)')
    // White label text
    expect(svg).toContain('fill=\'white\'')
    // clipPath for rounded corners
    expect(svg).toContain("id='clip'")
  })
})