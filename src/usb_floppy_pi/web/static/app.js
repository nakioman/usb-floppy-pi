async function fetchJson(url, opts) {
    const r = await fetch(url, opts);
    if (!r.ok) {
        const txt = await r.text();
        throw new Error(txt || r.statusText);
    }
    return r.json();
}

async function refresh() {
    const [sets, state] = await Promise.all([
        fetchJson("/api/sets"),
        fetchJson("/api/state"),
    ]);
    renderStatus(state);
    renderSets(sets.sets, state.mounted);
    populateUploadDropdown(sets.sets);
}

function renderStatus(state) {
    const el = document.getElementById("status");
    if (state.mounted) {
        el.className = "status mounted";
        const m = state.mounted;
        const sess = m.is_session ? " (session)" : "";
        const ro = m.read_only ? " [RO]" : " [RW]";
        el.textContent = `Mounted: ${m.set_name} / ${m.disk_filename}${ro}${sess}`;
    } else {
        el.className = "status";
        el.textContent = "No image mounted";
    }
}

function renderSets(sets, mounted) {
    const container = document.getElementById("sets-container");
    container.innerHTML = "";
    if (sets.length === 0) {
        container.innerHTML = "<p>No floppy sets yet. Upload an image or copy folders to the Samba share <code>\\\\floppy\\floppies</code>.</p>";
        return;
    }
    for (const s of sets) {
        const div = document.createElement("div");
        div.className = "set";
        const roBadge = s.read_only ? `<span class="ro-badge">[RO]</span>` : "";
        const roButton = s.read_only
            ? `<button onclick="setReadOnly('${escape(s.name)}', false)">Make writable</button>`
            : `<button onclick="setReadOnly('${escape(s.name)}', true)">Make read-only</button>`;
        let html = `<h3>${escapeHtml(s.name)}${roBadge}</h3>`;
        html += s.disks.map(d => {
            const isMounted = mounted && mounted.set_name === s.name && mounted.disk_filename === d;
            const cls = isMounted ? "mounted" : "";
            return `<div class="disk">
                <span class="disk-name">${escapeHtml(d)}</span>
                <button class="${cls}" onclick="mount('${escape(s.name)}', '${escape(d)}', false)">${isMounted ? "Mounted" : "Mount"}</button>
                <button class="session" onclick="mount('${escape(s.name)}', '${escape(d)}', true)">Session</button>
            </div>`;
        }).join("");
        html += `<div style="margin-top: 0.5rem">${roButton}`;
        if (mounted) {
            html += ` <button class="danger" onclick="eject()">Eject</button>`;
        }
        html += `</div>`;
        div.innerHTML = html;
        container.appendChild(div);
    }
}

function populateUploadDropdown(sets) {
    const sel = document.getElementById("upload-set");
    const current = sel.value;
    sel.innerHTML = "";
    for (const s of sets) {
        const opt = document.createElement("option");
        opt.value = s.name;
        opt.textContent = s.name;
        sel.appendChild(opt);
    }
    if (current) sel.value = current;
}

async function mount(setName, disk, session) {
    try {
        await fetchJson("/api/mount", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({set: setName, disk: disk, session: session}),
        });
        await refresh();
    } catch (e) { alert("Mount failed: " + e.message); }
}

async function eject() {
    try {
        await fetchJson("/api/eject", {method: "POST"});
        await refresh();
    } catch (e) { alert("Eject failed: " + e.message); }
}

async function setReadOnly(setName, ro) {
    try {
        await fetchJson(`/api/sets/${encodeURIComponent(setName)}/readonly`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({ro}),
        });
        await refresh();
    } catch (e) { alert("Set readonly failed: " + e.message); }
}

async function doUpload() {
    const setSel = document.getElementById("upload-set");
    const fileInput = document.getElementById("upload-file");
    const errEl = document.getElementById("upload-error");
    errEl.textContent = "";
    if (!fileInput.files.length) { errEl.textContent = "Pick a file first"; return; }
    const fd = new FormData();
    fd.append("set", setSel.value);
    fd.append("file", fileInput.files[0]);
    try {
        await fetchJson("/api/upload", {method: "POST", body: fd});
        fileInput.value = "";
        await refresh();
    } catch (e) {
        errEl.textContent = "Upload failed: " + e.message;
    }
}

function escapeHtml(s) {
    return s.replace(/[<>&"']/g, c => ({
        "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&#39;"
    })[c]);
}

// Refresh every 3 seconds + on load
refresh().catch(e => {
    document.getElementById("status").textContent = "Error: " + e.message;
});
setInterval(refresh, 3000);
