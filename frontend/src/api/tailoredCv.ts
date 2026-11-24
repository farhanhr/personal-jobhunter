export type TailoredCVMode = 'optimize' | 'ultra_optimize'

export type TailoredCVSkills = {
  technical_skills: string[]
  tools_frameworks: string[]
  domain_knowledge: string[]
}

export type TailoredExperienceItem = {
  company: string
  title: string
  location: string | null
  period: string
  bullets: string[]
}

export type TailoredEducationItem = {
  institution: string
  degree: string
  period: string
  details: string | null
}

export type TailoredProjectItem = {
  name: string
  tech_stack: string[]
  description: string
  link: string | null
}

export type TailoredCV = {
  mode: TailoredCVMode
  target_role: string
  target_company: string
  full_name: string
  contact_header: string
  summary: string
  skills: TailoredCVSkills
  experience: TailoredExperienceItem[]
  education: TailoredEducationItem[]
  projects: TailoredProjectItem[]
  upskilling_checklist: string[]
}

export class TailoredCVApiError extends Error {
  readonly code: string
  readonly status: number

  constructor(message: string, options: { code: string; status: number }) {
    super(message)
    this.name = 'TailoredCVApiError'
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

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === 'string'
}

function isSkills(value: unknown): value is TailoredCVSkills {
  return (
    isRecord(value) &&
    isStringArray(value.technical_skills) &&
    isStringArray(value.tools_frameworks) &&
    isStringArray(value.domain_knowledge)
  )
}

function isExperienceItem(value: unknown): value is TailoredExperienceItem {
  return (
    isRecord(value) &&
    typeof value.company === 'string' &&
    typeof value.title === 'string' &&
    isNullableString(value.location) &&
    typeof value.period === 'string' &&
    isStringArray(value.bullets)
  )
}

function isEducationItem(value: unknown): value is TailoredEducationItem {
  return (
    isRecord(value) &&
    typeof value.institution === 'string' &&
    typeof value.degree === 'string' &&
    typeof value.period === 'string' &&
    isNullableString(value.details)
  )
}

function isProjectItem(value: unknown): value is TailoredProjectItem {
  return (
    isRecord(value) &&
    typeof value.name === 'string' &&
    isStringArray(value.tech_stack) &&
    typeof value.description === 'string' &&
    isNullableString(value.link)
  )
}

function isTailoredCV(value: unknown): value is TailoredCV {
  return (
    isRecord(value) &&
    (value.mode === 'optimize' || value.mode === 'ultra_optimize') &&
    typeof value.target_role === 'string' &&
    typeof value.target_company === 'string' &&
    typeof value.full_name === 'string' &&
    typeof value.contact_header === 'string' &&
    typeof value.summary === 'string' &&
    isSkills(value.skills) &&
    Array.isArray(value.experience) &&
    value.experience.every(isExperienceItem) &&
    Array.isArray(value.education) &&
    value.education.every(isEducationItem) &&
    Array.isArray(value.projects) &&
    value.projects.every(isProjectItem) &&
    isStringArray(value.upskilling_checklist)
  )
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json()
  } catch {
    return null
  }
}

function errorFromPayload(payload: unknown, status: number): TailoredCVApiError {
  if (isRecord(payload) && isRecord(payload.error)) {
    const envelope = payload.error
    const code = typeof envelope.code === 'string' ? envelope.code : 'request_failed'
    const message =
      typeof envelope.message === 'string'
        ? envelope.message
        : 'The tailored CV request failed.'

    return new TailoredCVApiError(message, { code, status })
  }

  return new TailoredCVApiError('The tailored CV request failed.', {
    code: 'request_failed',
    status,
  })
}

/**
 * Generate a tailored CV for one opportunity.
 *
 * `mode` is the user's explicit choice; the backend never infers it, and the
 * result is not cached because a CV is a document the user asks for once.
 */
export async function generateTailoredCV(
  opportunityId: string,
  mode: TailoredCVMode,
  signal?: AbortSignal,
): Promise<TailoredCV> {
  const response = await fetch(
    `/api/v1/opportunities/${encodeURIComponent(opportunityId)}/tailored-cv?mode=${mode}`,
    {
      method: 'POST',
      headers: { Accept: 'application/json' },
      signal,
    },
  )
  const payload = await readJson(response)

  if (!response.ok) {
    throw errorFromPayload(payload, response.status)
  }

  if (!isTailoredCV(payload)) {
    throw new TailoredCVApiError(
      'The tailored CV endpoint returned an unexpected payload.',
      { code: 'unexpected_payload', status: response.status },
    )
  }

  return payload
}
