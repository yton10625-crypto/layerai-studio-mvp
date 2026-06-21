/* LayerAI Studio — frontend app logic (vanilla JS, no build step) */

const API = "";

const state = {
  view: "upload",
  projectId: null,
  project: null,
  layers: [],
  selectedLayerId: null,
  mergeSelection: new Set(),
  editingLayerId: null,
};

// ---------------- view switching ----------------
function goToView(view) {
  state.view = view;
  for (const el of document.querySelectorAll(".view")) el.hidden = true;
  document.getElementById(`view-${view}`).hidden = false;

  const order = ["upload", "analyzing", "workspace", "export"];
  const idx = order.indexOf(view);
  for (const li of document.querySelectorAll(".step")) {
    const liIdx = order.indexOf(li.dataset.step);
    li.classList.toggle("active", liIdx === idx);
    li.classList.toggle("done", liIdx < idx);
  }
}

// allow clicking completed step pills to jump back
document.getElementById("steps").addEventListener("click", (e) => {
  const li = e.target.closest(".step");
  if (!li || !li.classList.contains("done")) return;
  if (li.dataset.step === "workspace" || li.dataset.step === "export") {
    if (state.projectId) goToView(li.dataset.step);
  } else if (li.dataset.step === "upload") {
    goToView("upload");
  }
});

// ================== STEP 1: upload ==================
const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("fileInput");
const uploadError = document.getElementById("uploadError");

["dragenter", "dragover"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  })
);
["dragleave", "drop"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
  })
);
dropzone.addEventListener("drop", (e) => {
  const file = e.dataTransfer.files[0];
  if (file) handleFileSelected(file);
});
fileInput.addEventListener("change", (e) => {
  const file = e.target.files[0];
  if (file) handleFileSelected(file);
});

async function handleFileSelected(file) {
  uploadError.hidden = true;
  const allowed = ["image/png", "image/jpeg", "image/webp"];
  if (!allowed.includes(file.type)) {
    showUploadError("Unsupported file type. Use PNG, JPG, or WEBP.");
    return;
  }
  if (file.size > 20 * 1024 * 1024) {
    showUploadError("File exceeds the 20MB limit.");
    return;
  }

  const formData = new FormData();
  formData.append("file", file);
  formData.append("name", file.name);

  try {
    const res = await fetch(`${API}/api/projects/upload`, { method: "POST", body: formData });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Upload failed");

    state.projectId = data.project_id;
    state.project = data;

    const previewUrl = `${API}/api/projects/${data.project_id}/image`;
    document.getElementById("topbarMeta").textContent = data.name;
    runAnalysis(previewUrl);
  } catch (err) {
    showUploadError(err.message);
  }
}

function showUploadError(msg) {
  uploadError.textContent = msg;
  uploadError.hidden = false;
}

// ================== STEP 2: analyzing ==================
const STAGE_SEQUENCE = ["detect", "segment", "ocr", "classify", "tree"];

async function runAnalysis(previewUrl) {
  goToView("analyzing");
  document.getElementById("analyzingPreview").style.backgroundImage = `url(${previewUrl})`;

  const stepEls = STAGE_SEQUENCE.map((s) => document.querySelector(`#pipelineSteps li[data-stage="${s}"]`));
  stepEls.forEach((el) => el.classList.remove("active", "done"));

  // animate the stage list while the (fast, synchronous) backend pipeline runs —
  // gives the multi-stage AI pipeline a readable rhythm instead of a flash.
  let i = 0;
  const tick = () => {
    if (i > 0) stepEls[i - 1].classList.replace("active", "done");
    if (i < stepEls.length) {
      stepEls[i].classList.add("active");
      i++;
      setTimeout(tick, 420);
    }
  };
  tick();

  try {
    const startRes = await fetch(`${API}/api/analysis/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_id: state.projectId }),
    });
    if (!startRes.ok) throw new Error((await startRes.json()).error || "Analysis failed");

    // make sure the stage animation has had time to play out before switching views
    const minDelay = new Promise((r) => setTimeout(r, STAGE_SEQUENCE.length * 420 + 200));
    const [result] = await Promise.all([fetchAnalysisResult(), minDelay]);

    stepEls.forEach((el) => el.classList.add("done"));
    state.project = result.project;
    state.layers = result.layers;
    enterWorkspace();
  } catch (err) {
    alert(`Analysis failed: ${err.message}`);
    goToView("upload");
  }
}

async function fetchAnalysisResult() {
  const res = await fetch(`${API}/api/analysis/result?project_id=${state.projectId}`);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "Could not fetch result");
  return data;
}

// ================== STEP 3+4: workspace ==================
function enterWorkspace() {
  goToView("workspace");
  document.getElementById("canvasTitle").textContent = state.project.name;
  document.getElementById("canvasDims").textContent = `${state.project.image_width} × ${state.project.image_height}px`;
  document.getElementById("canvasImage").src = `${API}/api/projects/${state.projectId}/image`;
  document.getElementById("topbarMeta").textContent = `${state.layers.length} layers`;
  renderLayerList();
  renderBBoxes();

  document.getElementById("canvasImage").onload = renderBBoxes;
}

function renderLayerList() {
  const list = document.getElementById("layerList");
  list.innerHTML = "";

  // top-to-bottom visual stacking order = highest z-index first
  const sorted = [...state.layers].sort((a, b) => b.z_index - a.z_index);

  for (const layer of sorted) {
    const li = document.createElement("li");
    li.className = "layer-row";
    li.dataset.layerId = layer.id;
    if (layer.id === state.selectedLayerId) li.classList.add("selected");
    if (!layer.visible) li.classList.add("invisible");

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "layer-checkbox";
    checkbox.checked = state.mergeSelection.has(layer.id);
    checkbox.addEventListener("click", (e) => e.stopPropagation());
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.mergeSelection.add(layer.id);
      else state.mergeSelection.delete(layer.id);
      document.getElementById("mergeBtn").disabled = state.mergeSelection.size < 2;
    });

    const main = document.createElement("div");
    main.className = "layer-row-main";

    const nameInput = document.createElement("input");
    nameInput.className = "layer-name-input";
    nameInput.value = layer.name;
    nameInput.addEventListener("click", (e) => e.stopPropagation());
    nameInput.addEventListener("change", () => doLayerAction("rename", layer.id, { name: nameInput.value }));

    const meta = document.createElement("div");
    meta.className = "layer-meta";
    const badge = document.createElement("span");
    badge.className = `role-badge role-${layer.role}`;
    badge.textContent = layer.role.replace("_", " ");
    meta.appendChild(badge);
    if (layer.confidence != null) {
      const conf = document.createElement("span");
      conf.className = "confidence-mono";
      conf.textContent = `${Math.round(layer.confidence * 100)}%`;
      meta.appendChild(conf);
    }

    main.appendChild(nameInput);
    main.appendChild(meta);

    if (layer.id === state.editingLayerId && layer.type === "text") {
      const editor = document.createElement("div");
      editor.className = "layer-text-editor";
      const textInput = document.createElement("input");
      textInput.value = layer.content_text || "";
      textInput.addEventListener("click", (e) => e.stopPropagation());
      textInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          doLayerAction("edit_text", layer.id, { content_text: textInput.value });
          state.editingLayerId = null;
        }
      });
      editor.appendChild(textInput);
      main.appendChild(editor);
      setTimeout(() => textInput.focus(), 0);
    }

    const actions = document.createElement("div");
    actions.className = "layer-row-actions";

    if (layer.type === "text") {
      actions.appendChild(
        iconButton("✎", "Edit text", (e) => {
          e.stopPropagation();
          state.editingLayerId = state.editingLayerId === layer.id ? null : layer.id;
          renderLayerList();
        })
      );
    } else if (layer.type === "image") {
      const fileInputEl = document.createElement("input");
      fileInputEl.type = "file";
      fileInputEl.accept = "image/png,image/jpeg,image/webp";
      fileInputEl.hidden = true;
      fileInputEl.addEventListener("click", (e) => e.stopPropagation());
      fileInputEl.addEventListener("change", () => {
        if (fileInputEl.files[0]) replaceLayerImage(layer.id, fileInputEl.files[0]);
      });
      const replaceBtn = iconButton("⤓", "Replace image", (e) => {
        e.stopPropagation();
        fileInputEl.click();
      });
      replaceBtn.appendChild(fileInputEl);
      actions.appendChild(replaceBtn);
    }

    actions.appendChild(
      iconButton(layer.visible ? "◉" : "○", "Toggle visibility", (e) => {
        e.stopPropagation();
        doLayerAction("toggle_visible", layer.id);
      })
    );
    actions.appendChild(
      iconButton("↑", "Bring forward", (e) => {
        e.stopPropagation();
        moveLayer(layer.id, 1);
      })
    );
    actions.appendChild(
      iconButton("↓", "Send backward", (e) => {
        e.stopPropagation();
        moveLayer(layer.id, -1);
      })
    );
    const delBtn = iconButton("✕", "Delete layer", (e) => {
      e.stopPropagation();
      if (confirm(`Delete "${layer.name}"?`)) doLayerAction("delete", layer.id);
    });
    delBtn.classList.add("danger");
    actions.appendChild(delBtn);

    li.appendChild(checkbox);
    li.appendChild(main);
    li.appendChild(actions);

    li.addEventListener("click", () => {
      state.selectedLayerId = layer.id;
      renderLayerList();
      renderBBoxes();
    });

    list.appendChild(li);
  }

  document.getElementById("layerCount").textContent = `${state.layers.length} layers`;
  document.getElementById("mergeBtn").disabled = state.mergeSelection.size < 2;
}

function iconButton(label, title, onClick) {
  const btn = document.createElement("button");
  btn.className = "icon-btn";
  btn.type = "button";
  btn.title = title;
  btn.textContent = label;
  btn.addEventListener("click", onClick);
  return btn;
}

function renderBBoxes() {
  const img = document.getElementById("canvasImage");
  const bboxLayer = document.getElementById("bboxLayer");
  bboxLayer.innerHTML = "";
  if (!img.naturalWidth) return;

  const scaleX = img.clientWidth / img.naturalWidth;
  const scaleY = img.clientHeight / img.naturalHeight;

  for (const layer of state.layers) {
    if (layer.role === "background") continue; // covers the whole frame, not useful to outline
    const box = document.createElement("div");
    box.className = "bbox";
    if (!layer.visible) box.classList.add("hidden-layer");
    if (layer.id === state.selectedLayerId) box.classList.add("selected");
    box.style.left = `${layer.bbox_x * scaleX}px`;
    box.style.top = `${layer.bbox_y * scaleY}px`;
    box.style.width = `${layer.bbox_w * scaleX}px`;
    box.style.height = `${layer.bbox_h * scaleY}px`;

    if (layer.id === state.selectedLayerId) {
      const tag = document.createElement("span");
      tag.className = "bbox-tag";
      tag.textContent = layer.role.replace("_", " ");
      box.appendChild(tag);
    }

    box.addEventListener("click", () => {
      state.selectedLayerId = layer.id;
      renderLayerList();
      renderBBoxes();
    });
    bboxLayer.appendChild(box);
  }
}

window.addEventListener("resize", () => {
  if (state.view === "workspace") renderBBoxes();
});

async function doLayerAction(action, layerId, extra = {}) {
  try {
    const res = await fetch(`${API}/api/layers/update`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_id: state.projectId, action, layer_id: layerId, ...extra }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Action failed");
    await refreshLayers();
  } catch (err) {
    alert(err.message);
  }
}

async function moveLayer(layerId, direction) {
  const sorted = [...state.layers].sort((a, b) => a.z_index - b.z_index);
  const idx = sorted.findIndex((l) => l.id === layerId);
  const swapIdx = idx + direction;
  if (swapIdx < 0 || swapIdx >= sorted.length) return;
  [sorted[idx], sorted[swapIdx]] = [sorted[swapIdx], sorted[idx]];
  const orderedIds = sorted.map((l) => l.id);
  try {
    const res = await fetch(`${API}/api/layers/update`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_id: state.projectId, action: "reorder", ordered_layer_ids: orderedIds }),
    });
    if (!res.ok) throw new Error((await res.json()).error || "Reorder failed");
    await refreshLayers();
  } catch (err) {
    alert(err.message);
  }
}

document.getElementById("mergeBtn").addEventListener("click", async () => {
  const ids = [...state.mergeSelection];
  if (ids.length < 2) return;
  const name = prompt("Name for the merged layer:", "Merged Layer");
  if (name === null) return;
  try {
    const res = await fetch(`${API}/api/layers/update`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_id: state.projectId, action: "merge", layer_ids: ids, name }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Merge failed");
    state.mergeSelection.clear();
    await refreshLayers();
  } catch (err) {
    alert(err.message);
  }
});

async function replaceLayerImage(layerId, file) {
  const formData = new FormData();
  formData.append("project_id", state.projectId);
  formData.append("layer_id", layerId);
  formData.append("file", file);
  try {
    const res = await fetch(`${API}/api/layers/replace-image`, { method: "POST", body: formData });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Replace image failed");
    alert("Image replaced. (Composited canvas preview is a Phase 2 item — see README.)");
    await refreshLayers();
  } catch (err) {
    alert(err.message);
  }
}

async function refreshLayers() {
  const result = await fetchAnalysisResult();
  state.layers = result.layers;
  renderLayerList();
  renderBBoxes();
  document.getElementById("topbarMeta").textContent = `${state.layers.length} layers`;
  if (!document.getElementById("historyPanel").hidden) loadHistory();
}

document.getElementById("historyToggle").addEventListener("click", () => {
  const panel = document.getElementById("historyPanel");
  panel.hidden = !panel.hidden;
  if (!panel.hidden) loadHistory();
});

async function loadHistory() {
  const panel = document.getElementById("historyPanel");
  const res = await fetch(`${API}/api/layers/${state.projectId}/history`);
  const rows = await res.json();
  panel.innerHTML = "";
  if (!rows.length) {
    panel.innerHTML = `<div class="empty-hint">No edits yet.</div>`;
    return;
  }
  for (const r of rows) {
    const div = document.createElement("div");
    div.className = "history-row";
    const when = new Date(r.timestamp * 1000).toLocaleTimeString();
    div.innerHTML = `<b>${r.action}</b> · ${r.field} · ${when}`;
    panel.appendChild(div);
  }
}

// Add a "Continue to export" control into the layers pane footer
const continueBtn = document.createElement("button");
continueBtn.className = "btn-primary";
continueBtn.textContent = "Continue to export →";
continueBtn.style.margin = "10px 18px 16px";
continueBtn.addEventListener("click", () => {
  document.getElementById("topbarMeta").textContent = state.project.name;
  goToView("export");
  loadExports();
});
document.querySelector(".layers-pane").appendChild(continueBtn);

// ================== STEP 5: export ==================
document.getElementById("exportJsonBtn").addEventListener("click", () => doExport("json"));
document.getElementById("exportPsdBtn").addEventListener("click", () => doExport("psd"));

async function doExport(type) {
  const btn = document.getElementById(type === "json" ? "exportJsonBtn" : "exportPsdBtn");
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Exporting…";
  try {
    const res = await fetch(`${API}/api/export/${type}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_id: state.projectId }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Export failed");
    await loadExports();
  } catch (err) {
    alert(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}

async function loadExports() {
  const res = await fetch(`${API}/api/exports?project_id=${state.projectId}`);
  const rows = await res.json();
  const container = document.getElementById("exportHistory");
  container.innerHTML = "";
  if (!rows.length) {
    container.innerHTML = `<div class="empty-hint">No exports yet — generate one above.</div>`;
    return;
  }
  for (const r of rows) {
    const row = document.createElement("div");
    row.className = "export-history-row";
    const when = new Date(r.created_at * 1000).toLocaleString();
    row.innerHTML = `<span>${r.export_type.toUpperCase()} · ${when}</span>`;
    const link = document.createElement("a");
    link.href = `${API}/api/export/download/${r.id}`;
    link.textContent = "Download ↓";
    row.appendChild(link);
    container.appendChild(row);
  }
}

goToView("upload");
