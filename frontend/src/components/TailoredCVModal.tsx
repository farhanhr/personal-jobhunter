import { useEffect, useRef, useState } from 'react'
import {
  generateTailoredCV,
  type TailoredCV,
  type TailoredCVMode,
} from '../api/tailoredCv'
import { tailoredCvToMarkdown } from '../lib/tailoredCvMarkdown'

type GenerateState =
  | { status: 'idle' }
  | { status: 'generating'; mode: TailoredCVMode }
  | { status: 'error'; message: string }

type CopyState = 'idle' | 'copied' | 'failed'

const MODE_LABELS: Record<TailoredCVMode, string> = {
  optimize: 'Optimize',
  ultra_optimize: 'Ultra Optimize',
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Unknown tailored CV error'
}

function TailoredCVPreview({ cv }: { cv: TailoredCV }) {
  const skillLines: Array<[string, string[]]> = [
    ['Technical skills', cv.skills.technical_skills],
    ['Tools and frameworks', cv.skills.tools_frameworks],
    ['Domain knowledge', cv.skills.domain_knowledge],
  ]

  return (
    <div id="ats-cv-print-area" className="ats-cv">
      <header className="ats-cv-header">
        <h1>{cv.full_name}</h1>
        {cv.contact_header.trim().length > 0 && (
          <p className="ats-cv-contact">{cv.contact_header}</p>
        )}
      </header>

      {cv.summary.trim().length > 0 && (
        <section className="cv-section">
          <h2>Summary</h2>
          <p>{cv.summary}</p>
        </section>
      )}

      {skillLines.some(([, items]) => items.length > 0) && (
        <section className="cv-section">
          <h2>Technical Skills</h2>
          <ul>
            {skillLines
              .filter(([, items]) => items.length > 0)
              .map(([label, items]) => (
                <li key={label}>
                  <strong>{label}:</strong> {items.join(', ')}
                </li>
              ))}
          </ul>
        </section>
      )}

      {cv.experience.length > 0 && (
        <section className="cv-section">
          <h2>Professional Experience</h2>
          {cv.experience.map((item, index) => (
            <div className="cv-item" key={`${item.company}-${item.title}-${index}`}>
              <p className="cv-item-head">
                <strong>{item.title}</strong> — {item.company}
              </p>
              <p className="cv-item-meta">
                {[item.location, item.period]
                  .filter((part): part is string => part !== null && part.length > 0)
                  .join(' · ')}
              </p>
              <ul>
                {item.bullets.map((bullet, bulletIndex) => (
                  <li key={`${index}-${bulletIndex}`}>{bullet}</li>
                ))}
              </ul>
            </div>
          ))}
        </section>
      )}

      {cv.projects.length > 0 && (
        <section className="cv-section">
          <h2>Projects</h2>
          {cv.projects.map((project, index) => (
            <div className="cv-item" key={`${project.name}-${index}`}>
              <p className="cv-item-head">
                <strong>{project.name}</strong>
              </p>
              {project.tech_stack.length > 0 && (
                <p className="cv-item-meta">{project.tech_stack.join(', ')}</p>
              )}
              <p>{project.description}</p>
              {project.link !== null && project.link.trim().length > 0 && (
                <p className="cv-item-meta">{project.link}</p>
              )}
            </div>
          ))}
        </section>
      )}

      {cv.education.length > 0 && (
        <section className="cv-section">
          <h2>Education</h2>
          {cv.education.map((item, index) => (
            <div className="cv-item" key={`${item.institution}-${index}`}>
              <p className="cv-item-head">
                <strong>{item.degree}</strong> — {item.institution}
              </p>
              <p className="cv-item-meta">{item.period}</p>
              {item.details !== null && item.details.trim().length > 0 && (
                <p>{item.details}</p>
              )}
            </div>
          ))}
        </section>
      )}
    </div>
  )
}

/**
 * Mode choice and preview for the tailored ATS CV.
 *
 * The preview is the only printable region, so the browser's print-to-PDF
 * produces a clean document without the application chrome. The upskilling
 * checklist is deliberately outside that region: it is preparation material,
 * not part of the CV the user sends.
 */
export function TailoredCVModal({
  opportunityId,
  onClose,
}: {
  opportunityId: string
  onClose: () => void
}) {
  const [state, setState] = useState<GenerateState>({ status: 'idle' })
  const [cv, setCv] = useState<TailoredCV | null>(null)
  const [copyState, setCopyState] = useState<CopyState>('idle')
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

  async function generate(mode: TailoredCVMode) {
    setState({ status: 'generating', mode })
    setCv(null)
    setCopyState('idle')
    try {
      const generated = await generateTailoredCV(opportunityId, mode)
      setCv(generated)
      setState({ status: 'idle' })
    } catch (error: unknown) {
      setState({ status: 'error', message: errorMessage(error) })
    }
  }

  async function copyMarkdown() {
    if (cv === null) {
      return
    }
    try {
      await navigator.clipboard.writeText(tailoredCvToMarkdown(cv))
      setCopyState('copied')
    } catch {
      setCopyState('failed')
    }
  }

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
        aria-labelledby="tailored-cv-title"
        ref={panelRef}
        tabIndex={-1}
      >
        <div className="modal-header no-print">
          <div>
            <h2 id="tailored-cv-title">Tailor CV</h2>
            <p className="field-hint">
              Two honest ways to align your CV with this job. Nothing is sent
              anywhere except the AI provider you configured.
            </p>
          </div>
          <button type="button" className="button-secondary" onClick={onClose}>
            Close
          </button>
        </div>

        {cv === null && state.status !== 'generating' && (
          <div className="mode-list">
            <button
              type="button"
              className="mode-card"
              onClick={() => void generate('optimize')}
            >
              <span className="mode-card-title">Optimize (Recommended)</span>
              <span>
                Align your existing experience and keywords to this role without
                adding unverified skills.
              </span>
            </button>
            <button
              type="button"
              className="mode-card"
              onClick={() => void generate('ultra_optimize')}
            >
              <span className="mode-card-title">Ultra Optimize</span>
              <span>
                Upgrade your CV to match the job requirements, plus get a study
                checklist of skills to learn before the interview.
              </span>
            </button>
          </div>
        )}

        {state.status === 'generating' && (
          <p className="settings-note" role="status">
            Generating your {MODE_LABELS[state.mode]} CV…
          </p>
        )}

        {state.status === 'error' && (
          <div className="settings-alert settings-alert-error no-print" role="alert">
            <h3>Could not generate the tailored CV</h3>
            <p>{state.message}</p>
          </div>
        )}

        {cv !== null && (
          <>
            <div className="tailored-actions no-print">
              <span className="fit-badge is-strong">
                {MODE_LABELS[cv.mode]} · {cv.target_role} at {cv.target_company}
              </span>
              <button
                type="button"
                className="button-secondary"
                onClick={() => setCv(null)}
              >
                Change mode
              </button>
              <button
                type="button"
                className="button-primary"
                onClick={() => window.print()}
              >
                Download / Print PDF
              </button>
              <button
                type="button"
                className="button-secondary"
                onClick={() => void copyMarkdown()}
              >
                Copy Markdown
              </button>
            </div>

            {copyState === 'copied' && (
              <p className="settings-success no-print" role="status">
                Copied to clipboard.
              </p>
            )}
            {copyState === 'failed' && (
              <div className="settings-alert settings-alert-error no-print" role="alert">
                <p>
                  Could not copy automatically. Select the CV text and copy it
                  manually.
                </p>
              </div>
            )}

            {cv.mode === 'ultra_optimize' && (
              <div className="upskilling no-print">
                <div className="settings-alert settings-alert-warning">
                  <h3>Upskilling Checklist for this Role</h3>
                  <p>
                    Ultra Optimize names skills you have not used yet. Learn these
                    before the interview so every claim is true.
                  </p>
                </div>
                <ol className="upskilling-list">
                  {cv.upskilling_checklist.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ol>
              </div>
            )}

            <TailoredCVPreview cv={cv} />
          </>
        )}
      </div>
    </div>
  )
}
