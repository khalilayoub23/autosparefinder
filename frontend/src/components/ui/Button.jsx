import { forwardRef } from 'react'
import { Loader2 } from 'lucide-react'

/**
 * The ONE button. Variants map to the .btn-* classes in index.css (6px control radius, focus ring, disabled state);
 * a caller never passes a radius. Sizes guarantee a >=44px touch target at md. `loading` sets aria-busy, disables the
 * button and shows a spinner, so every async button has the same loading state.
 */
const VARIANT = { primary: 'btn-primary', secondary: 'btn-secondary', ghost: 'btn-ghost', danger: 'btn-danger' }
const SIZE = { sm: 'btn-sm', md: 'btn-md', lg: 'btn-lg' }

export function buttonClassName({ variant = 'primary', size = 'md', className = '' } = {}) {
  return ['btn', VARIANT[variant] || VARIANT.primary, SIZE[size] || SIZE.md, 'inline-flex items-center justify-center gap-2', className]
    .filter(Boolean).join(' ')
}

const Button = forwardRef(function Button(
  { variant = 'primary', size = 'md', loading = false, disabled = false, className = '', children, type = 'button', ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled || loading}
      aria-busy={loading ? 'true' : undefined}
      className={buttonClassName({ variant, size, className })}
      {...rest}
    >
      {loading && <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />}
      {children}
    </button>
  )
})

export default Button
