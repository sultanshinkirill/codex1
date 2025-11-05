(() => {
  const config = window.vibeConfig || {};
  const aspectOptions = config.aspects || {};
  const styleLabels = config.styles || {};
  const styleShortLabels = config.styleShort || {};
  const adConfig = config.ads || {};
  const { createFFmpeg, fetchFile} = window.FFmpeg || {};
  let ffmpegInstance = null;
  let ffmpegReady = false;
  let ffmpegLoadingPromise = null;
  let ffmpegProgressCallback = null;
  const FFMPEG_CORE_URL = "https://unpkg.com/@ffmpeg/core@0.12.6/dist/ffmpeg-core.js";
  const CLIENT_DURATION_LIMIT_SECONDS = config.clientDurationLimit ?? 75;

  // Tier management
  const TIER_LIMITS = {
    free: {
      maxFiles: 3,
      maxFileSizeMB: 50,
      maxDurationSeconds: 75,
      maxRatios: 2,
      mode: 'browser',
      dailyLimit: 3,
      label: 'FREE',
      description: 'Up to 3 videos (50MB max), 75s, 2 ratios - Browser processing'
    },
    paid: {
      maxFiles: 20,
      maxFileSizeMB: 300,
      maxDurationSeconds: 180,
      maxRatios: 4,
      mode: 'server',
      dailyLimit: null,
      label: 'PAID',
      description: 'Up to 20 videos (300MB max), 3 min, 4 ratios - Server processing'
    }
  };

  // Get current tier from URL parameter or default to free
  const urlParams = new URLSearchParams(window.location.search);
  let currentTier = urlParams.get('tier') || 'free';
  let currentLimits = TIER_LIMITS[currentTier];

  // Initialize tier UI
  function initializeTierUI() {
    const tierBadge = document.getElementById('tier-badge');
    const tierLimits = document.getElementById('tier-limits');
    const tierToggle = document.getElementById('tier-toggle');
    const tierUsage = document.getElementById('tier-usage');

    if (tierBadge) {
      tierBadge.textContent = currentLimits.label;
      if (currentTier === 'paid') {
        tierBadge.classList.add('paid');
      }
    }

    if (tierLimits) {
      tierLimits.textContent = currentLimits.description;
    }

    if (tierToggle) {
      if (currentTier === 'free') {
        tierToggle.textContent = 'Upgrade to PAID';
        tierToggle.classList.remove('downgrade');
      } else {
        tierToggle.textContent = 'Switch to FREE';
        tierToggle.classList.add('downgrade');
      }

      tierToggle.addEventListener('click', async () => {
        const newTier = currentTier === 'free' ? 'paid' : 'free';

        try {
          const response = await fetch('/upgrade', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tier: newTier })
          });

          if (response.ok) {
            // Update URL and reload
            const newUrl = new URL(window.location);
            newUrl.searchParams.set('tier', newTier);
            window.location.href = newUrl.toString();
          }
        } catch (error) {
          console.error('Failed to switch tier:', error);
          showFlash(`Failed to switch tier: ${error.message}`, 'error');
        }
      });
    }

    // Update UI elements with tier-specific limits
    updateTierUIElements();

    // Fetch and display usage
    fetchUsageStats();
  }

  function updateTierUIElements() {
    const maxFilesLabel = document.getElementById('max-files-label');
    const fileLimitNote = document.getElementById('file-limit-note');
    const ratioLabel = document.getElementById('ratio-label');

    if (maxFilesLabel) {
      maxFilesLabel.textContent = `Upload up to ${currentLimits.maxFiles} clips`;
    }

    if (fileLimitNote) {
      fileLimitNote.textContent = `Pick individual files. Only the first ${currentLimits.maxFiles} compatible videos are processed.`;
    }

    if (ratioLabel) {
      ratioLabel.textContent = `Output aspect ratios (choose up to ${currentLimits.maxRatios})`;
    }
  }

  async function fetchUsageStats() {
    try {
      const response = await fetch('/usage');
      if (response.ok) {
        const data = await response.json();
        const tierUsage = document.getElementById('tier-usage');

        if (tierUsage && data.renders_today !== undefined) {
          if (currentTier === 'free' && currentLimits.dailyLimit) {
            tierUsage.textContent = `${data.renders_today}/${currentLimits.dailyLimit} batches today`;
          } else {
            tierUsage.textContent = `${data.renders_today} batches today`;
          }
        }
      }
    } catch (error) {
      console.error('Failed to fetch usage:', error);
    }
  }

  // Backend routing configuration for dual deployment
  const BACKEND_CONFIG = {
    // Vercel backend (serverless, 10s timeout)
    vercel: window.location.origin, // Same domain when deployed on Vercel
    // Hostinger backend (VPS, no timeout limits)
    hostinger: config.hostingerApiUrl || '',
  };

  // Smart backend selection based on video characteristics
  function selectBackend(videoFile, duration) {
    const fileSizeMB = videoFile.size / (1024 * 1024);
    const isLargeFile = fileSizeMB > 50;
    const isLongVideo = duration > CLIENT_DURATION_LIMIT_SECONDS;

    // Use Hostinger for large files or when Hostinger URL is configured
    if ((isLargeFile || isLongVideo) && BACKEND_CONFIG.hostinger) {
      console.log(`[Backend] Using Hostinger for ${fileSizeMB.toFixed(1)}MB, ${duration.toFixed(1)}s video`);
      return BACKEND_CONFIG.hostinger;
    }

    // Default to current origin (Vercel or local dev)
    console.log(`[Backend] Using default backend (${window.location.origin})`);
    return BACKEND_CONFIG.vercel;
  }

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
  const renderButton = document.getElementById("render-button");
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
  // Tier-based limits (use currentLimits.maxFiles and currentLimits.maxRatios)
  const NAMING_STORAGE_KEY = "vibeNamingOptions_v2";
  const defaultAspect = { short: "1x1", label: "1:1 Square", size: [1080, 1080] };

  const VIDEO_EXTENSION_SET = new Set(videoExtensions);

  function stripKnownExtensions(name) {
    let base = name;
    while (true) {
      const dotIndex = base.lastIndexOf(".");
      if (dotIndex <= 0) {
        break;
      }
      const suffix = base.slice(dotIndex).toLowerCase();
      if (VIDEO_EXTENSION_SET.has(suffix)) {
        base = base.slice(0, dotIndex);
      } else {
        break;
      }
    }
    return base;
  }

  function removeNamingTokens(text) {
    if (!text) return "";
    let cleaned = text;
    const resolutionPattern = /\d{3,4}\s*(?:x|×|\*|\/|:|-|_|(?:\s+by\s+))\s*\d{3,4}/gi;
    const aspectPattern = /\b\d(?:\.\d+)?\s*(?:x|×|:|\/|\s+by\s+)\s*\d(?:\.\d+)?\b/gi;
    const wordPattern = /\b(portrait|vertical|landscape|square)\b/gi;
    cleaned = cleaned.replace(resolutionPattern, " ");
    cleaned = cleaned.replace(aspectPattern, " ");
    cleaned = cleaned.replace(wordPattern, " ");
    cleaned = cleaned.replace(/[._\-]{2,}/g, " ");
    cleaned = cleaned.replace(/\s+/g, " ");
    return cleaned.trim();
  }

  function sanitizeComponent(text) {
    return (text || "")
      .replace(/[^A-Za-z0-9._-]+/g, "_")
      .replace(/_+/g, "_")
      .replace(/-+/g, "-")
      .replace(/\s+/g, "_")
      .replace(/^[_\-.]+|[_\-.]+$/g, "") || "clip";
  }

  function cleanStub(originalName, keepTokens = false) {
    let base = stripKnownExtensions(originalName || "");
    base = base.trim();
    if (!keepTokens) {
      base = removeNamingTokens(base);
    }
    return sanitizeComponent(base);
  }

  function prepareBaseInfo(originalName, override, config) {
    const sourceName = override ? override.trim() : originalName;
    const baseCandidate = stripKnownExtensions(sourceName || "");
    const sanitizedBase = sanitizeComponent(baseCandidate);
    const autoClean = config.auto_clean !== false && !config.keep_tokens;
    const baseClean = autoClean ? cleanStub(baseCandidate, false) : sanitizedBase;
    return {
      base: sanitizedBase || "clip",
      base_clean: baseClean || "clip",
      original_filename: originalName,
    };
  }

  function sanitizeFilename(candidate, ext) {
    const normalizedExt = ext ? (ext.startsWith(".") ? ext : `.${ext}`) : "";
    let base = candidate || "clip";
    base = base.replace(/[^A-Za-z0-9._-]+/g, "_");
    base = base.replace(/_+/g, "_").replace(/-+/g, "-");
    base = base.replace(/\s+/g, "_").replace(/^[_\-.]+|[_\-.]+$/g, "");
    if (!base) base = "clip";
    let full = `${base}${normalizedExt}`;
    if (full.length > 120) {
      const maxBase = Math.max(1, 120 - normalizedExt.length);
      full = `${base.slice(0, maxBase)}${normalizedExt}`;
    }
    return full;
  }

  function ensureUniqueName(existingSet, filename) {
    if (!existingSet.has(filename)) {
      existingSet.add(filename);
      return filename;
    }
    const dot = filename.lastIndexOf(".");
    const base = dot >= 0 ? filename.slice(0, dot) : filename;
    const ext = dot >= 0 ? filename.slice(dot) : "";
    let counter = 1;
    let candidate = filename;
    while (existingSet.has(candidate)) {
      const suffix = `__${String(counter).padStart(3, "0")}`;
      candidate = `${base}${suffix}${ext}`;
      counter += 1;
    }
    existingSet.add(candidate);
    return candidate;
  }

  function generateOutputFilename(baseInfo, aspectKey, styleKey, configObj, seqNumber, ext, existingNames, dateStamp) {
    const ratioMeta = aspectOptions[aspectKey] || defaultAspect;
    const styleLong = styleLabels[styleKey] || styleKey;
    const styleShort = styleShortLabels[styleKey] || styleKey;
    const ratioShort = ratioMeta.short || aspectKey;
    const ratioFriendly = ratioMeta.label || aspectKey;
    const labelMode = configObj.label_mode === "friendly" ? "friendly" : "short";
    const ratioToken = labelMode === "friendly" ? ratioFriendly : ratioShort;
    const styleToken = labelMode === "friendly" ? styleLong : styleShort;
    const seqValue = typeof seqNumber === "number" ? String(seqNumber).padStart(3, "0") : "";
    const tokens = new Map([
      ["base", baseInfo.base],
      ["base_clean", baseInfo.base_clean],
      ["ratio", ratioToken],
      ["style", styleToken],
      ["w", ratioMeta.size ? ratioMeta.size[0] : 0],
      ["h", ratioMeta.size ? ratioMeta.size[1] : 0],
      ["date", dateStamp || configObj.date_stamp || new Date().toISOString().slice(0, 10)],
      ["seq", seqValue],
      ["ext", ext.replace(/^\./, "")],
    ]);

    const rawPattern = (configObj.pattern || "{base_clean}_{ratio}").toString();
    const formatted = rawPattern.replace(/\{([^}]+)\}/g, (_, key) => {
      const value = tokens.get(key);
      return value == null ? "" : String(value);
    }).replace(/[_\-\s]+$/g, "").replace(/^[_\-\s]+/g, "");

    let finalName = formatted || `${baseInfo.base_clean}_${ratioToken}`;
    const normalizedPattern = rawPattern.toLowerCase();
    if (configObj.add_sequence !== false && !normalizedPattern.includes("{seq}") && seqValue) {
      finalName = `${finalName}__${seqValue}`;
    }
    const dateToken = tokens.get("date");
    if (configObj.append_date !== false && !normalizedPattern.includes("{date}") && dateToken) {
      finalName = `${finalName}__${dateToken}`;
    }

    const sanitized = sanitizeFilename(finalName, ext);
    return ensureUniqueName(existingNames, sanitized);
  }

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

  async function ensureFfmpegLoaded() {
    if (ffmpegReady && ffmpegInstance) {
      return ffmpegInstance;
    }
    if (!createFFmpeg) {
      throw new Error("FFmpeg WASM is unavailable in this browser.");
    }
    if (!ffmpegLoadingPromise) {
      ffmpegInstance = createFFmpeg({
        log: true,
        corePath: FFMPEG_CORE_URL,
      });
      ffmpegLoadingPromise = ffmpegInstance.load().then(() => {
        ffmpegInstance.setProgress(({ ratio }) => {
          if (typeof ffmpegProgressCallback === "function") {
            ffmpegProgressCallback(Math.max(0, Math.min(1, ratio || 0)));
          }
        });
        ffmpegReady = true;
        return ffmpegInstance;
      });
    }
    return ffmpegLoadingPromise;
  }

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

  function yieldToBrowser() {
    return new Promise((resolve) => {
      if (typeof requestAnimationFrame === "function") {
        requestAnimationFrame(() => resolve());
      } else {
        setTimeout(resolve, 16);
      }
    });
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
    stopProcessingPolling();
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
    const tooLarge = [];
    const maxSizeBytes = currentLimits.maxFileSizeMB * 1024 * 1024;

    for (const file of files) {
      if (!file || !file.name) continue;
      const ext = file.name.split(".").pop().toLowerCase();
      if (!allowedExtensions.includes(ext)) {
        skipped.push(file.name);
        continue;
      }
      // Check file size against tier limit
      if (file.size > maxSizeBytes) {
        tooLarge.push(`${file.name} (${(file.size / 1024 / 1024).toFixed(1)}MB)`);
        continue;
      }
      picked.push(file);
    }
    const notices = [];
    if (skipped.length) {
      notices.push(`Skipped unsupported files: ${skipped.join(", ")}`);
    }
    if (tooLarge.length) {
      notices.push(`Files too large (max ${currentLimits.maxFileSizeMB}MB): ${tooLarge.join(", ")}`);
    }
    if (picked.length > currentLimits.maxFiles) {
      notices.push(`Max ${currentLimits.maxFiles} videos per batch. You picked ${picked.length}.`);
    }
    const usable = picked.slice(0, currentLimits.maxFiles);
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

  async function probeFileDuration(file) {
    if (!file) {
      return null;
    }
    if (!file.type || !file.type.startsWith("video/")) {
      return null;
    }
    return new Promise((resolve, reject) => {
      const video = document.createElement("video");
      let revoked = false;
      const objectUrl = URL.createObjectURL(file);

      const cleanup = () => {
        if (!revoked) {
          URL.revokeObjectURL(objectUrl);
          revoked = true;
        }
        video.removeAttribute("src");
        video.load();
      };

      const handleLoaded = () => {
        const duration = Number.isFinite(video.duration) ? video.duration : null;
        cleanup();
        resolve(duration && duration > 0 ? duration : null);
      };

      const handleError = () => {
        cleanup();
        reject(new Error("Failed to load video metadata"));
      };

      video.preload = "metadata";
      video.muted = true;
      video.playsInline = true;
      video.addEventListener("loadedmetadata", handleLoaded, { once: true });
      video.addEventListener("error", handleError, { once: true });
      video.src = objectUrl;
    });
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
    if (selected.length > currentLimits.maxRatios) {
      return selected.slice(0, currentLimits.maxRatios);
    }
    return selected;
  }

  function enforceRatioLimit(changedInput) {
    const selected = getSelectedRatios();
    if (selected.length > currentLimits.maxRatios) {
      changedInput.checked = false;
      showFlash(`Pick up to ${currentLimits.maxRatios} aspect ratios per batch.`);
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
    if (renderButton) {
      renderButton.disabled = disabled;
    }
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

  if (renderButton) {
    renderButton.addEventListener("click", async (event) => {
      event.preventDefault();
      if (formDisabled) {
        return;
      }
      await handleRenderClick();
    });
  }

  async function handleRenderClick() {
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

    // Check usage limits before rendering
    try {
      const usageResponse = await fetch('/usage');
      if (usageResponse.ok) {
        const usageStats = await usageResponse.json();

        // Check if FREE tier has reached daily limit
        if (usageStats.tier === 'free' && usageStats.dailyLimit &&
            usageStats.rendersToday >= usageStats.dailyLimit) {
          showFlash(`FREE tier daily limit reached (${usageStats.rendersToday}/${usageStats.dailyLimit}). Upgrade to PAID for unlimited rendering.`, 'error');
          return;
        }
      }
    } catch (error) {
      console.warn('Failed to check usage stats:', error);
    }

    // Check video durations against tier limit
    let tooLong = false;
    const maxDuration = currentLimits.maxDurationSeconds;
    for (const file of files) {
      try {
        const duration = await probeFileDuration(file);
        if (duration && duration > maxDuration) {
          showFlash(
            `${file.name} is ${Math.ceil(duration)}s (max ${maxDuration}s for ${currentLimits.label} tier). ${currentTier === 'free' ? 'Upgrade to PAID for longer videos.' : 'Please use shorter videos.'}`,
            "error"
          );
          tooLong = true;
          break;
        }
      } catch (error) {
        console.warn("Failed to inspect duration for", file?.name, error);
      }
    }

    if (tooLong) {
      return;
    }

    // Enforce rendering mode based on tier
    if (currentLimits.mode === 'server') {
      // PAID tier: always use server rendering
      await performFallbackRender(files, warnings, selectedRatios);
      return;
    }

    // FREE tier: use browser rendering only
    try {
      await performClientSideRender(files, warnings, selectedRatios);
    } catch (error) {
      console.error(error);
      showFlash("Client-side rendering failed. Please try again or contact support.", "error");
    }
  }

  function buildFfmpegFilters(styleKey, targetW, targetH) {
    const filters = {};
    if (styleKey === "fill") {
      filters.videoArgs = [
        "-vf",
        `scale=${targetW}:${targetH}:force_original_aspect_ratio=increase,crop=${targetW}:${targetH},format=yuv420p`,
      ];
    } else if (styleKey === "black") {
      filters.videoArgs = [
        "-vf",
        `scale=${targetW}:${targetH}:force_original_aspect_ratio=decrease,pad=${targetW}:${targetH}:(ow-iw)/2:(oh-ih)/2:black,format=yuv420p`,
      ];
    } else {
      const filterComplex = [
        `[0:v]scale=${targetW}:${targetH}:force_original_aspect_ratio=increase,crop=${targetW}:${targetH},boxblur=32:1[bg];`,
        `[0:v]scale=${targetW}:${targetH}:force_original_aspect_ratio=decrease,pad=${targetW}:${targetH}:(ow-iw)/2:(oh-ih)/2[fg];`,
        `[bg][fg]overlay=(main_w-overlay_w)/2:(main_h-overlay_h)/2:format=yuv420p[vout]`,
      ].join("");
      filters.videoArgs = ["-filter_complex", filterComplex, "-map", "[vout]"];
    }
    return filters;
  }

  async function performClientSideRender(files, warnings, selectedRatios) {
    if (!createFFmpeg) {
      throw new Error("FFmpeg WASM is unavailable.");
    }

    toggleForm(true);
    updateOverridesInput();
    downloadAllLink.href = "#";
    downloadAllLink.setAttribute("aria-disabled", "true");
    downloadAllLink.title = "Bundle download is unavailable for client-side renders.";

    const totalTargets = Math.max(1, files.length * selectedRatios.length);
    initializeProgress(totalTargets);
    const namingConfig = buildNamingPayload();
    const dateStamp = namingConfig.date_stamp || new Date().toISOString().slice(0, 10);
    let processedTargets = 0;
    const uniqueNames = new Set();
    const styleKey = styleSelector();
    const finalResults = [];

    const ffmpeg = await ensureFfmpegLoaded();

    try {
      for (const [fileIndex, file] of files.entries()) {
        const override = namingOptions.mode === "custom" ? (baseOverrides[file.name] || "") : "";
        const baseInfo = prepareBaseInfo(file.name, override, namingConfig);
        const inputName = `input_${Date.now()}_${Math.random().toString(16).slice(2)}.mp4`;
        ffmpeg.FS("writeFile", inputName, await fetchFile(file));

        const outputs = [];
        const ratioLabels = new Set();

        try {
          for (const [ratioIndex, aspectKey] of selectedRatios.entries()) {
            await yieldToBrowser();
            const aspectMeta = aspectOptions[aspectKey] || defaultAspect;
            const [targetW, targetH] = aspectMeta.size || [1080, 1080];
            const filters = buildFfmpegFilters(styleKey, targetW, targetH);
            const outputName = `output_${fileIndex}_${ratioIndex}_${Date.now()}.mp4`;
            const seqNumber = processedTargets + 1;
            const filename = generateOutputFilename(
              baseInfo,
              aspectKey,
              styleKey,
              namingConfig,
              seqNumber,
              "mp4",
              uniqueNames,
              dateStamp
            );
            const ratioLabel = namingConfig.label_mode === "friendly"
              ? (aspectMeta.label || aspectKey)
              : (aspectMeta.short || aspectKey);
            ratioLabels.add(ratioLabel);

            ffmpegProgressCallback = (fraction) => {
              updateOverallProgress(
                processedTargets,
                fraction,
                totalTargets,
                `Rendering ${file.name}`,
                `${fileIndex + 1} of ${files.length} clip(s) • ${aspectMeta.label || ratioLabel}`
              );
            };

            const args = [
              "-i", inputName,
              ...filters.videoArgs,
              "-c:v", "libx264",
              "-preset", "veryfast",
              "-crf", "20",
              "-pix_fmt", "yuv420p",
              "-movflags", "faststart",
              "-c:a", "aac",
              "-b:a", "128k",
              "-map", "0:a?",
              outputName,
            ];

            await ffmpeg.run(...args);
            ffmpegProgressCallback = null;
            processedTargets += 1;
            updateOverallProgress(
              processedTargets,
              0,
              totalTargets,
              `Rendering ${file.name}`,
              `${processedTargets} of ${totalTargets} outputs ready`
            );

            let blobUrl = null;
            try {
              const data = ffmpeg.FS("readFile", outputName);
              const blob = new Blob([data.buffer], { type: "video/mp4" });
              blobUrl = URL.createObjectURL(blob);
            } finally {
              try {
                ffmpeg.FS("unlink", outputName);
              } catch (cleanupError) {
                console.warn("Failed to remove ffmpeg output:", cleanupError);
              }
            }

            outputs.push({
              url: blobUrl,
              label: `${aspectMeta.label || ratioLabel} • ${styleLabels[styleKey] || styleKey}`,
              filename,
              ratio_label: ratioLabel,
            });

            await yieldToBrowser();
          }
        } finally {
          try {
            ffmpeg.FS("unlink", inputName);
          } catch (cleanupError) {
            console.warn("Failed to remove ffmpeg input:", cleanupError);
          }
        }

        finalResults.push({
          original_name: file.name,
          style_label: styleLabels[styleKey] || styleKey,
          ratio_labels: Array.from(ratioLabels),
          outputs,
        });
      }

      resultsSection.classList.remove("hidden");
      finalResults.forEach((entry) => appendResultCard(entry));

      // Increment usage counter after successful client-side rendering
      try {
        await fetch('/increment-usage', { method: 'POST' });
        // Refresh usage stats
        fetchUsageStats();
      } catch (error) {
        console.warn('Failed to increment usage counter:', error);
      }

      const totalOutputs = finalResults.reduce((sum, entry) => sum + entry.outputs.length, 0);
      const messages = [`Rendered ${totalOutputs} clip(s).`];
      if (warnings.length) {
        messages.push(`Warnings: ${warnings.join(" • ")}`);
      }
      showFlash(messages.join(" • "), "success");
    } finally {
      ffmpegProgressCallback = null;
      toggleForm(false);
    }
  }

  async function performFallbackRender(files, warnings, selectedRatios) {
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
          downloadAllLink.removeAttribute("title");
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

      // Refresh usage stats after successful server-side rendering
      fetchUsageStats();
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

    await performFallbackRender(files, warnings, selectedRatios);
  });

  updateStyleCards();
  applyNamingOptionsToControls();
  rebuildBaseEditor([]);
  updatePatternPreview();
  updateOverridesInput();

  // Initialize tier UI
  initializeTierUI();
})();
