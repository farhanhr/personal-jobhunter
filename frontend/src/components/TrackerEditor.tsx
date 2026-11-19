import { useState } from 'react'
import {
  TRACKER_FIELDS,
  updateTracker,
  type OpportunityDetail,
  type TrackerField,
  type TrackerState,
} from '../api/opportunities'
import {
  draftFromDetail,
  trackerUpdate,
  type TrackerDraft,
} from '../lib/trackerDraft'

type Draft = TrackerDraft

/**
 * ADR-021 records these as *suggestions* for a free-text column, not an enum.
 * They are offered through a datalist so the user can still type anything.
 */
const APPLICATION_STATUS_SUGGESTIONS = [
  'Need to Apply',
  'Applied',
  'Assessment',
  'Interview',
  'Offer',
  'Rejected',
  'Withdrawn',
  'No Feedback',
]

const NOTES_MAX_LENGTH = 10_000

const FIELD_LABELS: Record<TrackerField, string> = {
  job_type: 'Job type',
  date_applied: 'Date applied',
  application_status: 'Application status',
  follow_up_date: 'Follow-up date',
  interview_date: 'Interview date',
  vacancy_url: 'Vacancy URL',
  notes: 'Notes',
}

const FIELD_HINTS: Partial<Record<TrackerField, string>> = {
  job_type: 'Free text. For example: Internship, Contract, Full time.',
  application_status: 'Free text. Suggested values are offered, but any wording is kept.',
  vacancy_url: 'The link you used to apply. Never copied from the job source.',
}

const DATE_HINT =
  'YYYY-MM-DD preferred. DD/MM/YYYY is also read as day first. Stored exactly as typed.'

type TrackerEditorProps = {
  detail: OpportunityDetail
  onSaved: (state: TrackerState) => void
}

/**
 * The seven user-owned tracker fields.
 *
 * The form never sends a full replacement: it sends only what the user changed,
 * so a field this machine does not know about cannot be blanked by a save.
 */
export function TrackerEditor({ detail, onSaved }: TrackerEditorProps) {
  const [draft, setDraft] = useState<Draft>(() => draftFromDetail(detail))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const update = trackerUpdate(draft, detail)
  const changedFields = Object.keys(update) as TrackerField[]
  const known = new Set(detail.known_fields)

  function edit(field: TrackerField, value: string) {
    setDraft((current) => ({ ...current, [field]: value }))
    setError(null)
  }

  async function save() {
    if (changedFields.length === 0 || saving) {
      return
    }

    setSaving(true)
    setError(null)

    try {
      const state = await updateTracker(detail.opportunity_id, update)
      onSaved(state)
    } catch (caught) {
      // The draft is deliberately kept: a failed request must not discard what
      // the user typed, so they can retry or copy it out.
      setError(
        caught instanceof Error ? caught.message : 'The tracker update failed.',
      )
    } finally {
      setSaving(false)
    }
  }

  return (
    <form
      className="tracker-form"
      onSubmit={(event) => {
        event.preventDefault()
        void save()
      }}
    >
      <div className="tracker-form-grid">
        {TRACKER_FIELDS.map((field) => {
          const isKnown = known.has(field)
          const value = detail[field]
          const id = `tracker-${field}`
          const hintId = `${id}-hint`

          return (
            <div className="tracker-input" key={field}>
              <label htmlFor={id}>{FIELD_LABELS[field]}</label>

              {field === 'notes' ? (
                <textarea
                  id={id}
                  value={draft[field]}
                  onChange={(event) => edit(field, event.target.value)}
                  rows={5}
                  maxLength={NOTES_MAX_LENGTH}
                  aria-describedby={hintId}
                  disabled={saving}
                />
              ) : (
                <input
                  id={id}
                  type="text"
                  value={draft[field]}
                  onChange={(event) => edit(field, event.target.value)}
                  aria-describedby={hintId}
                  disabled={saving}
                  autoComplete="off"
                  list={
                    field === 'application_status'
                      ? 'application-status-suggestions'
                      : undefined
                  }
                />
              )}

              <p className="tracker-input-hint" id={hintId}>
                {FIELD_HINTS[field] ??
                  (field === 'date_applied' ||
                  field === 'follow_up_date' ||
                  field === 'interview_date'
                    ? DATE_HINT
                    : null)}
                {!isKnown && (
                  <span className="tracker-unknown">
                    {' '}
                    Unknown: no reliable local value is stored for this field
                    yet. Leave it empty to keep it unknown.
                  </span>
                )}
                {isKnown && value === null && (
                  <span className="tracker-known-blank">
                    {' '}
                    Not recorded in the spreadsheet.
                  </span>
                )}
              </p>
            </div>
          )
        })}
      </div>

      <datalist id="application-status-suggestions">
        {APPLICATION_STATUS_SUGGESTIONS.map((suggestion) => (
          <option key={suggestion} value={suggestion} />
        ))}
      </datalist>

      <div className="tracker-form-actions">
        <button
          type="submit"
          className="button-primary"
          disabled={changedFields.length === 0 || saving}
        >
          {saving ? 'Saving…' : 'Save tracker'}
        </button>
        <p className="tracker-form-status" aria-live="polite">
          {saving
            ? 'Saving your changes…'
            : changedFields.length === 0
              ? 'No changes to save.'
              : `${changedFields.length} field${
                  changedFields.length === 1 ? '' : 's'
                } changed.`}
        </p>
      </div>

      {error !== null && (
        <div className="settings-alert settings-alert-error" role="alert">
          <h3>Could not save the tracker</h3>
          <p>{error}</p>
          <p className="field-hint">
            Your edits are still in the form. Nothing was sent to the
            spreadsheet.
          </p>
        </div>
      )}
    </form>
  )
}
