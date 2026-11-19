import { describe, expect, it } from 'vitest'
import { descriptionAsText } from './description'

describe('descriptionAsText', () => {
  it('returns plain text unchanged', () => {
    expect(descriptionAsText('A plain description.')).toBe('A plain description.')
  })

  it('strips markup without keeping tags', () => {
    const result = descriptionAsText('<p>Build <strong>dashboards</strong>.</p>')

    expect(result).toBe('Build dashboards.')
    expect(result).not.toContain('<')
  })

  it('decodes entities as text', () => {
    expect(descriptionAsText('<p>R&amp;D &lt;team&gt;</p>')).toBe('R&D <team>')
  })

  it('keeps paragraphs on separate lines', () => {
    expect(descriptionAsText('<p>First.</p><p>Second.</p>')).toBe(
      'First.\n\nSecond.',
    )
  })

  it('keeps list items as a single block with markers', () => {
    const result = descriptionAsText(
      '<p>Duties:</p><ul>\n  <li>Own reporting.</li>\n  <li>Build dashboards.</li>\n</ul>',
    )

    expect(result).toBe('Duties:\n\n• Own reporting.\n• Build dashboards.')
  })

  it('drops indentation that came from the source HTML', () => {
    const result = descriptionAsText('<p>\n      Indented line.\n    </p>')

    expect(result).toBe('Indented line.')
  })

  it('does not execute embedded markup', () => {
    const result = descriptionAsText(
      '<p>Safe</p><script>window.__pwned = true</script>',
    )

    expect(result).not.toContain('<script')
    expect(result).not.toContain('__pwned')
    expect((window as unknown as { __pwned?: boolean }).__pwned).toBeUndefined()
  })

  it('drops script and style blocks instead of showing their source', () => {
    const result = descriptionAsText(
      '<p>Readable.</p><style>.x { color: red; }</style><template>hidden</template>',
    )

    expect(result).toBe('Readable.')
  })
})
