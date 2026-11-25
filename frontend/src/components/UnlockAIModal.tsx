import { useEffect, useRef } from 'react'
import { Link } from 'react-router'

export type AIUnlockStatus = {
  aiConfigured: boolean
  cvUploaded: boolean
}

type UnlockAIModalProps = {
  status: AIUnlockStatus
  onClose: () => void
}

/**
 * Explains the two optional AI prerequisites without pretending an AI action
 * ran. The buttons that open this modal stay visible so the feature is
 * discoverable, but nothing is sent to a provider until Settings is complete.
 */
export function UnlockAIModal({ status, onClose }: UnlockAIModalProps) {
  const panelRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    panelRef.current?.focus()
  }, [])

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        onClose()
      }
    }

    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  return (
    <div
      className="modal-overlay"
      onClick={(event) => {
        if (event.target === event.currentTarget) {
          onClose()
        }
      }}
    >
      <div
        className="modal-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="unlock-ai-title"
        ref={panelRef}
        tabIndex={-1}
      >
        <div className="modal-header">
          <div>
            <p className="unlock-ai-icon" aria-hidden="true">
              💡
            </p>
            <h2 id="unlock-ai-title">Unlock Candidate Fit &amp; Tailored ATS CV</h2>
            <p className="field-hint">
              This feature uses an AI provider (Groq, OpenRouter, Gemini, or Ollama) and your
              Master CV to analyze match and craft custom ATS resumes.
            </p>
          </div>
          <button type="button" className="button-secondary" onClick={onClose}>
            Close
          </button>
        </div>

        <ul className="unlock-ai-checklist">
          <li>
            <span>⚙️ AI Provider</span>
            <span className="unlock-ai-row-actions">
              <span className={status.aiConfigured ? 'chip is-match' : 'chip is-gap'}>
                {status.aiConfigured ? 'Configured' : 'Not Configured'}
              </span>
              <Link to="/settings" onClick={onClose}>
                Settings
              </Link>
            </span>
          </li>
          <li>
            <span>📄 Master CV</span>
            <span className="unlock-ai-row-actions">
              <span className={status.cvUploaded ? 'chip is-match' : 'chip is-gap'}>
                {status.cvUploaded ? 'Uploaded' : 'Not Uploaded'}
              </span>
              <Link to="/settings" onClick={onClose}>
                Settings
              </Link>
            </span>
          </li>
        </ul>

        <p className="field-hint">
          Both are optional. Search, filtering, tracking, and Google Sheets sync keep working
          without AI.
        </p>

        <div className="settings-actions">
          <Link className="button-primary" to="/settings" onClick={onClose}>
            Go to Settings
          </Link>
        </div>
      </div>
    </div>
  )
}
