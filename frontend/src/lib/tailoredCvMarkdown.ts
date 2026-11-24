import type { TailoredCV } from '../api/tailoredCv'

/**
 * Plain-Markdown rendering of a tailored CV.
 *
 * ATS parsers and recruiters both read this well: linear text, standard section
 * names, and no tables or graphics. The same order is used on screen and in the
 * printed document.
 */
export function tailoredCvToMarkdown(cv: TailoredCV): string {
  const lines: string[] = [`# ${cv.full_name}`]

  if (cv.contact_header.trim().length > 0) {
    lines.push(cv.contact_header.trim())
  }

  if (cv.summary.trim().length > 0) {
    lines.push('', '## Summary', cv.summary.trim())
  }

  const skillGroups: Array<[string, string[]]> = [
    ['Technical Skills', cv.skills.technical_skills],
    ['Tools and Frameworks', cv.skills.tools_frameworks],
    ['Domain Knowledge', cv.skills.domain_knowledge],
  ]
  const hasSkills = skillGroups.some(([, items]) => items.length > 0)
  if (hasSkills) {
    lines.push('', '## Technical Skills')
    for (const [label, items] of skillGroups) {
      if (items.length > 0) {
        lines.push(`- ${label}: ${items.join(', ')}`)
      }
    }
  }

  if (cv.experience.length > 0) {
    lines.push('', '## Professional Experience')
    for (const item of cv.experience) {
      lines.push('', `### ${item.title} — ${item.company}`)
      const meta = [item.location, item.period].filter(
        (part): part is string => part !== null && part.trim().length > 0,
      )
      if (meta.length > 0) {
        lines.push(meta.join(' · '))
      }
      for (const bullet of item.bullets) {
        lines.push(`- ${bullet}`)
      }
    }
  }

  if (cv.projects.length > 0) {
    lines.push('', '## Projects')
    for (const project of cv.projects) {
      lines.push('', `### ${project.name}`)
      if (project.tech_stack.length > 0) {
        lines.push(project.tech_stack.join(', '))
      }
      if (project.description.trim().length > 0) {
        lines.push(project.description.trim())
      }
      if (project.link !== null && project.link.trim().length > 0) {
        lines.push(project.link.trim())
      }
    }
  }

  if (cv.education.length > 0) {
    lines.push('', '## Education')
    for (const item of cv.education) {
      lines.push('', `### ${item.degree} — ${item.institution}`)
      if (item.period.trim().length > 0) {
        lines.push(item.period.trim())
      }
      if (item.details !== null && item.details.trim().length > 0) {
        lines.push(item.details.trim())
      }
    }
  }

  if (cv.upskilling_checklist.length > 0) {
    lines.push('', '## Upskilling Checklist')
    for (const item of cv.upskilling_checklist) {
      lines.push(`- [ ] ${item}`)
    }
  }

  return `${lines.join('\n')}\n`
}
