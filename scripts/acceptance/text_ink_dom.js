// Executed inside the actual Chromium-rendered application, never jsdom.
async ({ tokens }) => {
  const { contrastRatio } = await import('/src/utils/color-contrast.ts')
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = 1
  const context = canvas.getContext('2d', { willReadFrequently: true })
  function rgba(color) {
    context.clearRect(0, 0, 1, 1)
    context.fillStyle = color
    context.fillRect(0, 0, 1, 1)
    return Array.from(context.getImageData(0, 0, 1, 1).data)
  }
  function resolve(element) {
    const probe = document.createElement('span')
    probe.hidden = true
    element.append(probe)
    const result = Object.fromEntries(tokens.filter(token => getComputedStyle(element).getPropertyValue(token).trim()).map(token => {
      probe.style.color = `var(${token})`
      return [token, rgba(getComputedStyle(probe).color)]
    }))
    probe.remove()
    return result
  }
  const shell = document.querySelector('.inkline-shell, .inkline-preview') || document.body
  const palette = resolve(shell)
  const rootPalette = resolve(document.body)
  function background(element) {
    const layers = []
    // The mode selector paints its active paper with a positioned sibling, not an ancestor.
    const mode = element.closest('.mode-pill button[aria-pressed="true"]')
    if (mode) layers.push(rgba(getComputedStyle(mode.parentElement.querySelector('.mode-ink')).backgroundColor))
    for (let current = element; current; current = current.parentElement) {
      const color = rgba(getComputedStyle(current).backgroundColor)
      layers.push(color)
      if (color[3] === 255) break
    }
    return layers.reverse().reduce((base, layer) => base.map((channel, i) =>
      channel * (1 - layer[3] / 255) + layer[i] * layer[3] / 255), [255, 255, 255])
  }
  const rows = [], decorations = []
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const element = node.parentElement
    if (!element || !node.textContent.trim() || element.closest('svg,script,style,.sr-only,[inert],[aria-hidden="true"]')) continue
    const style = getComputedStyle(element), range = document.createRange()
    range.selectNodeContents(node)
    const rect = range.getBoundingClientRect()
    if (!rect.width || !rect.height || rect.bottom <= 0 || rect.top >= innerHeight || rect.right <= 0 || rect.left >= innerWidth) continue
    let visible = true, opacity = 1
    for (let ancestor = element; ancestor; ancestor = ancestor.parentElement) {
      const css = getComputedStyle(ancestor), box = ancestor.getBoundingClientRect()
      opacity *= Number(css.opacity)
      if (css.visibility !== 'visible' || css.display === 'none' ||
        (/(auto|scroll|hidden|clip)/.test(css.overflowY) && (rect.top >= box.bottom || rect.bottom <= box.top))) visible = false
    }
    if (!visible || !opacity) continue
    const color = rgba(style.color), bg = background(element)
    const matches = Object.entries(element.closest('.inkline-shell, .inkline-preview') ? palette : rootPalette)
      .filter(([, value]) => value.every((channel, i) => channel === color[i])).map(([token]) => token)
    const selector = [element.tagName.toLowerCase(), ...element.classList].join('.')
    rows.push({ text: node.textContent.trim().slice(0, 100), selector, color, background: bg,
      tokens: matches, opacity, disabled: Boolean(element.closest(':disabled,[aria-disabled="true"]')),
      ratio: contrastRatio(color, bg), surface: element.closest('.sidebar,.drawer,.composer,.titlebar,.windowbar,.messages-inner')?.className || 'page' })
  }
  for (const element of document.querySelectorAll('svg')) {
    const rect = element.getBoundingClientRect()
    if (rect.width && rect.height && rect.bottom > 0 && rect.top < innerHeight) {
      const css = getComputedStyle(element)
      decorations.push({ parent: element.parentElement.className, color: rgba(css.color), fill: css.fill, stroke: css.stroke })
    }
  }
  const matrix = Object.fromEntries(Object.entries(palette).map(([token, color]) => [token,
    ['--paper', '--paper-2', '--paper-3'].map(paper => contrastRatio(color, palette[paper]))]))
  return { palette, matrix, rows, decorations }
}
