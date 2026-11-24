export type ContactInfo = {
  full_name: string
  email: string | null
  phone: string | null
  location: string | null
  linkedin_url: string | null
  portfolio_url: string | null
}

export type WorkExperience = {
  company: string
  title: string
  location: string | null
  start_date: string
  end_date: string | null
  is_current: boolean
  achievements: string[]
}

export type Education = {
  institution: string
  degree: string
  field_of_study: string
  start_year: string | null
  end_year: string | null
  gpa: string | null
}

export type Project = {
  name: string
  description: string
  tech_stack: string[]
  url: string | null
}

export type SkillsCollection = {
  hard_skills: string[]
  tools: string[]
  soft_skills: string[]
}

export type CandidateProfile = {
  contact_info: ContactInfo
  summary: string
  skills: SkillsCollection
  experience: WorkExperience[]
  education: Education[]
  projects: Project[]
}

export type CVProfile = {
  has_profile: boolean
  profile: CandidateProfile | null
  file_name: string | null
  updated_at_utc: string | null
}

export class CVProfileApiError extends Error {
  readonly code: string
  readonly status: number

  constructor(message: string, options: { code: string; status: number }) {
    super(message)
    this.name = 'CVProfileApiError'
    this.code = options.code
    this.status = options.status
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string')
}

function isOptionalString(value: unknown): value is string | null {
  return value === null || typeof value === 'string'
}

function isContactInfo(value: unknown): value is ContactInfo {
  return (
    isRecord(value) &&
    typeof value.full_name === 'string' &&
    isOptionalString(value.email) &&
    isOptionalString(value.phone) &&
    isOptionalString(value.location) &&
    isOptionalString(value.linkedin_url) &&
    isOptionalString(value.portfolio_url)
  )
}

function isSkills(value: unknown): value is SkillsCollection {
  return (
    isRecord(value) &&
    isStringArray(value.hard_skills) &&
    isStringArray(value.tools) &&
    isStringArray(value.soft_skills)
  )
}

function isWorkExperience(value: unknown): value is WorkExperience {
  return (
    isRecord(value) &&
    typeof value.company === 'string' &&
    typeof value.title === 'string' &&
    isOptionalString(value.location) &&
    typeof value.start_date === 'string' &&
    isOptionalString(value.end_date) &&
    typeof value.is_current === 'boolean' &&
    isStringArray(value.achievements)
  )
}

function isEducation(value: unknown): value is Education {
  return (
    isRecord(value) &&
    typeof value.institution === 'string' &&
    typeof value.degree === 'string' &&
    typeof value.field_of_study === 'string' &&
    isOptionalString(value.start_year) &&
    isOptionalString(value.end_year) &&
    isOptionalString(value.gpa)
  )
}

function isProject(value: unknown): value is Project {
  return (
    isRecord(value) &&
    typeof value.name === 'string' &&
    typeof value.description === 'string' &&
    isStringArray(value.tech_stack) &&
    isOptionalString(value.url)
  )
}

function isCandidateProfile(value: unknown): value is CandidateProfile {
  return (
    isRecord(value) &&
    isContactInfo(value.contact_info) &&
    typeof value.summary === 'string' &&
    isSkills(value.skills) &&
    Array.isArray(value.experience) &&
    value.experience.every(isWorkExperience) &&
    Array.isArray(value.education) &&
    value.education.every(isEducation) &&
    Array.isArray(value.projects) &&
    value.projects.every(isProject)
  )
}

function isCVProfile(value: unknown): value is CVProfile {
  if (!isRecord(value) || typeof value.has_profile !== 'boolean') {
    return false
  }
  if (value.profile === null) {
    return value.has_profile === false
  }
  return isCandidateProfile(value.profile)
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json()
  } catch {
    return null
  }
}

function errorFromPayload(payload: unknown, status: number): CVProfileApiError {
  if (isRecord(payload) && isRecord(payload.error)) {
    const envelope = payload.error
    const code = typeof envelope.code === 'string' ? envelope.code : 'request_failed'
    const message =
      typeof envelope.message === 'string' ? envelope.message : 'The CV request failed.'

    return new CVProfileApiError(message, { code, status })
  }

  return new CVProfileApiError('The CV request failed.', {
    code: 'request_failed',
    status,
  })
}

async function readProfileResponse(response: Response): Promise<CVProfile> {
  const payload = await readJson(response)

  if (!response.ok) {
    throw errorFromPayload(payload, response.status)
  }

  if (!isCVProfile(payload)) {
    throw new CVProfileApiError('The CV endpoint returned an unexpected payload.', {
      code: 'unexpected_payload',
      status: response.status,
    })
  }

  return payload
}

export async function fetchCVProfile(signal?: AbortSignal): Promise<CVProfile> {
  const response = await fetch('/api/v1/cv/profile', {
    headers: { Accept: 'application/json' },
    signal,
  })

  return readProfileResponse(response)
}

export async function uploadCVFile(file: File): Promise<CVProfile> {
  const body = new FormData()
  body.append('file', file)

  const response = await fetch('/api/v1/cv/upload', {
    method: 'POST',
    headers: { Accept: 'application/json' },
    body,
  })

  return readProfileResponse(response)
}

export async function uploadCVText(text: string): Promise<CVProfile> {
  const body = new FormData()
  body.append('text', text)

  const response = await fetch('/api/v1/cv/upload', {
    method: 'POST',
    headers: { Accept: 'application/json' },
    body,
  })

  return readProfileResponse(response)
}

export async function saveCVProfile(profile: CandidateProfile): Promise<CVProfile> {
  const response = await fetch('/api/v1/cv/profile', {
    method: 'PUT',
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(profile),
  })

  return readProfileResponse(response)
}
