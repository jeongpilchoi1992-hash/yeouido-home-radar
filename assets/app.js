const $ = (sel) => document.querySelector(sel);

const state = { tab: "trades", data: null };

function fmtDate(value) {
  if (!value) return "-";
  const text = String(value).replaceAll(".", "-");
  if (/^\d{8}$/.test(text)) return `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}`;
  return text;
}

function fmtManwon(value) {
  const n = Number(String(value ?? "").replaceAll(",", ""));
  if (!Number.isFinite(n) || n <= 0) return "-";
  if (n >= 10000) return `${(n / 10000).toFixed(n % 10000 === 0 ? 0 : 1)}억`;
  return `${n.toLocaleString("ko-KR")}만`;
}

function areaLabel(rank) {
  if (rank === 0) return "여의도";
  if (rank === 1) return "영등포";
  if (rank === 2) return "서울";
  return "기타";
}

function setTab(name) {
  state.tab = name;
  document.querySelectorAll(".tabs button").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === name);
  });
  renderTable();
}

function kpis(data) {
  const yeouidoSales = (data.cheongyak || []).filter((x) => x.area_rank === 0).length;
  const seoulLh = (data.lh || []).length;
  $("#kpi-trade").textContent = String((data.trades || []).length);
  $("#kpi-rent").textContent = String((data.rents || []).length);
  $("#kpi-apply").textContent = String(yeouidoSales || (data.cheongyak || []).length);
  $("#kpi-lh").textContent = String(seoulLh);
}

function renderTable() {
  const data = state.data || {};
  const mount = $("#table-mount");
  const sourceKey = {
    trades: "seoul_trade",
    rents: "seoul_rent",
    cheongyak: "cheongyak",
    lh: "lh",
  }[state.tab];
  const source = (data.sources || {})[sourceKey] || {};
  if (source.ok === false) {
    mount.innerHTML = `<div class="error">수집 실패: ${source.error || "원인 미상"}</div>`;
    return;
  }

  if (state.tab === "trades") {
    const rows = data.trades || [];
    mount.innerHTML = rows.length
      ? `<table><thead><tr><th>계약일</th><th>단지</th><th>용도</th><th>면적</th><th>층</th><th>금액</th></tr></thead><tbody>${rows
          .map(
            (r) => `<tr>
              <td>${fmtDate(r.contract_day)}</td>
              <td>${r.name || "-"}<div class="muted-sub">${r.dong || ""}</div></td>
              <td>${r.use || "-"}</td>
              <td>${r.area ? `${r.area}㎡` : "-"}</td>
              <td>${r.floor ?? "-"}</td>
              <td>${fmtManwon(r.amount_manwon)}</td>
            </tr>`
          )
          .join("")}</tbody></table>`
      : `<div class="empty">여의도 매매 실거래가 아직 없습니다.</div>`;
    return;
  }

  if (state.tab === "rents") {
    const rows = data.rents || [];
    mount.innerHTML = rows.length
      ? `<table><thead><tr><th>계약일</th><th>단지</th><th>면적</th><th>보증금</th><th>월세</th></tr></thead><tbody>${rows
          .map(
            (r) => `<tr>
              <td>${fmtDate(r.contract_day)}</td>
              <td>${r.name || "-"}<div class="muted-sub">${r.dong || ""}</div></td>
              <td>${r.area ? `${r.area}㎡` : "-"}</td>
              <td>${fmtManwon(r.deposit_manwon)}</td>
              <td>${fmtManwon(r.monthly_manwon)}</td>
            </tr>`
          )
          .join("")}</tbody></table>`
      : `<div class="empty">여의도 전월세 실거래가 아직 없습니다.</div>`;
    return;
  }

  if (state.tab === "cheongyak") {
    const rows = data.cheongyak || [];
    mount.innerHTML = rows.length
      ? `<table><thead><tr><th>권역</th><th>단지</th><th>공고일</th><th>접수</th><th>공급</th></tr></thead><tbody>${rows
          .map(
            (r) => `<tr>
              <td><span class="badge ${r.area_rank === 0 ? "" : "warn"}">${areaLabel(r.area_rank)}</span></td>
              <td>${r.url ? `<a href="${r.url}" target="_blank" rel="noopener">${r.name || "-"}</a>` : r.name || "-"}
                <div class="muted-sub">${r.address || r.region || ""}</div></td>
              <td>${fmtDate(r.notice_date)}</td>
              <td>${fmtDate(r.receipt_start)} ~ ${fmtDate(r.receipt_end)}</td>
              <td>${r.supply ?? "-"}</td>
            </tr>`
          )
          .join("")}</tbody></table>`
      : `<div class="empty">최근 서울·영등포·여의도 청약 공고가 없습니다.</div>`;
    return;
  }

  const rows = data.lh || [];
  mount.innerHTML = rows.length
    ? `<table><thead><tr><th>권역</th><th>공고</th><th>유형</th><th>상태</th><th>게시</th></tr></thead><tbody>${rows
        .map(
          (r) => `<tr>
            <td><span class="badge ${r.area_rank === 0 ? "" : "warn"}">${areaLabel(r.area_rank)}</span></td>
            <td>${r.url ? `<a href="${r.url}" target="_blank" rel="noopener">${r.name || "-"}</a>` : r.name || "-"}
              <div class="muted-sub">${r.region || ""}</div></td>
            <td>${r.subtype || r.type || "-"}</td>
            <td>${r.status || "-"}</td>
            <td>${fmtDate(r.posted)}</td>
          </tr>`
        )
        .join("")}</tbody></table>`
    : `<div class="empty">서울 LH 분양·임대 공고가 없습니다.</div>`;
}

async function boot() {
  document.querySelectorAll(".tabs button").forEach((btn) => {
    btn.addEventListener("click", () => setTab(btn.dataset.tab));
  });
  try {
    const res = await fetch("./data/snapshot.json", { cache: "no-store" });
    if (!res.ok) throw new Error("snapshot missing");
    state.data = await res.json();
    $("#updated").textContent = `마지막 수집 ${state.data.generated_at || "-"}`;
    kpis(state.data);
    renderTable();
  } catch (err) {
    $("#updated").textContent = "아직 수집된 스냅샷이 없습니다.";
    $("#table-mount").innerHTML = `<div class="empty">첫 수집이 끝나면 이 화면에 실제 API 결과가 나타납니다.</div>`;
  }
}

boot();
