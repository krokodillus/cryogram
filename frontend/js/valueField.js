// The one typed value control: a value's declared type decides its widget and how it reads back
import * as api from "./api.js";
import { escapeHtml } from "./util.js";

// A locked value is always one readonly text field, whatever its type
function lockedField(f, id) {

  if (f.secret) {

    const isSet = !(f.value === null && !f.value_set);
    return `<input id="${id}" type="text" readonly value=""${
      isSet ? ` class="is-masked"` : ""
    } placeholder="${isSet ? "••••••••" : "(not set)"}">`;
  }

  const text = lockedText(f);
  return `<input id="${id}" type="text" readonly value="${escapeHtml(text)}"${
    text.length > TITLE_FROM_CHARS ? ` title="${escapeHtml(text)}"` : ""}>`;
}

const TITLE_FROM_CHARS = 32;

// What the locked field shows: TRUE or FALSE for booleans, a file's name for a stored file
function lockedText(f) {
  if (f.type === "boolean") {
    if (f.value === true) return "TRUE";
    if (f.value === false) return "FALSE";
    return "";
  }
  if (f.type === "file") return f.fileName || displayText(f.value);
  return displayText(f.value);
}

// The right widget for a declared type: date picker, dropdown, upload, tick, masked secret
export function fieldControl(f, id) {
  if (f.locked) return lockedField(f, id);
  const v = escapeHtml(String(f.value ?? ""));
  if (f.secret) return `<input id="${id}" type="password" value="${v}" autocomplete="off">`;
  switch (f.type) {
    case "longtext":
    case "record":
    case "list":
      return `<textarea id="${id}" rows="6">${v}</textarea>`;
    case "number":
      return `<input id="${id}" type="number" step="any" value="${v}">`;
    case "date":
      return `<input id="${id}" type="date" value="${v}">`;
    case "boolean":

      return `<select id="${id}">
          <option value=""${f.value === true || f.value === false ? "" : " selected"}></option>
          <option value="TRUE"${f.value === true ? " selected" : ""}>TRUE</option>
          <option value="FALSE"${f.value === false ? " selected" : ""}>FALSE</option>
        </select>`;
    case "enum":

      return `<select id="${id}">${(f.options || []).map((o) => {
        const val = typeof o === "object" ? o.value : o;
        const lab = typeof o === "object" ? o.label : o;
        return `<option value="${escapeHtml(String(val))}" ${val === f.value ? "selected" : ""}>${escapeHtml(String(lab))}</option>`;
      }).join("")}</select>`;
    case "file":
      return `<input id="${id}" type="file">`;
    case "folder":

      return `<input id="${id}" type="text" value="${v}" readonly
                placeholder="pick a folder below">
              <div class="fs-host" data-for="${id}"></div>`;
    case "filepath":

      return `<input id="${id}" type="text" value="${v}" readonly
                placeholder="pick a file below">
              <div class="fs-host" data-for="${id}" data-want="file"></div>`;
    default:
      return `<input id="${id}" type="text" value="${v}">`;
  }
}

// Gives every folder field its picker once the form's HTML lands
export function wireFolderFields(root) {
  (root || document).querySelectorAll(".fs-host[data-for]").forEach((h) => {
    import("./fsPicker.js").then(({ renderFsPicker }) => renderFsPicker(h, (p) => {
      const input = document.getElementById(h.dataset.for);
      if (input) input.value = p;
    }, h.dataset.want || "folder"));
  });
}

// Reads a widget's value back in the declared type
export async function fieldValue(f, id) {
  const el = document.getElementById(id);
  if (!el) return "";
  switch (f.type) {
    case "number": return el.value === "" ? "" : Number(el.value);
    case "boolean":

      return el.value === "" ? "" : el.value === "TRUE";
    case "record":
    case "list": {
      try { return JSON.parse(el.value); } catch { return el.value; }
    }
    case "file": {
      const file = el.files && el.files[0];
      if (!file) return "";

      const wfl = (location.hash.match(/^#\/(?:project|workflow)\/([^/]+)/) || [])[1] || "";
      return api.uploadBlob(file, wfl);
    }
    default: return el.value;
  }
}

// How a stored value reads: booleans as yes and no, records as JSON
export function displayText(value) {
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (value && typeof value === "object") {
    try { return JSON.stringify(value); } catch { return String(value); }
  }
  return String(value ?? "");
}
