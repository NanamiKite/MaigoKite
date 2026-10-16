/**
 * Vue 应用入口
 *
 * 创建 Vue 应用实例，挂载 Pinia 和 Router，挂载到 #app。
 */

import { createApp } from 'vue'
import App from './App.vue'
import pinia from './stores'
import router from './router'
import { setupGuards } from './router/guards'
import { i18n } from './i18n'
import './styles/base.scss'
import './styles/desk.scss'

const app = createApp(App)

app.use(pinia)
app.use(i18n)
app.use(router)
setupGuards(router)

app.mount('#app')
