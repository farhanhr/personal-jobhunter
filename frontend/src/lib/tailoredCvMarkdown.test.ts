import { describe, expect, it } from 'vitest'
import type { TailoredCV } from '../api/tailoredCv'
import { tailoredCvToMarkdown } from './tailoredCvMarkdown'

const cv: TailoredCV = {
  mode: 'ultra_optimize',
  target_role: 'Backend Engineer',
  target_company: 'Acme Example',
  full_name: 'Siti Example',
  contact_header: 'Jakarta, Indonesia | siti@example.test',
  summary: 'Backend engineer focused on payment APIs.',
  skills: {
    technical_skills: ['Python', 'Go'],
    tools_frameworks: ['Docker'],
    domain_knowledge: [],
  },
  experience: [
    {
      company: 'Example Fintech',
      title: 'Senior Backend Engineer',
      location: 'Jakarta',
      period: '2021 - Present',
      bullets: ['Architected the payments API rewrite.'],
    },
  ],
  education: [
    {
      institution: 'Example University',
      degree: 'Bachelor of Computer Science',
      period: '2014 - 2018',
      details: null,
    },
  ],
  projects: [
    {
      name: 'Ledger Service',
      tech_stack: ['Go', 'PostgreSQL'],
      description: 'Double-entry ledger for payouts.',
      link: 'https://example.test/ledger',
    },
  ],
  upskilling_checklist: ['Study Kafka fundamentals'],
}

describe('tailoredCvToMarkdown', () => {
  it('renders standard ATS section headings in order', () => {
    const markdown = tailoredCvToMarkdown(cv)
    const headings = markdown
      .split('\n')
      .filter((line) => line.startsWith('## '))
      .map((line) => line.replace('## ', ''))

    expect(headings).toEqual([
      'Summary',
      'Technical Skills',
      'Professional Experience',
      'Projects',
      'Education',
      'Upskilling Checklist',
    ])
  })

  it('keeps the contact line and experience bullets as plain text', () => {
    const markdown = tailoredCvToMarkdown(cv)

    expect(markdown.startsWith('# Siti Example')).toBe(true)
    expect(markdown).toContain('Jakarta, Indonesia | siti@example.test')
    expect(markdown).toContain('### Senior Backend Engineer — Example Fintech')
    expect(markdown).toContain('Jakarta · 2021 - Present')
    expect(markdown).toContain('- Architected the payments API rewrite.')
  })

  it('writes the checklist as unchecked tasks', () => {
    expect(tailoredCvToMarkdown(cv)).toContain('- [ ] Study Kafka fundamentals')
  })

  it('omits empty sections and an empty checklist', () => {
    const markdown = tailoredCvToMarkdown({
      ...cv,
      mode: 'optimize',
      skills: {
        technical_skills: [],
        tools_frameworks: [],
        domain_knowledge: [],
      },
      projects: [],
      education: [],
      upskilling_checklist: [],
    })

    expect(markdown).not.toContain('## Technical Skills')
    expect(markdown).not.toContain('## Projects')
    expect(markdown).not.toContain('## Education')
    expect(markdown).not.toContain('Upskilling Checklist')
  })
})
