import LegalPageHeader from './LegalPageHeader'

export default function LegalPageShell({
  title,
  subtitle,
  note,
  icon,
  children,
  className = 'max-w-3xl mx-auto px-4 py-10 space-y-6',
}) {
  return (
    <div className="min-h-screen bg-brand-surface" dir="rtl">
      <LegalPageHeader title={title} subtitle={subtitle} note={note} icon={icon} />
      <div className={className}>{children}</div>
    </div>
  )
}
