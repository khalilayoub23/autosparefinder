import BrandLogo from '../BrandLogo'

export default function AuthBrandHeader({
  title = <span className="text-brand-navy">AutoSpare Finder</span>,
  subtitle = 'חלקי חילוף בעזרת בינה מלאכותית',
}) {
  return (
    <div className="text-center mb-8">
      <div className="mb-5 flex items-center justify-center">
        <BrandLogo size="auth" alt="AutoSpare logo" priority blend />
      </div>
      <h1 className="text-3xl font-bold text-brand-navy">{title}</h1>
      <p className="text-slate-500 mt-1">{subtitle}</p>
    </div>
  )
}
