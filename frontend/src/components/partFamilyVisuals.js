const SVG_CACHE = new Map()
const BASE_URL = import.meta.env?.BASE_URL || '/'

const DEFAULT_FAMILY = {
  id: 'accessories',
  label: 'Parts',
  palette: ['#475569', '#cbd5e1'],
  icon_key: 'accessories',
}

function escapeXml(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;')
}

function normalizeColor(value, fallback) {
  return /^#[0-9a-f]{6}$/i.test(value || '') ? value : fallback
}

function normalizeFamily(family) {
  if (!family) return DEFAULT_FAMILY
  return {
    id: family.id || DEFAULT_FAMILY.id,
    label: family.label || family.id || DEFAULT_FAMILY.label,
    palette: [
      normalizeColor(family.palette?.[0], DEFAULT_FAMILY.palette[0]),
      normalizeColor(family.palette?.[1], DEFAULT_FAMILY.palette[1]),
    ],
    icon_key: family.icon_key || DEFAULT_FAMILY.icon_key,
  }
}

function familyCacheKey(family) {
  return [family.id, family.label, family.icon_key, ...(family.palette || [])].join('|')
}

function svgDataUri(svg) {
  return `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`
}

function assetUrl(fileName) {
  return `${BASE_URL}part-family/${fileName}`
}

const PART_FAMILY_ASSET_PATHS = {
  filters: assetUrl('real/filters.jpg'),
  fluids: assetUrl('real/fluids.jpg'),
  'belts-chains': assetUrl('real/belts-chains.jpg'),
  'service-general': assetUrl('real/service-general.jpg'),
  engine: assetUrl('real/engine.jpg'),
  cooling: assetUrl('real/cooling.jpg'),
  'fuel-air': assetUrl('real/fuel-air.jpg'),
  exhaust: assetUrl('real/exhaust.jpg'),
  'clutch-drivetrain': assetUrl('real/clutch-drivetrain.jpg'),
  gearbox: assetUrl('real/gearbox.jpg'),
  brakes: assetUrl('real/brakes.jpg'),
  'suspension-steering': assetUrl('real/suspension-steering.jpg'),
  'wheels-bearings': assetUrl('real/wheels-bearings.jpg'),
  'body-exterior': assetUrl('real/body-exterior.jpg'),
  lighting: assetUrl('real/lighting.jpg'),
  'electrical-sensors': assetUrl('real/electrical-sensors.jpg'),
  'air-conditioning-heating': assetUrl('real/air-conditioning-heating.jpg'),
  'wipers-washers': assetUrl('real/wipers-washers.jpg'),
  'interior-comfort': assetUrl('real/interior-comfort.jpg'),
  accessories: assetUrl('real/accessories.jpg'),
}

// Icon art: white/translucent strokes on coloured gradient background.
// Drawing area 160 × 70 px (bottom 26 px reserved for label tray). Centre ≈ (80, 33).
function buildPhotoObject(iconKey) {
  const W = "rgba(255,255,255,0.88)"
  const WF = "rgba(255,255,255,0.18)"
  const WM = "rgba(255,255,255,0.48)"
  const s = (x) => `stroke='${x}'`
  const sw = (n) => `stroke-width='${n}'`
  const f = (x) => `fill='${x}'`
  const lc = "stroke-linecap='round'"
  const lj = "stroke-linejoin='round'"
  void s; void sw; void f; void lc; void lj; void WM // silence any lint
  switch (iconKey) {
    case 'filter':
      // Cylindrical oil/air filter
      return `<g><ellipse cx='80' cy='16' rx='22' ry='6' stroke='${W}' stroke-width='2.5' fill='${WF}'/><rect x='58' y='16' width='44' height='42' stroke='${W}' stroke-width='2.5' fill='${WF}' rx='2'/><ellipse cx='80' cy='58' rx='22' ry='6' stroke='${W}' stroke-width='2.5' fill='${WF}'/><line x1='62' y1='27' x2='98' y2='27' stroke='${WM}' stroke-width='1.5'/><line x1='62' y1='37' x2='98' y2='37' stroke='${WM}' stroke-width='1.5'/><line x1='62' y1='47' x2='98' y2='47' stroke='${WM}' stroke-width='1.5'/></g>`
    case 'fluid':
      // Motor oil bottle
      return `<g><path d='M68 24l0 6-8 8 0 20a10 10 0 0 0 10 10l20 0a10 10 0 0 0 10-10l0-20-8-8 0-6Z' stroke='${W}' stroke-width='2.5' fill='${WF}' stroke-linejoin='round'/><rect x='72' y='12' width='16' height='13' rx='3' stroke='${W}' stroke-width='2' fill='rgba(255,255,255,0.28)'/><line x1='66' y1='46' x2='94' y2='46' stroke='${WM}' stroke-width='1.5'/><line x1='68' y1='54' x2='92' y2='54' stroke='rgba(255,255,255,0.3)' stroke-width='1.5'/></g>`
    case 'belt':
      // V-belt with two pulleys
      return `<g><circle cx='56' cy='34' r='20' stroke='${W}' stroke-width='2.5' fill='${WF}'/><circle cx='56' cy='34' r='7' fill='rgba(255,255,255,0.32)'/><circle cx='110' cy='34' r='13' stroke='${W}' stroke-width='2.5' fill='${WF}'/><circle cx='110' cy='34' r='4' fill='rgba(255,255,255,0.32)'/><line x1='56' y1='14' x2='110' y2='21' stroke='${W}' stroke-width='4' stroke-linecap='round'/><line x1='56' y1='54' x2='110' y2='47' stroke='${W}' stroke-width='4' stroke-linecap='round'/></g>`
    case 'service':
      // Ring-spanner wrench
      return `<g><circle cx='80' cy='28' r='22' stroke='${W}' stroke-width='7' fill='rgba(255,255,255,0.1)'/><circle cx='80' cy='28' r='9' fill='rgba(0,0,0,0.25)' stroke='rgba(255,255,255,0.5)' stroke-width='2'/><path d='M80 18l9 5 0 10-9 5-9-5 0-10Z' stroke='rgba(255,255,255,0.5)' stroke-width='1.5' fill='none'/><rect x='74' y='50' width='12' height='18' rx='6' stroke='${W}' stroke-width='2.5' fill='${WF}'/></g>`
    case 'engine':
      // Piston in cylinder
      return `<g><rect x='52' y='8' width='56' height='40' rx='4' stroke='rgba(255,255,255,0.55)' stroke-width='2' fill='rgba(255,255,255,0.08)'/><rect x='54' y='16' width='52' height='24' rx='3' stroke='${W}' stroke-width='2.5' fill='${WF}'/><line x1='56' y1='24' x2='104' y2='24' stroke='${WM}' stroke-width='2'/><line x1='56' y1='34' x2='104' y2='34' stroke='${WM}' stroke-width='2'/><line x1='80' y1='40' x2='80' y2='60' stroke='${W}' stroke-width='5' stroke-linecap='round'/><circle cx='80' cy='64' r='6' stroke='${W}' stroke-width='2' fill='${WF}'/></g>`
    case 'cooling':
      // Radiator with tubes and fins
      return `<g><rect x='30' y='8' width='100' height='56' rx='5' stroke='rgba(255,255,255,0.7)' stroke-width='2.5' fill='rgba(255,255,255,0.08)'/><line x1='44' y1='10' x2='44' y2='62' stroke='rgba(255,255,255,0.65)' stroke-width='3'/><line x1='57' y1='10' x2='57' y2='62' stroke='rgba(255,255,255,0.65)' stroke-width='3'/><line x1='70' y1='10' x2='70' y2='62' stroke='rgba(255,255,255,0.65)' stroke-width='3'/><line x1='83' y1='10' x2='83' y2='62' stroke='rgba(255,255,255,0.65)' stroke-width='3'/><line x1='96' y1='10' x2='96' y2='62' stroke='rgba(255,255,255,0.65)' stroke-width='3'/><line x1='109' y1='10' x2='109' y2='62' stroke='rgba(255,255,255,0.65)' stroke-width='3'/><line x1='32' y1='24' x2='128' y2='24' stroke='rgba(255,255,255,0.28)' stroke-width='2'/><line x1='32' y1='36' x2='128' y2='36' stroke='rgba(255,255,255,0.28)' stroke-width='2'/><line x1='32' y1='48' x2='128' y2='48' stroke='rgba(255,255,255,0.28)' stroke-width='2'/></g>`
    case 'fuel':
      // Fuel jerry-can
      return `<g><path d='M54 28l0-8 8-8 36 0 8 8 0 38a8 8 0 0 1-8 8l-36 0a8 8 0 0 1-8-8Z' stroke='${W}' stroke-width='2.5' fill='${WF}' stroke-linejoin='round'/><rect x='76' y='8' width='8' height='12' rx='2' stroke='${W}' stroke-width='2' fill='rgba(255,255,255,0.3)'/><line x1='60' y1='42' x2='100' y2='42' stroke='${WM}' stroke-width='2'/><line x1='60' y1='52' x2='100' y2='52' stroke='rgba(255,255,255,0.28)' stroke-width='2'/></g>`
    case 'exhaust':
      // Catalytic converter + pipe
      return `<g><rect x='50' y='24' width='34' height='24' rx='9' stroke='${W}' stroke-width='2.5' fill='${WF}'/><line x1='26' y1='36' x2='50' y2='36' stroke='${W}' stroke-width='6' stroke-linecap='round'/><line x1='84' y1='36' x2='122' y2='36' stroke='${W}' stroke-width='6' stroke-linecap='round'/><ellipse cx='124' cy='36' rx='4' ry='9' stroke='rgba(255,255,255,0.55)' stroke-width='2' fill='${WF}'/><path d='M118 28q3-4 0-8' stroke='rgba(255,255,255,0.38)' stroke-width='2' stroke-linecap='round' fill='none'/><path d='M122 26q4-5-1-10' stroke='rgba(255,255,255,0.25)' stroke-width='1.5' stroke-linecap='round' fill='none'/></g>`
    case 'drivetrain':
      // Clutch disc + shaft
      return `<g><circle cx='66' cy='34' r='22' stroke='${W}' stroke-width='2.5' fill='${WF}'/><circle cx='66' cy='34' r='8' stroke='rgba(255,255,255,0.55)' stroke-width='2' fill='rgba(255,255,255,0.28)'/><line x1='66' y1='12' x2='66' y2='56' stroke='${WM}' stroke-width='1.5'/><line x1='44' y1='34' x2='88' y2='34' stroke='${WM}' stroke-width='1.5'/><rect x='90' y='30' width='32' height='8' rx='4' stroke='${W}' stroke-width='2' fill='${WF}'/></g>`
    case 'gearbox':
      // Two interlocked gears
      return `<g><circle cx='62' cy='38' r='20' stroke='${W}' stroke-width='2.5' fill='${WF}'/><circle cx='62' cy='38' r='7' fill='rgba(0,0,0,0.25)' stroke='rgba(255,255,255,0.5)' stroke-width='2'/><circle cx='102' cy='28' r='14' stroke='${W}' stroke-width='2.5' fill='${WF}'/><circle cx='102' cy='28' r='5' fill='rgba(0,0,0,0.25)' stroke='rgba(255,255,255,0.5)' stroke-width='2'/></g>`
    case 'brake':
      // Brake disc + caliper
      return `<g><circle cx='72' cy='35' r='24' stroke='${W}' stroke-width='2.5' fill='${WF}'/><circle cx='72' cy='35' r='8' fill='rgba(0,0,0,0.3)' stroke='rgba(255,255,255,0.55)' stroke-width='2'/><line x1='72' y1='11' x2='72' y2='59' stroke='${WM}' stroke-width='1.5'/><line x1='48' y1='35' x2='96' y2='35' stroke='${WM}' stroke-width='1.5'/><rect x='92' y='22' width='18' height='26' rx='5' stroke='${W}' stroke-width='2.5' fill='${WF}'/></g>`
    case 'suspension':
      // Coil spring + shock body
      return `<g><line x1='80' y1='6' x2='80' y2='12' stroke='${W}' stroke-width='3' stroke-linecap='round'/><path d='M80 12q-14 4-14 10q0 6 14 10q14 4 14 10q0 6-14 10q-14 4-14 10q0 6 14 10' stroke='${W}' stroke-width='3' fill='none' stroke-linecap='round'/><line x1='80' y1='62' x2='80' y2='68' stroke='${W}' stroke-width='3' stroke-linecap='round'/><rect x='90' y='16' width='12' height='44' rx='6' stroke='rgba(255,255,255,0.6)' stroke-width='2' fill='rgba(255,255,255,0.15)'/></g>`
    case 'wheel':
      // Tyre + rim
      return `<g><circle cx='80' cy='34' r='28' stroke='${W}' stroke-width='7' fill='rgba(255,255,255,0.07)'/><circle cx='80' cy='34' r='16' stroke='rgba(255,255,255,0.72)' stroke-width='2.5' fill='rgba(255,255,255,0.15)'/><circle cx='80' cy='34' r='5' fill='rgba(255,255,255,0.45)'/><line x1='80' y1='20' x2='80' y2='48' stroke='${WM}' stroke-width='1.5'/><line x1='66' y1='34' x2='94' y2='34' stroke='${WM}' stroke-width='1.5'/><line x1='71' y1='24' x2='89' y2='44' stroke='${WM}' stroke-width='1.5'/><line x1='89' y1='24' x2='71' y2='44' stroke='${WM}' stroke-width='1.5'/></g>`
    case 'body':
      // Car side silhouette
      return `<g><path d='M26 50l104 0 0 12a6 6 0 0 1-6 6l-92 0a6 6 0 0 1-6-6Z' stroke='${W}' stroke-width='2' fill='${WF}'/><path d='M44 50l14-22 24 0 20 22' stroke='${W}' stroke-width='2.5' fill='${WF}' stroke-linejoin='round'/><circle cx='52' cy='68' r='8' stroke='${W}' stroke-width='2.5' fill='rgba(255,255,255,0.1)'/><circle cx='108' cy='68' r='8' stroke='${W}' stroke-width='2.5' fill='rgba(255,255,255,0.1)'/></g>`
    case 'lighting':
      // Headlamp with beam rays
      return `<g><path d='M36 22q14-10 32-10a26 26 0 0 1 0 52q-18 0-32-10Z' stroke='${W}' stroke-width='2.5' fill='${WF}'/><line x1='70' y1='34' x2='100' y2='24' stroke='${W}' stroke-width='3' stroke-linecap='round'/><line x1='70' y1='34' x2='106' y2='34' stroke='${W}' stroke-width='3' stroke-linecap='round'/><line x1='70' y1='34' x2='100' y2='44' stroke='${W}' stroke-width='3' stroke-linecap='round'/><line x1='100' y1='24' x2='118' y2='20' stroke='rgba(255,255,255,0.6)' stroke-width='2' stroke-linecap='round'/><line x1='106' y1='34' x2='124' y2='34' stroke='rgba(255,255,255,0.6)' stroke-width='2' stroke-linecap='round'/><line x1='100' y1='44' x2='118' y2='48' stroke='rgba(255,255,255,0.6)' stroke-width='2' stroke-linecap='round'/></g>`
    case 'electrical':
      // Car battery
      return `<g><rect x='36' y='24' width='76' height='42' rx='6' stroke='${W}' stroke-width='2.5' fill='${WF}'/><rect x='52' y='18' width='12' height='8' rx='2' stroke='${W}' stroke-width='2' fill='rgba(255,255,255,0.3)'/><rect x='84' y='18' width='12' height='8' rx='2' stroke='rgba(255,255,255,0.6)' stroke-width='2' fill='rgba(255,255,255,0.2)'/><line x1='54' y1='36' x2='54' y2='52' stroke='${W}' stroke-width='3' stroke-linecap='round'/><line x1='46' y1='44' x2='62' y2='44' stroke='${W}' stroke-width='3' stroke-linecap='round'/><line x1='86' y1='44' x2='98' y2='44' stroke='rgba(255,255,255,0.6)' stroke-width='3' stroke-linecap='round'/></g>`
    case 'climate':
      // Snowflake (AC)
      return `<g><line x1='80' y1='8' x2='80' y2='62' stroke='${W}' stroke-width='3' stroke-linecap='round'/><line x1='48' y1='17' x2='112' y2='53' stroke='${W}' stroke-width='3' stroke-linecap='round'/><line x1='112' y1='17' x2='48' y2='53' stroke='${W}' stroke-width='3' stroke-linecap='round'/><circle cx='80' cy='35' r='8' stroke='rgba(255,255,255,0.65)' stroke-width='2' fill='rgba(255,255,255,0.2)'/><circle cx='80' cy='8' r='3' fill='rgba(255,255,255,0.7)'/><circle cx='80' cy='62' r='3' fill='rgba(255,255,255,0.7)'/><circle cx='48' cy='17' r='3' fill='rgba(255,255,255,0.7)'/><circle cx='112' cy='53' r='3' fill='rgba(255,255,255,0.7)'/><circle cx='112' cy='17' r='3' fill='rgba(255,255,255,0.7)'/><circle cx='48' cy='53' r='3' fill='rgba(255,255,255,0.7)'/></g>`
    case 'wiper':
      // Wiper blade arc
      return `<g><path d='M22 64a78 78 0 0 1 116-52' stroke='rgba(255,255,255,0.32)' stroke-width='10' stroke-linecap='round' fill='none'/><path d='M22 64a78 78 0 0 1 116-52' stroke='${W}' stroke-width='3' stroke-linecap='round' fill='none'/><line x1='22' y1='64' x2='36' y2='52' stroke='${W}' stroke-width='4' stroke-linecap='round'/></g>`
    case 'interior':
      // Steering wheel
      return `<g><circle cx='80' cy='34' r='28' stroke='${W}' stroke-width='5' fill='none'/><circle cx='80' cy='34' r='9' stroke='rgba(255,255,255,0.7)' stroke-width='2.5' fill='${WF}'/><line x1='80' y1='6' x2='80' y2='25' stroke='rgba(255,255,255,0.65)' stroke-width='2.5'/><line x1='80' y1='43' x2='80' y2='62' stroke='rgba(255,255,255,0.65)' stroke-width='2.5'/><line x1='52' y1='34' x2='71' y2='34' stroke='rgba(255,255,255,0.65)' stroke-width='2.5'/><line x1='89' y1='34' x2='108' y2='34' stroke='rgba(255,255,255,0.65)' stroke-width='2.5'/></g>`
    default:
      // Toolbox / accessories
      return `<g><rect x='38' y='30' width='84' height='38' rx='6' stroke='${W}' stroke-width='2.5' fill='${WF}'/><path d='M60 30v-8a4 4 0 0 1 4-4h32a4 4 0 0 1 4 4v8' stroke='${W}' stroke-width='2.5' fill='${WF}' stroke-linejoin='round'/><line x1='38' y1='46' x2='122' y2='46' stroke='${WM}' stroke-width='2'/><rect x='70' y='42' width='20' height='8' rx='4' stroke='rgba(255,255,255,0.6)' stroke-width='1.5' fill='rgba(255,255,255,0.25)'/></g>`
  }
}

export function partFamilySvgMarkup(inputFamily) {
  const family = normalizeFamily(inputFamily)
  const [primary, secondary] = family.palette
  const title = escapeXml(family.label)
  const photoObject = buildPhotoObject(family.icon_key)

  return `<svg xmlns='http://www.w3.org/2000/svg' width='160' height='96' viewBox='0 0 160 96' role='img' aria-label='${title}'><defs><linearGradient id='bg' x1='0' y1='0' x2='1' y2='1'><stop offset='0%' stop-color='${primary}'/><stop offset='100%' stop-color='${secondary}'/></linearGradient><clipPath id='clip'><rect width='160' height='96' rx='10'/></clipPath></defs><g clip-path='url(#clip)'><rect width='160' height='96' fill='url(#bg)'/><rect x='0' y='70' width='160' height='26' fill='rgba(0,0,0,0.52)'/>${photoObject}<text x='80' y='87' text-anchor='middle' font-family='system-ui,Arial,sans-serif' font-size='11' font-weight='700' fill='white'>${title}</text></g></svg>`
}

export function partFamilyImageSrc(family) {
  const normalizedFamily = normalizeFamily(family)
  const assetPath = PART_FAMILY_ASSET_PATHS[normalizedFamily.id]
  if (assetPath) {
    return assetPath
  }

  const cacheKey = familyCacheKey(normalizedFamily)
  if (SVG_CACHE.has(cacheKey)) {
    return SVG_CACHE.get(cacheKey)
  }

  const uri = svgDataUri(partFamilySvgMarkup(normalizedFamily))
  SVG_CACHE.set(cacheKey, uri)
  return uri
}

export const partFamilyImageDataUri = partFamilyImageSrc