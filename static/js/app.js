(() => {
  const config = window.vibeConfig || {};
  const aspectOptions = config.aspects || {};
  const styleLabels = config.styles || {};
  const styleShortLabels = config.styleShort || {};
  const adConfig = config.ads || {};

  if (typeof window.launchRewardedAd !== "function") {
    window.launchRewardedAd = async (options = {}) => {
      if (!adConfig.client || !adConfig.rewardedSlot) {
        console.warn("[Free AutoFrame] Rewarded ad slot not configured; ignoring request.");
        return { status: "unconfigured" };
      }
      const detail = {
        slot: adConfig.rewardedSlot,
        client: adConfig.client,
        options,
        timestamp: Date.now(),
      };
      window.dispatchEvent(new CustomEvent("rewarded-ad-request", { detail }));
      return { status: "dispatched", detail };
    };
  }

  const apiProcessUrl = config.apiProcess || "/api/process";

  const form = document.getElementById("batch-form");
  const fileInput = document.getElementById("file-picker");
  const fileButton = document.getElementById("file-button");
  const fileInputWrapper = document.querySelector(".file-input");
  const ratioInputs = Array.from(document.querySelectorAll('input[name="ratio"]'));
  const styleInputs = Array.from(form.querySelectorAll('input[name="style"]'));
  const styleOptions = Array.from(document.querySelectorAll('.style-option'));
  const selectionSummary = document.getElementById("selection-summary");
  const asyncButton = document.getElementById("async-button");
  const submitButton = document.getElementById("submit-button");
  const resetButton = document.getElementById("reset-button");
  const flashBox = document.getElementById("dynamic-flash");
  const progressSection = document.getElementById("progress-section");
  const progressBar = document.getElementById("progress-bar");
  const progressTitle = document.getElementById("progress-title");
  const progressSubtitle = document.getElementById("progress-subtitle");
  const progressPercent = document.getElementById("progress-percent");
  const resultsSection = document.getElementById("results-section");
  const resultsList = document.getElementById("results-list");
  const downloadAllLink = document.getElementById("download-all");

  const presetSelect = document.getElementById("naming-preset");
  const customPatternWrapper = document.getElementById("custom-pattern-wrapper");
  const customPatternInput = document.getElementById("naming-custom-pattern");
  const autoCleanCheck = document.getElementById("opt-auto-clean");
  const keepTokensCheck = document.getElementById("opt-keep-tokens");
  const addSequenceCheck = document.getElementById("opt-add-sequence");
  const appendDateCheck = document.getElementById("opt-append-date");
  const labelModeInputs = Array.from(document.querySelectorAll('input[name="label-mode"]'));
  const baseEditor = document.getElementById("base-editor");
  const baseOverridesInput = document.getElementById("base-overrides-input");
  const namingModeInput = document.getElementById("naming-mode-input");
  const patternPreview = document.getElementById("pattern-preview");
  const modeAutoButton = document.getElementById("naming-mode-auto");
  const modeCustomButton = document.getElementById("naming-mode-custom");
  const customConfigSection = document.getElementById("custom-config");
  const autoSummary = document.getElementById("naming-auto-summary");

  const allowedExtensions = ["mp4", "mov", "m4v", "mkv"];
  const videoExtensions = allowedExtensions.map((ext) => `.${ext}`);
  const maxFiles = 10;
  const maxRatios = 3;
  const NAMING_STORAGE_KEY = "vibeNamingOptions_v2";
  const defaultAspect = { short: "1x1", label: "1:1 Square", size: [1080, 1080] };

  const namingDefaults = {
    mode: "auto",
    preset: "base_ratio",
    customPattern: "",
    autoClean: true,
    keepTokens: false,
    addSequence: false,
    appendDate: false,
    labelMode: "short",
  };

  let namingOptions = loadNamingOptions();
  let baseOverrides = {};
  let baseOverrideFlags = {};
  let customCache = null;
  let customOverridesCache = {};
  let customOverrideFlagsCache = {};
  let currentFiles = [];
  let formDisabled = false;
  let progressPoll = null;
  let currentJobId = null;

  if (namingOptions.mode === "custom") {
    customCache = { ...namingOptions };
  }

  let progressInterval = null;
  let displayedProgress = 0;
  let targetProgress = 0;

  const namingControls = [
    presetSelect,
    customPatternInput,
    autoCleanCheck,
    keepTokensCheck,
    addSequenceCheck,
    appendDateCheck,
    ...labelModeInputs,
  ];

  const styleSelector = () => form.querySelector('input[name="style"]:checked')?.value || "blur";

  function setCustomControlsEnabled(enabled) {
    namingControls.forEach((control) => {
      if (control) {
        control.disabled = !enabled;
      }
    });
    baseEditor.querySelectorAll("input").forEach((input) => {
      input.disabled = !enabled;
    });
  }

  function updateStyleCards() {
    styleOptions.forEach((option) => {
      const input = option.querySelector('input[type="radio"]');
      if (!input) return;
      option.classList.toggle("checked", input.checked);
      const card = option.querySelector(".style-card");
      if (card) {
        card.classList.toggle("selected", input.checked);
      }
    });
  }

  function updateOverallProgress(processedCount, partial, total, status, subtitle) {
    const safeTotal = Math.max(total, 1);
    const overall = Math.max(0, Math.min(1, (processedCount + partial) / safeTotal));
    updateProgressStatus(overall * 100, status, subtitle);
  }

  function stopProcessingPolling() {
    if (progressPoll) {
      clearInterval(progressPoll);
      progressPoll = null;
    }
  }

function startProcessingPolling(jobId, fileName, index, total, processedBaseline) {
    stopProcessingPolling();
    currentJobId = jobId;
    progressPoll = setInterval(async () => {
      try {
        const response = await fetch(`/progress/${encodeURIComponent(jobId)}`);
        if (!response.ok) {
          return;
        }
        const payload = await response.json();
        const serverProgress = Math.max(0, Math.min(1, payload.progress || 0));
        const statusLabel = payload.status === "done" ? "Finalising" : "Processing";
        updateOverallProgress(
          processedBaseline,
          serverProgress,
          total,
          `${statusLabel} ${fileName}`,
          `${index + 1} of ${total} clip(s) rendering (${Math.round(serverProgress * 100)}%)`
        );
        if (payload.status === "done" || serverProgress >= 1) {
          stopProcessingPolling();
        }
      } catch (error) {
        // ignore polling errors, will retry
      }
    }, 500);
  }

  function loadNamingOptions() {
    try {
      const stored = localStorage.getItem(NAMING_STORAGE_KEY);
      const parsed = stored ? JSON.parse(stored) : {};
      const merged = { ...namingDefaults, ...(parsed || {}) };
      const allowedPresets = new Set(["base_ratio", "base_ratio_style", "base_dash_ratio", "base_style", "custom"]);
      if (!allowedPresets.has(merged.preset)) {
        merged.preset = "base_ratio";
      }
      merged.mode = merged.mode === "custom" ? "custom" : "auto";
      return merged;
    } catch (error) {
      return { ...namingDefaults };
    }
  }

  function saveNamingOptions() {
    if (namingOptions.mode === "custom") {
      customCache = { ...namingOptions };
      customOverridesCache = { ...baseOverrides };
      customOverrideFlagsCache = { ...baseOverrideFlags };
    }
    localStorage.setItem(NAMING_STORAGE_KEY, JSON.stringify(namingOptions));
  }

  function applyNamingOptionsToControls() {
    const mode = namingOptions.mode === "custom" ? "custom" : "auto";
    namingOptions.mode = mode;

    const isAuto = mode === "auto";
    if (modeAutoButton && modeCustomButton) {
      modeAutoButton.classList.toggle("active", isAuto);
      modeCustomButton.classList.toggle("active", !isAuto);
      modeAutoButton.setAttribute("aria-pressed", String(isAuto));
      modeCustomButton.setAttribute("aria-pressed", String(!isAuto));
    }

    if (autoSummary) {
      autoSummary.classList.toggle("hidden", !isAuto);
    }
    if (customConfigSection) {
      customConfigSection.classList.toggle("hidden", isAuto);
    }
    if (namingModeInput) {
      namingModeInput.value = namingOptions.mode;
    }

    presetSelect.value = namingOptions.preset;
    customPatternInput.value = namingOptions.customPattern || "";
    customPatternWrapper.classList.toggle("hidden", presetSelect.value !== "custom");
    autoCleanCheck.checked = namingOptions.autoClean;
    keepTokensCheck.checked = namingOptions.keepTokens;
    addSequenceCheck.checked = namingOptions.addSequence;
    appendDateCheck.checked = namingOptions.appendDate;
    labelModeInputs.forEach((input) => {
      input.checked = input.value === namingOptions.labelMode;
    });

    setCustomControlsEnabled(!formDisabled && mode === "custom");
  }

  function updateOverridesInput() {
    const payload = {};
    Object.entries(baseOverrides).forEach(([key, value]) => {
      if (value) {
        payload[key] = value;
      }
    });
    baseOverridesInput.value = JSON.stringify(payload);
  }

  function stripExtensions(name) {
    let base = name;
    let changed = true;
    while (changed && base.length) {
      changed = false;
      const lower = base.toLowerCase();
      for (const ext of videoExtensions) {
        if (lower.endsWith(ext)) {
          base = base.slice(0, -ext.length);
          changed = true;
          break;
        }
      }
    }
    return base;
  }

  function removeNamingTokensJS(value) {
    let cleaned = value
      .replace(/[\[\]\(\)\{\}<>]/g, " ")
      .replace(/[\*_]/g, " ")
      .replace(/\s+by\s+/gi, "x");
    const resolutionRegex = /\b\d{3,4}\s*(?:x|×|\*|\/|:|-|_|\.)\s*\d{3,4}(?:\s*px)?\b/gi;
    const aspectNumericRegex = /\b\d+(?:\.\d+)?\s*(?:x|×|:|\/|x)\s*\d+(?:\.\d+)?\b/gi;
    const aspectWordRegex = /\b(portrait|vertical|landscape|square)\b/gi;
    cleaned = cleaned.replace(resolutionRegex, " ");
    cleaned = cleaned.replace(aspectNumericRegex, " ");
    cleaned = cleaned.replace(aspectWordRegex, " ");
    cleaned = cleaned.replace(/-{2,}/g, " ");
    cleaned = cleaned.replace(/_+/g, " ");
    cleaned = cleaned.replace(/\s+/g, " ");
    return cleaned;
  }

  function sanitizeBase(value) {
    let sanitized = value.replace(/["'`~!@#$%^&*+=\[\]{}|\\:;"<>?,\s]+/g, "_");
    sanitized = sanitized.replace(/_+/g, "_");
    sanitized = sanitized.replace(/^-+|-+$/g, "");
    sanitized = sanitized.replace(/^_+|_+$/g, "");
    sanitized = sanitized.slice(0, 120) || "clip";
    return sanitized;
  }

  function deriveBaseName(fileName) {
    const baseName = stripExtensions((fileName || "").split(/[\\/]/).pop() || "");
    const sanitized = sanitizeBase(baseName || "clip");
    if (namingOptions.autoClean && !namingOptions.keepTokens) {
      const cleaned = removeNamingTokensJS(baseName);
      return sanitizeBase(cleaned || sanitized);
    }
    return sanitized;
  }

  function rebuildBaseEditor(files) {
    currentFiles = files.slice();
    if (namingOptions.mode !== "custom") {
      baseOverrides = {};
      baseOverrideFlags = {};
      baseEditor.classList.add("empty");
      baseEditor.innerHTML = "<p>Switch to Custom to rename individual files.</p>";
      updateOverridesInput();
      return;
    }

    if (!files.length) {
      baseOverrides = {};
      baseOverrideFlags = {};
      customOverridesCache = {};
      customOverrideFlagsCache = {};
      baseEditor.classList.add("empty");
      baseEditor.innerHTML = "<p>No files selected yet.</p>";
      updateOverridesInput();
      return;
    }

    if (Object.keys(customOverridesCache).length) {
      baseOverrides = { ...customOverridesCache };
    }
    if (Object.keys(customOverrideFlagsCache).length) {
      baseOverrideFlags = { ...customOverrideFlagsCache };
    }

    const nextOverrides = {};
    const nextFlags = {};
    baseEditor.classList.remove("empty");
    const fragment = document.createDocumentFragment();

    files.forEach((file) => {
      const name = file.name;
      const derivedDefault = deriveBaseName(name);
      const existing = baseOverrides[name];
      const sanitizedExisting = existing ? sanitizeBase(existing) : "";
      let value = derivedDefault;
      let isManual = false;

      if (sanitizedExisting && baseOverrideFlags[name]) {
        if (sanitizedExisting !== derivedDefault) {
          value = sanitizedExisting;
          isManual = true;
        } else {
          value = derivedDefault;
          isManual = false;
        }
      } else if (sanitizedExisting && !baseOverrideFlags[name]) {
        value = sanitizedExisting;
      }

      nextOverrides[name] = value;
      nextFlags[name] = isManual;

      const row = document.createElement("div");
      row.className = "base-editor-row";
      row.dataset.filename = name;

      const label = document.createElement("div");
      label.className = "file-label";
      label.innerHTML = `<code>${name}</code>`;

      const input = document.createElement("input");
      input.type = "text";
      input.value = value;
      input.maxLength = 120;
      input.addEventListener("input", () => {
        const sanitized = sanitizeBase(input.value);
        const derived = deriveBaseName(name);
        const finalValue = sanitized || derived;
        input.value = sanitized;
        nextOverrides[name] = finalValue;
        baseOverrides[name] = finalValue;
        const manual = finalValue !== derived;
        nextFlags[name] = manual;
        baseOverrideFlags[name] = manual;
        customOverridesCache[name] = finalValue;
        customOverrideFlagsCache[name] = manual;
        updateOverridesInput();
        updatePatternPreview();
      });

      row.appendChild(label);
      row.appendChild(input);
      fragment.appendChild(row);
    });

    baseOverrides = nextOverrides;
    baseOverrideFlags = nextFlags;
    baseEditor.innerHTML = "";
    baseEditor.appendChild(fragment);
    updateOverridesInput();
    customOverridesCache = { ...baseOverrides };
    customOverrideFlagsCache = { ...baseOverrideFlags };
  }

  function getSelectedRatios() {
    return ratioInputs.filter((input) => input.checked).map((input) => input.value);
  }

  function getPatternString() {
    if (namingOptions.preset === "custom" && namingOptions.customPattern.trim()) {
      return namingOptions.customPattern.trim();
    }
    switch (namingOptions.preset) {
      case "base_ratio":
        return "{base_clean}_{ratio}";
      case "base_dash_ratio":
        return "{base_clean}-{ratio}";
      case "base_style":
        return "{base_clean}__{style}";
      case "base_ratio_style":
        return "{base_clean}__{ratio}__{style}";
      default:
        return "{base_clean}_{ratio}";
    }
  }

  function formatPattern(pattern, tokens) {
    return pattern.replace(/\{(\w+)\}/g, (_, key) => (tokens[key] ?? ""));
  }

  function buildPreviewTokens() {
    const selectedRatios = getSelectedRatios();
    const sampleKey = selectedRatios[0] || "square";
    const ratioMeta = aspectOptions[sampleKey] || aspectOptions.square || defaultAspect;
    const ratioToken = namingOptions.labelMode === "friendly" ? ratioMeta.label : ratioMeta.short;
    const styleKey = styleSelector();
    const styleToken = namingOptions.labelMode === "friendly" ? (styleLabels[styleKey] || styleKey) : (styleShortLabels[styleKey] || styleKey);
    const date = new Date().toISOString().slice(0, 10);

    let baseSource = "ExampleClip";
    if (currentFiles.length) {
      const sampleFile = currentFiles[0];
      const fileName = typeof sampleFile === "string" ? sampleFile : sampleFile.name || "";
      if (fileName) {
        if (namingOptions.mode === "custom" && baseOverrides[fileName]) {
          baseSource = sanitizeBase(baseOverrides[fileName]);
        } else {
          baseSource = sanitizeBase(deriveBaseName(fileName));
        }
      }
    }

    const size = Array.isArray(ratioMeta.size) ? ratioMeta.size : [0, 0];
    return {
      base: baseSource,
      base_clean: baseSource,
      ratio: ratioToken || sampleKey,
      style: styleToken,
      w: size[0],
      h: size[1],
      date,
      seq: "001",
      ext: "mp4",
    };
  }

  function updatePatternPreview() {
    const pattern = getPatternString();
    const tokens = buildPreviewTokens();
    let formatted = formatPattern(pattern, tokens).replace(/[\s_]+$/, "");
    if (!formatted) {
      formatted = `${tokens.base_clean}_${tokens.ratio}`;
    }
    const normalized = pattern.toLowerCase();
    if (namingOptions.addSequence && !normalized.includes("{seq}") && tokens.seq) {
      formatted = `${formatted}__${tokens.seq}`;
    }
    if (namingOptions.appendDate && !normalized.includes("{date}") && tokens.date) {
      formatted = `${formatted}__${tokens.date}`;
    }
    if (!formatted.toLowerCase().endsWith(`.${tokens.ext}`)) {
      formatted = `${formatted}.${tokens.ext}`;
    }
    patternPreview.textContent = formatted;
    if (autoSummary) {
      if (namingOptions.mode === "auto") {
        autoSummary.textContent = `Next filename preview: ${formatted}`;
      } else {
        autoSummary.textContent = "Switch back to Auto to let us manage file naming automatically.";
      }
    }
  }

  function setNamingMode(mode) {
    const targetMode = mode === "custom" ? "custom" : "auto";

    if (targetMode === "auto" && namingOptions.mode === "custom") {
      customCache = { ...namingOptions, mode: "custom" };
      customOverridesCache = { ...baseOverrides };
      customOverrideFlagsCache = { ...baseOverrideFlags };
    }

    if (targetMode === "custom") {
      if (customCache && customCache.mode === "custom") {
        namingOptions = { ...customCache };
      } else {
        namingOptions = { ...namingDefaults, mode: "custom" };
      }
      baseOverrides = { ...customOverridesCache };
      baseOverrideFlags = { ...customOverrideFlagsCache };
    } else {
      namingOptions = { ...namingDefaults, mode: "auto" };
      baseOverrides = {};
      baseOverrideFlags = {};
      updateOverridesInput();
    }

    saveNamingOptions();
    applyNamingOptionsToControls();
    rebuildBaseEditor(currentFiles);
    updatePatternPreview();
    updateStyleCards();
  }

  function clearFlash() {
    flashBox.textContent = "";
    flashBox.classList.add("hidden");
  }

  function showFlash(message, tone = "error") {
    flashBox.textContent = message;
    flashBox.classList.remove("hidden");
    flashBox.style.background = tone === "success"
      ? "rgba(34, 197, 94, 0.12)"
      : "rgba(248, 113, 113, 0.08)";
    flashBox.style.borderColor = tone === "success"
      ? "rgba(34, 197, 94, 0.35)"
      : "rgba(248, 113, 113, 0.3)";
    flashBox.style.color = tone === "success" ? "#166534" : "#b91c1c";
  }

  function renderProgress() {
    const clamped = Math.max(0, Math.min(100, displayedProgress));
    progressBar.value = clamped;
    progressPercent.textContent = `${Math.round(clamped)}%`;
  }

  function startProgressAnimation() {
    if (progressInterval) return;
    progressInterval = setInterval(() => {
      if (displayedProgress < targetProgress) {
        const delta = Math.max(0.6, (targetProgress - displayedProgress) * 0.25);
        displayedProgress = Math.min(targetProgress, displayedProgress + delta);
      } else if (displayedProgress < 98) {
        const driftCap = Math.min(targetProgress + 10, 98);
        displayedProgress = Math.min(driftCap, displayedProgress + (Math.random() * 1.4 + 0.2));
      }
      renderProgress();
      if (displayedProgress >= 100 && targetProgress >= 100) {
        stopProgressAnimation();
      }
    }, 200);
  }

  function stopProgressAnimation() {
    if (progressInterval) {
      clearInterval(progressInterval);
      progressInterval = null;
    }
  }

  function initializeProgress(total) {
    stopProcessingPolling();
    displayedProgress = 4;
    targetProgress = 6;
    progressTitle.textContent = "Preparing batch…";
    progressSubtitle.textContent = `0 of ${total} clip(s) rendered`;
    progressSection.classList.remove("hidden");
    renderProgress();
    startProgressAnimation();
  }

  function updateProgressStatus(percent, title, subtitle) {
    targetProgress = Math.max(targetProgress, Math.min(100, percent));
    progressTitle.textContent = title;
    progressSubtitle.textContent = subtitle;
    if (percent >= 100) {
      displayedProgress = Math.max(displayedProgress, 98);
    }
    renderProgress();
    startProgressAnimation();
  }

  function resetProgress() {
    stopProgressAnimation();
    displayedProgress = 0;
    targetProgress = 0;
    progressBar.value = 0;
    progressPercent.textContent = "0%";
    progressTitle.textContent = "Waiting to start";
    progressSubtitle.textContent = "No files queued yet.";
    progressSection.classList.add("hidden");
  }

  function clearResults() {
    resultsList.innerHTML = "";
    resultsSection.classList.add("hidden");
    downloadAllLink.setAttribute("aria-disabled", "true");
    downloadAllLink.href = "#";
  }

  function summarizeSelection(files) {
    if (!files.length) {
      selectionSummary.textContent = "No files selected yet.";
    } else if (files.length === 1) {
      selectionSummary.textContent = files[0].name;
    } else {
      selectionSummary.textContent = `${files.length} files selected.`;
    }
  }

  function sanitizeFileList(files) {
    const picked = [];
    const skipped = [];
    for (const file of files) {
      if (!file || !file.name) continue;
      const ext = file.name.split(".").pop().toLowerCase();
      if (!allowedExtensions.includes(ext)) {
        skipped.push(file.name);
        continue;
      }
      picked.push(file);
    }
    const notices = [];
    if (skipped.length) {
      notices.push(`Skipped unsupported files: ${skipped.join(", ")}`);
    }
    if (picked.length > maxFiles) {
      notices.push(`Max ${maxFiles} videos can be selected at once. You picked ${picked.length}.`);
    }
    const usable = picked.slice(0, maxFiles);
    summarizeSelection(usable);
    rebuildBaseEditor(usable);
    updatePatternPreview();
    return { files: usable, notices };
  }

  function updateFileInputFiles(files) {
    if (!fileInput || typeof DataTransfer === "undefined") return;
    const dataTransfer = new DataTransfer();
    files.forEach((file) => dataTransfer.items.add(file));
    fileInput.files = dataTransfer.files;
  }

  function processSelectedFiles(rawFiles, { announce = true } = {}) {
    const review = sanitizeFileList(rawFiles);
    updateFileInputFiles(review.files);
    if (announce) {
      if (review.notices.length) {
        showFlash(review.notices.join(" • "));
      } else {
        clearFlash();
      }
    }
    return review;
  }

  function getSelectedRatiosSafe() {
    const selected = getSelectedRatios();
    if (selected.length > maxRatios) {
      return selected.slice(0, maxRatios);
    }
    return selected;
  }

  function enforceRatioLimit(changedInput) {
    const selected = getSelectedRatios();
    if (selected.length > maxRatios) {
      changedInput.checked = false;
      showFlash(`Pick up to ${maxRatios} aspect ratios per batch.`);
    }
    updatePatternPreview();
  }

  function appendResultCard(result) {
    const card = document.createElement("article");
    card.className = "result-card";

    const meta = document.createElement("div");
    meta.className = "result-meta";
    const title = document.createElement("h3");
    title.textContent = result.original_name;
    const styleText = document.createElement("span");
    styleText.textContent = result.style_label;
    meta.appendChild(title);
    meta.appendChild(styleText);

    if (Array.isArray(result.ratio_labels) && result.ratio_labels.length) {
      const tags = document.createElement("div");
      tags.className = "result-badges";
      result.ratio_labels.forEach((label) => {
        const chip = document.createElement("span");
        chip.className = "aspect-chip";
        chip.textContent = label;
        tags.appendChild(chip);
      });
      meta.appendChild(tags);
    }

    const list = document.createElement("ul");
    list.className = "result-downloads";
    result.outputs.forEach((output) => {
      const item = document.createElement("li");
      const link = document.createElement("a");
      link.className = "secondary-button";
      link.href = output.url;
      link.textContent = output.label;
      link.setAttribute("download", output.filename);
      item.appendChild(link);
      list.appendChild(item);
    });

    card.appendChild(meta);
    card.appendChild(list);
    resultsList.appendChild(card);
  }

  function setNamingDisabled(disabled) {
    setCustomControlsEnabled(!disabled && namingOptions.mode === "custom");
  }

  function toggleForm(disabled) {
    if (asyncButton) {
      asyncButton.disabled = disabled;
    }
    submitButton.disabled = disabled;
    resetButton.disabled = disabled;
    fileInput.disabled = disabled;
    fileButton.disabled = disabled;
    if (modeAutoButton) {
      modeAutoButton.disabled = disabled;
    }
    if (modeCustomButton) {
      modeCustomButton.disabled = disabled;
    }
    ratioInputs.forEach((input) => {
      input.disabled = disabled;
    });
    styleInputs.forEach((input) => {
      input.disabled = disabled;
    });
    formDisabled = disabled;
    setNamingDisabled(disabled);
  }

  if (modeAutoButton) {
    modeAutoButton.addEventListener("click", () => setNamingMode("auto"));
  }

  if (modeCustomButton) {
    modeCustomButton.addEventListener("click", () => setNamingMode("custom"));
  }

  ratioInputs.forEach((input) => {
    input.addEventListener("change", (event) => {
      enforceRatioLimit(event.target);
    });
  });

  styleInputs.forEach((input) => {
    input.addEventListener("change", () => {
      updateStyleCards();
      updatePatternPreview();
    });
  });

  fileInput.addEventListener("change", () => {
    const files = Array.from(fileInput.files || []);
    processSelectedFiles(files);
  });

  fileButton.addEventListener("click", () => fileInput.click());

  if (fileInputWrapper) {
    const preventDefaults = (event) => {
      event.preventDefault();
      event.stopPropagation();
    };

    ["dragenter", "dragover"].forEach((eventName) => {
      fileInputWrapper.addEventListener(eventName, (event) => {
        preventDefaults(event);
        if (fileInput && fileInput.disabled) {
          return;
        }
        fileInputWrapper.classList.add("is-dragover");
      });
    });

    ["dragleave", "dragend"].forEach((eventName) => {
      fileInputWrapper.addEventListener(eventName, (event) => {
        preventDefaults(event);
        if (fileInput && fileInput.disabled) {
          fileInputWrapper.classList.remove("is-dragover");
          return;
        }
        if (eventName === "dragleave") {
          const related = event.relatedTarget;
          if (related && fileInputWrapper.contains(related)) {
            return;
          }
        }
        fileInputWrapper.classList.remove("is-dragover");
      });
    });

    fileInputWrapper.addEventListener("drop", (event) => {
      preventDefaults(event);
      fileInputWrapper.classList.remove("is-dragover");
      if (fileInput && fileInput.disabled) {
        return;
      }
      const files = Array.from(event.dataTransfer?.files || []);
      if (!files.length) {
        return;
      }
      processSelectedFiles(files);
    });

    fileInputWrapper.addEventListener("click", (event) => {
      if (fileInput && fileInput.disabled) {
        return;
      }
      const isButton = event.target.closest("button");
      if (isButton) {
        return;
      }
      fileInput.click();
    });
  }

  presetSelect.addEventListener("change", () => {
    namingOptions.preset = presetSelect.value;
    if (namingOptions.preset !== "custom") {
      namingOptions.customPattern = "";
      customPatternInput.value = "";
    }
    customPatternWrapper.classList.toggle("hidden", namingOptions.preset !== "custom");
    saveNamingOptions();
    updatePatternPreview();
  });

  customPatternInput.addEventListener("input", () => {
    namingOptions.customPattern = customPatternInput.value;
    saveNamingOptions();
    updatePatternPreview();
  });

  autoCleanCheck.addEventListener("change", () => {
    namingOptions.autoClean = autoCleanCheck.checked;
    if (namingOptions.autoClean) {
      namingOptions.keepTokens = false;
      keepTokensCheck.checked = false;
    }
    saveNamingOptions();
    rebuildBaseEditor(currentFiles);
    updatePatternPreview();
  });

  keepTokensCheck.addEventListener("change", () => {
    namingOptions.keepTokens = keepTokensCheck.checked;
    if (namingOptions.keepTokens) {
      namingOptions.autoClean = false;
      autoCleanCheck.checked = false;
    }
    saveNamingOptions();
    rebuildBaseEditor(currentFiles);
    updatePatternPreview();
  });

  addSequenceCheck.addEventListener("change", () => {
    namingOptions.addSequence = addSequenceCheck.checked;
    saveNamingOptions();
    updatePatternPreview();
  });

  appendDateCheck.addEventListener("change", () => {
    namingOptions.appendDate = appendDateCheck.checked;
    saveNamingOptions();
    updatePatternPreview();
  });

  labelModeInputs.forEach((input) => {
    input.addEventListener("change", () => {
      if (input.checked) {
        namingOptions.labelMode = input.value;
        saveNamingOptions();
        updatePatternPreview();
      }
    });
  });

  resetButton.addEventListener("click", () => {
    form.reset();
    fileInput.value = "";
    rebuildBaseEditor([]);
    clearFlash();
    resetProgress();
    clearResults();
    stopProcessingPolling();
    applyNamingOptionsToControls();
    updatePatternPreview();
  });

  function applyNamingToFormData(formData, fileName) {
    formData.append("naming_mode", namingOptions.mode === "custom" ? "custom" : "auto");
    formData.append("naming_preset", namingOptions.preset);
    formData.append("naming_custom_pattern", namingOptions.customPattern || "");
    formData.append("naming_auto_clean", namingOptions.autoClean ? "1" : "0");
    formData.append("naming_keep_tokens", namingOptions.keepTokens ? "1" : "0");
    formData.append("naming_add_sequence", namingOptions.addSequence ? "1" : "0");
    formData.append("naming_append_date", namingOptions.appendDate ? "1" : "0");
    formData.append("naming_label_mode", namingOptions.labelMode);
    const overrideValue = namingOptions.mode === "custom" ? (baseOverrides[fileName] || "") : "";
    formData.append("base_override", overrideValue);
  }

  if (asyncButton) {
    asyncButton.addEventListener("click", async (event) => {
      event.preventDefault();
      if (formDisabled) {
        return;
      }
      await handleAsyncBatch();
    });
  }

  async function handleAsyncBatch() {
    clearFlash();
    clearResults();
    resetProgress();

    const review = sanitizeFileList(Array.from(fileInput.files || []));
    const files = review.files;
    const warnings = [...(review.notices || [])];
    const selectedRatios = getSelectedRatiosSafe();

    if (!files.length) {
      const message = warnings.length
        ? warnings.join(" • ")
        : "Select at least one compatible video.";
      showFlash(message);
      return;
    }

    if (!selectedRatios.length) {
      showFlash("Choose at least one output aspect ratio.");
      return;
    }

    toggleForm(true);
    updateOverridesInput();

    try {
      initializeProgress(files.length);
      const namingPayload = buildNamingPayload();

      let jobId = (typeof crypto !== "undefined" && crypto.randomUUID)
        ? crypto.randomUUID()
        : `job_${Date.now()}_${Math.random().toString(16).slice(2)}`;

      const uploadedRecords = [];

      for (const [index, file] of files.entries()) {
        updateProgressStatus(
          Math.min(25, ((index) / Math.max(files.length, 1)) * 20 + 5),
          `Uploading ${file.name}`,
          `${index + 1} of ${files.length} clip(s) queued`
        );

        const uploadResult = await uploadAsyncFile(file, jobId);
        if (uploadResult.job_id) {
          jobId = uploadResult.job_id;
        }
        uploadedRecords.push(uploadResult.record);

        updateProgressStatus(
          Math.min(45, ((index + 1) / Math.max(files.length, 1)) * 30 + 10),
          `Uploaded ${file.name}`,
          `${index + 1} of ${files.length} clip(s) uploaded`
        );
      }

      const job = await createAsyncJob({
        job_id: jobId,
        files: uploadedRecords,
        ratios: selectedRatios,
        style: styleSelector(),
        naming: namingPayload,
      });
      jobId = job.id;

      updateProgressStatus(
        Math.max(targetProgress, 55),
        "Awaiting rewarded ad",
        "Complete the ad to begin rendering."
      );

      const adResult = await launchRewardedAd({ jobId });
      const rewardToken = adResult?.detail?.token || null;

      await startAsyncJob(jobId, rewardToken);
      updateProgressStatus(
        Math.max(targetProgress, 60),
        "Rendering…",
        "0% complete"
      );

      const finalJob = await pollAsyncJob(jobId, files.length);
      renderAsyncResults(finalJob);

      const messages = [];
      if (warnings.length) {
        messages.push(`Warnings: ${warnings.join(" • ")}`);
      }
      showFlash(["Batch completed successfully.", ...messages].join(" • "), "success");
    } catch (error) {
      console.error(error);
      showFlash(error.message || "Unable to render batch asynchronously.");
      resetProgress();
    } finally {
      toggleForm(false);
    }
  }

  async function uploadAsyncFile(file, jobId) {
    try {
      const presign = await requestPresignedUpload(file, jobId);
      await uploadToPresignedUrl(file, presign.upload);
      return {
        job_id: presign.job_id || jobId,
        record: buildFileRecord(file, presign.file.key),
      };
    } catch (error) {
      if (error && error.code === "S3_DISABLED") {
        const local = await uploadLocalFile(file, jobId);
        return {
          job_id: local.job_id,
          record: buildFileRecord(file, local.file.key),
        };
      }
      throw error;
    }
  }

  function buildFileRecord(file, key) {
    const baseOverride = namingOptions.mode === "custom" ? (baseOverrides[file.name] || "") : "";
    return {
      key,
      original_name: file.name,
      size: file.size || 0,
      content_type: file.type || "video/mp4",
      base_override: baseOverride || null,
    };
  }

  async function requestPresignedUpload(file, jobId) {
    const res = await fetch("/api/upload-url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_id: jobId,
        filename: file.name,
        size: file.size || 0,
        content_type: file.type || "video/mp4",
      }),
    });
    if (res.status === 503) {
      const error = new Error("Object storage not configured.");
      error.code = "S3_DISABLED";
      throw error;
    }
    const data = await res.json();
    if (!res.ok || data.error) {
      throw new Error(data.error || `Upload slot failed (HTTP ${res.status})`);
    }
    return data;
  }

  async function uploadToPresignedUrl(file, presign) {
    if (!presign || !presign.url) {
      throw new Error("Invalid presigned payload.");
    }
    const formData = new FormData();
    Object.entries(presign.fields || {}).forEach(([key, value]) => {
      formData.append(key, value);
    });
    formData.append("file", file);
    const response = await fetch(presign.url, {
      method: "POST",
      body: formData,
    });
    if (!response.ok) {
      throw new Error(`Upload failed for ${file.name} (HTTP ${response.status})`);
    }
  }

  async function uploadLocalFile(file, jobId) {
    const formData = new FormData();
    formData.append("video", file);
    formData.append("job_id", jobId);
    const res = await fetch("/api/local-upload", {
      method: "POST",
      body: formData,
    });
    const data = await res.json();
    if (!res.ok || data.error) {
      throw new Error(data.error || `Local upload failed (HTTP ${res.status})`);
    }
    return data;
  }

  async function createAsyncJob(payload) {
    const res = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok || data.error) {
      throw new Error(data.error || `Job creation failed (HTTP ${res.status})`);
    }
    return data.job;
  }

  async function startAsyncJob(jobId, rewardToken) {
    const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        reward_token: rewardToken || "",
      }),
    });
    const data = await res.json();
    if (!res.ok || data.error) {
      throw new Error(data.error || `Failed to start job (HTTP ${res.status})`);
    }
    return data.job;
  }

  async function pollAsyncJob(jobId, totalFiles) {
    while (true) {
      const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/status`, {
        cache: "no-store",
      });
      const data = await res.json();
      if (!res.ok || data.error) {
        throw new Error(data.error || `Failed to check job status (HTTP ${res.status})`);
      }
      const job = data.job;
      const percent = Math.round(Math.max(0, Math.min(1, job.progress || 0)) * 100);
      const statusText = job.status === "done" ? "Finalising…" : job.status === "failed" ? "Failed" : "Rendering…";
      updateProgressStatus(
        Math.max(targetProgress, percent || 0),
        statusText,
        `${percent}% complete`
      );

      if (job.status === "done") {
        updateProgressStatus(100, "Batch complete", `${totalFiles} clip(s) rendered`);
        return job;
      }
      if (job.status === "failed") {
        throw new Error(job.message || "Rendering failed.");
      }
      await sleep(3000);
    }
  }

  function buildNamingPayload() {
    const pattern = getPatternString();
    const dateStamp = new Date().toISOString().slice(0, 10);
    return {
      mode: namingOptions.mode === "custom" ? "custom" : "auto",
      pattern_choice: namingOptions.preset,
      pattern,
      custom_pattern: namingOptions.customPattern || "",
      auto_clean: !!namingOptions.autoClean,
      keep_tokens: !!namingOptions.keepTokens,
      add_sequence: !!namingOptions.addSequence,
      append_date: !!namingOptions.appendDate,
      label_mode: namingOptions.labelMode,
      date_stamp: dateStamp,
    };
  }

  function renderAsyncResults(job) {
    clearResults();
    const styleKey = job.style || styleSelector();
    (job.results || []).forEach((entry) => {
      const normalized = {
        original_name: entry.original_name,
        style_label: styleLabels[styleKey] || styleKey,
        ratio_labels: Array.from(new Set((entry.outputs || []).map((out) => out.ratio_label).filter(Boolean))),
        outputs: (entry.outputs || []).map((out) => ({
          url: out.url,
          label: out.label || out.filename,
          filename: out.filename,
        })),
      };
      appendResultCard(normalized);
    });

    if (job.results && job.results.length) {
      resultsSection.classList.remove("hidden");
      downloadAllLink.href = `/download/${encodeURIComponent(job.id)}/bundle`;
      downloadAllLink.setAttribute("aria-disabled", "false");
    } else {
      downloadAllLink.href = "#";
      downloadAllLink.setAttribute("aria-disabled", "true");
    }
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearFlash();
    clearResults();
    resetProgress();

    const review = sanitizeFileList(Array.from(fileInput.files || []));
    const files = review.files;
    const warnings = [...(review.notices || [])];
    const selectedRatios = getSelectedRatiosSafe();

    if (!files.length) {
      const message = warnings.length
        ? warnings.join(" • ")
        : "Select at least one compatible video.";
      showFlash(message);
      return;
    }

    if (!selectedRatios.length) {
      showFlash("Choose at least one output aspect ratio.");
      return;
    }

        toggleForm(true);
        updateOverridesInput();

        const errors = [];
        const total = files.length;
        let processed = 0;
        let batchId = (typeof crypto !== "undefined" && crypto.randomUUID)
          ? crypto.randomUUID()
          : `batch_${Date.now()}_${Math.random().toString(16).slice(2)}`;

        initializeProgress(total);

        for (const [index, file] of files.entries()) {
          const fileSize = file.size || 0;
          updateOverallProgress(
            processed,
            0,
            total,
            `Preparing ${file.name}`,
            `${index + 1} of ${total} clip(s) queued`
          );

          await new Promise((resolve) => {
            const formData = new FormData();
            formData.append("style", styleSelector());
            formData.append("batch_id", batchId);
            formData.append("video", file);
            selectedRatios.forEach((ratio) => formData.append("ratios", ratio));
            applyNamingToFormData(formData, file.name);

            const xhr = new XMLHttpRequest();
            xhr.open("POST", apiProcessUrl);
            xhr.responseType = "json";

            xhr.upload.onprogress = (event) => {
              if (!event.lengthComputable) {
                return;
              }
              const perFileRatio = fileSize ? Math.min(1, event.loaded / fileSize) : 0;
              updateOverallProgress(
                processed,
                0,
                total,
                `Uploading ${file.name}`,
                `${index + 1} of ${total} clip(s): ${(perFileRatio * 100).toFixed(0)}% uploaded`
              );
            };

            xhr.upload.onload = () => {
              updateOverallProgress(
                processed,
                0,
                total,
                `Processing ${file.name}`,
                `${index + 1} of ${total} clip(s) uploaded`
              );
              startProcessingPolling(batchId, file.name, index, total, processed);
            };

            xhr.onerror = () => {
              errors.push(`${file.name}: network error`);
              stopProcessingPolling();
              updateOverallProgress(processed, 0, total, "Error", `${index + 1} of ${total} clip(s) failed`);
              resolve();
            };

            xhr.onload = () => {
              const payload = xhr.response || {};
              if (xhr.status < 200 || xhr.status >= 300 || payload.error) {
                errors.push(`${file.name}: ${payload.error || `HTTP ${xhr.status}`}`);
                stopProcessingPolling();
                updateOverallProgress(processed, 0, total, "Error", `${index + 1} of ${total} clip(s) failed`);
                resolve();
                return;
              }

              batchId = payload.batch_id;
              appendResultCard(payload.result);
              processed += 1;
              updateOverallProgress(
                processed,
                0,
                total,
                `Finished ${file.name}`,
                `${processed} of ${total} clip(s) rendered`
              );

              downloadAllLink.href = payload.downloads.bundle;
              downloadAllLink.setAttribute("aria-disabled", "false");
              resultsSection.classList.remove("hidden");
              stopProcessingPolling();
              resolve();
            };

            xhr.setRequestHeader("Accept", "application/json");
            xhr.send(formData);
          });
        }

    if (processed === total) {
      stopProcessingPolling();
      targetProgress = 100;
      updateProgressStatus(100, "Batch complete", `${processed} of ${total} clip(s) rendered`);
      setTimeout(() => {
        displayedProgress = 100;
        renderProgress();
        stopProgressAnimation();
      }, 600);
    } else if (!processed) {
      resetProgress();
    }

    const messages = [];
    if (processed) {
      messages.push(`Rendered ${processed} clip(s).`);
    }
    if (warnings.length) {
      messages.push(`Warnings: ${warnings.join(" • ")}`);
    }
    if (errors.length) {
      messages.push(`Errors: ${errors.join(" • ")}`);
    }
    if (!processed && !errors.length) {
      messages.push("Nothing was rendered.");
    }

    if (messages.length) {
      const tone = errors.length ? "error" : processed ? "success" : "error";
      showFlash(messages.join(" • "), tone);
    } else {
      clearFlash();
    }

    toggleForm(false);
  });

  updateStyleCards();
  applyNamingOptionsToControls();
  rebuildBaseEditor([]);
  updatePatternPreview();
  updateOverridesInput();
})();
