// second-read front end. Plain JS, no build step. All writes go through /api/.
(function () {
  "use strict";

  function csrf() {
    const m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  async function api(method, url, body, isForm) {
    const opts = { method, headers: { "X-CSRFToken": csrf() }, credentials: "same-origin" };
    if (body !== undefined) {
      if (isForm) opts.body = body;
      else { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    }
    const res = await fetch(url, opts);
    const text = await res.text();
    const data = text ? JSON.parse(text) : null;
    if (!res.ok) {
      const detail = data && (data.detail || JSON.stringify(data));
      throw new Error(detail || res.statusText);
    }
    return data;
  }

  function say(text, isError) {
    const el = document.getElementById("message");
    if (!el) return;
    el.textContent = text || "";
    el.className = "message" + (isError ? " error" : "");
  }

  // ---------- upload ----------
  const upload = document.getElementById("upload");
  if (upload) {
    upload.addEventListener("submit", async (e) => {
      e.preventDefault();
      const status = document.getElementById("upload-status");
      status.textContent = "Uploading…";
      try {
        await api("POST", `/api/studies/${upload.dataset.study}/images/`, new FormData(upload), true);
        location.reload();
      } catch (err) {
        status.textContent = err.message;
        status.className = "error";
      }
    });
  }

  // ---------- viewer ----------
  const root = document.getElementById("viewer");
  if (!root) return;

  const mode = root.dataset.mode;
  const imageId = root.dataset.image;
  const W = +root.dataset.width, H = +root.dataset.height;
  const canvas = document.getElementById("canvas");
  const ctx = canvas.getContext("2d");
  canvas.width = W; canvas.height = H; // draw in image pixels, CSS scales it

  const labelColors = {};
  document.querySelectorAll("#label option").forEach(o => { labelColors[o.value] = o.dataset.color; });

  const img = new Image();
  img.src = root.dataset.src;

  let task = null;
  let annotations = [];
  let overlay = [];         // another reader's shapes, review mode only
  let selected = null;
  let tool = "box";
  let drag = null;          // {x0,y0,x1,y1}
  let poly = [];            // points of polygon in progress

  function toImage(evt) {
    const r = canvas.getBoundingClientRect();
    return [
      Math.max(0, Math.min(W, Math.round((evt.clientX - r.left) * W / r.width))),
      Math.max(0, Math.min(H, Math.round((evt.clientY - r.top) * H / r.height))),
    ];
  }

  function points(a) {
    if (a.shape === "box") {
      const [x, y, w, h] = a.geometry;
      return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]];
    }
    return a.geometry;
  }

  function strokePath(pts, color, width, dashed, fill) {
    ctx.beginPath();
    pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
    ctx.closePath();
    ctx.setLineDash(dashed ? [8, 6] : []);
    ctx.lineWidth = width;
    ctx.strokeStyle = color;
    if (fill) { ctx.fillStyle = color + "33"; ctx.fill(); }
    ctx.stroke();
  }

  function colorOf(label) { return labelColors[label] || "#53057F"; }

  function draw() {
    const lw = Math.max(2, W / 400);
    ctx.setLineDash([]);
    ctx.drawImage(img, 0, 0, W, H);
    overlay.forEach(a => strokePath(points(a), "#ffffff", lw, true, false));
    annotations.forEach(a => strokePath(points(a), colorOf(a.label), a === selected ? lw * 2 : lw, false, a === selected));
    if (drag) {
      const x = Math.min(drag.x0, drag.x1), y = Math.min(drag.y0, drag.y1);
      strokePath([[x, y], [Math.max(drag.x0, drag.x1), y], [Math.max(drag.x0, drag.x1), Math.max(drag.y0, drag.y1)], [x, Math.max(drag.y0, drag.y1)]],
        colorOf(currentLabel()), lw, true, false);
    }
    if (poly.length) {
      ctx.beginPath();
      poly.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
      ctx.setLineDash([6, 4]);
      ctx.strokeStyle = colorOf(currentLabel());
      ctx.lineWidth = lw;
      ctx.stroke();
    }
  }

  function currentLabel() {
    const sel = document.getElementById("label");
    return sel ? sel.value : null;
  }

  function editable() { return mode === "annotate" && task && (task.status === "draft" || task.status === "rejected"); }

  function renderList() {
    const list = document.getElementById("list");
    list.innerHTML = "";
    annotations.forEach((a, i) => {
      const li = document.createElement("li");
      li.className = a === selected ? "selected" : "";
      li.innerHTML = `<span class="swatch" style="background:${colorOf(a.label)}"></span>` +
        `<span>${i + 1}. ${a.label}</span><span class="muted">${a.shape}</span>`;
      li.onclick = () => { selected = a; renderList(); draw(); };
      list.appendChild(li);
    });
    document.getElementById("status").textContent = task ? task.status : "…";
    const submit = document.getElementById("submit");
    if (submit) submit.disabled = !editable() || annotations.length === 0;
    ["approve", "reject"].forEach(id => {
      const b = document.getElementById(id);
      if (b) b.disabled = !task || task.status !== "submitted";
    });
    const fb = document.getElementById("feedback");
    if (fb) {
      const last = task && task.reviews.length ? task.reviews[task.reviews.length - 1] : null;
      fb.innerHTML = last && task.status === "rejected"
        ? `<div class="feedback"><strong>Returned by ${last.reviewer}:</strong> ${escapeHtml(last.comment)}</div>` : "";
    }
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  async function save(shape, geometry) {
    try {
      const a = await api("POST", `/api/tasks/${task.id}/annotations/`, { label: currentLabel(), shape, geometry });
      annotations.push(a);
      selected = a;
      say("");
    } catch (err) { say(err.message, true); }
    renderList(); draw();
  }

  function hit(x, y) {
    // last drawn wins; point-in-polygon by ray casting
    for (let i = annotations.length - 1; i >= 0; i--) {
      const pts = points(annotations[i]);
      let inside = false;
      for (let j = 0, k = pts.length - 1; j < pts.length; k = j++) {
        const [xi, yi] = pts[j], [xk, yk] = pts[k];
        if ((yi > y) !== (yk > y) && x < (xk - xi) * (y - yi) / (yk - yi) + xi) inside = !inside;
      }
      if (inside) return annotations[i];
    }
    return null;
  }

  // ----- annotate mode -----
  if (mode === "annotate") {
    document.querySelectorAll("[data-tool]").forEach(b => b.addEventListener("click", () => {
      tool = b.dataset.tool;
      poly = [];
      document.querySelectorAll("[data-tool]").forEach(x => x.classList.toggle("active", x === b));
      draw();
    }));

    canvas.addEventListener("mousedown", (e) => {
      if (!editable()) return;
      const [x, y] = toImage(e);
      if (tool === "box") {
        const h = hit(x, y);
        if (h && !e.shiftKey) { selected = h; renderList(); draw(); return; }
        drag = { x0: x, y0: y, x1: x, y1: y };
      }
    });
    canvas.addEventListener("mousemove", (e) => {
      if (drag) { const [x, y] = toImage(e); drag.x1 = x; drag.y1 = y; draw(); }
    });
    window.addEventListener("mouseup", () => {
      if (!drag) return;
      const x = Math.min(drag.x0, drag.x1), y = Math.min(drag.y0, drag.y1);
      const w = Math.abs(drag.x1 - drag.x0), h = Math.abs(drag.y1 - drag.y0);
      drag = null;
      if (w >= 3 && h >= 3) save("box", [x, y, w, h]); else draw();
    });
    canvas.addEventListener("click", (e) => {
      if (!editable() || tool !== "polygon") return;
      poly.push(toImage(e));
      draw();
    });
    canvas.addEventListener("dblclick", () => {
      if (tool !== "polygon") return;
      // dblclick also fired two clicks; drop the duplicate last point
      const pts = poly.slice(0, -1);
      poly = [];
      if (pts.length >= 3) save("polygon", pts); else draw();
    });
    window.addEventListener("keydown", async (e) => {
      if (e.key === "Escape") { poly = []; drag = null; draw(); }
      if ((e.key === "Delete" || e.key === "Backspace") && selected && editable()
          && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) {
        e.preventDefault();
        try {
          await api("DELETE", `/api/annotations/${selected.id}/`);
          annotations = annotations.filter(a => a !== selected);
          selected = null;
        } catch (err) { say(err.message, true); }
        renderList(); draw();
      }
    });
    document.getElementById("submit").addEventListener("click", async () => {
      try {
        task = await api("POST", `/api/tasks/${task.id}/submit/`);
        say("Submitted. A reviewer will see it in their queue.");
      } catch (err) { say(err.message, true); }
      renderList();
    });
  }

  // ----- review mode -----
  if (mode === "review") {
    const decide = async (decision) => {
      const comment = document.getElementById("comment").value;
      try {
        task = await api("POST", `/api/tasks/${task.id}/${decision}/`, { comment });
        say(decision === "approve" ? "Approved." : "Returned to the reader.");
      } catch (err) { say(err.message, true); }
      renderList();
    };
    document.getElementById("approve").addEventListener("click", () => decide("approve"));
    document.getElementById("reject").addEventListener("click", () => decide("reject"));

    const compare = document.getElementById("compare");
    if (compare) compare.addEventListener("click", async () => {
      const other = document.getElementById("other").value;
      try {
        const [result, otherTask] = await Promise.all([
          api("GET", `/api/images/${imageId}/agreement/?task_a=${task.id}&task_b=${other}`),
          api("GET", `/api/tasks/${other}/`),
        ]);
        overlay = otherTask.annotations;
        draw();
        const f = v => (v === null ? "n/a" : v.toFixed(2));
        document.getElementById("agreement").innerHTML =
          `<p class="muted small">Dashed white: ${escapeHtml(otherTask.annotator)}. Instances matched at IoU ≥ ${result.iou_threshold}.</p>` +
          "<table><thead><tr><th>Label</th><th>A/B</th><th>IoU</th><th>Dice</th><th>F1</th></tr></thead><tbody>" +
          result.labels.map(l => `<tr><td>${escapeHtml(l.label)}</td><td>${l.reader_a_count}/${l.reader_b_count}</td>` +
            `<td>${f(l.iou)}</td><td>${f(l.dice)}</td><td>${f(l.f1)}</td></tr>`).join("") +
          "</tbody></table>";
      } catch (err) { say(err.message, true); }
    });
  }

  img.onload = async () => {
    draw();
    try {
      task = mode === "annotate"
        ? await api("POST", `/api/images/${imageId}/tasks/`)
        : await api("GET", `/api/tasks/${root.dataset.task}/`);
      annotations = task.annotations;
    } catch (err) { say(err.message, true); }
    renderList(); draw();
  };
})();
