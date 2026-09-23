"use strict";

const assert = require("node:assert/strict");
const worksheet = require("../docs/compatibility-worksheet.js");

function makeElement(id) {
  return {
    id,
    value: "",
    disabled: false,
    hidden: false,
    textContent: "",
    listeners: {},
    addEventListener(type, listener) {
      this.listeners[type] = listener;
    },
    focus() {
      this.focused = true;
    },
    select() {
      this.selected = true;
    },
    click() {
      this.clicked = true;
    },
    remove() {
      this.removed = true;
      this.parentNode = null;
    },
  };
}

function makeDocument() {
  const ids = [
    "compatibility-worksheet",
    "worksheet-summary",
    "worksheet-status",
    "copy-worksheet-summary",
    "save-worksheet-summary",
    "worksheet-report-actions",
    "steady-pasted-count",
    "steady-recovered-count",
    "steady-unsafe-count",
    "focus-copied-count",
    "focus-inserted-count",
    "focus-other-count",
  ];
  const elements = new Map(ids.map((id) => [id, makeElement(id)]));
  const outcomes = new Map();
  const form = elements.get("compatibility-worksheet");
  form.querySelector = (selector) => {
    const match = selector.match(/name="([^"]+)"/);
    return match && outcomes.has(match[1]) ? { value: outcomes.get(match[1]) } : null;
  };

  const doc = {
    elements,
    outcomes,
    body: {
      appendChild(element) {
        element.parentNode = this;
        doc.downloadLink = element;
      },
    },
    getElementById(id) {
      return elements.get(id) || null;
    },
    createElement(tag) {
      assert.equal(tag, "a");
      const anchor = makeElement("download-link");
      anchor.click = () => {
        anchor.clicked = true;
      };
      return anchor;
    },
  };
  return doc;
}

async function main() {
  const originalBlob = globalThis.Blob;
  const originalURL = globalThis.URL;
  const originalSetTimeout = globalThis.setTimeout;
  const blobs = [];
  const revokedUrls = [];
  const timers = [];
  globalThis.Blob = class CapturedBlob {
    constructor(parts, options) {
      this.parts = parts;
      this.options = options;
      blobs.push(this);
    }
  };
  globalThis.URL = {
    createObjectURL(blob) {
      assert.equal(blob, blobs.at(-1));
      return "blob:compatibility-report";
    },
    revokeObjectURL(url) {
      revokedUrls.push(url);
    },
  };
  globalThis.setTimeout = (callback, delay) => {
    timers.push({ callback, delay });
    return timers.length;
  };

  try {
    const doc = makeDocument();
    const form = doc.getElementById("compatibility-worksheet");
    const summary = doc.getElementById("worksheet-summary");
    const status = doc.getElementById("worksheet-status");
    const copy = doc.getElementById("copy-worksheet-summary");
    const save = doc.getElementById("save-worksheet-summary");
    const reportActions = doc.getElementById("worksheet-report-actions");

    worksheet.mount(doc);
    assert.equal(form.hidden, false);
    assert.equal(copy.disabled, true);
    assert.equal(save.disabled, true);
    assert.equal(reportActions.hidden, true);

    ["pasted", "pasted", "recovered", "unsafe", "recovered"].forEach(
      (value, index) => doc.outcomes.set(`steady-${index + 1}`, value),
    );
    ["copied", "inserted", "other"].forEach((value, index) =>
      doc.outcomes.set(`focus-${index + 1}`, value),
    );
    form.listeners.change();

    assert.equal(copy.disabled, false);
    assert.equal(save.disabled, false);
    assert.equal(reportActions.hidden, false);
    assert.match(summary.value, /Pasted once: 2/);
    assert.match(summary.value, /Recovered safely: 2/);
    assert.match(summary.value, /Incorrect or unsafe: 1/);
    assert.match(summary.value, /Copied for manual paste without inserting anywhere: 1/);
    assert.match(summary.value, /Inserted into any field: 1/);
    assert.match(summary.value, /Other or not completed: 1/);
    assert.match(summary.value, /Overall result: An incorrect or unsafe result occurred/);
    assert.doesNotMatch(summary.value, /Platform|Target app|Operating-system version/);
    assert.match(
      worksheet.formatReportDraft(summary.value),
      /Platform \(macOS or Windows\):\nPresspeech version:\nOperating-system version:/,
    );
    assert.match(
      worksheet.formatReportDraft(summary.value),
      /Target app and public version:\nTarget class:\nGeneric field type:\nHardware \(optional; generic model only\):\nClipboard preservation during steady-focus check:/,
    );
    assert.match(
      worksheet.formatReportDraft(summary.value),
      /does not submit a report or notify maintainers; saved drafts are not monitored/,
    );
    assert.match(
      worksheet.formatReportDraft(summary.value),
      /https:\/\/github\.com\/rcourtman\/presspeech\/blob\/main\/SUPPORT\.md/,
    );
    assert.match(
      worksheet.formatReportDraft(summary.value),
      /keep this draft private and retry later/,
    );

    await save.listeners.click();
    assert.equal(blobs.length, 1);
    assert.deepEqual(
      blobs[0].parts,
      [`${worksheet.formatReportDraft(summary.value)}\n`],
    );
    assert.equal(blobs[0].options.type, "text/plain;charset=utf-8");
    assert.match(
      blobs[0].parts[0],
      /Target app and public version:\nTarget class:\nGeneric field type:/,
    );
    assert.match(blobs[0].parts[0], /saved drafts are not monitored/);
    assert.match(blobs[0].parts[0], /SUPPORT\.md/);
    assert.match(blobs[0].parts[0], /Five steady-focus results[\s\S]*Overall result:/);
    assert.doesNotMatch(blobs[0].parts[0], /amber rabbit|blue otter|dictated text/i);
    assert.equal(doc.downloadLink.download, "presspeech-compatibility-report-draft.txt");
    assert.equal(doc.downloadLink.href, "blob:compatibility-report");
    assert.equal(doc.downloadLink.clicked, true);
    assert.equal(doc.downloadLink.removed, true);
    assert.match(status.textContent, /no phrases or transcript/i);
    assert.equal(timers[0].delay, 1000);
    timers[0].callback();
    assert.deepEqual(revokedUrls, ["blob:compatibility-report"]);

    const originalCreateElement = doc.createElement;
    doc.createElement = () => { throw new Error("download unavailable"); };
    await save.listeners.click();
    assert.deepEqual(revokedUrls, ["blob:compatibility-report", "blob:compatibility-report"]);
    assert.match(status.textContent, /download was unavailable/i);
    doc.createElement = originalCreateElement;

    doc.outcomes.delete("focus-3");
    form.listeners.change();
    assert.equal(save.disabled, true);
    assert.equal(reportActions.hidden, true);
    await save.listeners.click();
    assert.equal(blobs.length, 2, "incomplete worksheets must not be downloaded");

    doc.outcomes.set("focus-3", "other");
    form.listeners.change();
    globalThis.Blob = undefined;
    await save.listeners.click();
    assert.equal(summary.focused, true);
    assert.equal(summary.selected, true);
    assert.match(status.textContent, /download was unavailable/i);

    assert.equal(
      worksheet.summarise(Array(5).fill(null), Array(3).fill(null)).remaining,
      8,
    );
    console.log("compatibility worksheet tests passed");
  } finally {
    globalThis.Blob = originalBlob;
    globalThis.URL = originalURL;
    globalThis.setTimeout = originalSetTimeout;
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
