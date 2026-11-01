// Release notes -> a few plain blocks. The notes come from the update server, so they are untrusted: the result is
// only ever rendered as text nodes (never {@html}); links lose their URL and nothing is executed or fetched.

export interface NoteBlock {
  kind: 'h' | 'li' | 'p'
  text: string
}

const MAX_BLOCKS = 200
const MAX_LINE = 600

/** Drop the few inline Markdown marks that would look like noise as text. */
function plain(s: string): string {
  return s
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1') // [text](url) -> text
    .replace(/(\*\*|__)(.+?)\1/g, '$2')
    .replace(/`([^`]*)`/g, '$1')
    .trim()
    .slice(0, MAX_LINE)
}

/** Headings (#), bullets (- or *) and paragraphs; everything else is text. */
export function parseNotes(body: string | null | undefined): NoteBlock[] {
  const out: NoteBlock[] = []
  let para: string[] = []
  const flush = () => {
    if (para.length) out.push({ kind: 'p', text: plain(para.join(' ')) })
    para = []
  }
  for (const raw of (body ?? '').split(/\r?\n/)) {
    if (out.length >= MAX_BLOCKS) break
    const line = raw.trim()
    const h = /^#{1,6}\s+(.*)$/.exec(line)
    const li = /^[-*]\s+(.*)$/.exec(line)
    if (!line) flush()
    else if (h) {
      flush()
      out.push({ kind: 'h', text: plain(h[1]) })
    } else if (li) {
      flush()
      out.push({ kind: 'li', text: plain(li[1]) })
    } else para.push(line)
  }
  flush()
  return out.filter((b) => b.text)
}
