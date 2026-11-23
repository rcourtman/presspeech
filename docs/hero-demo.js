(function () {
  var root = document.querySelector("[data-hero-demo]");
  if (!root) return;

  var hud = root.querySelector("[data-demo-hud]");
  var menubarMic = root.querySelector("[data-demo-mic]");
  var transcriptEl = root.querySelector("[data-demo-transcript]");
  var speedBadge = root.querySelector("[data-demo-speed]");
  var statusEl = root.querySelector("[data-demo-status]");
  var holdBtn = root.querySelector("[data-demo-hold]");
  var replayBtn = root.querySelector("[data-demo-replay]");
  var bars = Array.prototype.slice.call(root.querySelectorAll("[data-demo-bar]"));

  var phrases = [
    "This sentence was spoken, not typed.",
    "Send the updated notes to Alex before Friday.",
    "Keep the audio local and paste right at the cursor."
  ];
  var phraseIndex = 0;

  var reducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var state = "idle"; // idle | recording | transcribing | pasted
  var timers = [];
  var rafId = 0;
  var phase = 0;
  var autoPlaying = !reducedMotion;
  var userHolding = false;
  var activePhrase = phrases[0];
  var speechPulse = 0.2;

  function clearTimers() {
    while (timers.length) {
      window.clearTimeout(timers.pop());
    }
  }

  function stopWave() {
    if (rafId) {
      window.cancelAnimationFrame(rafId);
      rafId = 0;
    }
  }

  // Matches RecordingHUDView in swift/Sources/Presspeech/main.swift:
  // bell-curve envelope across bars driven by spoken-word bursts.
  function renderWave(now) {
    if (state !== "recording") return;
    phase += 0.24;
    speechPulse = Math.max(0.18, speechPulse * 0.93);
    var count = bars.length;
    var center = (count - 1) / 2;
    var denom = Math.max(center, 1);
    var envelope = Math.min(1, speechPulse + 0.14 * Math.abs(Math.sin(now / 95)));
    for (var i = 0; i < count; i++) {
      var dist = Math.abs(i - center) / denom;
      var env = Math.pow(Math.max(0, 1 - dist), 0.55);
      var ripple = (Math.sin(i * 0.74 + phase) + 1) / 2;
      var fine = (Math.sin(i * 1.73 - phase * 0.7) + 1) / 2;
      var motion = ripple * 0.68 + fine * 0.32;
      var quiet = 0.08 + 0.1 * env;
      var active = 0.16 + env * (0.5 + 0.5 * motion);
      var activity = Math.max(quiet, Math.min(1, quiet + envelope * active));
      var px = Math.round(3 + 21 * activity);
      bars[i].style.height = px + "px";
      bars[i].style.opacity = (0.32 + 0.68 * activity).toFixed(2);
    }
    rafId = window.requestAnimationFrame(renderWave);
  }

  function setStatus(label, quote) {
    if (!statusEl) return;
    statusEl.textContent = "";
    if (label) {
      var span = document.createElement("span");
      span.className = "sim-status__label";
      span.textContent = label;
      statusEl.appendChild(span);
    }
    if (quote) {
      var em = document.createElement("em");
      em.className = "sim-status__speech";
      em.textContent = quote;
      statusEl.appendChild(em);
    }
  }

  function setState(next, label, quote) {
    state = next;
    root.setAttribute("data-state", next);
    if (holdBtn) {
      holdBtn.setAttribute("aria-pressed", next === "recording" ? "true" : "false");
    }
    if (menubarMic) {
      menubarMic.setAttribute("data-recording", next === "recording" ? "true" : "false");
    }
    if (hud) {
      hud.setAttribute("data-mode", next);
    }
    if (typeof label === "string") {
      setStatus(label, quote);
    }
  }

  function resetComposer() {
    if (transcriptEl) {
      transcriptEl.textContent = "";
      transcriptEl.classList.remove("is-flash");
    }
    if (speedBadge) {
      speedBadge.classList.remove("is-visible");
    }
  }

  function showPasted(text) {
    if (transcriptEl) {
      transcriptEl.textContent = text;
      transcriptEl.classList.remove("is-flash");
      void transcriptEl.offsetWidth;
      transcriptEl.classList.add("is-flash");
    }
    if (speedBadge) {
      speedBadge.classList.add("is-visible");
    }
    setState("pasted", "Pasted at cursor (~100 ms local)");
  }

  // Reveal the spoken words outside the macOS frame in sync with the waveform
  // so silent viewers immediately see what is being spoken while the real HUD
  // stays true to Presspeech (waveform only, no streaming text inside the HUD).
  function scheduleSpokenCaption(phrase, onComplete) {
    var words = phrase.split(" ");
    var stepMs = Math.max(150, Math.min(220, Math.round(1450 / words.length)));
    var spoken = [];
    words.forEach(function (word, idx) {
      timers.push(window.setTimeout(function () {
        if (state !== "recording") return;
        spoken.push(word);
        speechPulse = 0.72 + Math.min(0.28, word.length * 0.035);
        setStatus("Speaking ", "\u201c" + spoken.join(" ") + "\u201d");
      }, idx * stepMs));
    });
    if (typeof onComplete === "function") {
      timers.push(window.setTimeout(onComplete, words.length * stepMs + 240));
    }
  }

  function startRecording(autoFinish) {
    stopWave();
    resetComposer();
    activePhrase = phrases[phraseIndex % phrases.length];
    phraseIndex += 1;
    speechPulse = 0.85;
    setState("recording", "Speaking ", "\u201c...\u201d");
    if (!reducedMotion) {
      rafId = window.requestAnimationFrame(renderWave);
    }
    scheduleSpokenCaption(activePhrase, autoFinish ? function () {
      if (autoPlaying && state === "recording") {
        finishRecording();
      }
    } : null);
  }

  function finishRecording() {
    clearTimers();
    stopWave();
    var phrase = activePhrase;
    setState("transcribing", "Transcribing...");
    timers.push(window.setTimeout(function () {
      // Hide capsule cleanly right as the sentence lands.
      showPasted(phrase);
      if (autoPlaying) {
        timers.push(window.setTimeout(runAutoCycle, 3000));
      }
    }, reducedMotion ? 60 : 200));
  }

  function cancelRecording() {
    clearTimers();
    stopWave();
    userHolding = false;
    autoPlaying = false;
    setState("idle", "Cancelled (Esc)");
  }

  function runAutoCycle() {
    clearTimers();
    stopWave();
    resetComposer();
    setState("idle", "Hold Right Option to speak");
    timers.push(window.setTimeout(function () {
      if (!autoPlaying) return;
      startRecording(true);
    }, 550));
  }

  if (holdBtn) {
    var beginHold = function (event) {
      if (event && event.type === "keydown") {
        if (event.repeat) return;
        if (event.key !== " " && event.key !== "Enter") return;
        event.preventDefault();
      }
      if (userHolding) return;
      autoPlaying = false;
      clearTimers();
      userHolding = true;
      startRecording(false);
    };

    var endHold = function (event) {
      if (event && event.type === "keyup") {
        if (event.key !== " " && event.key !== "Enter") return;
      }
      if (!userHolding) return;
      userHolding = false;
      finishRecording();
    };

    holdBtn.addEventListener("pointerdown", function (event) {
      if (event.button !== 0 && event.pointerType === "mouse") return;
      if (holdBtn.setPointerCapture) {
        try { holdBtn.setPointerCapture(event.pointerId); } catch (_) {}
      }
      beginHold(event);
    });
    holdBtn.addEventListener("pointerup", endHold);
    holdBtn.addEventListener("pointercancel", function () {
      if (userHolding) cancelRecording();
    });
    holdBtn.addEventListener("keydown", beginHold);
    holdBtn.addEventListener("keyup", endHold);
    holdBtn.addEventListener("blur", function () {
      if (userHolding) endHold();
    });
  }

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && state === "recording") {
      cancelRecording();
    }
  });

  if (replayBtn) {
    replayBtn.addEventListener("click", function () {
      userHolding = false;
      autoPlaying = !reducedMotion;
      if (reducedMotion) {
        clearTimers();
        resetComposer();
        var phrase = phrases[phraseIndex % phrases.length];
        phraseIndex += 1;
        showPasted(phrase);
      } else {
        runAutoCycle();
      }
    });
  }

  if (reducedMotion) {
    showPasted(phrases[0]);
    phraseIndex = 1;
  } else {
    runAutoCycle();
  }
})();
