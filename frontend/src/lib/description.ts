/**
 * Turn a stored job description into readable text.
 *
 * Connectors store the source's HTML, so the raw string contains tags. This
 * helper reads it as *text*: it never injects markup into the page, so a
 * description cannot execute anything, and it keeps paragraph and list
 * boundaries so the result still reads like prose.
 *
 * Nothing is rewritten or summarised; only markup is dropped.
 */
export function descriptionAsText(value: string): string {
  if (!/[<&]/.test(value)) {
    return value
  }

  const parsed = new DOMParser().parseFromString(value, 'text/html')

  // Script, style, and template blocks are not readable description content.
  // Their text would otherwise survive the flattening and be shown as prose.
  for (const element of parsed.querySelectorAll(
    'script, style, noscript, template',
  )) {
    element.remove()
  }

  // Paragraph-level blocks get a blank line; list items and line breaks only
  // end the current line, so a list stays a tight block.
  for (const element of parsed.querySelectorAll('p, div, h1, h2, h3, h4, h5, h6, tr')) {
    element.append(parsed.createTextNode('\n\n'))
  }
  for (const element of parsed.querySelectorAll('li, br')) {
    element.append(parsed.createTextNode('\n'))
  }

  // Keep the shape of a list: flattening to text would otherwise lose the
  // marker and leave the items reading as unrelated sentences.
  for (const item of parsed.querySelectorAll('li')) {
    item.prepend(parsed.createTextNode('• '))
  }

  const text = parsed.body.textContent ?? ''

  return text
    .replace(/\r\n?/g, '\n')
    // Source HTML indents list items and paragraphs; that indentation is not
    // part of the text.
    .split('\n')
    .map((line) => line.trim())
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    // A bullet list reads as one block, whatever whitespace the source used.
    .replace(/(•[^\n]*)\n+(?=• )/g, '$1\n')
    .trim()
}
