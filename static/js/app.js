(() => {
  "use strict";

  const TERMINAL = new Set(["completed", "failed", "cancelled"]);
  const STORAGE_KEY = "webcrawler:lastJob";
  const POLL_INTERVAL = 900;

  const state = {
    jobId: null,
    job: null,
    result: null,
    timer: null,
    loading: false,
    loadedFor: null,
    filter: "",
    pollFailures: 0,
    restored: false,
  };

  const $ = (id) => document.getElementById(id);

  const el = {
    form: $("crawlForm"),
    urlInput: $("urlInput"),
    startBtn: $("startBtn"),
    startLabel: document.querySelector("#startBtn .btn-label"),
    maxPages: $("maxPages"),
    delay: $("delay"),
    timeout: $("timeout"),
    sameSite: $("sameSite"),
    respectRobots: $("respectRobots"),
    allowPrivate: $("allowPrivate"),
    ignoreSsl: $("ignoreSsl"),
    errorBanner: $("errorBanner"),
    errorText: $("errorText"),
    dismissError: $("dismissError"),
    idleSection: $("idleSection"),
    progressSection: $("progressSection"),
    radar: $("radar"),
    progressKicker: $("progressKicker"),
    progressTitle: $("progressTitle"),
    progressUrl: $("progressUrl"),
    cancelBtn: $("cancelBtn"),
    progressBar: $("progressBar"),
    progressFraction: $("progressFraction"),
    progressPercent: $("progressPercent"),
    statDone: $("statDone"),
    statSuccess: $("statSuccess"),
    statError: $("statError"),
    statTime: $("statTime"),
    progressMessage: $("progressMessage"),
    progressWarnings: $("progressWarnings"),
    resultSection: $("resultSection"),
    resultMeta: $("resultMeta"),
    summaryChips: $("summaryChips"),
    downloadJson: $("downloadJson"),
    downloadCsv: $("downloadCsv"),
    resultFilter: $("resultFilter"),
    resultRows: $("resultRows"),
    resultTable: document.querySelector(".result-table"),
    noResults: $("noResults"),
    noResultsText: document.querySelector("#noResults p"),
    clearFilter: $("clearFilter"),
    errorDetails: $("errorDetails"),
    errorSummary: $("errorSummary"),
    errorList: $("errorList"),
    warningDetails: $("warningDetails"),
    warningSummary: $("warningSummary"),
    warningList: $("warningList"),
    detailDialog: $("detailDialog"),
    detailTitle: $("detailTitle"),
    detailUrl: $("detailUrl"),
    detailBody: $("detailBody"),
    closeDialog: $("closeDialog"),
    toast: $("toast"),
  };

  let toastTimer = null;

  function init() {
    bindEvents();
    restoreLastJob();
  }

  function bindEvents() {
    el.form.addEventListener("submit", handleSubmit);
    el.cancelBtn.addEventListener("click", cancelJob);
    el.dismissError.addEventListener("click", hideError);
    el.resultFilter.addEventListener("input", () => {
      state.filter = el.resultFilter.value.trim().toLowerCase();
      renderRows();
    });
    el.clearFilter.addEventListener("click", () => {
      state.filter = "";
      el.resultFilter.value = "";
      renderRows();
      el.resultFilter.focus();
    });
    el.resultRows.addEventListener("click", (event) => {
      const button = event.target.closest("[data-page-index]");
      if (button) {
        openDetail(Number(button.dataset.pageIndex));
        return;
      }
      const row = event.target.closest("tr[data-page-index]");
      if (row) {
        openDetail(Number(row.dataset.pageIndex));
      }
    });
    el.closeDialog.addEventListener("click", () => el.detailDialog.close());
    el.detailDialog.addEventListener("click", (event) => {
      if (event.target === el.detailDialog) {
        el.detailDialog.close();
      }
    });
    el.detailBody.addEventListener("click", (event) => {
      const copyButton = event.target.closest("[data-copy-url]");
      if (copyButton) {
        copyText(copyButton.dataset.copyUrl);
      }
    });
  }

  async function handleSubmit(event) {
    event.preventDefault();
    if (state.loading) return;

    let url;
    try {
      url = normalizeUrlInput(el.urlInput.value);
      validateOptions();
    } catch (error) {
      showError(error.message);
      el.urlInput.focus();
      return;
    }

    const payload = {
      url,
      depth: Number(
        document.querySelector('input[name="depth"]:checked').value
      ),
      max_pages: Number.parseInt(el.maxPages.value, 10),
      delay: Number.parseFloat(el.delay.value),
      timeout: Number.parseFloat(el.timeout.value),
      same_site: el.sameSite.checked,
      respect_robots: el.respectRobots.checked,
      allow_private: el.allowPrivate.checked,
      verify_ssl: !el.ignoreSsl.checked,
    };

    resetForNewJob(url, payload.max_pages);
    setBusy(true);
    hideError();
    closeDialogIfOpen();

    try {
      const response = await fetch("/api/crawl", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        throw await parseError(response);
      }
      const job = await response.json();
      state.jobId = job.id;
      state.job = job;
      state.loadedFor = null;
      storeJobId(job.id);
      renderJob(job);
      schedulePoll(120);
    } catch (error) {
      setBusy(false);
      el.progressSection.hidden = true;
      el.idleSection.hidden = false;
      showError(error.message || "无法创建采集任务");
    }
  }

  function normalizeUrlInput(rawValue) {
    let raw = rawValue.trim();
    if (!raw) {
      throw new Error("请输入要采集的网址");
    }
    if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(raw)) {
      raw = "https://" + raw;
    }
    let parsed;
    try {
      parsed = new URL(raw);
    } catch {
      throw new Error("网址格式不正确，例如：https://example.com");
    }
    if (!["http:", "https:"].includes(parsed.protocol)) {
      throw new Error("只支持 http:// 或 https:// 网址");
    }
    if (!parsed.hostname) {
      throw new Error("网址缺少有效的域名");
    }
    return parsed.href;
  }

  function validateOptions() {
    const maxPages = Number(el.maxPages.value);
    if (!Number.isInteger(maxPages) || maxPages < 1 || maxPages > 50) {
      throw new Error("最多页面数必须是 1 到 50 之间的整数");
    }
    const delay = Number(el.delay.value);
    if (!Number.isFinite(delay) || delay < 0 || delay > 10) {
      throw new Error("请求间隔必须在 0 到 10 秒之间");
    }
    const timeout = Number(el.timeout.value);
    if (!Number.isFinite(timeout) || timeout < 3 || timeout > 60) {
      throw new Error("请求超时必须在 3 到 60 秒之间");
    }
  }

  function resetForNewJob(url, maxPages) {
    stopPolling();
    state.jobId = null;
    state.job = null;
    state.result = null;
    state.loadedFor = null;
    state.filter = "";
    state.pollFailures = 0;
    clearStoredJobId();
    el.resultFilter.value = "";
    el.resultFilter.disabled = false;
    el.idleSection.hidden = true;
    el.resultSection.hidden = true;
    el.progressSection.hidden = false;
    el.progressSection.classList.remove("is-done", "is-error", "is-cancelled");
    el.progressKicker.textContent = "STARTING";
    el.progressTitle.textContent = "正在创建采集任务…";
    el.progressUrl.textContent = url;
    el.progressBar.value = 0;
    el.progressPercent.textContent = "0%";
    el.progressFraction.textContent = `0 / ${maxPages} 页`;
    el.statDone.textContent = "0";
    el.statSuccess.textContent = "0";
    el.statError.textContent = "0";
    el.statTime.textContent = "0.0s";
    el.progressMessage.textContent = "正在提交采集任务…";
    el.progressWarnings.hidden = true;
    el.progressWarnings.textContent = "";
    el.cancelBtn.hidden = false;
    el.cancelBtn.disabled = false;
    el.cancelBtn.innerHTML =
      '<span class="stop-square" aria-hidden="true"></span>停止采集';
    el.downloadJson.hidden = false;
    el.downloadCsv.hidden = false;
    el.downloadJson.href = "#";
    el.downloadCsv.href = "#";
    document.title = "采集中 · 蛛网";
  }

  function setBusy(busy) {
    state.loading = busy;
    el.startBtn.disabled = busy;
    el.startLabel.textContent = busy ? "采集中…" : "开始采集";
    el.form.setAttribute("aria-busy", busy ? "true" : "false");
  }

  function schedulePoll(delay) {
    stopPolling();
    state.timer = window.setTimeout(pollJob, delay);
  }

  function stopPolling() {
    if (state.timer) {
      window.clearTimeout(state.timer);
      state.timer = null;
    }
  }

  async function pollJob() {
    if (!state.jobId) return;
    state.timer = null;
    try {
      const response = await fetch(`/api/jobs/${encodeURIComponent(state.jobId)}`, {
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      if (!response.ok) {
        throw await parseError(response);
      }
      const job = await response.json();
      state.job = job;
      state.pollFailures = 0;
      renderJob(job);

      if (TERMINAL.has(job.status)) {
        setBusy(false);
        stopPolling();
        if (job.result_available) {
          await loadResult(!state.restored);
        } else if (job.status === "failed") {
          showError(job.error_message || "采集失败");
        } else if (job.status === "cancelled" && job.successes === 0) {
          el.progressMessage.textContent = "采集已取消，没有页面需要展示。";
        }
        state.restored = false;
        return;
      }
      schedulePoll(POLL_INTERVAL);
    } catch (error) {
      if (error.status === 404) {
        abandonJob("任务不存在或服务已重启，请重新开始采集。");
        return;
      }
      state.pollFailures += 1;
      if (state.pollFailures >= 6) {
        setBusy(false);
        el.progressMessage.textContent = "无法获取任务进度，请刷新页面重试。";
        showError(error.message || "无法获取任务进度");
        return;
      }
      el.progressMessage.textContent = "连接暂时中断，正在重试…";
      schedulePoll(1800);
    }
  }

  function renderJob(job) {
    const maxPages = Number(job.config?.max_pages || el.maxPages.value || 10);
    const done = Number(job.pages_done || 0);
    const successes = Number(job.successes || 0);
    const errors = Number(job.error_count || 0);
    const finished = TERMINAL.has(job.status);

    el.progressSection.hidden = false;
    el.idleSection.hidden = true;
    el.progressSection.classList.toggle("is-done", job.status === "completed");
    el.progressSection.classList.toggle("is-error", job.status === "failed");
    el.progressSection.classList.toggle(
      "is-cancelled",
      job.status === "cancelled" || job.status === "cancelling"
    );

    const labels = {
      queued: ["QUEUED", "任务已排队，等待开始…"],
      running: ["CRAWLING", "正在采集网页数据"],
      cancelling: ["STOPPING", "正在停止采集…"],
      completed: ["COMPLETE", "采集完成，页面数据已就绪"],
      failed: ["FAILED", "采集失败"],
      cancelled: ["CANCELLED", "采集已取消"],
    };
    const [kicker, title] = labels[job.status] || ["WORKING", "正在处理…"];
    el.progressKicker.textContent = kicker;
    el.progressTitle.textContent = title;
    el.progressUrl.textContent = job.current_url || job.start_url || "";
    el.progressMessage.textContent =
      job.status === "failed"
        ? job.error_message || job.message || "没有采集到页面"
        : job.message || "";

    let percent;
    if (job.status === "completed") {
      percent = 100;
    } else {
      percent = Math.min(100, Math.round((done / Math.max(1, maxPages)) * 100));
    }
    el.progressBar.value = percent;
    el.progressPercent.textContent = `${percent}%`;
    el.progressFraction.textContent = `${done} / ${maxPages} 页`;
    el.statDone.textContent = formatNumber(done);
    el.statSuccess.textContent = formatNumber(successes);
    el.statError.textContent = formatNumber(errors);

    let elapsed = job.duration_ms;
    if (elapsed == null && job.started_at) {
      elapsed = Date.now() - Date.parse(job.started_at);
    }
    el.statTime.textContent = formatDuration(elapsed);
    el.cancelBtn.hidden = finished || job.status === "cancelling";
    if (!finished) {
      el.cancelBtn.disabled = false;
    }
    if (!el.urlInput.value) {
      el.urlInput.value = job.start_url || "";
    }

    if (job.status === "completed") {
      document.title = "采集完成 · 蛛网";
    } else if (job.status === "failed") {
      document.title = "采集失败 · 蛛网";
    } else if (job.status === "cancelled") {
      document.title = "已取消 · 蛛网";
    } else {
      document.title = `采集中 ${done}/${maxPages} · 蛛网`;
    }
  }

  async function loadResult(shouldScroll) {
    if (!state.jobId || state.loadedFor === state.jobId) return;
    try {
      const response = await fetch(
        `/api/jobs/${encodeURIComponent(state.jobId)}/result`,
        { headers: { Accept: "application/json" }, cache: "no-store" }
      );
      if (response.status === 409) {
        schedulePoll(500);
        return;
      }
      if (!response.ok) {
        throw await parseError(response);
      }
      const result = await response.json();
      state.result = result;
      state.loadedFor = state.jobId;
      if (result.job) {
        state.job = result.job;
        renderJob(result.job);
      }
      renderResult(result, shouldScroll);
    } catch (error) {
      showError(error.message || "无法读取采集结果");
    }
  }

  function renderResult(result, shouldScroll) {
    const pages = Array.isArray(result.pages) ? result.pages : [];
    const errors = Array.isArray(result.errors) ? result.errors : [];
    const warnings = Array.isArray(result.warnings) ? result.warnings : [];
    const job = result.job || state.job || {};
    const summary = result.summary || {};
    const totalWords = pages.reduce(
      (total, page) => total + (Number(page.word_count) || 0),
      0
    );
    const totalLinks = pages.reduce(
      (total, page) => total + (page.links?.length || 0),
      0
    );
    const totalImages = pages.reduce(
      (total, page) => total + (page.images?.length || 0),
      0
    );
    const skipped = Number(summary.skipped_count || 0);

    el.idleSection.hidden = true;
    el.resultSection.hidden = false;
    const host = hostOf(job.start_url || pages[0]?.url || "");
    const duration = formatDuration(
      summary.duration_ms ?? job.duration_ms ?? 0
    );
    el.resultMeta.textContent = [
      host,
      `${pages.length} 个页面`,
      duration,
      errors.length ? `${errors.length} 条失败` : "",
    ]
      .filter(Boolean)
      .join(" · ");

    el.summaryChips.innerHTML = [
      chip(formatNumber(pages.length), "页面"),
      chip(formatNumber(totalWords), "字"),
      chip(formatNumber(totalLinks), "链接"),
      chip(formatNumber(totalImages), "图片"),
      errors.length ? chip(formatNumber(errors.length), "失败", true) : "",
      skipped ? chip(formatNumber(skipped), "robots 跳过", true) : "",
    ]
      .filter(Boolean)
      .join("");

    const downloadable = Boolean(job.result_available);
    el.downloadJson.hidden = !downloadable;
    el.downloadCsv.hidden = !downloadable;
    if (downloadable) {
      el.downloadJson.href = `/api/jobs/${encodeURIComponent(job.id)}/download/json`;
      el.downloadCsv.href = `/api/jobs/${encodeURIComponent(job.id)}/download/csv`;
    }

    state.filter = "";
    el.resultFilter.value = "";
    el.resultFilter.disabled = pages.length === 0;
    el.resultFilter.parentElement.hidden = pages.length === 0;
    el.resultTable.hidden = pages.length === 0;
    renderRows();

    if (!pages.length) {
      el.noResults.hidden = false;
      el.noResultsText.textContent = "没有采集到页面，请查看下方失败记录。";
      el.clearFilter.hidden = true;
    } else {
      el.noResults.hidden = true;
      el.noResultsText.textContent = "没有匹配的页面";
      el.clearFilter.hidden = false;
    }

    renderIssues(errors, warnings);

    if (shouldScroll) {
      window.setTimeout(() => {
        const reduceMotion = window.matchMedia(
          "(prefers-reduced-motion: reduce)"
        ).matches;
        el.resultSection.scrollIntoView({
          behavior: reduceMotion ? "auto" : "smooth",
          block: "start",
        });
      }, 180);
    }
  }

  function renderRows() {
    const pages = state.result?.pages || [];
    if (!pages.length) {
      el.resultRows.innerHTML = "";
      el.noResults.hidden = false;
      return;
    }

    const query = state.filter;
    const indices = [];
    pages.forEach((page, index) => {
      if (!query) {
        indices.push(index);
        return;
      }
      const haystack = `${page.title || ""} ${page.url || ""}`.toLowerCase();
      if (haystack.includes(query)) {
        indices.push(index);
      }
    });

    el.resultRows.innerHTML = indices
      .map((index) => {
        const page = pages[index];
        const title = escapeHtml(page.title || "（无标题）");
        const url = escapeHtml(page.url || "");
        const statusClass = page.redirected ? "is-redirect" : "";
        return `<tr data-page-index="${index}">
          <td class="cell-page">
            <span class="page-title">${title}</span>
            <span class="page-url" title="${url}">${url}</span>
          </td>
          <td class="cell-number">${formatNumber(page.word_count || 0)}</td>
          <td class="cell-number">${formatNumber(page.links?.length || 0)}</td>
          <td class="cell-number">${formatNumber(page.images?.length || 0)}</td>
          <td><span class="status-badge ${statusClass}">${escapeHtml(page.status || "")}</span></td>
          <td><button type="button" class="row-open" data-page-index="${index}">查看 →</button></td>
        </tr>`;
      })
      .join("");

    const empty = indices.length === 0;
    el.noResults.hidden = !empty;
    if (empty) {
      el.noResultsText.textContent = "没有匹配的页面";
      el.clearFilter.hidden = false;
    }
  }

  function renderIssues(errors, warnings) {
    el.errorDetails.hidden = errors.length === 0;
    el.errorSummary.textContent = `${errors.length} 条失败记录`;
    el.errorList.innerHTML = errors
      .map(
        (error) =>
          `<li><strong>失败</strong> · ${escapeHtml(error.message || "未知错误")}<br>${escapeHtml(error.url || "")}</li>`
      )
      .join("");

    el.warningDetails.hidden = warnings.length === 0;
    el.warningSummary.textContent = `${warnings.length} 条提示`;
    el.warningList.innerHTML = warnings
      .map((warning) => `<li><strong>提示</strong> · ${escapeHtml(warning)}</li>`)
      .join("");
  }

  function openDetail(index) {
    const page = state.result?.pages?.[index];
    if (!page) return;

    el.detailTitle.textContent = page.title || "（无标题）";
    el.detailUrl.textContent = page.url || "";
    el.detailUrl.href = safeUrl(page.url);

    const sections = [];
    sections.push(`
      <div class="detail-stats">
        <span class="detail-stat">状态 <strong>${escapeHtml(page.status)}</strong></span>
        <span class="detail-stat">字数 <strong>${formatNumber(page.word_count || 0)}</strong></span>
        <span class="detail-stat">深度 <strong>${escapeHtml(page.depth)}</strong></span>
        <span class="detail-stat">链接 <strong>${formatNumber(page.links?.length || 0)}</strong></span>
        <span class="detail-stat">图片 <strong>${formatNumber(page.images?.length || 0)}</strong></span>
        <span class="detail-stat">${formatBytes(page.size_bytes)}</span>
        <span class="detail-stat">${formatDuration(page.elapsed_ms)}</span>
      </div>
    `);

    if (page.description) {
      sections.push(section("页面描述", `<p class="detail-description">${escapeHtml(page.description)}</p>`));
    }

    sections.push(
      section(
        "正文内容",
        `<pre class="detail-text">${escapeHtml(page.text || "（没有提取到正文）")}</pre>` +
          (page.text_truncated
            ? '<p class="empty-note mt-8">正文超出长度限制，仅显示前部分；完整内容以 JSON 为准。</p>'
            : "")
      )
    );

    const headings = Array.isArray(page.headings) ? page.headings : [];
    sections.push(
      section(
        "标题结构",
        headings.length
          ? `<div class="tag-list">${headings
              .map(
                (heading) =>
                  `<span class="tag"><b>H${escapeHtml(heading.level)}</b>${escapeHtml(heading.text)}</span>`
              )
              .join("")}</div>`
          : '<p class="empty-note">该页面没有可识别的标题。</p>'
      )
    );

    const links = Array.isArray(page.links) ? page.links : [];
    sections.push(section("页面链接", renderLinks(links)));

    const images = Array.isArray(page.images) ? page.images : [];
    sections.push(section("图片资源", renderImages(images)));

    const tables = Array.isArray(page.tables) ? page.tables : [];
    if (tables.length) {
      sections.push(section("表格数据", renderTables(tables)));
    }

    const meta = page.meta && typeof page.meta === "object" ? page.meta : {};
    const metaEntries = Object.entries(meta).slice(0, 14);
    if (metaEntries.length) {
      sections.push(
        section(
          "元信息",
          `<dl class="meta-grid">${metaEntries
            .map(
              ([key, value]) =>
                `<div><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd></div>`
            )
            .join("")}</dl>`
        )
      );
    }

    const structured = Array.isArray(page.structured_data)
      ? page.structured_data
      : [];
    if (structured.length) {
      sections.push(
        section(
          "结构化数据（JSON-LD）",
          `<div class="tag-list">${structured
            .map(
              (item) =>
                `<span class="tag"><b>${escapeHtml(item.type)}</b>${escapeHtml(item.name || item.url || "")}</span>`
            )
            .join("")}</div>`
        )
      );
    }

    sections.push(`
      <div class="dialog-foot">
        <button type="button" class="btn" data-copy-url="${escapeHtml(page.url || "")}">复制页面链接</button>
      </div>
    `);

    el.detailBody.innerHTML = sections.join("");
    el.detailBody.scrollTop = 0;
    if (!el.detailDialog.open) {
      el.detailDialog.showModal();
    }
  }

  function renderLinks(links) {
    if (!links.length) {
      return '<p class="empty-note">该页面没有可提取的链接。</p>';
    }
    const visible = links.slice(0, 80);
    const rows = visible
      .map((link) => {
        const href = safeUrl(link.url);
        const label = link.text || link.url;
        const kind = `${link.internal ? "站内" : "站外"}${link.nofollow ? " · nofollow" : ""}`;
        return `<li>
          <a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer" title="${escapeHtml(link.url)}">${escapeHtml(label)}</a>
          <span class="link-kind">${kind}</span>
        </li>`;
      })
      .join("");
    return `<ul class="link-rows">${rows}</ul>${
      links.length > visible.length
        ? `<p class="empty-note mt-8">仅显示前 ${visible.length} 条链接，完整数据请下载 JSON。</p>`
        : ""
    }`;
  }

  function renderImages(images) {
    if (!images.length) {
      return '<p class="empty-note">该页面没有可提取的图片。</p>';
    }
    const visible = images.slice(0, 30);
    return `<div class="image-urls">${visible
      .map((image) => {
        const label = image.alt ? `${image.alt} · ${image.url}` : image.url;
        return `<a href="${escapeHtml(safeUrl(image.url))}" target="_blank" rel="noopener noreferrer" title="${escapeHtml(image.url)}">${escapeHtml(label)}</a>`;
      })
      .join("")}</div>${
      images.length > visible.length
        ? `<p class="empty-note mt-8">仅显示前 ${visible.length} 张图片，完整数据请下载 JSON。</p>`
        : ""
    }`;
  }

  function renderTables(tables) {
    return tables
      .map((table) => {
        const rows = (table.rows || []).slice(0, 15);
        const caption = table.caption
          ? `<p class="table-caption">${escapeHtml(table.caption)}</p>`
          : "";
        const body = rows
          .map(
            (row) =>
              `<tr>${row
                .map((cell) => `<td>${escapeHtml(cell)}</td>`)
                .join("")}</tr>`
          )
          .join("");
        const truncation =
          (table.rows?.length || 0) > rows.length
            ? `<p class="empty-note mt-6">仅显示前 ${rows.length} 行。</p>`
            : "";
        return `<div class="table-block">${caption}<table class="mini-table">${body}</table>${truncation}</div>`;
      })
      .join("");
  }

  function section(title, content) {
    return `<section class="detail-section"><h3>${title}</h3>${content}</section>`;
  }

  async function cancelJob() {
    if (!state.jobId) return;
    el.cancelBtn.disabled = true;
    el.cancelBtn.textContent = "正在停止…";
    try {
      const response = await fetch(
        `/api/jobs/${encodeURIComponent(state.jobId)}/cancel`,
        { method: "POST", headers: { Accept: "application/json" } }
      );
      if (!response.ok) {
        throw await parseError(response);
      }
      const job = await response.json();
      state.job = job;
      renderJob(job);
      schedulePoll(250);
    } catch (error) {
      el.cancelBtn.disabled = false;
      el.cancelBtn.innerHTML =
        '<span class="stop-square" aria-hidden="true"></span>停止采集';
      showError(error.message || "停止采集失败");
    }
  }

  function restoreLastJob() {
    const jobId = readStoredJobId();
    if (!jobId) return;
    state.jobId = jobId;
    state.restored = true;
    setBusy(true);
    el.idleSection.hidden = true;
    el.progressSection.hidden = false;
    el.progressKicker.textContent = "RESTORING";
    el.progressTitle.textContent = "正在恢复上次采集任务…";
    el.progressMessage.textContent = "正在读取任务状态…";
    pollJob();
  }

  function abandonJob(message) {
    stopPolling();
    setBusy(false);
    clearStoredJobId();
    state.jobId = null;
    state.job = null;
    state.restored = false;
    el.progressSection.hidden = true;
    el.idleSection.hidden = false;
    document.title = "蛛网 · 网页数据采集器";
    showError(message);
  }

  function showError(message) {
    el.errorText.textContent = message;
    el.errorBanner.hidden = false;
  }

  function hideError() {
    el.errorBanner.hidden = true;
    el.errorText.textContent = "";
  }

  function closeDialogIfOpen() {
    if (el.detailDialog.open) {
      el.detailDialog.close();
    }
  }

  function storeJobId(jobId) {
    try {
      window.localStorage.setItem(STORAGE_KEY, jobId);
    } catch {
      /* 隐私模式下忽略 */
    }
  }

  function readStoredJobId() {
    try {
      return window.localStorage.getItem(STORAGE_KEY);
    } catch {
      return null;
    }
  }

  function clearStoredJobId() {
    try {
      window.localStorage.removeItem(STORAGE_KEY);
    } catch {
      /* ignore */
    }
  }

  async function parseError(response) {
    let message = `请求失败（HTTP ${response.status}）`;
    try {
      const data = await response.json();
      if (data && data.error) message = data.error;
    } catch {
      /* 响应不是 JSON */
    }
    const error = new Error(message);
    error.status = response.status;
    return error;
  }

  async function copyText(text) {
    if (!text) return;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
      } else {
        const input = document.createElement("textarea");
        input.value = text;
        input.setAttribute("readonly", "");
        input.className = "copy-helper";
        document.body.appendChild(input);
        input.select();
        document.execCommand("copy");
        input.remove();
      }
      showToast("链接已复制");
    } catch {
      showToast("复制失败，请手动选择链接");
    }
  }

  function showToast(message) {
    el.toast.textContent = message;
    el.toast.hidden = false;
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => {
      el.toast.hidden = true;
    }, 2200);
  }

  function chip(value, label, warning = false) {
    return `<span class="chip${warning ? " is-warn" : ""}"><strong>${value}</strong>${label}</span>`;
  }

  function safeUrl(value) {
    try {
      const url = new URL(value);
      return ["http:", "https:"].includes(url.protocol) ? url.href : "#";
    } catch {
      return "#";
    }
  }

  function hostOf(value) {
    try {
      return new URL(value).hostname;
    } catch {
      return value || "";
    }
  }

  function formatNumber(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "0";
    return new Intl.NumberFormat("zh-CN").format(number);
  }

  function formatDuration(milliseconds) {
    if (milliseconds == null || !Number.isFinite(Number(milliseconds))) {
      return "—";
    }
    const ms = Math.max(0, Number(milliseconds));
    if (ms < 1000) return `${(ms / 1000).toFixed(1)}s`;
    if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
    const minutes = Math.floor(ms / 60000);
    const seconds = Math.round((ms % 60000) / 1000);
    return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
  }

  function formatBytes(bytes) {
    const value = Number(bytes);
    if (!Number.isFinite(value) || value <= 0) return "—";
    if (value < 1024) return `${value} B`;
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
    return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => {
      return {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      }[character];
    });
  }

  init();
})();
