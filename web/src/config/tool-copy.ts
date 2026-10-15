export interface ToolCopy {
  title: string
  summary: string
  details: string
}

// UI copy is localized separately from the instructions sent to the model.
export const builtinToolCopy: Readonly<Record<string, ToolCopy | undefined>> = {
  image_generate: {
    title: '图片生成',
    summary: '根据描述生成图片并保存到工作区。',
    details: '生成的图片可在对话中预览，点击打开文件。',
  },
  file_read: {
    title: '读取文件',
    summary: '读取工作区中的文本文件。',
    details:
      '按路径读取文本文件，支持指定编码。较大的 UTF-8 文件可以分段继续读取，长内容不会一次全部载入。',
  },
  file_write: {
    title: '写入文件',
    summary: '创建或覆盖文本文件。',
    details:
      '在指定路径创建文件或覆盖已有内容，可指定编码并按需创建父文件夹。覆盖会替换原文件内容。',
  },
  file_edit: {
    title: '编辑文件',
    summary: '精确替换文件中的指定内容。',
    details: '替换文件中完全匹配的文字，空格和换行也必须一致。可以限制替换次数，或替换全部匹配项。',
  },
  glob: {
    title: '查找文件',
    summary: '按文件名或路径查找文件。',
    details: '按文件名或路径模式查找文件。遵循忽略规则，跳过隐藏文件，不跟随符号链接。',
  },
  grep: {
    title: '搜索内容',
    summary: '搜索文件中的文字。',
    details:
      '在文件或文件夹中搜索文字，支持正则表达式、忽略大小写和显示匹配处的前后内容。遵循忽略规则，跳过隐藏文件和二进制文件，不跟随符号链接。',
  },
  exec_command: {
    title: '执行命令',
    summary: '在工作区中运行命令。',
    details:
      '在指定工作目录执行命令，返回标准输出、错误输出和退出码。支持 PowerShell 或直接运行程序；受限权限下使用沙箱，联网或沙箱外执行可能需要批准。',
  },
  web_search: {
    title: '联网搜索',
    summary: '查找网上的信息。',
    details:
      '通过 Tavily、Google 或 DuckDuckGo 搜索网页，返回标题、链接和摘要。Tavily 和 Google 需要配置搜索凭证，DuckDuckGo 无需密钥。',
  },
  web_fetch: {
    title: '读取网页',
    summary: '提取网页正文。',
    details:
      '读取公开 HTTP(S) 网页并转换为便于阅读的文本，不运行网页脚本。需要登录或交互的网页可使用浏览器工具。',
  },
  browser: {
    title: '操作浏览器',
    summary: '打开网页、填写表单、点击和截图。',
    details:
      '打开网页、读取内容、填写表单、点击、按键和截图，也可以上传或下载工作区文件。可保留登录状态；需要登录时，由你在浏览器窗口中操作。',
  },
  skill: {
    title: '读取技能',
    summary: '按需加载本地技能的工作指引。',
    details:
      '列出已安装的本地技能，或读取某个技能的操作指引。读取指引不会自动执行技能目录中的程序。',
  },
  todo_write: {
    title: '工作区待办',
    summary: '记录和更新待办事项。',
    details:
      '在当前工作区查看、添加、更新或清空待办事项，也可以标记完成。清空会删除该工作区的待办记录。',
  },
  sleep: {
    title: '短暂等待',
    summary: '等待片刻后继续当前任务。',
    details: '暂停当前任务一段时间后继续，适合需要等待的操作。',
  },
  ask_user_question: {
    title: '向你提问',
    summary: '向你提问，等待回答后继续。',
    details:
      '当任务需要你的补充信息或选择时提出问题，并等待回答后继续。可以提供候选答案，也可以直接填写回复。',
  },
  delegate: {
    title: '派发子任务',
    summary: '将子任务交给只读助手处理。',
    details:
      '把独立的资料查找任务交给只读子助手。子助手仅能读取、查找和搜索工作区文件，不能修改文件或继续派发任务；使用的模型、派发数量和并发数由当前对话设置限制。',
  },
  list_subagent_models: {
    title: '查看子助手模型',
    summary: '查看可供子助手使用的模型。',
    details:
      '查看当前对话允许子助手使用的已配置模型，包含模型名称与准确标识。列表不包含服务地址或密钥，也不代表模型服务当前一定可连接。',
  },
}
