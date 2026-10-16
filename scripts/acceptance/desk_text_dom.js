// Real visible text rectangles. A second screenshot removes text, not its paper.
() => {
  const root = document.querySelector('.app-layout'), canvas = document.createElement('canvas')
  canvas.width = canvas.height = 1
  const ctx = canvas.getContext('2d', { willReadFrequently: true })
  const rgb = value => {
    ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = value; ctx.fillRect(0, 0, 1, 1)
    return Array.from(ctx.getImageData(0, 0, 1, 1).data).slice(0, 3)
  }
  const tokens = ['--ink', '--ink-2', '--ink-3', '--accent-ink', '--accent-ink-action', '--wait-ink', '--seal', '--seal-ink-action', '--stone-ink', '--code-addition-ink', '--code-deletion-ink', '--on-accent', '--desk-seal-ink']
  const readPalette = parent => {
    const probe = document.createElement('span'); probe.hidden = true; parent.append(probe)
    const palette = Object.fromEntries(tokens.map(token => {
      probe.style.color = `var(${token})`; return [token, rgb(getComputedStyle(probe).color)]
    }))
    probe.remove(); return palette
  }
  // Scope matters: the chat sheet has stronger text inks; the shell is unchanged.
  const palette = readPalette(root), chat = root.querySelector('.chat-body')
  const chatPalette = chat ? readPalette(chat) : palette
  const rows = [], surfaces = '.chat-body,.messages-inner,.workpaper,.sidebar,.drawer,.composer,.titlebar,.windowbar,.appearance-preview'
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const el = node.parentElement
    if (!el || !node.textContent.trim() || !el.closest(surfaces) || el.closest('svg,script,style,.sr-only,[aria-hidden="true"],:disabled,[aria-disabled="true"]')) continue
    const css = getComputedStyle(el), range = document.createRange(); range.selectNodeContents(node)
    const scoped = el.closest('.chat-body') ? chatPalette : palette
    const color = rgb(css.color), matches = tokens.filter(token => scoped[token].every((v, i) => v === color[i]))
    for (const rect of range.getClientRects()) {
      let left = Math.max(0, rect.left), right = Math.min(innerWidth, rect.right)
      let top = Math.max(0, rect.top), bottom = Math.min(innerHeight, rect.bottom), visible = true
      for (let p = el; p; p = p.parentElement) {
        const style = getComputedStyle(p), box = p.getBoundingClientRect()
        if (style.visibility !== 'visible' || style.display === 'none' || Number(style.opacity) < 0.99) visible = false
        if (/(auto|scroll|hidden|clip)/.test(style.overflowY)) { top = Math.max(top, box.top); bottom = Math.min(bottom, box.bottom) }
        if (/(auto|scroll|hidden|clip)/.test(style.overflowX)) { left = Math.max(left, box.left); right = Math.min(right, box.right) }
      }
      if (!visible || right - left < 2 || bottom - top < 2) continue
      const hit = document.elementFromPoint((left + right) / 2, (top + bottom) / 2)
      if (!hit || !(el === hit || el.contains(hit) || hit.contains(el))) continue
      rows.push({ text: node.textContent.trim().slice(0, 80), color, tokens: matches,
        target: matches.includes('--ink') ? 7 : 4.5, rect: [left, top, right, bottom],
        surface: el.closest(surfaces).className, selector: el.tagName + '.' + [...el.classList].join('.') })
    }
  }
  return { palette, chatPalette, rows }
}
