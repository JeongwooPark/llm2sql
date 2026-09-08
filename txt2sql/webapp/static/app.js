(() => {
  const messagesEl = document.getElementById("messages");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("question");
  const sendBtn = document.getElementById("send");
  const newChatBtn = document.getElementById("new-chat");
  const suggestions = document.getElementById("suggestions");

  const SESSION_KEY = "txt2sql_session_id";
  let sessionId = localStorage.getItem(SESSION_KEY) || null;
  let busy = false;
  let stickToBottom = true;
  let scrollRaf = 0;
  const includeMap = document.body?.dataset?.ui === "map";

  function chatPayload(question) {
    return {
      question,
      session_id: sessionId,
      include_map: includeMap,
    };
  }

  function isNearBottom(threshold = 80) {
    const gap =
      messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight;
    return gap <= threshold;
  }

  function scrollToBottom(force = false) {
    if (!force && !stickToBottom) return;
    if (scrollRaf) cancelAnimationFrame(scrollRaf);
    const pin = () => {
      messagesEl.scrollTop = messagesEl.scrollHeight;
    };
    scrollRaf = requestAnimationFrame(() => {
      scrollRaf = 0;
      pin();
      // 스트리밍·메타/SQL 블록 추가로 높이가 늘어난 뒤 한 번 더
      requestAnimationFrame(pin);
    });
  }

  messagesEl.addEventListener(
    "scroll",
    () => {
      stickToBottom = isNearBottom();
    },
    { passive: true }
  );

  function clearWelcome() {
    const welcome = messagesEl.querySelector(".welcome");
    if (welcome) welcome.remove();
  }

  function appendUser(text) {
    clearWelcome();
    stickToBottom = true;
    const row = document.createElement("div");
    row.className = "row user";
    row.innerHTML = `<div class="bubble"></div>`;
    row.querySelector(".bubble").textContent = text;
    messagesEl.appendChild(row);
    scrollToBottom(true);
  }

  function appendBotShell() {
    clearWelcome();
    stickToBottom = true;
    const row = document.createElement("div");
    row.className = "row bot";
    row.innerHTML = `
      <div class="avatar">AI</div>
      <div class="bubble">
        <div class="status">
          <span class="spinner" aria-hidden="true"></span>
          <span class="status-text">질문 분석 중…</span>
        </div>
        <div class="answer" hidden></div>
        <div class="result-table-wrap" hidden></div>
        <div class="chart-offer" hidden>
          <p class="chart-offer-text">이 내용을 차트로도 정리할 수 있어요. 차트로 보시겠어요?</p>
          <div class="choices chart-choices"></div>
        </div>
        <div class="chart-wrap" hidden><canvas></canvas></div>
        <div class="meta" hidden></div>
        <details class="sql-details" hidden>
          <summary class="sql-summary">
            <svg class="sql-chevron" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
              <path fill="currentColor" d="M6.2 3.2a.75.75 0 0 1 1.06 0l4.5 4.5a.75.75 0 0 1 0 1.06l-4.5 4.5A.75.75 0 0 1 6.2 12.2L10.14 8.25 6.2 4.3a.75.75 0 0 1 0-1.1z"/>
            </svg>
            <span>SQL</span>
          </summary>
          <pre class="sql-block"></pre>
        </details>
      </div>
    `;
    messagesEl.appendChild(row);
    scrollToBottom(true);
    return {
      row,
      bubble: row.querySelector(".bubble"),
      status: row.querySelector(".status"),
      statusText: row.querySelector(".status-text"),
      answer: row.querySelector(".answer"),
      tableWrap: row.querySelector(".result-table-wrap"),
      chartOffer: row.querySelector(".chart-offer"),
      chartChoices: row.querySelector(".chart-choices"),
      chartWrap: row.querySelector(".chart-wrap"),
      chartCanvas: row.querySelector(".chart-wrap canvas"),
      meta: row.querySelector(".meta"),
      sqlDetails: row.querySelector(".sql-details"),
      sql: row.querySelector(".sql-block"),
    };
  }

  function setBusy(next) {
    busy = next;
    sendBtn.disabled = next;
    input.disabled = next;
  }

  function stageLabel(stage, message) {
    const map = {
      start: "질문 수신",
      route: "의도 분석",
      schema: "스키마 검색",
      llm: "SQL 생성",
      sql: "SQL 준비",
      validate: "검증",
      execute: "DB 조회",
      result: "결과 정리",
      answer: "답변 작성",
      clarify: "확인 필요",
      meta: "메타 설명",
      profile: "특징 요약",
      map: "지도 발행",
      error: "오류 처리",
    };
    return message || map[stage] || stage;
  }

  function networkErrorMessage(err) {
    const msg = err instanceof Error ? err.message : String(err || "");
    if (
      msg === "Failed to fetch" ||
      /networkerror when attempting to fetch/i.test(msg) ||
      msg === "Load failed" ||
      msg === "NetworkError"
    ) {
      return (
        "서버와 연결이 끊겼습니다. 웹 서버가 실행 중인지 확인하거나, " +
        "구 전체 비교처럼 무거운 질의는 잠시 후 다시 시도해 주세요."
      );
    }
    return msg || "알 수 없는 오류가 발생했습니다.";
  }

  function clearSession() {
    sessionId = null;
    try {
      localStorage.removeItem(SESSION_KEY);
    } catch {
      /* ignore */
    }
  }

  async function ensureSession({ forceNew = false } = {}) {
    if (sessionId && !forceNew) return sessionId;
    const res = await fetch("/api/session", { method: "POST" });
    if (!res.ok) throw new Error("세션을 만들 수 없습니다.");
    const data = await res.json();
    sessionId = data.session_id;
    localStorage.setItem(SESSION_KEY, sessionId);
    return sessionId;
  }

  async function readHttpError(res) {
    try {
      const data = await res.json();
      const detail = data?.detail;
      if (typeof detail === "string") {
        return { message: detail, code: data?.code || null };
      }
      if (detail && typeof detail === "object") {
        return {
          message:
            detail.detail ||
            detail.message ||
            `요청 실패 (${res.status})`,
          code: detail.code || data?.code || null,
        };
      }
      return {
        message: data?.message || `요청 실패 (${res.status})`,
        code: data?.code || null,
      };
    } catch {
      return { message: `요청 실패 (${res.status})`, code: null };
    }
  }

  async function openChatStream(question, { retried = false } = {}) {
    await ensureSession();
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(chatPayload(question)),
    });
    if (res.ok && res.body) return res;
    const err = await readHttpError(res);
    if (
      !retried &&
      (err.code === "session_not_found" || err.code === "session_invalid")
    ) {
      clearSession();
      await ensureSession({ forceNew: true });
      return openChatStream(question, { retried: true });
    }
    throw new Error(err.message || `요청 실패 (${res.status})`);
  }

  async function sendQuestion(question) {
    const q = question.trim();
    if (!q || busy) return;

    setBusy(true);
    appendUser(q);
    input.value = "";
    autoResize();

    const shell = appendBotShell();

    try {
      const res = await openChatStream(q);
      if (!res.ok || !res.body) {
        throw new Error(`요청 실패 (${res.status})`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      const state = {
        startedAnswer: false,
        streamed: "",
        finished: false,
      };

      const ingest = (chunk) => {
        buffer += chunk;
        const parts = buffer.split("\n\n");
        buffer = parts.pop() || "";
        for (const part of parts) {
          const line = part.split("\n").find((l) => l.startsWith("data: "));
          if (!line) continue;
          let evt;
          try {
            evt = JSON.parse(line.slice(6));
          } catch {
            continue;
          }
          handleEvent(evt, shell, state);
        }
      };

      while (true) {
        const { value, done } = await reader.read();
        if (value) ingest(decoder.decode(value, { stream: true }));
        if (done) {
          ingest(decoder.decode());
          if (buffer.trim()) ingest(`${buffer}\n\n`);
          break;
        }
      }

      if (!state.finished) {
        finishBot(shell, state.streamed || "(응답이 중간에 끊겼습니다.)");
      }
    } catch (err) {
      finishBot(
        shell,
        networkErrorMessage(err),
        { error: true }
      );
    } finally {
      hideLoading(shell);
      setBusy(false);
      input.focus();
      scrollToBottom(true);
    }
  }

  function hideLoading(shell) {
    if (!shell?.status) return;
    shell.status.hidden = true;
    shell.status.style.display = "none";
    const spinner = shell.status.querySelector(".spinner");
    if (spinner) spinner.remove();
    const cursor = shell.answer?.querySelector(".cursor");
    if (cursor) cursor.remove();
  }

  function parsePipeRow(line) {
    const trimmed = String(line || "").trim();
    if (!trimmed.startsWith("|")) return null;
    return trimmed
      .replace(/^\||\|$/g, "")
      .split("|")
      .map((cell) => cell.trim());
  }

  function isPipeSeparator(line) {
    const cells = parsePipeRow(line);
    return Boolean(
      cells &&
        cells.length &&
        cells.every((cell) => /^:?-{3,}:?$/.test(cell.replace(/\s+/g, "")))
    );
  }

  function splitMarkdownTables(text) {
    const raw = String(text || "").replace(/\r\n/g, "\n");
    const lines = raw.split("\n");
    const tables = [];
    const kept = [];
    for (let i = 0; i < lines.length; i += 1) {
      const header = parsePipeRow(lines[i]);
      const sep = i + 1 < lines.length && isPipeSeparator(lines[i + 1]);
      if (!header || !sep) {
        kept.push(lines[i]);
        continue;
      }
      const rows = [];
      let j = i + 2;
      while (j < lines.length) {
        const cells = parsePipeRow(lines[j]);
        if (!cells) break;
        rows.push(cells);
        j += 1;
      }
      tables.push({ headers: header, rows });
      i = j - 1;
    }
    const prose = kept.join("\n").replace(/\n{3,}/g, "\n\n").trim();
    return { prose, tables };
  }

  function tableFromMarkdown(parsed) {
    if (!parsed?.headers || parsed.headers.length < 2) return null;
    const body = [];
    let total = 0;
    let peak = null;
    for (const cells of parsed.rows || []) {
      if (!cells.length || cells[0] === "합계") continue;
      const n = Number(String(cells[1] || "").replace(/,/g, ""));
      const pct = parseFloat(String(cells[2] || "").replace(/%/g, ""));
      const row = {
        range: cells[0],
        n: Number.isFinite(n) ? n : 0,
        pct: Number.isFinite(pct) ? pct : 0,
      };
      body.push(row);
      total += row.n;
      if (!peak || row.n > peak.n) peak = row;
    }
    if (!body.length) return null;
    return {
      range_header: parsed.headers[0],
      count_header: parsed.headers[1],
      share_header: parsed.headers[2] || "비율",
      rows: body,
      total,
      peak,
    };
  }

  function proseWithoutMarkdownTable(text) {
    return splitMarkdownTables(text).prose;
  }

  function renderResultTable(shell, table) {
    if (!shell?.tableWrap || !table || !Array.isArray(table.rows)) return;
    const wrap = shell.tableWrap;
    wrap.hidden = false;
    wrap.innerHTML = "";
    const el = document.createElement("table");
    el.className = "result-table";
    const thead = document.createElement("thead");
    const hr = document.createElement("tr");
    for (const key of ["range_header", "count_header", "share_header"]) {
      const th = document.createElement("th");
      th.textContent =
        table[key] ||
        { range_header: "구간", count_header: "동 수", share_header: "비율" }[
          key
        ];
      hr.appendChild(th);
    }
    thead.appendChild(hr);
    el.appendChild(thead);
    const tbody = document.createElement("tbody");
    const peakRange = table.peak && table.peak.range;
    for (const row of table.rows) {
      const tr = document.createElement("tr");
      if (peakRange && row.range === peakRange) tr.className = "peak";
      const c1 = document.createElement("td");
      c1.textContent = String(row.range ?? "");
      const c2 = document.createElement("td");
      c2.textContent = Number(row.n || 0).toLocaleString("ko-KR");
      const c3 = document.createElement("td");
      const pct = Number(row.pct);
      c3.textContent = Number.isFinite(pct) ? `${pct}%` : "";
      tr.append(c1, c2, c3);
      tbody.appendChild(tr);
    }
    el.appendChild(tbody);
    const tfoot = document.createElement("tfoot");
    const fr = document.createElement("tr");
    const f1 = document.createElement("td");
    f1.textContent = "합계";
    const f2 = document.createElement("td");
    f2.textContent = Number(table.total || 0).toLocaleString("ko-KR");
    const f3 = document.createElement("td");
    f3.textContent = "100%";
    fr.append(f1, f2, f3);
    tfoot.appendChild(fr);
    el.appendChild(tfoot);
    wrap.appendChild(el);
  }

  function finishBot(shell, text, { error = false, table = null } = {}) {
    hideLoading(shell);
    shell.answer.hidden = false;
    const parsed = splitMarkdownTables(text);
    const shown = parsed.prose || (table ? "" : String(text || ""));
    shell.answer.textContent = shown;
    const resolved =
      table && Array.isArray(table.rows) && table.rows.length
        ? table
        : parsed.tables.map(tableFromMarkdown).find(Boolean) || null;
    if (resolved) renderResultTable(shell, resolved);
    if (error) shell.bubble.classList.add("error");
  }

  function handleEvent(evt, shell, state) {
    if (evt.type === "ready" && evt.session_id) {
      sessionId = evt.session_id;
      localStorage.setItem(SESSION_KEY, sessionId);
      return;
    }

    if (evt.type === "progress") {
      if (!state.startedAnswer && !state.finished) {
        shell.statusText.textContent = stageLabel(evt.stage, evt.message);
      }
      return;
    }

    if (evt.type === "token") {
      if (state.finished) return;
      if (!state.startedAnswer) {
        state.startedAnswer = true;
        hideLoading(shell);
        shell.answer.hidden = false;
        shell.answer.textContent = "";
        const cursor = document.createElement("span");
        cursor.className = "cursor";
        shell.answer.appendChild(document.createTextNode(""));
        shell.answer.appendChild(cursor);
      }
      state.streamed += evt.text || "";
      const cursor = shell.answer.querySelector(".cursor");
      if (shell.answer.firstChild) {
        shell.answer.firstChild.textContent = state.streamed;
      }
      if (cursor) shell.answer.appendChild(cursor);
      scrollToBottom();
      return;
    }

    if (evt.type === "done") {
      state.finished = true;
      if (evt.session_id) {
        sessionId = evt.session_id;
        localStorage.setItem(SESSION_KEY, sessionId);
      }
      const result = evt.result || {};
      const finalAnswer = result.answer || state.streamed || "(답변 없음)";

      finishBot(shell, finalAnswer, { error: !result.ok, table: result.table });

      const chips = [];
      if (result.route) chips.push(`route: ${result.route}`);
      if (result.row_count != null) chips.push(`rows: ${result.row_count}`);
      if (Array.isArray(result.tables) && result.tables.length) {
        chips.push(`tables: ${result.tables.slice(0, 3).join(", ")}`);
      }
      if (chips.length) {
        shell.meta.hidden = false;
        shell.meta.innerHTML = chips
          .map((c) => `<span class="chip">${escapeHtml(c)}</span>`)
          .join("");
      }
      if (result.sql) {
        if (shell.sqlDetails) shell.sqlDetails.hidden = false;
        shell.sql.hidden = false;
        shell.sql.textContent = result.sql;
      }

      if (window.Txt2SqlMap) {
        if (result.map && result.map.available) {
          window.Txt2SqlMap.addAnalysisLayer(result.map);
        } else if (result.map && result.map.error) {
          window.Txt2SqlMap.showBanner(result.map.error);
        }
      }

      if (
        result.route === "clarify_place" &&
        Array.isArray(result.rows) &&
        result.rows.length > 0
      ) {
        const choices = document.createElement("div");
        choices.className = "choices";
        result.rows.forEach((opt, i) => {
          const n = i + 1;
          const place = opt.place || opt.A4 || `${n}번`;
          const count =
            opt.n != null ? ` · 건물 ${Number(opt.n).toLocaleString("ko-KR")}동` : "";
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "choice-btn";
          btn.dataset.q = String(n);
          btn.innerHTML = `<span class="choice-num">${n}</span><span class="choice-label">${escapeHtml(
            String(place)
          )}${escapeHtml(count)}</span>`;
          choices.appendChild(btn);
        });
        shell.bubble.appendChild(choices);
      }

      const chartSpec = result.chart || result.chart_spec;
      if (chartSpec && (result.route === "chart_render" || result.chart)) {
        ensureChartShell(shell);
        renderChart(shell, chartSpec);
      } else if (result.chart_offer && result.chart_spec) {
        const offerRe =
          /(?:\n\n)?이 내용을 차트로도 정리할 수 있어요\.\s*차트로 보시겠어요\?\s*$/;
        if (shell.answer) {
          shell.answer.textContent = String(shell.answer.textContent || "")
            .replace(offerRe, "")
            .trim();
          if (!shell.answer.textContent) shell.answer.hidden = true;
        }
        const offer = shell.chartOffer;
        const choices =
          shell.chartChoices || offer?.querySelector(".chart-choices");
        if (offer && choices) {
          choices.innerHTML = "";
          choices.dataset.chartSpec = JSON.stringify(result.chart_spec);
          const yes = document.createElement("button");
          yes.type = "button";
          yes.className = "choice-btn chart-accept";
          yes.dataset.q = "차트로 보여줘";
          yes.innerHTML =
            '<span class="choice-num">네</span><span class="choice-label">차트로 보기</span>';
          const no = document.createElement("button");
          no.type = "button";
          no.className = "choice-btn chart-decline";
          no.dataset.q = "괜찮아요";
          no.innerHTML =
            '<span class="choice-num">아니요</span><span class="choice-label">텍스트만 볼게요</span>';
          choices.appendChild(yes);
          choices.appendChild(no);
          offer.hidden = false;
        }
      }

      scrollToBottom(true);
      return;
    }

    if (evt.type === "error") {
      state.finished = true;
      finishBot(shell, evt.message || "오류가 발생했습니다.", { error: true });
      scrollToBottom(true);
    }
  }

  function escapeHtml(s) {
    return String(s)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  const CHART_COLORS = [
    "#2ec4b6",
    "#f4a261",
    "#5b8def",
    "#e76f51",
    "#9b5de5",
    "#00bbf9",
    "#fee440",
    "#8ac926",
    "#ff85a1",
    "#90e0ef",
  ];

  function ensureChartShell(shell) {
    if (!shell) return;
    if (!shell.chartWrap || !shell.chartCanvas) {
      const wrap = document.createElement("div");
      wrap.className = "chart-wrap";
      wrap.hidden = true;
      const canvas = document.createElement("canvas");
      wrap.appendChild(canvas);
      const anchor = shell.answer?.nextSibling;
      if (shell.bubble) {
        shell.bubble.insertBefore(wrap, anchor || shell.meta || null);
      }
      shell.chartWrap = wrap;
      shell.chartCanvas = canvas;
    }
  }

  function renderChart(shell, spec) {
    ensureChartShell(shell);
    if (!shell?.chartWrap || !spec) return;
    const labels = Array.isArray(spec.labels) ? spec.labels : [];
    const datasetsIn = Array.isArray(spec.datasets) ? spec.datasets : [];
    if (!labels.length || !datasetsIn.length) return;

    shell.chartWrap.hidden = false;
    shell.chartWrap.removeAttribute("hidden");

    if (typeof Chart === "undefined") {
      shell.chartWrap.textContent =
        "차트를 표시할 수 없습니다. 페이지를 새로고침한 뒤 다시 시도해 주세요.";
      return;
    }

    // 기존 canvas가 파괴됐을 수 있어 항상 새로 준비
    shell.chartWrap.innerHTML = "";
    const canvas = document.createElement("canvas");
    shell.chartWrap.appendChild(canvas);
    shell.chartCanvas = canvas;

    if (shell._chart) {
      try {
        shell._chart.destroy();
      } catch {
        /* ignore */
      }
      shell._chart = null;
    }

    const type =
      spec.type === "pie" ||
      spec.type === "doughnut" ||
      spec.type === "line"
        ? spec.type
        : "bar";
    const datasets = datasetsIn.map((ds, i) => {
      const data = Array.isArray(ds.data) ? ds.data : [];
      const base = {
        label: ds.label || "값",
        data,
        borderWidth: 1,
      };
      if (type === "pie" || type === "doughnut") {
        return {
          ...base,
          backgroundColor: labels.map(
            (_, j) => CHART_COLORS[j % CHART_COLORS.length]
          ),
          borderColor: "rgba(11, 28, 36, 0.85)",
        };
      }
      if (type === "line") {
        return {
          ...base,
          borderColor: CHART_COLORS[i % CHART_COLORS.length],
          backgroundColor: "transparent",
          tension: 0.25,
          pointRadius: 4,
        };
      }
      return {
        ...base,
        backgroundColor: CHART_COLORS[i % CHART_COLORS.length],
        borderColor: CHART_COLORS[i % CHART_COLORS.length],
        borderRadius: 6,
        maxBarThickness: 42,
      };
    });

    const options = {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        title: {
          display: Boolean(spec.title),
          text: spec.title || "",
          color: "#e8f2f6",
          font: { size: 14, weight: "600", family: "IBM Plex Sans KR" },
          padding: { bottom: 10 },
        },
        legend: {
          position: type === "bar" ? "top" : "right",
          labels: {
            color: "#8eacba",
            boxWidth: 12,
            font: { family: "IBM Plex Sans KR", size: 11 },
          },
        },
        tooltip: {
          callbacks: {
            label(ctx) {
              let v = ctx.parsed;
              if (v && typeof v === "object") {
                v = v.y ?? v.r ?? Object.values(v)[0];
              }
              const num =
                typeof v === "number"
                  ? v.toLocaleString("ko-KR")
                  : String(v ?? "");
              const unit = spec.unit ? ` ${spec.unit}` : "";
              const name = ctx.dataset.label ? `${ctx.dataset.label}: ` : "";
              return `${name}${num}${unit}`;
            },
          },
        },
      },
    };
    if (type === "bar" || type === "line") {
      options.scales = {
        x: {
          ticks: { color: "#8eacba", maxRotation: 45, minRotation: 0 },
          grid: { color: "rgba(142, 172, 186, 0.12)" },
        },
        y: {
          beginAtZero: true,
          ticks: { color: "#8eacba" },
          grid: { color: "rgba(142, 172, 186, 0.12)" },
        },
      };
    }

    const paint = () => {
      try {
        shell._chart = new Chart(canvas, {
          type,
          data: { labels, datasets },
          options,
        });
      } catch (err) {
        shell.chartWrap.textContent = `차트 렌더링 오류: ${
          err instanceof Error ? err.message : String(err)
        }`;
      }
      scrollToBottom(true);
    };
    requestAnimationFrame(() => requestAnimationFrame(paint));
  }

  function autoResize() {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 140)}px`;
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    sendQuestion(input.value);
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendQuestion(input.value);
    }
  });

  input.addEventListener("input", autoResize);

  suggestions?.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-q]");
    if (!btn) return;
    sendQuestion(btn.getAttribute("data-q") || "");
  });

  messagesEl.addEventListener("click", (e) => {
    const btn = e.target.closest("button.choice-btn[data-q]");
    if (!btn || busy) return;

    // 차트 수락: 같은 답변 카드에만 그리고, 중복 메시지/차트는 만들지 않음
    if (btn.classList.contains("chart-accept")) {
      const box = btn.closest(".chart-choices");
      let spec = null;
      try {
        spec = box?.dataset?.chartSpec
          ? JSON.parse(box.dataset.chartSpec)
          : null;
      } catch {
        spec = null;
      }
      const bubble = btn.closest(".bubble");
      const offerBox = btn.closest(".chart-offer");
      if (offerBox) offerBox.hidden = true;
      else box?.remove();
      if (spec && bubble) {
        const shell = {
          bubble,
          answer: bubble.querySelector(".answer"),
          chartWrap: bubble.querySelector(".chart-wrap"),
          chartCanvas: bubble.querySelector(".chart-wrap canvas"),
          meta: bubble.querySelector(".meta"),
        };
        ensureChartShell(shell);
        renderChart(shell, spec);
      }
      // 세션만 동기화 (화면에 두 번째 차트/메시지를 추가하지 않음)
      syncSessionQuestion(btn.getAttribute("data-q") || "차트로 보여줘");
      return;
    }

    // 차트 거절: 버튼만 닫고 세션 동기화
    if (btn.classList.contains("chart-decline")) {
      const offer = btn.closest(".chart-offer");
      if (offer) offer.hidden = true;
      else btn.closest(".chart-choices")?.remove();
      syncSessionQuestion(btn.getAttribute("data-q") || "괜찮아요");
      return;
    }

    sendQuestion(btn.getAttribute("data-q") || "");
  });

  async function syncSessionQuestion(question) {
    const q = String(question || "").trim();
    if (!q) return;
    try {
      const res = await openChatStream(q);
      if (!res.ok || !res.body) return;
      const reader = res.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        if (value) buffer += decoder.decode(value, { stream: true });
        if (done) {
          buffer += decoder.decode();
          break;
        }
      }
      // SSE done 이벤트에서 session_id만 갱신
      for (const part of buffer.split("\n\n")) {
        const line = part.split("\n").find((l) => l.startsWith("data: "));
        if (!line) continue;
        try {
          const evt = JSON.parse(line.slice(6));
          if (evt.session_id) {
            sessionId = evt.session_id;
            localStorage.setItem(SESSION_KEY, sessionId);
          }
        } catch {
          /* ignore */
        }
      }
    } catch {
      /* ignore sync errors */
    }
  }

  newChatBtn.addEventListener("click", async () => {
    const oldSession = sessionId;
    if (
      document.body?.dataset?.ui === "map" &&
      window.Txt2SqlMap &&
      typeof window.Txt2SqlMap.clearAnalysis === "function"
    ) {
      try {
        await window.Txt2SqlMap.clearAnalysis(oldSession);
      } catch {
        /* ignore */
      }
    }
    localStorage.removeItem(SESSION_KEY);
    sessionId = null;
    messagesEl.innerHTML = `
      <div class="welcome">
        <h2>무엇을 찾아볼까요?</h2>
        <p>
          건물 건수·순위·특징, 메타데이터 설명, 후속 질문까지 대화로
          이어집니다.
        </p>
        <div class="suggestions" id="suggestions">
          <button type="button" data-q="기능 알려줘">기능 알려줘</button>
          <button type="button" data-q="해운대구 건물 몇 채야?">
            해운대구 건물 몇 채야?
          </button>
          <button type="button" data-q="구서동에서 건물면적이 가장 큰 아파트는?">
            구서동에서 건물면적이 가장 큰 아파트는?
          </button>
          <button type="button" data-q="구서동 아파트의 특징은?">
            구서동 아파트의 특징은?
          </button>
        </div>
      </div>
    `;
    document.getElementById("suggestions")?.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-q]");
      if (!btn) return;
      sendQuestion(btn.getAttribute("data-q") || "");
    });
    try {
      await ensureSession();
    } catch {
      /* ignore */
    }
    input.focus();
  });

  input.focus();
})();
