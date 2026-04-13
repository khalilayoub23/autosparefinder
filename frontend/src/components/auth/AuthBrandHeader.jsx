import { useState } from 'react'
import { Wrench } from 'lucide-react'

const BRAND_LOGO_URL = import.meta.env.VITE_BRAND_LOGO_URL || '/brand-logo.svg'

export default function AuthBrandHeader({
  title = <>Auto <span className="text-brand-600">Spare</span></>,
  subtitle = 'חלקי חילוף בעזרת בינה מלאכותית',
}) {
  const [logoError, setLogoError] = useState(false)

  return (
    <div className="text-center mb-8">
      <div className="inline-flex items-center justify-center w-20 h-20 bg-white rounded-2xl mb-4 shadow-lg border border-gray-100 overflow-hidden">
        {!logoError ? (
          <img
            src={BRAND_LOGO_URL}
            alt="AutoSpare logo"
            className="w-full h-full object-cover"
            onError={() => setLogoError(true)}
          />
        ) : (
          <div className="w-full h-full bg-brand-600 flex items-center justify-center">
            <Wrench className="w-9 h-9 text-white" />
          </div>
        )}
      </div>
      <h1 className="text-3xl font-bold text-gray-900">{title}</h1>
      <p className="text-gray-500 mt-1">{subtitle}</p>
    </div>
  )
}
