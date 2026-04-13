import { Component } from 'react'
import { Link } from 'react-router-dom'

/**
 * ErrorBoundary — catches any unhandled render errors and shows a friendly
 * fallback instead of a completely blank page.
 * Usage: wrap any route or subtree that might throw.
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false, errorMessage: '' }
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, errorMessage: error?.message || 'שגיאה לא ידועה' }
  }

  componentDidCatch(error, info) {
    // Log for developer visibility (does not expose info to the user)
    console.error('[ErrorBoundary]', error, info?.componentStack)
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-[60vh] flex flex-col items-center justify-center text-center px-4">
          <div className="text-5xl mb-4">⚠️</div>
          <h2 className="text-xl font-bold text-gray-800 mb-2">אירעה שגיאה בלתי צפויה</h2>
          <p className="text-sm text-gray-500 mb-6 max-w-sm">
            {this.state.errorMessage}
          </p>
          <div className="flex gap-3">
            <button
              onClick={() => this.setState({ hasError: false, errorMessage: '' })}
              className="btn-secondary"
            >
              נסה שוב
            </button>
            <Link to="/" className="btn-primary">חזור לדף הבית</Link>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
