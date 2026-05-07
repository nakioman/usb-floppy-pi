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

function makeButton(label, className, onClick) {
    const btn = document.createElement("button");
    btn.textContent = label;
    if (className) btn.className = className;
    btn.addEventListener("click", onClick);
    return btn;
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

        const h3 = document.createElement("h3");
        h3.textContent = s.name;
        if (s.read_only) {
            const badge = document.createElement("span");
            badge.className = "ro-badge";
            badge.textContent = " [RO]";
            h3.appendChild(badge);
        }
        div.appendChild(h3);

        for (const d of s.disks) {
            const isMounted = mounted && mounted.set_name === s.name && mounted.disk_filename === d;
            const row = document.createElement("div");
            row.className = "disk";

            const name = document.createElement("span");
            name.className = "disk-name";
            name.textContent = d;
            row.appendChild(name);

            row.appendChild(makeButton(
                isMounted ? "Mounted" : "Mount",
                isMounted ? "mounted" : "",
                () => mount(s.name, d, false),
            ));
            row.appendChild(makeButton(
                "Session",
                "session",
                () => mount(s.name, d, true),
            ));
            div.appendChild(row);
        }

        const actions = document.createElement("div");
        actions.style.marginTop = "0.5rem";
        actions.appendChild(makeButton(
            s.read_only ? "Make writable" : "Make read-only",
            "",
            () => setReadOnly(s.name, !s.read_only),
        ));
        if (mounted) {
            actions.appendChild(document.createTextNode(" "));
            actions.appendChild(makeButton("Eject", "danger", () => eject()));
        }
        div.appendChild(actions);

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
    const newSetInput = document.getElementById("upload-new-set");
    const fileInput = document.getElementById("upload-file");
    const errEl = document.getElementById("upload-error");
    const resultsEl = document.getElementById("upload-results");
    errEl.textContent = "";
    resultsEl.innerHTML = "";

    if (!fileInput.files.length) {
        errEl.textContent = "Pick at least one file first";
        return;
    }

    const newSetName = newSetInput.value.trim();
    const useNewSet = newSetName.length > 0;
    const targetSet = useNewSet ? newSetName : setSel.value;
    if (!targetSet) {
        errEl.textContent = "Choose a set or type a new set name";
        return;
    }

    // Show pending rows immediately for UX feedback during the upload.
    const fileNames = Array.from(fileInput.files).map(f => f.name);
    resultsEl.innerHTML = fileNames
        .map(n => `<div class="pending" data-name="${escapeHtml(n)}">⏳ ${escapeHtml(n)} — uploading...</div>`)
        .join("");

    const fd = new FormData();
    fd.append("set", targetSet);
    if (useNewSet) fd.append("create_new", "true");
    for (const f of fileInput.files) fd.append("files", f);

    let body;
    try {
        body = await fetchJson("/api/upload", {method: "POST", body: fd});
    } catch (e) {
        errEl.textContent = "Upload failed: " + e.message;
        resultsEl.innerHTML = "";
        return;
    }

    // Replace the pending list with per-file outcomes.
    resultsEl.innerHTML = body.results.map(r => {
        const name = r.filename || "(unknown)";
        if (r.kind === "error") {
            return `<div class="err">✗ ${escapeHtml(name)} — ${escapeHtml(r.detail || "error")}</div>`;
        }
        const final = r.final_filename && r.final_filename !== name
            ? ` → ${escapeHtml(r.final_filename)}`
            : "";
        return `<div class="ok">✓ ${escapeHtml(name)}${final} (${r.kind})</div>`;
    }).join("");

    // Clear inputs and refresh the set list so the new images show up.
    fileInput.value = "";
    newSetInput.value = "";
    await refresh();
    // Pre-select the just-used set in the dropdown for follow-up uploads.
    const sel = document.getElementById("upload-set");
    if (Array.from(sel.options).some(o => o.value === body.set)) {
        sel.value = body.set;
    }
}

document.getElementById("upload-button")?.addEventListener("click", doUpload);

// Refresh every 3 seconds + on load
refresh().catch(e => {
    document.getElementById("status").textContent = "Error: " + e.message;
});
setInterval(refresh, 3000);
