function cleanupWebSocket() {
    // 取消所有订阅
    WebSocketService.unsubscribe();
    // 销毁连接
    WebSocketService.destroy();
}

/**
 * 开始新会话：更换学习会话ID + 清空聊天与本地状态。
 * 后端快通道的所有流程状态（重做清单/待选择题目/待确认操作）都按 session_id 存储，
 * 换一个 learningSessionId 即等于全新会话，无需重启后端。
 */
function startNewSession() {
    try {
        WebSocketService.destroy();
        if (typeof resetUIState === 'function') resetUIState();
        // 1. 移除旧会话ID，下一条消息发出时会自动生成新的
        localStorage.removeItem('cosight:learningSessionId');
        // 2. 清空本地缓存（聊天内容、可信度数据、步骤快照、待重发请求）
        localStorage.removeItem('cosight:lastManusStep');
        localStorage.removeItem('cosight:pendingRequests');
        localStorage.removeItem('cosight:planIdByTopic');
        localStorage.removeItem('cosight:stepToolEvents');
        localStorage.removeItem('cosight:workspace');
        localStorage.removeItem('cosight:uploadedFiles');
        if (window.credibilityService && typeof window.credibilityService.clearCredibilityData === 'function') {
            window.credibilityService.clearCredibilityData();
        }
        // 3. 清空聊天面板
        const chat = document.getElementById('chat-messages');
        if (chat) chat.innerHTML = '';
        // 4. 给用户一个明确反馈
        if (typeof window.appendChatMessage === 'function') {
            window.appendChatMessage('已开始新会话。历史对话仍可从“聊天历史”查看；先前提交的请求可能仍在后台完成，但不会混入本次聊天。', 'ai');
        }
        WebSocketService.initWebSocket();
        console.log('startNewSession >>> 已重置学习会话');
    } catch (e) {
        console.warn('开始新会话失败:', e);
    }
}

/**
 * 可信度总览：在右侧面板汇总显示所有步骤的可信分级数据，
 * 点击任一条目进入该节点的可信度详情（与 DAG 节点上绿色 T 圆圈的点击效果一致）。
 */
function showCredibilityOverview() {
    try {
        if (typeof showRightPanel !== 'function') return;
        const result = showRightPanel();
        if (!result) return;
        const iframe = document.getElementById('content-iframe');
        if (iframe) iframe.style.display = 'none';
        const markdownContent = document.getElementById('markdown-content');
        if (!markdownContent) return;
        markdownContent.style.display = 'block';

        const svc = window.credibilityService;
        const data = (svc && svc.allCredibilityData) || {};
        const keys = Object.keys(data).map(Number).filter(k => !isNaN(k)).sort((a, b) => a - b);

        const rightStatus = document.getElementById('right-container-status');
        if (rightStatus) rightStatus.textContent = '可信度分析总览';

        if (keys.length === 0) {
            markdownContent.innerHTML = `
                <div style="text-align: center; color: #999; padding: 40px 20px;">
                    <i class="fas fa-shield-halved" style="font-size: 48px; margin-bottom: 16px; color: #ccc;"></i>
                    <h3 style="color: #666; margin-bottom: 8px;">暂无可信度数据</h3>
                    <p style="color: #999;">可信度分析在引擎执行过程中异步生成，运行一次任务后再来看这里。</p>
                </div>
            `;
            return;
        }

        let html = `
            <div class="credibility-section">
                <h4><i class="fas fa-shield-alt"></i> 可信度分析总览（${keys.length} 个步骤）</h4>
                <div class="credibility-levels">
        `;
        keys.forEach(k => {
            const cd = data[k];
            const title = escapeHtml((cd && cd.title) || `步骤 ${k + 1}`);
            const levels = (cd && cd.content) ? cd.content.length : 0;
            html += `
                <div class="cred-overview-item" data-step="${k}"
                     style="border: 1px solid #e0e0e0; border-radius: 8px; padding: 10px 14px; margin-bottom: 8px; cursor: pointer; transition: background 0.2s;"
                     onmouseover="this.style.background='#f5f7fa'" onmouseout="this.style.background=''">
                    <div style="font-weight: 600; color: #333;">
                        <i class="fas fa-shield-alt" style="color: #4CAF50; margin-right: 6px;"></i>步骤 ${k + 1}：${title}
                    </div>
                    <div style="font-size: 12px; color: #888; margin-top: 4px;">
                        ${levels > 0 ? `已生成 ${levels} 级可信分类 · 点击查看详情` : '暂无分级数据'}
                    </div>
                </div>
            `;
        });
        html += `</div></div>`;
        markdownContent.innerHTML = html;

        // 点击条目 → 展示该节点可信度详情（复用现有详情渲染）
        if (svc && typeof svc.showCredibilityInfoInRightPanel === 'function') {
            markdownContent.querySelectorAll('.cred-overview-item').forEach(el => {
                el.addEventListener('click', () => {
                    const k = Number(el.getAttribute('data-step'));
                    svc.showCredibilityInfoInRightPanel(k + 1, data[k]);
                });
            });
        }
    } catch (e) {
        console.warn('展示可信度总览失败:', e);
    }
}

function setupHeaderActions() {
    const newSessionBtn = document.getElementById('new-session-btn');
    if (newSessionBtn) newSessionBtn.addEventListener('click', startNewSession);
    const credBtn = document.getElementById('credibility-overview-btn');
    if (credBtn) credBtn.addEventListener('click', showCredibilityOverview);
}

function setupMessageHandling() {    // 检查是否有pending的请求需要重发
    try {
        const pendingRaw = localStorage.getItem('cosight:pendingRequests');
        if (pendingRaw) {
            const pendings = JSON.parse(pendingRaw);
            Object.entries(pendings).forEach(([topic, data]) => {
                if (data && data.message) {
                    console.log('恢复pending订阅:', topic);
                    // 重新订阅
                    WebSocketService.subscribe(topic, messageService.receiveMessage.bind(messageService));
                    // 仅当明确 stillPending===true 时才重发，避免刷新重复执行
                    if (data.stillPending === true) {
                        console.log('重发pending请求:', topic);
                        WebSocketService.sendMessage(topic, JSON.stringify(data.message));
                    }
                }
            });
        }
    } catch (e) {
        console.warn('处理pending请求失败:', e);
    }
}

document.addEventListener("DOMContentLoaded", async function () {
    const account = await window.authReady;
    if (!account) return;
    document.getElementById('account-name').textContent = account.username;
    document.getElementById('account-name').title = account.username;
    window.refreshModelStatus = async () => {
        const label = document.getElementById('cloud-model-status');
        const button = document.getElementById('header-model-settings');
        try {
            const response = await fetch('/api/nae-deep-research/v1/deep-research/settings');
            if (!response.ok) throw new Error('设置读取失败');
            const payload = await response.json();
            const enabled = payload.settings.allow_cloud_model === true;
            label.textContent = enabled ? '在线答疑：已开启' : '在线答疑：未开启';
            button.dataset.enabled = String(enabled);
            button.title = enabled ? '当前账号已允许将必要内容发送给在线模型；点击修改授权' : '当前账号尚未授权在线模型；点击学习设置后可自行开启';
        } catch (_) {
            label.textContent = '在线答疑设置';
            button.title = '暂时无法读取状态，点击重试';
        }
    };
    window.refreshModelStatus();
    document.getElementById('account-logout').onclick = () => window.logoutAccount().catch(error => alert(error.message));
    // 初始化websocket连接
    WebSocketService.initWebSocket();
    
    // 等待WebSocket连接建立后设置消息处理
    WebSocketService.websocketConnected.addEventListener('connected', function() {
        console.log('WebSocket连接已建立，设置消息处理...');
        setupMessageHandling();
    });
    
    // 初始化输入框
    initInputHandler();
    // 初始化头部按钮（新会话 / 可信度总览）
    setupHeaderActions();
    if (typeof initStudyQuickChannel === 'function') {
        initStudyQuickChannel();
    }
    
    // 检查是否有保存的DAG数据需要恢复
    checkAndRestoreDAGData();

    // 监听窗口大小变化
    window.addEventListener("resize", handleResize);

    // 添加全局点击事件，关闭所有验证步骤提示框
    document.addEventListener('click', function (event) {
        // 检查点击的是否是验证步骤图标或全局提示框
        const isVerificationIcon = event.target.closest('.verification-icon');
        const isVerificationTooltip = event.target.id === 'verification-tooltip';

        // 如果点击的不是验证步骤相关元素，关闭所有提示框
        if (!isVerificationIcon && !isVerificationTooltip) {
            closeAllVerificationTooltips();
        }
    });

    // 添加ESC键监听器，关闭所有验证步骤提示框
    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') {
            closeAllVerificationTooltips();
        }
    });

    // 为全局验证步骤提示框添加鼠标事件
    const verificationTooltip = document.getElementById('verification-tooltip');
    if (verificationTooltip) {
        verificationTooltip.addEventListener('mouseenter', function () {
            // 清除隐藏定时器，保持提示框显示
            if (verificationTooltipTimeout) {
                clearTimeout(verificationTooltipTimeout);
                verificationTooltipTimeout = null;
            }
        });

        verificationTooltip.addEventListener('mouseleave', function () {
            // 鼠标离开提示框时隐藏
            hideVerificationTooltip();
        });
    }

    // 为tooltip添加鼠标事件，允许用户移动到tooltip上点击链接
    const tooltipElement = document.getElementById('tooltip');
    if (tooltipElement) {
        tooltipElement.addEventListener('mouseenter', function () {
            // 清除隐藏定时器，保持tooltip显示
            if (typeof tooltipTimeout !== 'undefined' && tooltipTimeout) {
                clearTimeout(tooltipTimeout);
                tooltipTimeout = null;
            }
        });

        tooltipElement.addEventListener('mouseleave', function () {
            // 鼠标离开tooltip时隐藏
            if (typeof hideTooltip === 'function') {
                hideTooltip();
            }
        });
    }

    // 为动态标题添加hover事件
    const dynamicTitle = document.getElementById('dynamic-title');
    if (dynamicTitle) {
        dynamicTitle.addEventListener('mouseenter', showStepsTooltip);
        dynamicTitle.addEventListener('mouseleave', hideStepsTooltip);
        dynamicTitle.addEventListener('mousemove', showStepsTooltip);
    }

    // 页面卸载时清理
    window.addEventListener('beforeunload', cleanupWebSocket);
});
