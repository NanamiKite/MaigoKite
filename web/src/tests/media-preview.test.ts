import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { parseImages } from '@/api/images'
import FilePreview from '@/components/file-explorer/FilePreview.vue'
import { renderHTMLFile, renderMarkdownFile, renderSVGFile } from '@/utils/file-preview'

describe('untrusted workspace previews', () => {
  it('renders Markdown as inert formatted text without model escape rewriting', () => {
    const html = renderMarkdownFile('# Title\n\n<script>alert(1)</script>\n![remote](https://example.com/x)\n[link](javascript:alert(1))\n`\\n`')
    expect(html).toContain('<h1>Title</h1>')
    expect(html).not.toMatch(/<script|<img|href=|src=/i)
    expect(html).toContain('\\n')
  })

  it('isolates HTML, removes executable elements and denies network by CSP', () => {
    const result = renderHTMLFile('<h1>Safe</h1><script>EVIL()</script><img src="https://example.com/x"><iframe src="/settings"></iframe><a href="/chat">Link</a>')
    expect(result).toContain('<h1>Safe</h1>')
    expect(result).toContain("default-src 'none'")
    expect(result).not.toMatch(/<script|<iframe|src=|href=|EVIL/i)
  })

  it('keeps SVG gradients but removes scripts, foreign documents and external references', () => {
    const result = renderSVGFile('<svg xmlns="http://www.w3.org/2000/svg"><defs><linearGradient id="a"/></defs><rect fill="url(#a)"/><script>EVIL()</script><foreignObject/><image href="https://example.com/x"/><use href="#a"/><image href="file:///etc/passwd"/></svg>')
    expect(result).not.toBeNull()
    const decoded = decodeURIComponent(result!.split(',', 2)[1])
    expect(decoded).toContain('url(#a)')
    expect(decoded).toContain('href="#a"')
    expect(decoded).not.toMatch(/<script|foreignObject|https:|file:|EVIL/i)
  })

  it.each(['<svg>', '<html/>', '<!DOCTYPE svg [<!ENTITY x "evil">]><svg xmlns="http://www.w3.org/2000/svg">&x;</svg>'])('rejects malformed or entity-bearing SVG: %s', source => {
    expect(renderSVGFile(source)).toBeNull()
  })

  it('uses the real preview toggle and keyboard behavior, and refuses truncated SVG', async () => {
    const wrapper = mount(FilePreview, { props: {
      node: { name: 'drawing.svg', path: 'drawing.svg', type: 'file', children: [] },
      content: { path: 'drawing.svg', content: '<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>', binary: false, size: 80, modified_at: '', truncated: false, mime_type: 'text/plain', data_url: '' },
    } })
    expect(wrapper.find('img').exists()).toBe(true)
    const group = wrapper.get('[role="group"]')
    await group.trigger('keydown', { key: 'Home' })
    expect(wrapper.find('img').exists()).toBe(false)
    await group.trigger('keydown', { key: 'End' })
    expect(wrapper.find('img').exists()).toBe(true)
    await wrapper.setProps({ content: { ...wrapper.props('content')!, truncated: true } })
    await flushPromises()
    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.text()).toContain('源码')
  })

  it('accepts only generated image references matching the public contract', () => {
    const image = { type: 'image', path: 'generated-images/a.png', folder_id: '', sha256: 'a'.repeat(64), mime_type: 'image/png', size: 20 }
    expect(parseImages(JSON.stringify({ data: { images: [image, { ...image, sha256: 'bad' }] } }))).toEqual([image])
    expect(parseImages('not JSON')).toEqual([])
    expect(parseImages(JSON.stringify({ images: [{ ...image, size: -1 }] }))).toEqual([])
  })
})
