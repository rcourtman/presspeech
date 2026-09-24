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
    "steady-notrun-count",
    "focus-copied-count",
    "focus-inserted-count",
    "focus-failed-count",
    "focus-notrun-count",
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

    doc.outcomes.set("steady-1", "unsafe");
    form.listeners.change();
    assert.match(status.textContent, /Stop testing after this result/);
    assert.equal(save.disabled, true);
    doc.outcomes.clear();

    Array.from({ length: 5 }, (_, index) => `steady-${index + 1}`)
      .concat(Array.from({ length: 3 }, (_, index) => `focus-${index + 1}`))
      .forEach((name) => doc.outcomes.set(name, "notrun"));
    form.listeners.change();
    assert.equal(copy.disabled, true, "zero observed checks must not create a shareable result");
    assert.equal(save.disabled, true);
    assert.equal(reportActions.hidden, true);
    assert.equal(summary.value, "");
    assert.match(status.textContent, /No checks were completed/);
    assert.equal(
      worksheet.summarise(Array(5).fill("notrun"), Array(3).fill("notrun")).reportable,
      false,
    );
    doc.outcomes.clear();

    ["pasted", "pasted", "recovered", "pasted", "recovered"].forEach(
      (value, index) => doc.outcomes.set(`steady-${index + 1}`, value),
    );
    ["copied", "failed", "copied"].forEach((value, index) =>
      doc.outcomes.set(`focus-${index + 1}`, value),
    );
    form.listeners.change();

    assert.equal(copy.disabled, false);
    assert.equal(save.disabled, false);
    assert.equal(reportActions.hidden, false);
    assert.match(summary.value, /Pasted once: 3/);
    assert.match(summary.value, /Recovered safely: 2/);
    assert.match(summary.value, /Incorrect or unsafe: 0\nNot completed: 0/);
    assert.equal(doc.getElementById("steady-notrun-count").textContent, "0");
    assert.match(summary.value, /Copied for manual paste without inserting anywhere: 2/);
    assert.match(summary.value, /Inserted into any field: 0/);
    assert.match(summary.value, /No insertion, but recovery failed: 1/);
    assert.match(summary.value, /Not completed: 0/);
    assert.equal(doc.getElementById("focus-failed-count").textContent, "1");
    assert.equal(doc.getElementById("focus-notrun-count").textContent, "0");
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
      /Fill in public versions, generic field type, and relevant conditions below before sharing\. Do not pool counts across different versions or conditions or infer a general success rate/,
    );
    assert.match(
      worksheet.formatReportDraft(summary.value),
      /open the rcourtman\/presspeech repository on GitHub and read SUPPORT\.md for current reporting routes/,
    );
    assert.match(
      worksheet.formatReportDraft(summary.value),
      /Relevant conditions \(keyboard layout\/input source if relevant\):/,
    );
    assert.doesNotMatch(worksheet.formatReportDraft(summary.value), /https?:\/\//);
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
    assert.match(
      blobs[0].parts[0],
      /rcourtman\/presspeech repository on GitHub and read SUPPORT\.md/,
    );
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

    ["unsafe", "notrun", "notrun", "notrun", "notrun"].forEach(
      (value, index) => doc.outcomes.set(`steady-${index + 1}`, value),
    );
    ["notrun", "notrun", "notrun"].forEach((value, index) =>
      doc.outcomes.set(`focus-${index + 1}`, value),
    );
    form.listeners.change();
    assert.equal(save.disabled, false, "an early safety stop must be reportable");
    assert.equal(doc.getElementById("steady-notrun-count").textContent, "4");
    assert.match(summary.value, /Incorrect or unsafe: 1\nNot completed: 4/);
    assert.match(summary.value, /Overall result: An incorrect or unsafe result occurred/);
    assert.match(worksheet.formatReportDraft(summary.value), /Not completed: 4/);
    await save.listeners.click();
    assert.equal(blobs.length, 3, "an early safety stop must be downloadable");
    assert.match(blobs[2].parts[0], /Incorrect or unsafe: 1\nNot completed: 4/);
    assert.match(blobs[2].parts[0], /Overall result: An incorrect or unsafe result occurred/);
    assert.doesNotMatch(blobs[2].parts[0], /amber rabbit|blue otter|dictated text/i);

    ["pasted", "unsafe", "pasted", "notrun", "notrun"].forEach(
      (value, index) => doc.outcomes.set(`steady-${index + 1}`, value),
    );
    ["notrun", "notrun", "notrun"].forEach((value, index) =>
      doc.outcomes.set(`focus-${index + 1}`, value),
    );
    form.listeners.change();
    assert.equal(copy.disabled, false, "observed counts remain locally copyable");
    assert.equal(save.disabled, false, "observed counts remain locally downloadable");
    assert.equal(reportActions.hidden, true, "a sequence past a safety stop is not report-ready");
    assert.equal(save.textContent, "Download noncomparable draft");
    assert.match(status.textContent, /relabel completed attempts as unrun/);
    assert.match(summary.value, /Protocol status: Noncomparable/);
    await copy.listeners.click();
    assert.match(status.textContent, /noncomparable count block|Noncomparable counts copied/);
    assert.match(status.textContent, /do not submit/i);
    await save.listeners.click();
    assert.equal(blobs.length, 4);
    assert.equal(doc.downloadLink.download, "presspeech-compatibility-noncomparable-draft.txt");
    assert.match(blobs[3].parts[0], /NONCOMPARABLE: completed checks followed a stop or Not completed slot/);
    assert.match(blobs[3].parts[0], /Pasted once: 2[\s\S]*Incorrect or unsafe: 1/);
    assert.match(status.textContent, /Do not submit it as a compatibility baseline/);

    ["pasted", "pasted", "pasted", "pasted", "pasted"].forEach(
      (value, index) => doc.outcomes.set(`steady-${index + 1}`, value),
    );
    ["copied", "inserted", "copied"].forEach((value, index) =>
      doc.outcomes.set(`focus-${index + 1}`, value),
    );
    form.listeners.change();
    assert.equal(reportActions.hidden, true, "a focus-safety insertion requires stopping");
    assert.match(summary.value, /Protocol status: Noncomparable/);
    assert.equal(
      worksheet.summarise(Array(5).fill("pasted"), ["copied", "inserted", "notrun"]).reportable,
      true,
      "a correctly recorded focus-safety stop remains reportable",
    );
    assert.equal(
      worksheet.summarise(["pasted", "unsafe", "notrun", "notrun", "notrun"],
        Array(3).fill("notrun")).reportable,
      true,
      "a correctly recorded steady-focus stop remains reportable",
    );
    assert.equal(
      worksheet.summarise(["pasted", "unsafe", "pasted", "notrun", "notrun"],
        Array(3).fill("notrun")).reportable,
      false,
      "continuing after an unsafe result is noncomparable",
    );
    assert.equal(
      worksheet.summarise(["pasted", "notrun", "pasted", "notrun", "notrun"],
        Array(3).fill("notrun")).reportable,
      false,
      "a completed check after an unrun slot is noncomparable",
    );
    assert.equal(
      worksheet.summarise(Array(5).fill("pasted"), ["copied", "notrun", "copied"]).reportable,
      false,
      "a focus-check gap cannot be called a comparable baseline",
    );

    globalThis.Blob = undefined;
    await save.listeners.click();
    assert.equal(summary.focused, true);
    assert.equal(summary.selected, true);
    assert.match(status.textContent, /download was unavailable/i);
    assert.match(status.textContent, /noncomparable block/);

    assert.equal(
      worksheet.summarise(Array(5).fill(null), Array(3).fill(null)).remaining,
      8,
    );
    const allPasted = Array(5).fill("pasted");
    assert.equal(
      worksheet.summarise(allPasted, ["copied", "failed", "copied"]).overall,
      "An incorrect or unsafe result occurred",
      "a completed recovery failure must not be reported as an unrun test",
    );
    assert.equal(
      worksheet.summarise(allPasted, ["copied", "notrun", "copied"]).overall,
      "Testing could not be completed",
    );
    assert.equal(
      worksheet.summarise(["pasted", "notrun", "notrun", "notrun", "notrun"],
        Array(3).fill("notrun")).overall,
      "Testing could not be completed",
      "unrun steady-focus slots without a completed failure are not a pass",
    );
    assert.equal(
      worksheet.summarise(["unsafe", ...Array(4).fill("notrun")],
        Array(3).fill("notrun")).overall,
      "An incorrect or unsafe result occurred",
      "a stopped safety failure takes precedence over unrun slots",
    );
    assert.equal(
      worksheet.summarise(allPasted, ["failed", "notrun", "notrun"]).overall,
      "An incorrect or unsafe result occurred",
      "completed failures take precedence over subsequent unrun slots",
    );
    assert.equal(
      worksheet.summarise(allPasted, Array(3).fill("copied")).overall,
      "All five steady-focus attempts pasted once; all three focus-change attempts recovered safely",
    );
    assert.equal(
      worksheet.summarise(["recovered", ...Array(4).fill("pasted")], Array(3).fill("copied")).overall,
      "Manual-paste recovery occurred during steady focus; no incorrect or unsafe result occurred",
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
