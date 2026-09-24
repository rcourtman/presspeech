"use strict";

(function initialise(factory) {
  const worksheet = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = worksheet;
  }
  if (typeof document !== "undefined") {
    worksheet.mount(document);
  }
})(function createWorksheet() {
  const STEADY_OUTCOMES = ["pasted", "recovered", "unsafe", "notrun"];
  const FOCUS_OUTCOMES = ["copied", "inserted", "failed", "notrun"];

  function countsFor(values, allowed) {
    const counts = Object.fromEntries(allowed.map((value) => [value, 0]));
    for (const value of values) {
      if (value === null) continue;
      if (!allowed.includes(value)) {
        throw new TypeError(`Unknown worksheet outcome: ${value}`);
      }
      counts[value] += 1;
    }
    return counts;
  }

  function summarise(steady, focus) {
    if (!Array.isArray(steady) || steady.length !== 5) {
      throw new TypeError("The worksheet requires five steady-focus outcomes.");
    }
    if (!Array.isArray(focus) || focus.length !== 3) {
      throw new TypeError("The worksheet requires three focus-change outcomes.");
    }

    const steadyCounts = countsFor(steady, STEADY_OUTCOMES);
    const focusCounts = countsFor(focus, FOCUS_OUTCOMES);
    const remaining = [...steady, ...focus].filter((value) => value === null).length;
    const completed = (value) => value !== null && value !== "notrun";
    const steadyStopIndex = steady.findIndex((value) => value === "unsafe" || value === "notrun");
    const focusStopIndex = focus.findIndex((value) => value === "inserted" || value === "notrun");
    const stopCondition = steady.includes("unsafe") || focus.includes("inserted");
    const sequenceViolation =
      (steadyStopIndex >= 0 &&
        (steady.slice(steadyStopIndex + 1).some(completed) || focus.some(completed))) ||
      (focusStopIndex >= 0 && focus.slice(focusStopIndex + 1).some(completed));
    let overall = "";
    if (remaining === 0) {
      if (steadyCounts.unsafe > 0 || focusCounts.inserted > 0 || focusCounts.failed > 0) {
        overall = "An incorrect or unsafe result occurred";
      } else if (steadyCounts.notrun > 0 || focusCounts.notrun > 0) {
        overall = "Testing could not be completed";
      } else if (steadyCounts.recovered > 0) {
        overall = "Manual-paste recovery occurred during steady focus; no incorrect or unsafe result occurred";
      } else {
        overall = "All five steady-focus attempts pasted once; all three focus-change attempts recovered safely";
      }
    }

    return {
      complete: remaining === 0,
      reportable: remaining === 0 && !sequenceViolation,
      remaining,
      stopCondition,
      sequenceViolation,
      steady: steadyCounts,
      focus: focusCounts,
      overall,
    };
  }

  function formatSummary(result) {
    if (!result.complete) return "";
    const lines = [
      "Five steady-focus results",
      `Pasted once: ${result.steady.pasted}`,
      `Recovered safely: ${result.steady.recovered}`,
      `Incorrect or unsafe: ${result.steady.unsafe}`,
      `Not completed: ${result.steady.notrun}`,
      "",
      "Three focus-change results",
      `Copied for manual paste without inserting anywhere: ${result.focus.copied}`,
      `Inserted into any field: ${result.focus.inserted}`,
      `No insertion, but recovery failed: ${result.focus.failed}`,
      `Not completed: ${result.focus.notrun}`,
      "",
      `Overall result: ${result.overall}`,
    ];
    if (result.sequenceViolation) {
      lines.push(
        "",
        "Protocol status: Noncomparable — a completed outcome follows a stop or Not completed slot. " +
          "Do not submit these counts as an eight-check compatibility baseline.",
      );
    }
    return lines.join("\n");
  }

  function formatReportDraft(summaryText, reportable = true) {
    if (!summaryText) return "";
    return [
      "Presspeech target-app compatibility report draft",
      ...(reportable ? [] : [
        "NONCOMPARABLE: completed checks followed a stop or Not completed slot. Preserve actual " +
          "observations; do not relabel completed checks as unrun or submit this as a " +
          "compatibility baseline. Check SUPPORT.md for an appropriate reporting route.",
      ]),
      "Fill in public or generic context only. Do not add dictated or recognized text, audio, " +
        "clipboard contents, document/account/window names, private paths, credentials, or device serial numbers.",
      "",
      "This download does not submit a report or notify maintainers; saved drafts are not monitored. " +
        "Your browser or operating system controls the downloaded file.",
      "These counts describe only the exact tested setup. Fill in public versions, generic " +
        "field type, and relevant conditions below before sharing. Do not pool counts across " +
        "different versions or conditions or infer a general success rate.",
      "Before sharing, open the rcourtman/presspeech repository on GitHub and read " +
        "SUPPORT.md for current reporting routes; search for a matching report first.",
      "If no suitable route is available, keep this draft private and retry later. Do not post private data elsewhere to work around a restriction.",
      "",
      "Test date (optional):",
      "Platform (macOS or Windows):",
      "Presspeech version:",
      "Operating-system version:",
      "Target app and public version:",
      "Target class:",
      "Generic field type:",
      "Hardware (optional; generic model only):",
      "Clipboard preservation during steady-focus check:",
      "Relevant conditions (keyboard layout/input source if relevant):",
      "Known clipboard-change interruptions excluded:",
      "",
      summaryText,
    ].join("\n");
  }

  function mount(doc) {
    const form = doc.getElementById("compatibility-worksheet");
    if (!form) return;

    const summary = doc.getElementById("worksheet-summary");
    const status = doc.getElementById("worksheet-status");
    const copy = doc.getElementById("copy-worksheet-summary");
    const save = doc.getElementById("save-worksheet-summary");
    const reportActions = doc.getElementById("worksheet-report-actions");
    let latestResult;
    const countOutputs = {
      "steady-pasted-count": ["steady", "pasted"],
      "steady-recovered-count": ["steady", "recovered"],
      "steady-unsafe-count": ["steady", "unsafe"],
      "steady-notrun-count": ["steady", "notrun"],
      "focus-copied-count": ["focus", "copied"],
      "focus-inserted-count": ["focus", "inserted"],
      "focus-failed-count": ["focus", "failed"],
      "focus-notrun-count": ["focus", "notrun"],
    };

    function selected(prefix, total) {
      return Array.from({ length: total }, (_, index) => {
        const input = form.querySelector(`input[name="${prefix}-${index + 1}"]:checked`);
        return input ? input.value : null;
      });
    }

    function render() {
      const result = summarise(selected("steady", 5), selected("focus", 3));
      latestResult = result;
      for (const [id, [group, outcome]] of Object.entries(countOutputs)) {
        doc.getElementById(id).textContent = String(result[group][outcome]);
      }
      summary.value = formatSummary(result);
      copy.disabled = !result.complete;
      save.disabled = !result.complete;
      save.textContent = result.complete && !result.reportable
        ? "Download noncomparable draft"
        : "Download report draft";
      reportActions.hidden = !result.reportable;
      if (result.sequenceViolation) {
        status.textContent =
          "A completed check follows a stop or Not completed slot. Do not repeat unsafe checks or " +
          "relabel completed attempts as unrun. These counts are noncomparable; " +
          "download a local draft and check SUPPORT.md for a suitable reporting route.";
      } else if (result.complete) {
        status.textContent = `All eight check slots classified. Overall: ${result.overall}.`;
      } else if (result.stopCondition) {
        status.textContent =
          "Stop testing after this result. Mark every later unrun slot Not completed; " +
          `${result.remaining} ${result.remaining === 1 ? "outcome remains" : "outcomes remain"}.`;
      } else {
        status.textContent =
          `${result.remaining} ${result.remaining === 1 ? "outcome remains" : "outcomes remain"}.`;
      }
    }

    async function copySummary() {
      if (!summary.value) return;
      const reportable = latestResult.reportable;
      let copied = false;
      try {
        if (typeof navigator !== "undefined" && navigator.clipboard && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(summary.value);
          copied = true;
        }
      } catch (_error) {
        // The explicit selection fallback below still lets the user copy.
      }
      if (!copied) {
        summary.focus();
        summary.select();
        try {
          copied = Boolean(doc.execCommand && doc.execCommand("copy"));
        } catch (_error) {
          copied = false;
        }
      }
      status.textContent = copied
        ? (reportable
          ? "Aggregate counts and overall result copied. Review them before adding them to GitHub."
          : "Noncomparable counts copied. Do not submit them as an eight-check compatibility baseline.")
        : (reportable
          ? "Automatic copy was unavailable. The count and overall-result block is selected for manual copy."
          : "Automatic copy was unavailable. The noncomparable count block is selected for manual copy; do not submit it as a compatibility baseline.");
    }

    function saveSummary() {
      if (!summary.value) return;
      let link;
      let downloadUrl;
      try {
        const file = new Blob([`${formatReportDraft(summary.value, latestResult.reportable)}\n`], {
          type: "text/plain;charset=utf-8",
        });
        downloadUrl = URL.createObjectURL(file);
        link = doc.createElement("a");
        link.href = downloadUrl;
        link.download = latestResult.reportable
          ? "presspeech-compatibility-report-draft.txt"
          : "presspeech-compatibility-noncomparable-draft.txt";
        link.hidden = true;
        doc.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(downloadUrl), 1000);
        status.textContent = latestResult.reportable
          ? "Report draft download requested with aggregate counts and blank context fields. " +
            "It contains no phrases or transcript."
          : "Noncomparable draft download requested with observed counts and blank context fields. " +
            "Do not submit it as a compatibility baseline; it contains no phrases or transcript.";
      } catch (_error) {
        if (link && link.parentNode) link.remove();
        if (downloadUrl) URL.revokeObjectURL(downloadUrl);
        summary.focus();
        summary.select();
        status.textContent = latestResult.reportable
          ? "File download was unavailable. The aggregate-only block is selected for manual copying."
          : "File download was unavailable. The noncomparable block is selected for manual copying; do not submit it as a compatibility baseline.";
      }
    }

    form.hidden = false;
    form.addEventListener("change", render);
    form.addEventListener("reset", () => setTimeout(render, 0));
    copy.addEventListener("click", copySummary);
    save.addEventListener("click", saveSummary);
    render();
  }

  return { formatReportDraft, formatSummary, mount, summarise };
});
