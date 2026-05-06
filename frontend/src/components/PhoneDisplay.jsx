const normalizePhoneForDisplay = (value) => {
  if (value == null) return ''
  const text = String(value).trim()
  if (!text) return ''
  return text.replace(/\+/g, '')
}

export default function PhoneDisplay({ value, className = '' }) {
  const phone = normalizePhoneForDisplay(value)
  if (!phone) return null

  const mergedClassName = className ? `phone-display ${className}` : 'phone-display'

  return (
    <span className={mergedClassName} dir="ltr">
      {phone}
    </span>
  )
}
