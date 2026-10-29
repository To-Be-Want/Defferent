class MessageService {
    constructor() {
        // 存储每个step的tool events
        this.stepToolEvents = new Map(); // stepIndex -> Array of tool events
        // 存储当前step的pending tool starts (等待complete的tool_start消息)
        this.pendingToolStarts = new Map(); // stepIndex -> Array of tool_start events

        // 恢复本地存储的step tool events
        this.restoreStepToolEvents();
    }

    setChatStatus(text, pending = true) {
        let status = document.getElementById('study-chat-status');
        const messages = document.getElementById('chat-messages');
        if (!status && messages) {
            status = document.createElement('div');
            status.id = 'study-chat-status';
            status.className = 'study-thinking-status';
            status.setAttribute('role', 'status');
            status.setAttribute('aria-live', 'polite');
            const dots = document.createElement('span');
            dots.className = 'study-thinking-dots';
            dots.setAttribute('aria-hidden', 'true');
            dots.textContent = '•••';
            const label = document.createElement('span');
            label.className = 'study-thinking-label';
            const elapsed = document.createElement('small');
            elapsed.className = 'study-thinking-elapsed';
            elapsed.setAttribute('aria-hidden', 'true');
            status.append(dots, label, elapsed);
            messages.before(status);
        }
        if (!text) {
            if (this._thinkingTimer) clearInterval(this._thinkingTimer);
            this._thinkingTimer = null;
            this._thinkingStarted = null;
        }
        if (status) {
            status.querySelector('.study-thinking-label').textContent = text;
            status.hidden = !text;
            status.dataset.pending = String(pending);
            if (text && pending && !this._thinkingTimer) {
                this._thinkingStarted = Date.now();
                const elapsed = status.querySelector('.study-thinking-elapsed');
                elapsed.textContent = '';
                this._thinkingTimer = setInterval(() => {
                    const seconds = Math.floor((Date.now() - this._thinkingStarted) / 1000);
                    elapsed.textContent = seconds >= 1 ? `${seconds} 秒` : '';
                }, 1000);
            }
            if (!pending && this._thinkingTimer) {
                clearInterval(this._thinkingTimer);
                this._thinkingTimer = null;
                status.querySelector('.study-thinking-elapsed').textContent = '';
            }
        }
    }

    receiveMessage(message) {
        console.log('MessageService.receiveMessage >>>>>>>>>>>>>> ', message);

        try {
            // 解析消息
            const messageData = typeof message === 'string' ? JSON.parse(message) : message;

            // 首次收到该topic的任意消息则清除stillPending标记
            try {
                const topic = messageData.topic;
                if (topic) {
                    const pendingRaw = localStorage.getItem('cosight:pendingRequests');
                    const pendings = pendingRaw ? JSON.parse(pendingRaw) : {};
                    if (pendings[topic] && pendings[topic].stillPending === true) {
                        pendings[topic].stillPending = false;
                        localStorage.setItem('cosight:pendingRequests', JSON.stringify(pendings));
                    }
                }
            } catch (e) {
                console.warn('更新pending标记失败:', e);
            }

            // 处理控制类结束信号，标记pending完成
            if (messageData && messageData.data && messageData.data.type === 'control-status-message') {
                this.setChatStatus('');
                try {
                    const topic = messageData.topic;
                    const pendingRaw = localStorage.getItem('cosight:pendingRequests');
                    const pendings = pendingRaw ? JSON.parse(pendingRaw) : {};
                    if (topic && pendings[topic]) {
                        delete pendings[topic];
                        localStorage.setItem('cosight:pendingRequests', JSON.stringify(pendings));
                    }
                } catch (e) {
                    console.warn('更新pending失败:', e);
                }
            }

            // 处理 lui-message-tool-event 类型的消息
            // 支持 contentType 和 type 两种字段名以保持兼容性
            const messageType = messageData.data?.contentType || messageData.data?.type;
            if (messageType === 'study-agent-progress') {
                const progress = messageData.data;
                const stages = {
                    locating: '正在定位对应知识点…',
                    reading: progress.count ? `正在读取 ${progress.count} 段课件原文、例题或代码…` : '正在检查知识点的原文位置…',
                    indexing: '正在补建课件位置索引，后续可直接定位…',
                    context: '正在读取已有学习资料…',
                    thinking: '正在结合课件组织讲解…',
                    'tool-result': '已完成一步，正在继续…'
                };
                const text = progress.stage === 'waiting' ? '仍在处理，请稍候…'
                    : progress.stage === 'tool' ? (['read_course_material', 'read_knowledge_block'].includes(progress.tool)
                        ? '正在定位课件原文与解题步骤…'
                        : progress.tool === 'generate_study_plan' ? '正在根据知识点依赖和可用时间计算排期…' : '正在处理学习任务…')
                    : stages[progress.stage] || '正在处理…';
                this.setChatStatus(text);
                return;
            }
            if (messageType === 'study-engine-route') {
                const engine = messageData.data.engine;
                const label = document.getElementById('study-engine-label');
                if (label) {
                    label.textContent = engine === 'agent' ? '学习助手' : (engine === 'deep' ? '完整引擎' : '快速通道');
                    label.dataset.engine = engine;
                }
                const title = document.querySelector('.dag-preview-header strong');
                if (title) title.textContent = engine === 'agent' ? '学习任务进度' : (engine === 'deep' ? '完整引擎任务流程' : '任务流程（最近一次完整任务）');
                return;
            }
            
            if (messageType === 'lui-message-tool-event') {
                console.log('收到 lui-message-tool-event 消息:', messageData);
                this.handleToolEvent(messageData);
                return;
            }

            // 处理 lui-message-credibility-analysis 类型的消息
            if (messageType === 'lui-message-credibility-analysis') {
                console.log('收到 lui-message-credibility-analysis 消息:', messageData);
                credibilityService.credibilityMessageHandler(messageData);
                return;
            }

            // 检查是否是 lui-message-manus-step 类型的消息
        if (messageType === 'lui-message-manus-step') {
    console.log('收到 lui-message-manus-step 消息，开始创建DAG图');
    console.log('完整消息数据:', messageData);
    this.stepMessageHandler(messageData);
    window.updateStudyRun?.(messageData.data?.content || messageData.data?.initData);
} else if (messageType === 'multi-modal') {
    // 处理快通道返回的聊天消息
    console.log('收到 multi-modal 聊天消息:', messageData);
    try {
        // 后端会把用户消息以 from:"human" 回推一条（用于状态标记），
        // 但用户消息已由 sendMessage 本地渲染过，这里跳过避免重复显示（右侧AI气泡冒出用户原文）。
        if (messageData.data?.from === 'human') {
            this.setChatStatus('消息已收到，正在处理…');
            return;
        }
        const initData = messageData.data?.initData || messageData.data?.content;
        if (initData && Array.isArray(initData)) {
            const textContent = initData
                .filter(item => item.type === 'text')
                .map(item => item.value)
                .join('');
            if (textContent) {
                console.log('渲染快通道消息内容:', textContent);
                // 统一追加到固定聊天面板（AI 回复），并自动展开
                this.setChatStatus('');
                if (typeof window.appendChatMessage === 'function') {
                    window.appendChatMessage(textContent, 'ai');
                }
                if (typeof window.expandChatPanel === 'function') {
                    window.expandChatPanel();
                }
            }
        }
    } catch (e) {
        console.error('处理 multi-modal 消息失败:', e);
    }
} else {
    console.log('收到其他类型的消息:', messageType || 'unknown');
}
        } catch (error) {
            console.error('处理消息时发生错误:', error);
        }
    }

    stepMessageHandler(messageData) {
        // 调用 createDag 方法来创建DAG图
        const result = createDag(messageData);
        if (!result) {
            return;
        }

        // 成功后持久化消息到本地以便刷新恢复
        try {
            localStorage.setItem('cosight:lastManusStep', JSON.stringify({
                message: messageData,
                savedAt: Date.now()
            }));
        } catch (e) {
            console.warn('保存本地状态失败:', e);
        }

        // 获取initData，支持多种格式
        const initData = messageData.data?.content || messageData.data?.initData;
        
        // 显示标题信息
        if (initData && initData.title) {
            updateDynamicTitle(initData.title);
            showStepsTooltip();
            setTimeout(() => {
                hideStepsTooltip();
            }, 3000);
        }
        
        // 在接收到步骤状态更新后，自动关闭已完成且无运行中工具的步骤面板
        try {
            if (initData) {
                this._autoCloseCompletedStepPanels(initData);
            }
        } catch (e) {
            console.warn('自动关闭完成步骤面板时发生异常:', e);
        }
    }

    /**
     * 生成并复用稳定的学习会话ID
     * 同一段对话内的所有消息共用同一个 ID，后端据此累积上下文（错题暂存等）
     */
    ensureLearningSessionId() {
        try {
            const key = 'cosight:learningSessionId';
            let sid = localStorage.getItem(key);
            if (!sid) {
                sid = WebSocketService.generateUUID();
                localStorage.setItem(key, sid);
            }
            return sid;
        } catch (e) {
            console.warn('生成/读取学习会话ID失败，退化为临时ID:', e);
            return WebSocketService.generateUUID();
        }
    }

    /**
     * 基于topic生成并复用稳定的planId(messageSerialNumber)
     */
    ensurePlanIdForTopic(topic) {
        try {
            const raw = localStorage.getItem('cosight:planIdByTopic');
            const map = raw ? JSON.parse(raw) : {};
            let rec = map[topic];
            if (!rec || rec.completed === true || !rec.planId) {
                const planId = (crypto && crypto.randomUUID) ? crypto.randomUUID() : `${topic}-${Date.now()}`;
                rec = { planId, stillPending: true, completed: false };
                map[topic] = rec;
                localStorage.setItem('cosight:planIdByTopic', JSON.stringify(map));
                console.log('store planIdByTopic',JSON.stringify(map));                
            }
            return rec.planId;
        } catch (e) {
            console.warn('生成/读取planId失败，退化为时间戳:', e);
            return `${topic}-${Date.now()}`;
        }
    }

    /**
     * 上传文件到后端 upload_files 目录
     * @param {FileList|File[]} files - 用户选择的文件
     * @returns {Promise<Array>} 返回 [{filename, size_mb, url}, ...]
     */
    async uploadFiles(files, subject = '自动分类') {
        if (!files || files.length === 0) return [];
        const formData = new FormData();
        for (const f of files) {
            formData.append('files', f, f.name);
        }
        formData.append('subject', subject);
        const resp = await fetch('/api/nae-deep-research/v1/upload', {
            method: 'POST',
            body: formData
        });
        if (!resp.ok) {
            const error = await resp.json().catch(() => ({}));
            throw new Error(error.detail || error.message || `上传请求失败 HTTP ${resp.status}`);
        }
        const json = await resp.json();
        if (json.code !== 0) {
            throw new Error(json.message || '上传失败');
        }
        return json.data || [];
    }

    /**
     * 上传文件到"学习资料库"（队友功能：资料索引+科目分类）。
     * 走 /deep-research/upload（x-file-name/x-subject header + 二进制 body）。
     * @returns {Promise<Array>} 返回 [{upload_id, filename, subject, size_mb}, ...]
     */
    async uploadToLibrary(files, subject = '自动分类') {
        if (!files || files.length === 0) return [];
        const uploaded = [];
        for (const file of Array.from(files)) {
            const data = await this.uploadSingleToLibrary(file, subject);
            uploaded.push(data);
        }
        return uploaded;
    }

    async uploadSingleToLibrary(file, subject = '自动分类') {
        const url = '/api/nae-deep-research/v1/deep-research/upload';
        const headers = {
            'Content-Type': 'application/octet-stream',
            'X-File-Name': encodeURIComponent(file.name),
            'X-Subject': encodeURIComponent(subject)
        };
        try {
            const response = await fetch(url, {method: 'POST', headers, body: file});
            const data = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(data.detail || `上传失败 (${response.status})`);
            return data;
        } catch (error) {
            // 部分浏览器或安全软件会中断 fetch 的本地文件请求，使用同源 XHR 重试。
            const isNetworkFailure = error instanceof TypeError || /fetch|network/i.test(error?.message || '');
            if (!isNetworkFailure) throw error;
            return await new Promise((resolve, reject) => {
                const request = new XMLHttpRequest();
                request.open('POST', url, true);
                Object.entries(headers).forEach(([name, value]) => request.setRequestHeader(name, value));
                request.onload = () => {
                    let data = {};
                    try { data = JSON.parse(request.responseText || '{}'); } catch (_) {}
                    if (request.status >= 200 && request.status < 300) resolve(data);
                    else reject(new Error(data.detail || `上传失败 (${request.status})`));
                };
                request.onerror = () => reject(new Error('无法连接上传接口，请确认页面与后端都使用 localhost:7788'));
                request.send(file);
            });
        }
    }

    /**
     * 读取当前已上传文件列表（localStorage 持久化，刷新不丢）
     */
    getUploadedFilesInfo() {
        try {
            const raw = localStorage.getItem('cosight:uploadedFiles');
            return raw ? JSON.parse(raw) : [];
        } catch (e) {
            console.warn('读取已上传文件失败:', e);
            return [];
        }
    }

    /**
     * 保存已上传文件列表
     */
    setUploadedFilesInfo(files) {
        try {
            localStorage.setItem('cosight:uploadedFiles', JSON.stringify(files || []));
        } catch (e) {
            console.warn('保存已上传文件失败:', e);
        }
    }

    /**
     * 清空已上传文件列表
     */
    clearUploadedFiles() {
        try {
            localStorage.removeItem('cosight:uploadedFiles');
        } catch (e) {
            console.warn('清空已上传文件失败:', e);
        }
    }

    /**
     * 处理tool event消息
     */
    handleToolEvent(messageData) {
        // 兼容多种数据格式：
        // 1. messageData.data.content (标准格式，contentType字段)
        // 2. messageData.data.initData.plan (旧格式)
        // 3. messageData.data.initData (旧格式)
        const toolEventData = messageData.data?.content || messageData.data?.initData?.plan || messageData.data?.initData;
        
        if (!toolEventData) {
            console.warn('无法获取tool event数据:', messageData);
            return;
        }
        
        const stepIndex = toolEventData.step_index;
        const eventType = toolEventData.event_type;
        
        console.log(`处理tool event: ${eventType}, step: ${stepIndex}, tool: ${toolEventData.tool_name}`);
        console.log('完整的toolEventData:', toolEventData);
        
        // 特殊处理 mark_step 工具事件，更新节点的 step_notes
        if (toolEventData.tool_name === 'mark_step' && eventType === 'tool_complete') {
            this.handleMarkStepEvent(toolEventData);
        }

        // 确保stepIndex对应的数组存在
        if (!this.stepToolEvents.has(stepIndex)) {
            this.stepToolEvents.set(stepIndex, []);
        }
        if (!this.pendingToolStarts.has(stepIndex)) {
            this.pendingToolStarts.set(stepIndex, []);
        }

        const stepEvents = this.stepToolEvents.get(stepIndex);
        const pendingStarts = this.pendingToolStarts.get(stepIndex);

        if (eventType === 'tool_start') {
            // 处理tool_start消息
            const toolStartEvent = {
                ...toolEventData,
                messageData: messageData,
                timestamp: Date.now()
            };
            
            // 添加到pending列表
            pendingStarts.push(toolStartEvent);
            
            // 检查是否是该step的第一个tool event，如果是则弹出panel
            if (stepEvents.length === 0 && pendingStarts.length === 1) {
                console.log(`Step ${stepIndex} 的第一个tool event，弹出panel`);
                this.showStepPanel(stepIndex);
            }
            
            // 立即创建一个"运行中"状态的工具调用记录并显示在panel上
            const runningToolCallRecord = {
                tool_name: toolEventData.tool_name,
                tool_args: toolEventData.tool_args,
                tool_result: null,
                status: 'running',
                duration: 0,
                timestamp: toolEventData.timestamp,
                step_index: stepIndex,
                start_event: toolStartEvent,
                complete_event: null,
                messageData: messageData
            };
            
            // 添加到step events（作为临时记录）
            stepEvents.push(runningToolCallRecord);
            
            // 立即更新panel显示
            this.updateStepPanel(stepIndex, runningToolCallRecord);
            
        } else if (eventType === 'tool_complete' || eventType === 'tool_error') {
            // 处理tool_complete或tool_error消息
            // 找到对应的tool_start消息（按顺序匹配）
            const matchingStartIndex = pendingStarts.findIndex(start => 
                start.tool_name === toolEventData.tool_name
            );
            
            let toolStartEvent = null;
            if (matchingStartIndex >= 0) {
                toolStartEvent = pendingStarts.splice(matchingStartIndex, 1)[0];
            }
            
            // 找到对应的"运行中"记录并更新它（依据同名工具且处于running状态）
            const runningRecordIndex = stepEvents.findIndex(record => 
                record.tool_name === toolEventData.tool_name && 
                record.status === 'running'
            );

            if (runningRecordIndex >= 0) {
                // 更新现有的运行中记录
                const runningRecord = stepEvents[runningRecordIndex];
                runningRecord.tool_result = toolEventData.processed_result || toolEventData.raw_result;
                runningRecord.status = eventType === 'tool_complete' ? 'completed' : 'failed';
                runningRecord.duration = toolEventData.duration || 0;
                runningRecord.complete_event = toolEventData;

                console.log(`更新运行中的工具记录: ${toolEventData.tool_name}, 状态: ${runningRecord.status}`);

                // 更新step panel显示
                this.updateStepPanel(stepIndex, runningRecord);
            } else {
                // 如果没找到对应的运行中记录，创建新的完整记录（兼容旧逻辑）
                console.log(`未找到运行中的记录，创建新记录: ${toolEventData.tool_name}`);
                const toolCallRecord = {
                    tool_name: toolEventData.tool_name,
                    tool_args: toolEventData.tool_args,
                    tool_result: toolEventData.processed_result || toolEventData.raw_result,
                    status: eventType === 'tool_complete' ? 'completed' : 'failed',
                    duration: toolEventData.duration || 0,
                    timestamp: toolEventData.timestamp,
                    step_index: stepIndex,
                    start_event: toolStartEvent,
                    complete_event: toolEventData,
                    messageData: messageData
                };

                // 添加到step events
                stepEvents.push(toolCallRecord);

                // 更新step panel显示
                this.updateStepPanel(stepIndex, toolCallRecord);
            }
        }

        // 持久化最新的step tool events
        this.persistStepToolEvents();
    }

    /**
     * 处理 mark_step 工具事件，更新节点的 step_notes
     */
    handleMarkStepEvent(toolEventData) {
        try {
            const stepIndex = toolEventData.step_index;
            const nodeId = stepIndex + 1; // stepIndex从0开始，DAG节点ID从1开始
            
            // 从tool_args中提取step_notes
            let stepNotes = '';
            if (toolEventData.tool_args) {
                try {
                    const args = JSON.parse(toolEventData.tool_args);
                    stepNotes = args.step_notes || '';
                } catch (e) {
                    console.warn('解析mark_step工具参数失败:', e);
                    return;
                }
            }
            
            console.log(`handleMarkStepEvent: stepIndex=${stepIndex}, nodeId=${nodeId}, step_notes=`, stepNotes);
            
            // 更新dagData中对应节点的step_notes
            if (typeof dagData !== 'undefined' && dagData.nodes) {
                const node = dagData.nodes.find(n => n.id === nodeId);
                if (node) {
                    node.step_notes = stepNotes;
                    console.log(`已更新节点 ${nodeId} 的 step_notes:`, stepNotes);
                } else {
                    console.warn(`未找到节点 ID ${nodeId}`);
                }
            } else {
                console.warn('dagData 未定义或没有 nodes 数组');
            }
        } catch (error) {
            console.error('处理mark_step事件时发生错误:', error);
        }
    }

    /**
     * 显示step panel
     */
    showStepPanel(stepIndex) {
        // stepIndex从0开始，DAG节点从1开始，需要转换
        const nodeId = stepIndex + 1;

        // 获取step信息
        const stepName = `Step ${nodeId}`;
        let stepTitle = stepName;

        // 尝试从DAG数据获取更详细的标题
        if (typeof dagData !== 'undefined' && dagData.nodes) {
            const node = dagData.nodes.find(n => n.id === nodeId);
            if (node) {
                const nodeText = node.fullName || node.title;
                if (nodeText) {
                    stepTitle = `${stepName} - ${nodeText}`;
                }
            }
        }

        // 创建并显示panel
        if (typeof createNodeToolPanel === 'function') {
            createNodeToolPanel(nodeId, stepTitle, false);
        }
    }

    /**
     * 更新step panel显示
     */
    updateStepPanel(stepIndex, toolCallRecord) {
        const nodeId = stepIndex + 1;
        console.log(`更新Step Panel: stepIndex=${stepIndex}, nodeId=${nodeId}`, toolCallRecord);

        // 转换为main.js期望的格式
        const toolCall = this.convertToToolCallFormat(toolCallRecord, nodeId);
        console.log('转换后的toolCall:', toolCall);

        // 更新panel显示
        if (typeof updateNodeToolPanel === 'function') {
            console.log('调用updateNodeToolPanel函数');
            updateNodeToolPanel(nodeId, toolCall);
        } else {
            console.error('updateNodeToolPanel函数不存在');
        }
    }

    /**
     * 转换tool call记录为main.js期望的格式
     */
    convertToToolCallFormat(toolCallRecord, nodeId) {
        // 生成并复用稳定的UI层ID，确保一次调用仅一个banner
        if (!toolCallRecord.ui_id) {
            toolCallRecord.ui_id = `tool_${toolCallRecord.tool_name}_${toolCallRecord.step_index}_${Date.now()}_${Math.random().toString(36).slice(2,8)}`;
        }
        const callId = toolCallRecord.ui_id;

        let url = null;
        let path = null;
        let descriptionOverride = null;

        // 处理搜索工具的结果，提取URL
        if (['search_baidu', 'search_google', 'tavily_search', 'image_search', 'search_wiki'].includes(toolCallRecord.tool_name)) {
            const processedResult = toolCallRecord.tool_result;
            if (processedResult && processedResult.first_url) {
                url = processedResult.first_url;
            }
        }

        // 处理网页抓取工具，提取URL
        if (['fetch_website_content', 'fetch_website_content_with_images', 'fetch_website_images_only'].includes(toolCallRecord.tool_name)) {
            const processedResult = toolCallRecord.tool_result;
            if (processedResult && processedResult.url) {
                url = processedResult.url;
            }
        }

        // 处理文件保存工具，提取路径
        if (toolCallRecord.tool_name === 'file_saver') {
            try {
                // 优先使用 processed_result.file_path（已包含完整API路径）
                const processed = toolCallRecord.tool_result;
                let filePath = null;
                
                if (processed && processed.file_path) {
                    // processed_result.file_path 已经包含完整的 API 路径前缀
                    filePath = processed.file_path;
                } else {
                    // 回退到从 tool_args 中提取
                    const args = JSON.parse(toolCallRecord.tool_args);
                    if (args.file_path) {
                        filePath = buildApiWorkspacePath(args.file_path);
                    }
                }
                
                if (filePath) {
                    path = filePath;
                    const filename = extractFileName(filePath);
                    if (filename) {
                        descriptionOverride = (window.I18nService ? `${window.I18nService.t('info_saved_to')}${filename}` : `信息保存到:${filename}`);
                    }
                }
            } catch (e) {
                console.warn('解析文件保存工具参数失败:', e);
            }
        }

        // 处理文件读取工具，提取路径
        if (toolCallRecord.tool_name === 'file_read') {
            try {
                // 优先 processed_result.file_path
                const processed = toolCallRecord.tool_result;
                let filePath = processed && processed.file_path ? processed.file_path : null;
                if (!filePath) {
                    // 回退从 tool_args 读取 { file: "..." }
                    const args = JSON.parse(toolCallRecord.tool_args || '{}');
                    filePath = args.file || args.path || null;
                }
                if (filePath) {
                    path = buildApiWorkspacePath(filePath);
                }
            } catch (e) {
                console.warn('解析文件读取工具参数失败:', e);
            }
        }

        // 处理代码执行工具，为代码内容设置特殊标识
        if (toolCallRecord.tool_name === 'execute_code') {
            try {
                const args = JSON.parse(toolCallRecord.tool_args || '{}');
                if (args.code) {
                    // 为execute_code工具设置一个特殊的path标识，表示有代码内容可查看
                    path = 'code://execute_code';
                }
            } catch (e) {
                console.warn('解析代码执行工具参数失败:', e);
            }
        }
        
        // 结果文本
        let resultText = '';
        if (toolCallRecord.status === 'running') {
            // 运行中状态的描述
            resultText = toolCallRecord.start_event?.status_text || (window.I18nService ? window.I18nService.t('running') : '正在执行中...');
        } else if (toolCallRecord.tool_result) {
            if (typeof toolCallRecord.tool_result === 'string') {
                resultText = toolCallRecord.tool_result;
            } else if (toolCallRecord.tool_result.summary) {
                resultText = toolCallRecord.tool_result.summary;
            } else {
                resultText = JSON.stringify(toolCallRecord.tool_result);
            }
        }

        // file_saver特殊处理
        if (toolCallRecord.tool_name === 'file_saver' && descriptionOverride) {
            resultText = descriptionOverride;
            descriptionOverride = '';
        }

        // 处理网页抓取类工具：从参数或结果中提取原始 website_url，用于在右侧 iframe 中直接打开
        if (['fetch_website_content', 'fetch_website_content_with_images', 'fetch_website_images_only'].includes(toolCallRecord.tool_name)) {
            try {
                // 优先从 tool_args 中解析 website_url
                if (!url && toolCallRecord.tool_args) {
                    try {
                        const args = JSON.parse(toolCallRecord.tool_args || '{}');
                        url = args.website_url || args.url || url;
                    } catch (e) {
                        console.warn('解析网页抓取工具参数失败:', e);
                    }
                }
                // 再从结构化结果中兜底提取 url（with_images / images_only 会返回 dict，包含 url 字段）
                if (!url && toolCallRecord.tool_result && typeof toolCallRecord.tool_result === 'object') {
                    url = toolCallRecord.tool_result.url || toolCallRecord.tool_result.website_url || url;
                }
            } catch (e) {
                console.warn('为网页抓取工具提取 URL 失败:', e);
            }
        }

        // 根据状态生成合适的描述
        let statusDescription = '';
        if (toolCallRecord.status === 'running') {
            statusDescription = (window.I18nService ? `${window.I18nService.t('executing')}${getToolDisplayName(toolCallRecord.tool_name)}` : `正在执行: ${getToolDisplayName(toolCallRecord.tool_name)}`);
        } else if (toolCallRecord.status === 'completed') {
            statusDescription = (window.I18nService ? `${window.I18nService.t('execution_completed')}${getToolDisplayName(toolCallRecord.tool_name)}` : `执行完成: ${getToolDisplayName(toolCallRecord.tool_name)}`);
        } else if (toolCallRecord.status === 'failed') {
            statusDescription = (window.I18nService ? `${window.I18nService.t('execution_failed')}${getToolDisplayName(toolCallRecord.tool_name)}` : `执行失败: ${getToolDisplayName(toolCallRecord.tool_name)}`);
        } else {
            statusDescription = (window.I18nService ? `${window.I18nService.t('execute_tool')}${getToolDisplayName(toolCallRecord.tool_name)}` : `执行工具: ${getToolDisplayName(toolCallRecord.tool_name)}`);
        }

        return {
            id: callId,
            nodeId: nodeId,
            duration: (toolCallRecord.duration || 0) * 1000, // 转换为毫秒
            tool: toolCallRecord.tool_name,
            toolName: getToolDisplayName(toolCallRecord.tool_name),
            description: descriptionOverride || statusDescription,
            status: toolCallRecord.status,
            startTime: Date.now() - (toolCallRecord.duration || 0) * 1000,
            endTime: toolCallRecord.status === 'running' ? null : Date.now(),
            result: resultText,
            error: toolCallRecord.status === 'failed' ? (window.I18nService ? window.I18nService.t('tool_execution_failed') : '工具执行失败') : null,
            url: url,
            path: path,
            timestamp: toolCallRecord.timestamp,
            // 为execute_code工具保留原始参数，以便显示代码内容
            tool_args: toolCallRecord.tool_args
        };
    }

    /**
     * 持久化step tool events到localStorage
     */
    persistStepToolEvents() {
        try {
            const eventsData = {};
            this.stepToolEvents.forEach((events, stepIndex) => {
                eventsData[stepIndex] = events;
            });

            localStorage.setItem('cosight:stepToolEvents', JSON.stringify({
                events: eventsData,
                savedAt: Date.now()
            }));
        } catch (e) {
            console.warn('持久化step tool events失败:', e);
        }
    }

    /**
     * 从localStorage恢复step tool events
     */
    restoreStepToolEvents() {
        try {
            const raw = localStorage.getItem('cosight:stepToolEvents');
            if (!raw) return;

            const stored = JSON.parse(raw);
            if (stored && stored.events) {
                Object.entries(stored.events).forEach(([stepIndex, events]) => {
                    this.stepToolEvents.set(parseInt(stepIndex), events);
                });
            }
        } catch (e) {
            console.warn('恢复step tool events失败:', e);
        }
    }

    /**
     * 获取指定step的tool events
     */
    getStepToolEvents(stepIndex) {
        return this.stepToolEvents.get(stepIndex) || [];
    }

    /**
     * 清理step tool events
     */
    clearStepToolEvents() {
        this.stepToolEvents.clear();
        this.pendingToolStarts.clear();
        try {
            localStorage.removeItem('cosight:stepToolEvents');
        } catch (e) {
            console.warn('清理step tool events失败:', e);
        }
    }

    /**
     * 自动关闭已完成步骤的面板（前提：该步骤无运行中工具调用）
     */
    _autoCloseCompletedStepPanels(initData) {
        try {
            const stepStatuses = initData?.step_statuses || {};
            const steps = initData?.steps || [];
            if (!steps.length) return;

            steps.forEach((stepName, index) => {
                const status = stepStatuses[stepName];
                // 仅处理标记为 completed 的步骤
                if (status === 'completed') {
                    const stepIndex = index; // steps 为0基
                    // 确认该step无运行中的工具调用
                    const hasRunning = this._hasRunningTools(stepIndex);
                    if (!hasRunning) {
                        const nodeId = stepIndex + 1; // DAG节点从1开始
                        try {
                            if (typeof closeNodeToolPanel === 'function') {
                                closeNodeToolPanel(nodeId);
                            }
                        } catch (_) {}
                    }
                }
            });
        } catch (e) {
            console.warn('_autoCloseCompletedStepPanels error:', e);
        }
    }

    /**
     * 判断指定step是否存在运行中的工具调用
     */
    _hasRunningTools(stepIndex) {
        try {
            const events = this.getStepToolEvents(stepIndex) || [];
            // 只要存在状态为running的记录或挂起的start事件，则认为仍在运行
            if (events.some(rec => rec?.status === 'running')) return true;
            const pending = this.pendingToolStarts?.get(stepIndex) || [];
            if (pending.length > 0) return true;
        } catch (_) {}
        return false;
    }

    sendMessage(content) {
        console.log('MessageService.sendMessage >>>>>>>>>>>>>> ', content);
        // 把用户发送的消息显示到聊天面板
        try {
            if (typeof window.appendChatMessage === 'function') {
                window.appendChatMessage(content, 'user');
            }
        } catch (e) { console.warn('显示用户消息失败:', e); }
        // 新消息发送前清理之前的tool events和历史数据
        this.clearStepToolEvents();

        // 清理历史的planId和pending请求
        try {
            localStorage.removeItem('cosight:planIdByTopic');
            localStorage.removeItem('cosight:pendingRequests');
            console.log('清理历史localStorage数据cosight:planIdByTopic, cosight:pendingRequests');
        } catch (e) {
            console.warn('清理历史localStorage数据失败:', e);
        }
        const topic = WebSocketService.generateUUID();
        WebSocketService.subscribe(topic, this.receiveMessage.bind(this));

        // 生成并复用稳定的 planId 作为 messageSerialNumber
        const planId = this.ensurePlanIdForTopic(topic);
        // 生成并复用稳定的学习会话ID（同一段对话内所有消息共用）
        const learningSessionId = this.ensureLearningSessionId();

        const message = {
            uuid: WebSocketService.generateUUID(),
            type: "multi-modal",
            from: "human",
            timestamp: Date.now(),
            initData: [{type: "text", value: content}],
            roleInfo: {name: "admin"},
            mentions: [],
            extra: {
                fromBackEnd: {
                    actualPrompt: JSON.stringify({deepResearchEnabled: true})
                }
            },
            // 会被服务端解析的会话信息
            sessionInfo: {
                messageSerialNumber: planId,
                learningSessionId: learningSessionId
            }
        }
        // 附带已上传文件（文件名列表），服务端据此复制到工作区/供解析使用
        try {
            const uploaded = this.getUploadedFilesInfo();
            if (uploaded && uploaded.length > 0) {
                const names = uploaded.map(f => f.filename || f.name || f.url).filter(Boolean);
                if (names.length > 0) {
                    message.extra.fromBackEnd.uploadedFiles = names;
                }
            }
        } catch (e) {
            console.warn('附带已上传文件失败:', e);
        }
        // 记录pending请求，便于刷新后重发
        try {
            const pendingRaw = localStorage.getItem('cosight:pendingRequests');
            const pendings = pendingRaw ? JSON.parse(pendingRaw) : {};
            pendings[topic] = { message, savedAt: Date.now(), stillPending: true };
            localStorage.setItem('cosight:pendingRequests', JSON.stringify(pendings));
            console.log('store pendingRequests',JSON.stringify(pendings));
        } catch (e) {
            console.warn('保存pending失败:', e);
        }
        if (WebSocketService.sendMessage(topic, JSON.stringify(message)) === false) {
            this.setChatStatus('连接暂时断开，这条消息尚未发送。请稍后重新发送；原消息保留在聊天中。', false);
        } else {
            this.setChatStatus('消息已发送，等待处理…');
        }
    }

    /**
     * 发送回放请求
     * @param {string} workspacePath - 工作区路径，如 'work_space/work_space_20251010_161223_071211'
     * @param {string} replayPlanId - 可选的planId
     */
    sendReplay(workspacePath, replayPlanId) {
        try {
            console.log('sendReplay 被调用，参数:', { workspacePath, replayPlanId });
            
            // 解析 workspace
            let replayWorkspace = null;
            
            // 1) 优先使用传入的参数
            if (workspacePath && typeof workspacePath === 'string' && workspacePath.trim().length > 0) {
                replayWorkspace = workspacePath.trim();
                console.log('使用传入的工作区路径:', replayWorkspace);
            }
            
            // 2) 回退逻辑
            try {
                if (!replayWorkspace) {
                    // 尝试从 localStorage 读取
                    const wsRaw = localStorage.getItem('cosight:workspace');
                    if (wsRaw && typeof wsRaw === 'string' && wsRaw.trim().length > 0) {
                        replayWorkspace = wsRaw.trim();
                        console.log('从 localStorage 获取工作区路径:', replayWorkspace);
                    }
                }
                // 2) 回退到从 lastManusStep 推断
                if (!replayWorkspace) {
                    const lastStepRaw = localStorage.getItem('cosight:lastManusStep');
                    if (lastStepRaw) {
                        const lastStep = JSON.parse(lastStepRaw);
                        const initData = lastStep?.message?.data?.initData;
                        const stepFiles = initData?.step_files || {};
                        // 取第一个有 path 的文件，解析其工作空间前缀
                        outer: for (const key of Object.keys(stepFiles)) {
                            const arr = stepFiles[key];
                            if (Array.isArray(arr) && arr.length > 0) {
                                for (const item of arr) {
                                    const p = item?.path;
                                    if (typeof p === 'string') {
                                        // 示例: work_space_20250926_194936_689374/xxx/yyy
                                        const idx = p.indexOf('/');
                                        replayWorkspace = idx > 0 ? p.slice(0, idx) : p;
                                        break outer;
                                    }
                                }
                            }
                        }
                    }
                }
            } catch (e) {
                console.warn('解析workspace失败:', e);
            }

            // 解析 planId
            // 1) 优先使用传入的参数
            if (!replayPlanId || typeof replayPlanId !== 'string' || replayPlanId.trim().length === 0) {
                // 2) 尝试从 localStorage 获取
                try {
                    const planRaw = localStorage.getItem('cosight:planIdByTopic');
                    if (planRaw) {
                        const map = JSON.parse(planRaw);
                        const entries = Object.entries(map);
                        if (entries.length > 0) {
                            // 取最后一个
                            replayPlanId = entries[entries.length - 1][1];
                            console.log('从 localStorage 获取 planId:', replayPlanId);
                        }
                    }
                } catch (e) {
                    console.warn('解析planId失败:', e);
                }
            } else {
                console.log('使用传入的 planId:', replayPlanId);
            }

            // planId 是可选的，不强制要求
            if (!replayPlanId) {
                console.log('未找到 planId，将生成新的');
                replayPlanId = WebSocketService.generateUUID();
            }
            
            if (!replayWorkspace) {
                alert('未找到可用的 workspace，无法回放');
                console.error('replayWorkspace 为空');
                return;
            }
            
            console.log('最终使用的回放参数:', { replayWorkspace, replayPlanId });

            // 生成新的 topic 用于订阅这次回放
            const topic = WebSocketService.generateUUID();
            WebSocketService.subscribe(topic, this.receiveMessage.bind(this));

            const message = {
                uuid: WebSocketService.generateUUID(),
                type: 'multi-modal',
                from: 'human',
                timestamp: Date.now(),
                initData: [{ type: 'text', value: '[Replay] 请求回放' }],
                roleInfo: { name: 'admin' },
                mentions: [],
                extra: {
                    // 两处都放置以兼容服务端提取逻辑
                    replay: true,
                    replayWorkspace: replayWorkspace,
                    replayPlanId: replayPlanId,
                    fromBackEnd: {
                        actualPrompt: JSON.stringify({ deepResearchEnabled: true }),
                        replay: true,
                        replayWorkspace: replayWorkspace,
                        replayPlanId: replayPlanId
                    }
                },
                sessionInfo: {
                    // 提示服务端不要生成新的 planId
                    messageSerialNumber: replayPlanId
                }
            };

            // 记录为 pending（便于刷新后恢复订阅）
            try {
                const pendingRaw = localStorage.getItem('cosight:pendingRequests');
                const pendings = pendingRaw ? JSON.parse(pendingRaw) : {};
                pendings[topic] = { message, savedAt: Date.now(), stillPending: true };
                localStorage.setItem('cosight:pendingRequests', JSON.stringify(pendings));
            } catch (e) {
                console.warn('保存回放pending失败:', e);
            }

            WebSocketService.sendMessage(topic, JSON.stringify(message));
        } catch (e) {
            console.error('发送回放请求失败:', e);
        }
    }
}

// 创建全局实例
window.messageService = new MessageService();

// 导出类（如果使用模块化）
if (typeof module !== 'undefined' && module.exports) {
    module.exports = MessageService;
}


// ===== 快通道聊天面板：统一渲染 + 展开/收起（阶段4 新增）=====
window.appendChatMessage = function (text, sender) {
    try {
        const body = document.getElementById('chat-messages');
        if (!body) return;
        const row = document.createElement('div');
        row.className = 'chat-msg ' + (sender === 'user' ? 'user' : 'ai');
        const bubble = document.createElement('div');
        bubble.className = 'chat-bubble';
        bubble.dataset.sourceText = String(text);
        if (sender === 'ai' && typeof marked !== 'undefined' && marked.parse) {
            // AI 回复用 markdown 渲染（**加粗**、列表、代码等正常显示）
            bubble.classList.add('markdown-body');
            bubble.innerHTML = window.safeMarkdown(text);
        } else {
            // 用户消息按纯文本处理（转义防注入，保留换行）
            const _safe = document.createElement('div');
            _safe.textContent = String(text);
            bubble.innerHTML = _safe.innerHTML.replace(/\n/g, '<br>');
        }
        row.appendChild(bubble);
        body.appendChild(row);
        body.scrollTop = body.scrollHeight;
    } catch (e) { console.warn('appendChatMessage 失败:', e); }
};

window.expandChatPanel = function () {
    const panel = document.getElementById('chat-panel');
    const reopenBtn = document.getElementById('chat-reopen-btn');
    if (panel) panel.classList.remove('collapsed');
    if (reopenBtn) reopenBtn.style.display = 'none';
};

window.toggleChatPanel = function () {
    const panel = document.getElementById('chat-panel');
    const reopenBtn = document.getElementById('chat-reopen-btn');
    if (!panel) return;
    const collapsed = panel.classList.toggle('collapsed');
    if (reopenBtn) reopenBtn.style.display = collapsed ? 'flex' : 'none';
};


// ===== 聊天面板：标题栏拖动（阶段4 B）=====
window.initChatPanelDrag = function () {
    const panel = document.getElementById('chat-panel');
    if (!panel) return;
    if (panel.dataset.layout === 'docked') return;
    const header = panel.querySelector('.chat-panel-header');
    if (!header || header.dataset.dragBound === '1') return;
    header.dataset.dragBound = '1';
    header.style.cursor = 'move';
    let dragging = false, sx = 0, sy = 0, startLeft = 0, startTop = 0;
    header.addEventListener('mousedown', function (e) {
        // 点在收起按钮上时不触发拖动
        if (e.target.closest('.chat-collapse-btn')) return;
        dragging = true;
        startLeft = panel.offsetLeft;
        startTop = panel.offsetTop;
        sx = e.clientX; sy = e.clientY;
        // 拖动前固定尺寸，避免 calc 宽高与 left/top 冲突
        const r = panel.getBoundingClientRect();
        panel.style.width = r.width + 'px';
        panel.style.height = r.height + 'px';
        document.body.style.userSelect = 'none';
        e.preventDefault();
    });
    document.addEventListener('mousemove', function (e) {
        if (!dragging) return;
        panel.style.left = (startLeft + e.clientX - sx) + 'px';
        panel.style.top = (startTop + e.clientY - sy) + 'px';
    });
    document.addEventListener('mouseup', function () {
        if (dragging) { dragging = false; document.body.style.userSelect = ''; }
    });
};

// 页面就绪后初始化聊天面板拖动
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { window.initChatPanelDrag(); });
} else {
    window.initChatPanelDrag();
}
