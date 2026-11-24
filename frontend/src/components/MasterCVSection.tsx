import {
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
} from 'react'
import {
  fetchCVProfile,
  saveCVProfile,
  uploadCVFile,
  uploadCVText,
  type CandidateProfile,
  type ContactInfo,
  type Education,
  type Project,
  type SkillsCollection,
  type WorkExperience,
} from '../api/cvProfile'
import { Input } from './ui/input'
import { Label } from './ui/label'

type LoadState =
  | { status: 'loading' }
  | { status: 'loaded' }
  | { status: 'error'; message: string }

type UploadState =
  | { status: 'idle' }
  | { status: 'extracting' }
  | { status: 'parsing' }
  | { status: 'error'; message: string }

type SaveState =
  | { status: 'idle' }
  | { status: 'saving' }
  | { status: 'saved' }
  | { status: 'error'; message: string }

const MAX_PDF_BYTES = 5 * 1024 * 1024

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Unknown CV error'
}

function splitList(value: string): string[] {
  return value
    .split(/[,\n]/)
    .map((item) => item.trim())
    .filter((item) => item.length > 0)
}

function fromLines(value: string): string[] {
  return value
    .split('\n')
    .map((item) => item.trim())
    .filter((item) => item.length > 0)
}

function orNull(value: string): string | null {
  const trimmed = value.trim()
  return trimmed.length === 0 ? null : trimmed
}

function emptyExperience(): WorkExperience {
  return {
    company: '',
    title: '',
    location: null,
    start_date: '',
    end_date: null,
    is_current: false,
    achievements: [],
  }
}

function emptyEducation(): Education {
  return {
    institution: '',
    degree: '',
    field_of_study: '',
    start_year: null,
    end_year: null,
    gpa: null,
  }
}

function emptyProject(): Project {
  return { name: '', description: '', tech_stack: [], url: null }
}

type TextFieldProps = {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  placeholder?: string
  disabled?: boolean
}

function TextField({ id, label, value, onChange, placeholder, disabled }: TextFieldProps) {
  return (
    <>
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        autoComplete="off"
        disabled={disabled}
      />
    </>
  )
}

type TextAreaFieldProps = {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  rows?: number
  hint?: string
  disabled?: boolean
}

function TextAreaField({
  id,
  label,
  value,
  onChange,
  rows = 4,
  hint,
  disabled,
}: TextAreaFieldProps) {
  return (
    <>
      <Label htmlFor={id}>{label}</Label>
      <textarea
        id={id}
        className="settings-textarea"
        rows={rows}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
      />
      {hint && <p className="field-hint">{hint}</p>}
    </>
  )
}

export function MasterCVSection() {
  const [loadState, setLoadState] = useState<LoadState>({ status: 'loading' })
  const [uploadState, setUploadState] = useState<UploadState>({ status: 'idle' })
  const [saveState, setSaveState] = useState<SaveState>({ status: 'idle' })
  const [draft, setDraft] = useState<CandidateProfile | null>(null)
  const [fileName, setFileName] = useState<string | null>(null)
  const [updatedAt, setUpdatedAt] = useState<string | null>(null)
  const [showPaste, setShowPaste] = useState(false)
  const [pastedText, setPastedText] = useState('')
  const [isDragging, setIsDragging] = useState(false)
  const [reloadToken, setReloadToken] = useState(0)
  const fileInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const controller = new AbortController()

    fetchCVProfile(controller.signal)
      .then((result) => {
        setDraft(result.profile)
        setFileName(result.file_name)
        setUpdatedAt(result.updated_at_utc)
        setLoadState({ status: 'loaded' })
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) {
          return
        }
        setLoadState({ status: 'error', message: errorMessage(error) })
      })

    return () => controller.abort()
  }, [reloadToken])

  const isBusy = uploadState.status === 'extracting' || uploadState.status === 'parsing'
  const isSaving = saveState.status === 'saving'

  function applyResult(result: {
    profile: CandidateProfile | null
    file_name: string | null
    updated_at_utc: string | null
  }) {
    setDraft(result.profile)
    setFileName(result.file_name)
    setUpdatedAt(result.updated_at_utc)
    setSaveState({ status: 'idle' })
  }

  async function handleFile(file: File) {
    setUploadState({ status: 'extracting' })
    setSaveState({ status: 'idle' })

    if (!file.name.toLowerCase().endsWith('.pdf')) {
      setUploadState({ status: 'error', message: 'Upload the CV as a PDF file.' })
      return
    }
    if (file.size > MAX_PDF_BYTES) {
      setUploadState({ status: 'error', message: 'The PDF is larger than 5 MB.' })
      return
    }

    try {
      setUploadState({ status: 'parsing' })
      const result = await uploadCVFile(file)
      applyResult(result)
      setUploadState({ status: 'idle' })
    } catch (error: unknown) {
      setUploadState({ status: 'error', message: errorMessage(error) })
    }
  }

  async function handlePasteSubmit() {
    if (pastedText.trim().length === 0) {
      setUploadState({ status: 'error', message: 'Paste the CV text first.' })
      return
    }

    setUploadState({ status: 'parsing' })
    setSaveState({ status: 'idle' })
    try {
      const result = await uploadCVText(pastedText)
      applyResult(result)
      setUploadState({ status: 'idle' })
      setPastedText('')
      setShowPaste(false)
    } catch (error: unknown) {
      setUploadState({ status: 'error', message: errorMessage(error) })
    }
  }

  function onFileInputChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (file) {
      void handleFile(file)
    }
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    setIsDragging(false)
    const file = event.dataTransfer.files?.[0]
    if (file) {
      void handleFile(file)
    }
  }

  function editContact(patch: Partial<ContactInfo>) {
    setDraft((current) =>
      current ? { ...current, contact_info: { ...current.contact_info, ...patch } } : current,
    )
    setSaveState({ status: 'idle' })
  }

  function editSkills(patch: Partial<SkillsCollection>) {
    setDraft((current) =>
      current ? { ...current, skills: { ...current.skills, ...patch } } : current,
    )
    setSaveState({ status: 'idle' })
  }

  function editExperience(index: number, patch: Partial<WorkExperience>) {
    setDraft((current) =>
      current
        ? {
            ...current,
            experience: current.experience.map((item, position) =>
              position === index ? { ...item, ...patch } : item,
            ),
          }
        : current,
    )
    setSaveState({ status: 'idle' })
  }

  function editEducation(index: number, patch: Partial<Education>) {
    setDraft((current) =>
      current
        ? {
            ...current,
            education: current.education.map((item, position) =>
              position === index ? { ...item, ...patch } : item,
            ),
          }
        : current,
    )
    setSaveState({ status: 'idle' })
  }

  function editProject(index: number, patch: Partial<Project>) {
    setDraft((current) =>
      current
        ? {
            ...current,
            projects: current.projects.map((item, position) =>
              position === index ? { ...item, ...patch } : item,
            ),
          }
        : current,
    )
    setSaveState({ status: 'idle' })
  }

  function addToList(key: 'experience' | 'education' | 'projects') {
    setDraft((current) => {
      if (!current) {
        return current
      }
      if (key === 'experience') {
        return { ...current, experience: [...current.experience, emptyExperience()] }
      }
      if (key === 'education') {
        return { ...current, education: [...current.education, emptyEducation()] }
      }
      return { ...current, projects: [...current.projects, emptyProject()] }
    })
    setSaveState({ status: 'idle' })
  }

  function removeExperience(index: number) {
    setDraft((current) =>
      current
        ? { ...current, experience: current.experience.filter((_, position) => position !== index) }
        : current,
    )
    setSaveState({ status: 'idle' })
  }

  function removeEducation(index: number) {
    setDraft((current) =>
      current
        ? { ...current, education: current.education.filter((_, position) => position !== index) }
        : current,
    )
    setSaveState({ status: 'idle' })
  }

  function removeProject(index: number) {
    setDraft((current) =>
      current
        ? { ...current, projects: current.projects.filter((_, position) => position !== index) }
        : current,
    )
    setSaveState({ status: 'idle' })
  }

  async function handleSave() {
    if (!draft) {
      return
    }

    setSaveState({ status: 'saving' })
    try {
      const result = await saveCVProfile(draft)
      applyResult(result)
      setSaveState({ status: 'saved' })
    } catch (error: unknown) {
      setSaveState({ status: 'error', message: errorMessage(error) })
    }
  }

  return (
    <section className="settings-panel" aria-labelledby="master-cv-heading">
      <div className="settings-heading">
        <h2 id="master-cv-heading">Master CV</h2>
        <p>
          Upload your CV once. It is parsed into a structured profile that stays
          on this machine and is used for fit scoring and tailored CVs.
        </p>
      </div>

      {loadState.status === 'loading' && (
        <p className="settings-note" aria-live="polite">
          Loading master CV…
        </p>
      )}

      {loadState.status === 'error' && (
        <div className="settings-alert settings-alert-error" role="alert">
          <h3>Could not load the master CV</h3>
          <p>{loadState.message}</p>
          <button
            type="button"
            className="button-secondary"
            onClick={() => {
              setLoadState({ status: 'loading' })
              setReloadToken((token) => token + 1)
            }}
          >
            Retry
          </button>
        </div>
      )}

      {loadState.status === 'loaded' && (
        <>
          <div
            className={`cv-dropzone${isDragging ? ' is-active' : ''}`}
            onDragOver={(event) => {
              event.preventDefault()
              setIsDragging(true)
            }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={onDrop}
          >
            <p>Drop a PDF here, or choose a file.</p>
            <button
              type="button"
              className="button-secondary"
              onClick={() => fileInputRef.current?.click()}
              disabled={isBusy || isSaving}
            >
              Choose PDF
            </button>
            <input
              ref={fileInputRef}
              id="cv-file"
              type="file"
              accept="application/pdf,.pdf"
              className="visually-hidden"
              onChange={onFileInputChange}
              disabled={isBusy || isSaving}
            />
            <p className="field-hint">
              Digital PDF with selectable text, up to 5 MB. {fileName ? `Current file: ${fileName}.` : ''}
            </p>
          </div>

          <div className="settings-actions">
            <button
              type="button"
              className="button-secondary"
              aria-expanded={showPaste}
              onClick={() => setShowPaste((visible) => !visible)}
              disabled={isBusy || isSaving}
            >
              {showPaste ? 'Hide paste text' : 'Paste text instead'}
            </button>
          </div>

          {showPaste && (
            <div className="cv-paste">
              <TextAreaField
                id="cv-paste-text"
                label="CV text"
                value={pastedText}
                onChange={setPastedText}
                rows={8}
                hint="Paste the full CV text. The profile is parsed the same way as a PDF."
                disabled={isBusy || isSaving}
              />
              <div className="settings-actions">
                <button
                  type="button"
                  className="button-primary"
                  onClick={() => void handlePasteSubmit()}
                  disabled={isBusy || isSaving}
                >
                  Parse pasted text
                </button>
              </div>
            </div>
          )}

          {isBusy && (
            <ol className="cv-steps" aria-live="polite">
              <li>
                {uploadState.status === 'extracting'
                  ? 'Extracting text…'
                  : 'Text extracted.'}
              </li>
              {uploadState.status === 'parsing' && <li>Parsing profile with AI…</li>}
            </ol>
          )}

          {uploadState.status === 'error' && (
            <div className="settings-alert settings-alert-error" role="alert">
              <h3>Could not read the CV</h3>
              <p>{uploadState.message}</p>
            </div>
          )}

          {draft === null && !isBusy && (
            <p className="settings-empty">
              No master CV parsed yet. Upload a PDF or paste the text to create one.
            </p>
          )}

          {draft !== null && (
            <div className="cv-review">
              <div className="cv-review-heading">
                <h3>Parsed profile</h3>
                <p className="field-hint">
                  {updatedAt
                    ? `Last updated ${new Date(updatedAt).toLocaleString()}.`
                    : 'Review and correct anything the parser got wrong.'}
                </p>
              </div>

              <fieldset className="role-card" disabled={isSaving}>
                <legend>Contact</legend>
                <TextField
                  id="cv-full-name"
                  label="Full name"
                  value={draft.contact_info.full_name}
                  onChange={(value) => editContact({ full_name: value })}
                />
                <TextField
                  id="cv-email"
                  label="Email"
                  value={draft.contact_info.email ?? ''}
                  onChange={(value) => editContact({ email: orNull(value) })}
                />
                <TextField
                  id="cv-phone"
                  label="Phone"
                  value={draft.contact_info.phone ?? ''}
                  onChange={(value) => editContact({ phone: orNull(value) })}
                />
                <TextField
                  id="cv-location"
                  label="Location"
                  value={draft.contact_info.location ?? ''}
                  onChange={(value) => editContact({ location: orNull(value) })}
                />
                <TextField
                  id="cv-linkedin"
                  label="LinkedIn URL"
                  value={draft.contact_info.linkedin_url ?? ''}
                  onChange={(value) => editContact({ linkedin_url: orNull(value) })}
                />
                <TextField
                  id="cv-portfolio"
                  label="Portfolio or GitHub URL"
                  value={draft.contact_info.portfolio_url ?? ''}
                  onChange={(value) => editContact({ portfolio_url: orNull(value) })}
                />
              </fieldset>

              <fieldset className="role-card" disabled={isSaving}>
                <legend>Summary</legend>
                <TextAreaField
                  id="cv-summary"
                  label="Professional summary"
                  value={draft.summary}
                  onChange={(value) => {
                    setDraft((current) => (current ? { ...current, summary: value } : current))
                    setSaveState({ status: 'idle' })
                  }}
                  rows={4}
                />
              </fieldset>

              <fieldset className="role-card" disabled={isSaving}>
                <legend>Skills</legend>
                <TextField
                  id="cv-hard-skills"
                  label="Hard skills"
                  value={draft.skills.hard_skills.join(', ')}
                  onChange={(value) => editSkills({ hard_skills: splitList(value) })}
                  placeholder="Python, SQL, React"
                />
                <TextField
                  id="cv-tools"
                  label="Tools"
                  value={draft.skills.tools.join(', ')}
                  onChange={(value) => editSkills({ tools: splitList(value) })}
                  placeholder="Docker, Git, Google Cloud"
                />
                <TextField
                  id="cv-soft-skills"
                  label="Soft skills"
                  value={draft.skills.soft_skills.join(', ')}
                  onChange={(value) => editSkills({ soft_skills: splitList(value) })}
                  placeholder="Mentoring, stakeholder communication"
                />
                <p className="field-hint">Separate entries with commas.</p>
              </fieldset>

              <fieldset className="role-card" disabled={isSaving}>
                <legend>Experience</legend>
                {draft.experience.length === 0 && (
                  <p className="field-hint">No experience entries yet.</p>
                )}
                {draft.experience.map((entry, index) => (
                  <div className="cv-entry" key={`experience-${index}`}>
                    <TextField
                      id={`cv-experience-company-${index}`}
                      label="Company"
                      value={entry.company}
                      onChange={(value) => editExperience(index, { company: value })}
                    />
                    <TextField
                      id={`cv-experience-title-${index}`}
                      label="Title"
                      value={entry.title}
                      onChange={(value) => editExperience(index, { title: value })}
                    />
                    <TextField
                      id={`cv-experience-location-${index}`}
                      label="Location"
                      value={entry.location ?? ''}
                      onChange={(value) => editExperience(index, { location: orNull(value) })}
                    />
                    <TextField
                      id={`cv-experience-start-${index}`}
                      label="Start"
                      value={entry.start_date}
                      onChange={(value) => editExperience(index, { start_date: value })}
                      placeholder="2021"
                    />
                    <TextField
                      id={`cv-experience-end-${index}`}
                      label="End"
                      value={entry.end_date ?? ''}
                      onChange={(value) => editExperience(index, { end_date: orNull(value) })}
                      placeholder="Leave empty if current"
                    />
                    <label className="cv-checkbox">
                      <input
                        type="checkbox"
                        checked={entry.is_current}
                        onChange={(event) =>
                          editExperience(index, { is_current: event.target.checked })
                        }
                      />
                      Current role
                    </label>
                    <TextAreaField
                      id={`cv-experience-achievements-${index}`}
                      label="Achievements"
                      value={entry.achievements.join('\n')}
                      onChange={(value) =>
                        editExperience(index, { achievements: fromLines(value) })
                      }
                      rows={3}
                      hint="One achievement per line."
                    />
                    <div className="settings-actions">
                      <button
                        type="button"
                        className="button-danger"
                        onClick={() => removeExperience(index)}
                      >
                        Remove
                      </button>
                    </div>
                  </div>
                ))}
                <div className="settings-actions">
                  <button
                    type="button"
                    className="button-secondary"
                    onClick={() => addToList('experience')}
                  >
                    Add experience
                  </button>
                </div>
              </fieldset>

              <fieldset className="role-card" disabled={isSaving}>
                <legend>Education</legend>
                {draft.education.length === 0 && (
                  <p className="field-hint">No education entries yet.</p>
                )}
                {draft.education.map((entry, index) => (
                  <div className="cv-entry" key={`education-${index}`}>
                    <TextField
                      id={`cv-education-institution-${index}`}
                      label="Institution"
                      value={entry.institution}
                      onChange={(value) => editEducation(index, { institution: value })}
                    />
                    <TextField
                      id={`cv-education-degree-${index}`}
                      label="Degree"
                      value={entry.degree}
                      onChange={(value) => editEducation(index, { degree: value })}
                    />
                    <TextField
                      id={`cv-education-field-${index}`}
                      label="Field of study"
                      value={entry.field_of_study}
                      onChange={(value) => editEducation(index, { field_of_study: value })}
                    />
                    <TextField
                      id={`cv-education-start-${index}`}
                      label="Start year"
                      value={entry.start_year ?? ''}
                      onChange={(value) => editEducation(index, { start_year: orNull(value) })}
                    />
                    <TextField
                      id={`cv-education-end-${index}`}
                      label="End year"
                      value={entry.end_year ?? ''}
                      onChange={(value) => editEducation(index, { end_year: orNull(value) })}
                    />
                    <TextField
                      id={`cv-education-gpa-${index}`}
                      label="GPA"
                      value={entry.gpa ?? ''}
                      onChange={(value) => editEducation(index, { gpa: orNull(value) })}
                    />
                    <div className="settings-actions">
                      <button
                        type="button"
                        className="button-danger"
                        onClick={() => removeEducation(index)}
                      >
                        Remove
                      </button>
                    </div>
                  </div>
                ))}
                <div className="settings-actions">
                  <button
                    type="button"
                    className="button-secondary"
                    onClick={() => addToList('education')}
                  >
                    Add education
                  </button>
                </div>
              </fieldset>

              <fieldset className="role-card" disabled={isSaving}>
                <legend>Projects</legend>
                {draft.projects.length === 0 && (
                  <p className="field-hint">No project entries yet.</p>
                )}
                {draft.projects.map((entry, index) => (
                  <div className="cv-entry" key={`project-${index}`}>
                    <TextField
                      id={`cv-project-name-${index}`}
                      label="Name"
                      value={entry.name}
                      onChange={(value) => editProject(index, { name: value })}
                    />
                    <TextAreaField
                      id={`cv-project-description-${index}`}
                      label="Description"
                      value={entry.description}
                      onChange={(value) => editProject(index, { description: value })}
                      rows={3}
                    />
                    <TextField
                      id={`cv-project-stack-${index}`}
                      label="Tech stack"
                      value={entry.tech_stack.join(', ')}
                      onChange={(value) =>
                        editProject(index, { tech_stack: splitList(value) })
                      }
                      placeholder="TypeScript, PostgreSQL"
                    />
                    <TextField
                      id={`cv-project-url-${index}`}
                      label="URL"
                      value={entry.url ?? ''}
                      onChange={(value) => editProject(index, { url: orNull(value) })}
                    />
                    <div className="settings-actions">
                      <button
                        type="button"
                        className="button-danger"
                        onClick={() => removeProject(index)}
                      >
                        Remove
                      </button>
                    </div>
                  </div>
                ))}
                <div className="settings-actions">
                  <button
                    type="button"
                    className="button-secondary"
                    onClick={() => addToList('projects')}
                  >
                    Add project
                  </button>
                </div>
              </fieldset>

              <div className="settings-actions">
                <button
                  type="button"
                  className="button-primary"
                  onClick={() => void handleSave()}
                  disabled={isSaving || isBusy}
                >
                  {isSaving ? 'Saving…' : 'Save Changes'}
                </button>
              </div>

              {saveState.status === 'saved' && (
                <p className="settings-success" role="status">
                  Master CV saved.
                </p>
              )}

              {saveState.status === 'error' && (
                <div className="settings-alert settings-alert-error" role="alert">
                  <h3>Could not save the master CV</h3>
                  <p>{saveState.message}</p>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </section>
  )
}
