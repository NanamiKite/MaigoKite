import DOMPurify from 'dompurify'
import MarkdownIt from 'markdown-it'

// File source is literal: do not apply model-output escape repair to documents.
const markdown = new MarkdownIt({ html: false, linkify: true }).disable('image')

export function renderMarkdownFile(source: string): string {
  return DOMPurify.sanitize(markdown.render(source), {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ['form', 'iframe', 'img', 'input', 'button', 'style', 'video', 'audio'],
    FORBID_ATTR: ['style', 'href', 'src', 'target'],
  })
}

export function renderSVGFile(source: string): string | null {
  // Parse XML without custom entities. Exported DOCTYPE declarations are not
  // serialized: only the SVG root is passed to the existing sanitizer.
  if (/<!ENTITY\s/i.test(source)) return null
  const document = new DOMParser().parseFromString(source, 'image/svg+xml')
  const root = document.documentElement
  const svgNamespace = 'http://www.w3.org/2000/svg'
  if (
    root.localName !== 'svg' ||
    root.namespaceURI !== svgNamespace ||
    document.getElementsByTagName('parsererror').length
  )
    return null
  const serializer = new XMLSerializer()
  const clean = DOMPurify.sanitize(serializer.serializeToString(root), {
    USE_PROFILES: { svg: true, svgFilters: true },
    // The SVG profile excludes use. Allow it only in this isolated image path;
    // the pass below restricts both href and xlink:href to local fragments.
    ADD_TAGS: ['use'],
    FORBID_TAGS: [
      'script',
      'foreignObject',
      'animate',
      'animateMotion',
      'animateTransform',
      'set',
      'discard',
    ],
    FORBID_ATTR: ['xml:base'],
    ALLOW_DATA_ATTR: false,
    RETURN_DOM_FRAGMENT: true,
  })
  const svg = clean.firstElementChild
  if (svg?.localName !== 'svg' || svg.namespaceURI !== svgNamespace) return null
  // Preserve local gradients/symbols and embedded raster images, not links to
  // files or remote servers. SVG-as-img also isolates CSS and disables scripts
  // and external loads; never insert this markup with v-html or as a document.
  for (const element of [svg, ...svg.querySelectorAll('*')]) {
    for (const attribute of [...element.attributes]) {
      if (attribute.localName !== 'href') continue
      const value = attribute.value.trim()
      const localReference = /^#\S+$/.test(value)
      const embeddedImage =
        element.localName === 'image' &&
        /^data:image\/(png|jpeg|gif|webp);base64,[A-Za-z0-9+/]+=*$/.test(value)
      if (!localReference && !embeddedImage) element.removeAttributeNode(attribute)
    }
  }
  return 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(serializer.serializeToString(svg))
}

export function renderHTMLFile(source: string): string {
  const content = DOMPurify.sanitize(source, {
    WHOLE_DOCUMENT: true,
    USE_PROFILES: { html: true },
    FORBID_TAGS: ['script', 'iframe', 'object', 'embed', 'base', 'meta', 'link', 'form'],
    FORBID_ATTR: ['href', 'src', 'srcset', 'action', 'formaction', 'target', 'ping'],
  })
  // CSP precedes user markup; the iframe also has an empty sandbox. Inline CSS
  // can render, but scripts, networking, navigation and application access cannot.
  return (
    '<!doctype html><html><head><meta charset="utf-8">' +
    '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; ' +
    "style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'\">" +
    '<meta name="referrer" content="no-referrer"></head><body>' +
    content +
    '</body></html>'
  )
}
