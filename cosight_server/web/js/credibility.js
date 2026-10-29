// 搜索结果自洽性检测
function checkSearchResultsConsistency(tool, result) {
    // 模拟检测10条搜索结果的自洽性
    let isConsistent = false; // Unknown is not verified; never fabricate a confidence result.

    // 基于结果内容进行更精确的判断
    if (result.includes('官方') || result.includes('权威') || result.includes('正式') ||
        result.includes('百度百科') || result.includes('政府') || result.includes('体育局')) {
        isConsistent = false; // Source labels alone do not establish independent verification.
    }
    if (result.includes('矛盾') || result.includes('不一致') || result.includes('冲突') ||
        result.includes('争议') || result.includes('不确定') || result.includes('待确认')) {
        isConsistent = false; // 明确提到矛盾的信息
    }

    // 基于查询内容调整自洽性
    if (result.includes('2025年') && result.includes('江苏') && result.includes('足球联赛')) {
        isConsistent = false;
    }

    // 为了测试交叉验证，让某些搜索不自洽
    if (tool === 'search_google' && result.includes('积分榜')) {
        isConsistent = false; // Google搜索积分榜时不自洽，触发交叉验证
    }

    console.log(`自洽性检测: 工具=${tool}, 结果包含=${result.substring(0, 50)}..., 自洽=${isConsistent}`);

    return isConsistent;
}

// 获取工具对应的验证步骤（考虑自洽性）
function getVerificationStepsForTool(tool, result) {
    const mapping = toolVerificationMapping[tool];

    if (!mapping) {
        console.log(`未找到工具 ${tool} 的映射关系`);
        return [];
    }

    // 如果是搜索工具，需要检查自洽性
    if (tool === 'search_baidu' || tool === 'search_google') {
        const isConsistent = checkSearchResultsConsistency(tool, result);
        console.log(`工具 ${tool} 自洽性检测结果: ${isConsistent}`);
        const steps = isConsistent ? mapping.consistent : mapping.inconsistent;
        console.log(`返回的验证步骤: ${steps.join(', ')}`);
        return steps;
    }

    // 其他工具直接返回对应的验证步骤
    const steps = Array.isArray(mapping) ? mapping : [];
    console.log(`工具 ${tool} 直接返回验证步骤: ${steps.join(', ')}`);
    return steps;
}

// 导出可信验证相关函数（如果使用模块化）
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        // 验证步骤
        checkSearchResultsConsistency,
        getVerificationStepsForTool
    };
}

class CredibilityService {
    constructor() {
        this.allCredibilityData = {};
        this.restoreCredibilityData();
    }

    credibilityMessageHandler(messageData) {
        const credibilityData = messageData.data.initData;
        const stepIndex = credibilityData.stepIndex;

        this.persistStepCredibilityData(stepIndex, credibilityData);
        this.updateNodeCredibilityIndicator(stepIndex);
    }

    // 添加节点运行状态指示器 - 在节点开始运行时显示灰色圆圈
    addNodeIndicators(nodeId) {
        console.log("add node credibility indicator >>>>>>>>>>>>>>>>>>>>> nodeId: ", nodeId);
        const nodeElement = svg.selectAll(".node").filter(d => d.id === nodeId);

        // 移除现有的可信分级指示器
        nodeElement.selectAll(".node-indicator, .credibility-circle-group").remove();

        // 添加灰色运行状态指示器（创建时不添加点击事件）
        addNodeIndicator(nodeElement, nodeId, "credibility-circle-group", "translate(20, -20)", "#9E9E9E", "T");

        // 文件列表指示器
        // addNodeIndicator(nodeElement, nodeId, "file-circle-group", "translate(-20, -20)", "#4CAF50", "F");

        // 如果当前节点已经存在可信分级数据，则更新
        const credibilityData = this.getStepCredibilityData(nodeId - 1);
        if (credibilityData) {
            this.updateNodeCredibilityIndicator(nodeId - 1, credibilityData);
        }
    }

    // 更新可信分级状态指示器 - 在节点完成时检查可信分级信息
    updateNodeCredibilityIndicator(stepIndex, credibilityData) {
        const nodeId = stepIndex + 1;
        console.log("update node credibility indicator >>>>>>>>>>>>>>>>>>>>> nodeId: ", nodeId);
        const nodeElement = svg.selectAll(".node").filter(d => d.id === nodeId);
        const runningIndicator = nodeElement.selectAll(".node-indicator, .credibility-circle-group");
        if (runningIndicator.empty()) {
            console.log("update node credibility indicator >>>>>>>>>>>>>>>>>>>>> 未找到可信分级指示器，跳过更新");
            return;
        }

        // 检查是否存在可信分级信息
        credibilityData = credibilityData ? credibilityData : this.getStepCredibilityData(stepIndex);
        console.log("update node credibility indicator >>>>>>>>>>>>>>>>>>>>> credibilityData: ", credibilityData);
        if (credibilityData) {
            // 如果存在可信分级信息，将圆圈按可信度评分着色并添加点击事件
            const circle = runningIndicator.select(".action-circle");

            // 先添加点击事件
            circle.on("click", event => {
                event.stopPropagation();
                // 在右侧内容面板中显示可信分级信息
                this.showCredibilityInfoInRightPanel(nodeId, credibilityData);
            });

            // 计算该节点的可信度评分（1~5级加权），评分越高越绿、越低越红
            const score = this.computeCredibilityScore(credibilityData);
            const scoreColor = this.scoreToColor(score);

            // 然后进行样式更新和过渡动画
            circle
                .style("fill", scoreColor)
                .style("cursor", "pointer")
                .transition()
                .duration(500);

            // 圆圈title悬停提示：显示评分，便于理解颜色含义
            circle.select("title").remove();
            circle.append("title").text(`可信度评分: ${score != null ? (score * 100).toFixed(0) + "分" : "无"}（绿=可信，红=存疑）`);

            // 更新文字为 "T" 表示 Credibility
            runningIndicator.select("text")
                .html(`T`);
            console.log(`update node credibility indicator >>>>>>>>>>>>>>>>>>>>> 节点 ${nodeId} 存在可信分级信息，评分 ${score}，指示器已着色 ${scoreColor}`);
        } else {
            // 如果没有可信分级信息，保持灰色，不添加点击事件
            const circle = runningIndicator.select(".action-circle");
            // 移除点击事件
            circle.on("click", null);
            // 更新样式
            circle
                .style("fill", "#9E9E9E")
                .style("cursor", "not-allowed");
            // 更新文字为 "D" 表示 Done
            runningIndicator.select("text")
                .html(`D`);
            console.log(`update node credibility indicator >>>>>>>>>>>>>>>>>>>>> 节点 ${nodeId} 无可信分级信息，指示器保持灰色`);
        }
    }

    // 在右侧内容面板中显示可信分级信息
    showCredibilityInfoInRightPanel(nodeId, credibilityData) {
        console.log(`在右侧内容面板中显示节点 ${nodeId} 的可信分级信息`);
        const result = showRightPanel();
        if (!result) {
            return;
        }

        const iframe = document.getElementById('content-iframe');
        iframe.style.display = 'none';
        const markdownContent = document.getElementById('markdown-content');
        markdownContent.style.display = 'block';

        // 获取右侧内容面板
        const rightContent = document.getElementById('markdown-content');
        const rightStatus = document.getElementById('right-container-status');
        // 更新右侧面板状态
        if (rightStatus) {
            rightStatus.textContent = (window.I18nService ? window.I18nService.t('viewing_credibility_info').replace('{nodeId}', nodeId) : `正在查看节点 ${nodeId} 的可信分级信息...`);
        }

        // 检查是否存在可信分级信息
        if (!credibilityData) {
            rightContent.innerHTML = `
                <div style="text-align: center; color: #999; padding: 40px 20px;">
                    <i class="fas fa-info-circle" style="font-size: 48px; margin-bottom: 16px; color: #ccc;"></i>
                    <h3 style="color: #666; margin-bottom: 8px;">${(window.I18nService ? window.I18nService.t('no_credibility_info') : '暂无可信分级信息')}</h3>
                    <p style="color: #999;">${(window.I18nService ? window.I18nService.t('no_credibility_info_desc').replace('{nodeId}', nodeId) : `节点 ${nodeId} 目前没有可信分级数据`)}</p>
                </div>
            `;
            return;
        }

        // 获取可信分级数据
        const currentStepLevels = credibilityData.content;
        if (!currentStepLevels || currentStepLevels.length === 0) {
            rightContent.innerHTML = `
                <div style="text-align: center; color: #999; padding: 40px 20px;">
                    <i class="fas fa-exclamation-triangle" style="font-size: 48px; margin-bottom: 16px; color: #ff9800;"></i>
                    <h3 style="color: #666; margin-bottom: 8px;">${(window.I18nService ? window.I18nService.t('data_anomaly') : '数据异常')}</h3>
                    <p style="color: #999;">${(window.I18nService ? window.I18nService.t('data_anomaly_desc').replace('{nodeId}', nodeId) : `节点 ${nodeId} 的可信分级数据格式异常`)}</p>
                </div>
            `;
            return;
        }

        // 构建可信分级信息HTML
        let credibilityHTML = `
            <div class="credibility-section">
                <h4>
                    <i class="fas fa-shield-alt"></i> ${(window.I18nService ? window.I18nService.t('credibility_info_title').replace('{nodeId}', nodeId) : `步骤 ${nodeId} 的可信分级信息`)}
                </h4>
                <div class="credibility-levels">
        `;

        // 遍历可信分级数据
        currentStepLevels.forEach((levelData, index) => {
            const level = index + 1;
            credibilityHTML += `
            <div class="credibility-level level-${level}" style="border-left: 4px solid ${this.getLevelColor(level)};">
                <div class="level-badge level-${level}" style="background: ${this.getLevelColor(level)};">
                    ${level}
                </div>
                <div class="level-content">
                    <div class="level-content-header">
                        <div class="level-title">
                            ${levelData.title}
                        </div>
                    </div>
                    <div class="level-content-description">
                        <ul>
                            ${levelData.items.map(item => `<li>${item}</li>`).join('')}
                        </ul>
                    </div>
                </div>
            </div>
        `;
        });

        credibilityHTML += `
                </div>
            </div>
        `;

        // 设置内容并显示右侧面板
        rightContent.innerHTML = credibilityHTML;

        console.log(`节点 ${nodeId} 的可信分级信息已显示在右侧内容面板中`);
    }

    /**
     * 计算节点的可信度评分（0~1）。
     * content 为 5 个可信等级的条目数组（下标0~4 对应 等级1常识 ~ 等级5猜测），
     * 按各等级条目数占比加权求和：等级越高（越不可信）权重越低。
     * 例：全部条目都在"已验证"级 → 接近1；全部在"猜测"级 → 接近0.1。
     */
    computeCredibilityScore(credibilityData) {
        try {
            const weights = [1.0, 0.8, 0.5, 0.3, 0.1]; // 等级1~5 的可信权重
            let total = 0, weighted = 0;
            (credibilityData.content || []).forEach((lv, i) => {
                const n = (lv && lv.items ? lv.items.length : 0);
                const w = weights[i] != null ? weights[i] : 0.1;
                total += n;
                weighted += n * w;
            });
            if (!total) return null;
            return weighted / total;
        } catch (e) {
            console.warn('计算可信度评分失败:', e);
            return null;
        }
    }

    /**
     * 评分 → 颜色：
     * ≥0.65 绿（可信为主） / ≥0.45 蓝（一般） / ≥0.30 橙（存疑偏多） / 其余 红（猜测为主）
     * 评分为空（无条目）时给中性灰绿。
     */
    scoreToColor(score) {
        if (score == null) return '#8BC34A';
        if (score >= 0.65) return '#4CAF50';
        if (score >= 0.45) return '#2196F3';
        if (score >= 0.30) return '#FF9800';
        return '#F44336';
    }

    // 获取可信分级颜色
    getLevelColor(level) {
        const colors = {
            1: '#4caf50',  // 绿色
            2: '#2196f3',  // 蓝色
            3: '#ff9800',  // 橙色
            4: '#9c27b0',  // 紫色
            5: '#f44336'   // 红色
        };
        return colors[level] || '#ddd';
    }

    persistStepCredibilityData(stepIndex, credibilityData) {
        let allCredibilityData = {};
        const raw = localStorage.getItem('cosight:credibilityData');
        try {
            allCredibilityData = raw ? JSON.parse(raw) : {};
        } catch (e) {
            allCredibilityData = {};
        }
        allCredibilityData[stepIndex] = credibilityData;

        localStorage.setItem('cosight:credibilityData', JSON.stringify(allCredibilityData));
        this.allCredibilityData = allCredibilityData;
    }

    getStepCredibilityData(stepIndex) {
        return this.allCredibilityData[stepIndex];
    }

    restoreCredibilityData() {
        try {
            const raw = localStorage.getItem('cosight:credibilityData');
            if (!raw) return;

            this.allCredibilityData = JSON.parse(raw);
        } catch (e) {
            console.warn('恢复可信分级信息失败:', e);
            this.allCredibilityData = {};
        }
    }

    clearCredibilityData() {
        localStorage.removeItem('cosight:credibilityData');
        this.allCredibilityData = {};
    }
}

// 创建全局实例
window.credibilityService = new CredibilityService();

// 导出类（如果使用模块化）
if (typeof module !== 'undefined' && module.exports) {
    module.exports = CredibilityService;
}
