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
  const STEADY_OUTCOMES = ["pasted", "recovered", "unsafe"];
  const FOCUS_OUTCOMES = ["copied", "inserted", "other"];

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
    let overall = "";
    if (remaining === 0) {
      if (steadyCounts.unsafe > 0 || focusCounts.inserted > 0) {
        overall = "An incorrect or unsafe result occurred";
      } else if (focusCounts.other > 0) {
        overall = "Testing could not be completed";
      } else if (steadyCounts.recovered > 0) {
        overall = "Manual-paste recovery occurred during steady focus; no incorrect or unsafe result occurred";
      } else {
        overall = "All five steady-focus attempts pasted once; all three focus-change attempts recovered safely";
      }
    }

    return {
      complete: remaining === 0,
      remaining,
      steady: steadyCounts,
      focus: focusCounts,
      overall,
    };
  }

  function formatSummary(result) {
    if (!result.complete) return "";
    return [
      "Five steady-focus results",
      `Pasted once: ${result.steady.pasted}`,
      `Recovered safely: ${result.steady.recovered}`,
      `Incorrect or unsafe: ${result.steady.unsafe}`,
      "",
      "Three focus-change results",
      `Copied for manual paste without inserting anywhere: ${result.focus.copied}`,
      `Inserted into any field: ${result.focus.inserted}`,
      `Other or not completed: ${result.focus.other}`,
      "",
      `Overall result: ${result.overall}`,
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
    const countOutputs = {
      "steady-pasted-count": ["steady", "pasted"],
      "steady-recovered-count": ["steady", "recovered"],
      "steady-unsafe-count": ["steady", "unsafe"],
      "focus-copied-count": ["focus", "copied"],
      "focus-inserted-count": ["focus", "inserted"],
      "focus-other-count": ["focus", "other"],
    };

    function selected(prefix, total) {
      return Array.from({ length: total }, (_, index) => {
        const input = form.querySelector(`input[name="${prefix}-${index + 1}"]:checked`);
        return input ? input.value : null;
      });
    }

    function render() {
      const result = summarise(selected("steady", 5), selected("focus", 3));
      for (const [id, [group, outcome]] of Object.entries(countOutputs)) {
        doc.getElementById(id).textContent = String(result[group][outcome]);
      }
      summary.value = formatSummary(result);
      copy.disabled = !result.complete;
      save.disabled = !result.complete;
      reportActions.hidden = !result.complete;
      status.textContent = result.complete
        ? `All eight outcomes recorded. Overall: ${result.overall}.`
        : `${result.remaining} ${result.remaining === 1 ? "outcome remains" : "outcomes remain"}.`;
    }

    async function copySummary() {
      if (!summary.value) return;
      let copied = false;
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
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
        ? "Aggregate counts and overall result copied. Review them before adding them to GitHub."
        : "Automatic copy was unavailable. The count and overall-result block is selected for manual copy.";
    }

    function saveSummary() {
      if (!summary.value) return;
      let link;
      let downloadUrl;
      try {
        const file = new Blob([`${summary.value}\n`], {
          type: "text/plain;charset=utf-8",
        });
        downloadUrl = URL.createObjectURL(file);
        link = doc.createElement("a");
        link.href = downloadUrl;
        link.download = "presspeech-compatibility-report.txt";
        link.hidden = true;
        doc.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(downloadUrl), 1000);
        status.textContent = "Aggregate-only download requested. The file contains no phrases or transcript.";
      } catch (_error) {
        if (link && link.parentNode) link.remove();
        if (downloadUrl) URL.revokeObjectURL(downloadUrl);
        summary.focus();
        summary.select();
        status.textContent = "File download was unavailable. The aggregate-only block is selected for manual copying.";
      }
    }

    form.hidden = false;
    form.addEventListener("change", render);
    form.addEventListener("reset", () => setTimeout(render, 0));
    copy.addEventListener("click", copySummary);
    save.addEventListener("click", saveSummary);
    render();
  }

  return { formatSummary, mount, summarise };
});
