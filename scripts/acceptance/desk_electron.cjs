// Acceptance-only isolated profile; production BrowserWindow/preload/security unchanged.
const path = require('node:path')
const { app } = require('electron')
if (!process.env.DESK_ACCEPTANCE_PROFILE) throw new Error('An isolated profile is required')
app.setPath('userData', process.env.DESK_ACCEPTANCE_PROFILE)
process.env.MAIGOKITE_DESKTOP_BUILT = '1'
process.env.MAIGOKITE_EXTERNAL_RUNTIME = '1'
require(path.resolve(__dirname, '../../web/dist-electron/main.cjs'))
