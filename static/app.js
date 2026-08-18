// app.js —— 前端逻辑（页面行为全在这里）
//
// 前端的工作模式：页面只是骨架（index.html），数据全靠 JS 调用后端的 API 拿回来，
// 再渲染成界面。用户操作（新建/编辑/删除/筛选）也是 JS 调 API 完成的。
// 这种模式叫"前后端分离"，好处：界面逻辑和数据逻辑互不干扰。

/* ==================== 一、调用后端 API 的小助手 ==================== */

async function api(path, options = {}) {
    // 所有请求都走这一个函数，好处：
    //   1) 统一加 JSON 头；2) 统一处理错误（后端返回的错误信息用中文弹出来）。
    try {
        const resp = await fetch(path, {
            headers: { "Content-Type": "application/json" },
            ...options,
        });
        if (!resp.ok) {
            // 后端出错时返回的 JSON 形如 {detail: "姓名不能为空"}，取出来当提示。
            // detail 通常是字符串，但某些校验错误（旧版后端）是数组，做一层兼容。
            let msg = `请求失败（HTTP ${resp.status}）`;
            try {
                const body = await resp.json();
                if (Array.isArray(body.detail)) {
                    msg = body.detail[0]?.msg || msg;
                } else if (body.detail) {
                    msg = body.detail;
                }
            } catch { /* 后端没返回 JSON 就用上面的默认提示 */ }
            throw new Error(msg);
        }
        return await resp.json();
    } catch (err) {
        // 网络断了（fetch 本身抛错）也会落到这里
        throw new Error(err.message || "网络错误，请检查连接");
    }
}

/* ==================== 二、防 XSS 攻击的文本转义 ==================== */

function escapeHtml(text) {
    // 客户输入的内容（姓名、备注…）原样插进页面时，可能夹带 <script> 等
    // HTML 代码（XSS 攻击）。所以任何用户输入显示前都先转义：
    // < 变成 &lt;，浏览器就会把它当普通文字而不是代码执行。
    const div = document.createElement("div");
    div.textContent = text == null ? "" : String(text);
    return div.innerHTML;
}

/* ==================== 三、渲染函数（把数据变成界面） ==================== */

// 状态 -> 徽章样式类名 的对照表
const BADGE_CLASS = {
    "待跟进": "badge-pending",
    "进行中": "badge-ongoing",
    "已完成": "badge-done",
};

function statusBadge(status) {
    return `<span class="badge ${BADGE_CLASS[status] || ""}">${escapeHtml(status)}</span>`;
}

function todayStr() {
    // 本地时区的今天，格式 YYYY-MM-DD（用于判断跟进日期是否过期）
    const d = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function clientCardHTML(client, { showQuickDone = false } = {}) {
    // 把一个客户对象转成一张卡片 HTML。首页和列表页共用。
    const followup = client.next_followup || "";
    const overdue = followup && followup < todayStr(); // 跟进日期已过 = 过期标红
    const meta = [
        client.source ? `<span>来源：${escapeHtml(client.source)}</span>` : "",
        client.quote != null ? `<span>报价：¥${escapeHtml(client.quote)}</span>` : "",
        followup ? `<span>跟进：${overdue ? `<span class="meta-overdue">${escapeHtml(followup)}</span>` : escapeHtml(followup)}</span>` : "",
    ].join("");

    return `
    <div class="card" data-id="${client.id}">
        <div class="card-top">
            <span class="card-name">${escapeHtml(client.name)}</span>
            ${statusBadge(client.status)}
        </div>
        <div class="card-meta">${meta}</div>
        ${client.project_desc ? `<div class="card-desc">${escapeHtml(client.project_desc)}</div>` : ""}
        ${client.notes ? `<div class="card-desc">备注：${escapeHtml(client.notes)}</div>` : ""}
        <div class="card-actions">
            ${showQuickDone && client.status !== "已完成"
                ? `<button class="btn btn-primary btn-done" data-id="${client.id}">标记完成</button>` : ""}
            <button class="btn btn-edit" data-id="${client.id}">编辑</button>
            <button class="btn btn-danger btn-del" data-id="${client.id}">删除</button>
        </div>
    </div>`;
}

async function renderStats() {
    // 首页：三个状态数字 + 今日要跟进列表
    const data = await api("/api/stats");

    document.getElementById("stat-pending").textContent = data.counts["待跟进"];
    document.getElementById("stat-ongoing").textContent = data.counts["进行中"];
    document.getElementById("stat-done").textContent = data.counts["已完成"];

    const list = document.getElementById("followup-list");
    if (data.today_followups.length === 0) {
        document.getElementById("followup-hint").textContent = "今天没有要跟进的客户 🎉";
        list.innerHTML = "";
        return;
    }
    document.getElementById("followup-hint").textContent =
        `有 ${data.today_followups.length} 个客户需要联系（跟进日期 ≤ 今天且未完成）`;
    // showQuickDone=true：这里多一个"标记完成"快捷按钮
    list.innerHTML = data.today_followups
        .map((c) => clientCardHTML(c, { showQuickDone: true }))
        .join("");
}

async function renderClients() {
    // 列表页：按当前筛选条件拉数据并渲染
    const status = document.getElementById("filter-status").value;
    const source = document.getElementById("filter-source").value;
    const q = document.getElementById("filter-q").value.trim();

    // 把筛选条件拼到查询字符串上（空的条件自动被后端忽略）
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    if (source) params.set("source", source);
    if (q) params.set("q", q);
    const query = params.toString() ? `?${params}` : "";

    const clients = await api(`/api/clients${query}`);

    document.getElementById("list-hint").textContent =
        `共 ${clients.length} 个客户${q ? `（关键词「${q}」）` : ""}`;

    const list = document.getElementById("client-list");
    list.innerHTML = clients.length
        ? clients.map((c) => clientCardHTML(c)).join("")
        : `<p class="hint">没有符合条件的客户。点右下角 ＋ 新建一个吧。</p>`;
}

// 所有客户的全量缓存（不带筛选），用于构建"来源"下拉选项。
// 为什么单独存一份？如果从"筛选后的结果"里提取来源，选项会随筛选
// 结果变化而缩水，用户选中的来源还可能被静默清空，体验很怪。
let allClients = [];

async function loadAllClientsForSources() {
    // 拉一次全量列表（不要筛选参数），只用于来源下拉框的选项
    allClients = await api("/api/clients");
    refreshSourceOptions();
}

function refreshSourceOptions() {
    // 来源下拉框 + 表单输入联想：选项来自全量客户中出现过的来源（去重排序）。
    // 安全要点：不用字符串拼接 HTML，而是 createElement + 直接赋值
    // .value / .textContent——这样无论来源里夹带什么字符（引号、<script>），
    // 都只是普通文字，绝无可能变成 HTML/JS 执行（防存储型 XSS）。
    const sources = [...new Set(allClients.map((c) => c.source).filter(Boolean))].sort();

    const select = document.getElementById("filter-source");
    const current = select.value; // 记住用户当前选的值，重建后别丢
    select.innerHTML = "";        // 清空（此处没有拼接任何用户数据，安全）

    const allOpt = document.createElement("option");
    allOpt.value = "";
    allOpt.textContent = "全部来源";
    select.appendChild(allOpt);
    for (const s of sources) {
        const opt = document.createElement("option");
        opt.value = s;          // 直接赋值属性，浏览器负责安全处理
        opt.textContent = s;
        select.appendChild(opt);
    }
    select.value = current;     // 若旧选中值已不存在，浏览器自动回落到第一项

    const datalist = document.getElementById("source-options");
    datalist.innerHTML = "";
    for (const s of sources) {
        const opt = document.createElement("option");
        opt.value = s;
        datalist.appendChild(opt);
    }
}

/* ==================== 四、弹窗表单（新建 / 编辑共用） ==================== */

function openModal(client = null) {
    // client 为 null = 新建（空表单）；传了客户对象 = 编辑（预填数据）
    const form = document.getElementById("client-form");
    form.reset();
    document.getElementById("form-id").value = client ? client.id : "";
    document.getElementById("modal-title").textContent = client ? "编辑客户" : "新建客户";

    if (client) {
        document.getElementById("form-name").value = client.name;
        document.getElementById("form-source").value = client.source || "";
        document.getElementById("form-desc").value = client.project_desc || "";
        document.getElementById("form-quote").value = client.quote ?? "";
        document.getElementById("form-status").value = client.status;
        document.getElementById("form-followup").value = client.next_followup || "";
        document.getElementById("form-notes").value = client.notes || "";
    } else {
        // 新建时"下次跟进日期"默认今天（最常见的场景：今天建客户今天联系）
        document.getElementById("form-followup").value = todayStr();
    }
    document.getElementById("modal").hidden = false;
}

function closeModal() {
    document.getElementById("modal").hidden = true;
}

async function submitForm(event) {
    // 表单提交（点"保存"）：根据有没有隐藏 id 判断是新建还是编辑
    event.preventDefault(); // 阻止浏览器默认刷新页面

    // 防重复提交：请求期间禁用保存按钮。不这么做的话，快速双击
    // "保存"会发两个请求、创建两条重复客户。
    const saveBtn = document.querySelector('#client-form button[type="submit"]');
    if (saveBtn.disabled) return;   // 已经在提交中了，忽略这次点击
    saveBtn.disabled = true;
    const originalText = saveBtn.textContent;
    saveBtn.textContent = "保存中…";

    // 注意：必须是 .value（输入框里存的值），拿元素本身的话
    // 恒为真值，保存会永远走"修改"分支发错请求
    const id = document.getElementById("form-id").value;
    // 报价输入框是文本类型；空串要转成 null 再提交——
    // 因为后端接口声明的是"数字或空"，空字符串会在接口校验时被拒绝
    const quoteInput = document.getElementById("form-quote").value.trim();
    const payload = {
        name: document.getElementById("form-name").value.trim(),
        source: document.getElementById("form-source").value.trim(),
        project_desc: document.getElementById("form-desc").value.trim(),
        quote: quoteInput === "" ? null : quoteInput,
        status: document.getElementById("form-status").value,
        next_followup: document.getElementById("form-followup").value,
        notes: document.getElementById("form-notes").value.trim(),
    };

    try {
        if (id) {
            await api(`/api/clients/${id}`, { method: "PUT", body: JSON.stringify(payload) });
        } else {
            await api("/api/clients", { method: "POST", body: JSON.stringify(payload) });
        }
        closeModal();
        await refreshAll(); // 数据变了，首页和列表页都重新拉一遍
    } catch (err) {
        alert(err.message); // 后端校验不过（如姓名为空）时，这里弹出中文原因
    } finally {
        // 无论成功失败都恢复按钮，否则下次想保存时按钮还是灰的
        saveBtn.disabled = false;
        saveBtn.textContent = originalText;
    }
}

async function deleteClient(id) {
    // 删除前必须确认：误删客户记录找不回来
    if (!confirm("确定删除这个客户吗？删除后无法恢复。")) return;
    try {
        await api(`/api/clients/${id}`, { method: "DELETE" });
        await refreshAll();
    } catch (err) {
        alert(err.message);
    }
}

/* ==================== 五、页面切换与事件绑定 ==================== */

function switchView(view) {
    // 切换"首页 / 客户"标签：控制两个区域的显示隐藏 + 标签高亮
    document.querySelectorAll(".tab").forEach((tab) => {
        tab.classList.toggle("active", tab.dataset.view === view);
    });
    document.getElementById("view-home").hidden = view !== "home";
    document.getElementById("view-clients").hidden = view !== "clients";
    // 切到客户页时重新渲染（可能数据有变化）
    if (view === "clients") renderClients().catch(showError);
}

async function refreshAll() {
    // 首页和列表页同时刷新（增删改之后调用）；
    // 顺带更新全量客户缓存（来源下拉框的选项来源）
    renderStats().catch(showError);
    renderClients().catch(showError);
    loadAllClientsForSources().catch(showError);
}

function showError(err) {
    // 统一的错误出口：任何接口失败都在页面上明说，不静默
    console.error(err);
    alert(`加载失败：${err.message}`);
}

function bindEvents() {
    // 把所有按钮的点击事件集中绑在这里，页面加载完执行一次

    // 标签切换：顺便把当前标签写进网址 # 号后面（如 #clients），
    // 这样刷新页面后还能停留在同一个标签，也可以直接发链接给朋友
    document.querySelectorAll(".tab").forEach((tab) => {
        tab.addEventListener("click", () => {
            location.hash = tab.dataset.view;
            switchView(tab.dataset.view);
        });
    });

    // 新建按钮（浮动球）
    document.getElementById("btn-new").addEventListener("click", () => openModal());

    // 表单提交 / 取消 / 点遮罩关闭
    document.getElementById("client-form").addEventListener("submit", submitForm);
    document.getElementById("btn-cancel").addEventListener("click", closeModal);
    document.getElementById("modal").addEventListener("click", (e) => {
        if (e.target === document.getElementById("modal")) closeModal(); // 只点在遮罩上才关
    });

    // 筛选：点搜索 = 重新拉列表；点重置 = 清空三个条件再拉
    document.getElementById("btn-filter").addEventListener("click", () => renderClients().catch(showError));
    document.getElementById("btn-reset").addEventListener("click", () => {
        document.getElementById("filter-status").value = "";
        document.getElementById("filter-source").value = "";
        document.getElementById("filter-q").value = "";
        renderClients().catch(showError);
    });
    // 输入框里按回车也能直接搜
    document.getElementById("filter-q").addEventListener("keydown", (e) => {
        if (e.key === "Enter") renderClients().catch(showError);
    });

    // 卡片上的按钮是动态生成的，用"事件委托"：把监听挂在列表容器上，
    // 点击时通过 e.target 找最近的按钮，再按 class 分派动作。
    // （动态内容没法在 bindEvents 时直接绑，这是标准解法）
    document.addEventListener("click", async (e) => {
        const editBtn = e.target.closest(".btn-edit");
        const delBtn = e.target.closest(".btn-del");
        const doneBtn = e.target.closest(".btn-done");

        if (editBtn) {
            const id = editBtn.dataset.id;
            try {
                const client = await api(`/api/clients/${id}`);
                openModal(client); // 拿到详情后打开弹窗预填
            } catch (err) {
                showError(err);
            }
        }
        if (delBtn) {
            deleteClient(delBtn.dataset.id);
        }
        if (doneBtn) {
            // 首页"标记完成"快捷按钮：只改状态。
            // 注意：后端修改接口是"全字段覆盖"，所以必须先取回该客户的
            // 完整数据，改掉 status 后再整体提交，否则会把其他字段抹掉。
            const id = doneBtn.dataset.id;
            try {
                const client = await api(`/api/clients/${id}`);
                await api(`/api/clients/${id}`, {
                    method: "PUT",
                    body: JSON.stringify({ ...client, status: "已完成" }),
                });
                await refreshAll();
            } catch (err) {
                showError(err);
            }
        }
    });
}

/* ==================== 六、启动 ==================== */

// 页面加载完成后：绑定事件 + 根据网址 # 号决定显示哪个标签
// （默认首页；#clients 则直接进列表页）
document.addEventListener("DOMContentLoaded", () => {
    bindEvents();
    switchView(location.hash === "#clients" ? "clients" : "home");
    renderStats().catch(showError);            // 首页统计始终要加载
    loadAllClientsForSources().catch(showError); // 来源下拉框选项
});
