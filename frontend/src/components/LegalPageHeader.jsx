import BrandLogo from './BrandLogo'

export default function LegalPageHeader({
  title,
  subtitle,
  note,
  icon: Icon,
}) {
  return (
    <div className="bg-brand-700 text-white py-10 px-4">
      <div className="max-w-3xl mx-auto">
        <div className="flex flex-col items-center text-center">
          <BrandLogo
            size="legal"
            className="mb-4"
            alt="AutoSpare logo"
            priority
            blend
          />
          <div className="flex items-center justify-center gap-2">
            {Icon ? <Icon className="w-7 h-7 text-brand-100 shrink-0" /> : null}
            <h1 className="text-2xl font-bold">{title}</h1>
          </div>
          {subtitle ? <p className="text-brand-100 text-sm mt-1">{subtitle}</p> : null}
          {note ? <p className="text-brand-200 text-xs mt-0.5">{note}</p> : null}
        </div>
      </div>
    </div>
  )
}
