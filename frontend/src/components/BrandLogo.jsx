import { useMemo, useState } from 'react'
import { Wrench } from 'lucide-react'

const LOGO_SOURCES = [
  '/autosparefinder%20logo.svg',
  '/autosparefinder logo.svg',
  import.meta.env.VITE_BRAND_LOGO_URL,
  '/brand-logo-attached.svg',
  '/brand-logo.svg',
  '/brand-logo-wordmark.svg',
].filter(Boolean)

const LOGO_SIZE_PRESETS = {
  default: 'h-20 sm:h-24 lg:h-32 w-auto max-w-[400px] lg:max-w-[460px]',
  appHeader: 'h-20 sm:h-24 lg:h-32 xl:h-36 w-auto max-w-[460px] lg:max-w-[520px] xl:max-w-[560px]',
  dashboard: 'h-16 sm:h-20 lg:h-28 w-auto max-w-[260px] lg:max-w-[300px]',
  auth: 'h-24 sm:h-28 lg:h-36 w-auto max-w-[460px] lg:max-w-[520px]',
  legal: 'h-20 sm:h-24 lg:h-32 w-auto max-w-[380px] lg:max-w-[440px]',
}

export default function BrandLogo({
  size = 'default',
  className = '',
  alt = 'AutoSpare logo',
  priority = false,
  blend = false,
}) {
  const sources = useMemo(() => (blend ? ['/autosparefinder-logo-header.svg', '/brand-logo-wordmark.svg', ...LOGO_SOURCES] : LOGO_SOURCES), [blend])
  const sizeClassName = LOGO_SIZE_PRESETS[size] || LOGO_SIZE_PRESETS.default
  const mergedClassName = `${sizeClassName} ${className}`.trim()
  const [sourceIndex, setSourceIndex] = useState(0)
  const [hasError, setHasError] = useState(false)

  const src = sources[Math.min(sourceIndex, Math.max(sources.length - 1, 0))]

  const handleError = () => {
    if (sourceIndex < sources.length - 1) {
      setSourceIndex((idx) => idx + 1)
      return
    }
    setHasError(true)
  }

  if (!hasError && src) {
    return (
      <img
        src={src}
        alt={alt}
        className={`${mergedClassName} object-contain object-center shrink-0 ${blend ? 'brand-logo-fused' : ''}`.trim()}
        loading={priority ? 'eager' : 'lazy'}
        decoding="async"
        draggable={false}
        onError={handleError}
      />
    )
  }

  return (
    <span className={`${mergedClassName} inline-flex items-center justify-center ${blend ? 'bg-transparent border-0' : 'rounded-xl border border-cyan-300 bg-slate-100'}`.trim()}>
      <Wrench className="h-1/2 w-1/2 text-cyan-500" />
    </span>
  )
}
