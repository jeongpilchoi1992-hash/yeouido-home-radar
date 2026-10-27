#!/usr/bin/env python3
"""Collect Yeouido housing signals from public APIs. Keys come from env only."""

from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
TIMEOUT = 45
UA = "yeouido-home-radar/1.0 (GitHub Actions; public housing dashboard)"

CHEONGYAK_ENDPOINTS = [
    ("apt", "getAPTLttotPblancDetail"),
    ("officetel", "getUrbtyOfctlLttotPblancDetail"),
    ("remain", "getRemndrLttotPblancDetail"),
    ("public_rent", "getPblPvtRentLttotPblancDetail"),
    ("optional", "getOPTLttotPblancDetail"),
]

LH_TYPES = [
    ("05", "분양주택"),
    ("06", "임대주택"),
    ("39", "신혼희망타운"),
]


def now_kst() -> datetime:
    return datetime.now(KST)


def keys() -> dict[str, str]:
    return {
        "cheongyak": os.environ.get("CHEONGYAK_SERVICE_KEY", "").strip(),
        "lh": os.environ.get("LH_SERVICE_KEY", "").strip(),
        "seoul": os.environ.get("SEOUL_OPEN_API_KEY", "").strip(),
    }


def redact(text: str, secrets: list[str]) -> str:
    out = text
    for secret in secrets:
        if secret:
            out = out.replace(secret, "***")
    return out


def http_get(url: str, secrets: list[str]) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    ctx = ssl.create_default_context()
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as resp:
                return resp.status, resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(redact(f"HTTP {exc.code}: {body[:400]}", secrets)) from None
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    raise RuntimeError(redact(str(last_error), secrets)) from None


def load_json(raw: str) -> Any:
    text = raw.lstrip("\ufeff").strip()
    if not text:
        return None
    return json.loads(text)


def contains_any(text: str, needles: tuple[str, ...]) -> bool:
    value = text or ""
    return any(n in value for n in needles)


def blob(item: dict[str, Any]) -> str:
    return " ".join(str(v) for v in item.values() if v is not None)


def rank_area(text: str) -> int:
    if "여의도" in text:
        return 0
    if "영등포" in text:
        return 1
    if "서울" in text:
        return 2
    return 3


def collect_cheongyak(key: str, secrets: list[str]) -> dict[str, Any]:
    if not key:
        return {"ok": False, "error": "missing CHEONGYAK_SERVICE_KEY", "items": []}
    start = (now_kst().date() - timedelta(days=180)).isoformat()
    items: list[dict[str, Any]] = []
    for kind, op in CHEONGYAK_ENDPOINTS:
        page = 1
        while page <= 8:
            qs = urllib.parse.urlencode(
                {
                    "page": page,
                    "perPage": 100,
                    "returnType": "JSON",
                    "serviceKey": key,
                    "cond[RCRIT_PBLANC_DE::GTE]": start,
                }
            )
            url = f"https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1/{op}?{qs}"
            _status, raw = http_get(url, secrets)
            payload = load_json(raw) or {}
            if isinstance(payload, dict) and payload.get("code") and not payload.get("data"):
                raise RuntimeError(payload.get("message") or str(payload)[:240])
            rows = payload.get("data") if isinstance(payload, dict) else []
            if not rows:
                break
            for row in rows:
                if not isinstance(row, dict):
                    continue
                address = str(row.get("HSSPLY_ADRES") or "")
                region = str(row.get("SUBSCRPT_AREA_CODE_NM") or "")
                name = str(row.get("HOUSE_NM") or "")
                text = f"{address} {region} {name}"
                if rank_area(text) > 2:
                    continue
                items.append(
                    {
                        "kind": kind,
                        "name": name,
                        "address": address,
                        "region": region,
                        "supply": row.get("TOT_SUPLY_HSHLDCO"),
                        "notice_date": row.get("RCRIT_PBLANC_DE"),
                        "receipt_start": row.get("RCEPT_BGNDE") or row.get("SUBSCRPT_RCEPT_BGNDE"),
                        "receipt_end": row.get("RCEPT_ENDDE") or row.get("SUBSCRPT_RCEPT_ENDDE"),
                        "winner_date": row.get("PRZWNER_PRESNATN_DE"),
                        "move_in": row.get("MVN_PREARNGE_YM"),
                        "builder": row.get("CNSTRCT_ENTRPS_NM"),
                        "developer": row.get("BSNS_MBY_NM"),
                        "url": row.get("PBLANC_URL") or row.get("HMPG_ADRES"),
                        "phone": row.get("MDHS_TELNO"),
                        "area_rank": rank_area(text),
                    }
                )
            total = int(payload.get("totalCount") or 0)
            if page * 100 >= total or len(rows) < 100:
                break
            page += 1
    items.sort(key=lambda x: (x.get("area_rank", 9), str(x.get("notice_date") or "")), reverse=False)
    items.sort(key=lambda x: str(x.get("notice_date") or ""), reverse=True)
    items.sort(key=lambda x: x.get("area_rank", 9))
    return {"ok": True, "error": None, "items": items[:200]}


def flatten_lh(payload: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for child in node:
                walk(child)
            return
        if not isinstance(node, dict):
            return
        if "PAN_NM" in node or "panNm" in node:
            found.append(node)
            return
        for key in ("dsSch", "dsList", "item", "items", "data", "body"):
            if key in node:
                walk(node[key])

    walk(payload)
    return found


def collect_lh(key: str, secrets: list[str]) -> dict[str, Any]:
    if not key:
        return {"ok": False, "error": "missing LH_SERVICE_KEY", "items": []}
    today = now_kst().date()
    posted = (today - timedelta(days=90)).strftime("%Y.%m.%d")
    closes = (today + timedelta(days=180)).strftime("%Y.%m.%d")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for code, _label in LH_TYPES:
        page = 1
        while page <= 6:
            qs = urllib.parse.urlencode(
                {
                    "serviceKey": key,
                    "PG_SZ": 50,
                    "PAGE": page,
                    "CNP_CD": 11,
                    "UPP_AIS_TP_CD": code,
                    "PAN_NT_ST_DT": posted,
                    "CLSG_DT": closes,
                }
            )
            url = f"https://apis.data.go.kr/B552555/lhLeaseNoticeInfo1/lhLeaseNoticeInfo1?{qs}"
            _status, raw = http_get(url, secrets)
            payload = load_json(raw)
            rows = flatten_lh(payload)
            if not rows:
                break
            for row in rows:
                name = str(row.get("PAN_NM") or row.get("panNm") or "")
                region = str(row.get("CNP_CD_NM") or row.get("cnpCdNm") or "")
                detail_url = str(row.get("DTL_URL") or row.get("dtlUrl") or "")
                ident = f"{name}|{detail_url}|{row.get('PAN_SS')}"
                if not name or ident in seen:
                    continue
                seen.add(ident)
                text = blob(row)
                items.append(
                    {
                        "name": name,
                        "type": row.get("UPP_AIS_TP_NM") or row.get("uppAisTpNm"),
                        "subtype": row.get("AIS_TP_CD_NM") or row.get("aisTpCdNm"),
                        "region": region,
                        "status": row.get("PAN_SS") or row.get("panSs"),
                        "posted": row.get("PAN_NT_ST_DT") or row.get("panNtStDt"),
                        "closes": row.get("CLSG_DT") or row.get("clsgDt"),
                        "url": detail_url,
                        "area_rank": rank_area(text),
                    }
                )
            if len(rows) < 50:
                break
            page += 1
    items.sort(key=lambda x: (x.get("area_rank", 9), str(x.get("posted") or "")), reverse=False)
    items.sort(key=lambda x: str(x.get("posted") or ""), reverse=True)
    items.sort(key=lambda x: x.get("area_rank", 9))
    return {"ok": True, "error": None, "items": items[:200]}


def seoul_page(key: str, service: str, year: int, secrets: list[str], start: int, end: int) -> tuple[int, list[dict[str, Any]]]:
    path = f"http://openapi.seoul.go.kr:8088/{key}/json/{service}/{start}/{end}/{year}/11560"
    _status, raw = http_get(path, secrets)
    payload = load_json(raw) or {}
    block = payload.get(service) if isinstance(payload, dict) else None
    if not isinstance(block, dict):
        result = payload.get("RESULT") if isinstance(payload, dict) else None
        if isinstance(result, dict):
            raise RuntimeError(f"{result.get('CODE')} {result.get('MESSAGE')}")
        raise RuntimeError(str(payload)[:240])
    result = block.get("RESULT") or {}
    code = str(result.get("CODE") or "")
    if code and not code.startswith("INFO-"):
        raise RuntimeError(f"{code} {result.get('MESSAGE')}")
    rows = [row for row in (block.get("row") or []) if isinstance(row, dict)]
    total = int(block.get("list_total_count") or 0)
    return total, rows


def seoul_rows(key: str, service: str, year: int, secrets: list[str], max_pages: int = 8) -> list[dict[str, Any]]:
    all_rows: list[dict[str, Any]] = []
    start = 1
    for _ in range(max_pages):
        end = start + 999
        total, rows = seoul_page(key, service, year, secrets, start, end)
        all_rows.extend(rows)
        if start + 999 >= total or not rows:
            break
        start += 1000
    return all_rows


def yeouido_only(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept = []
    for row in rows:
        dong = str(row.get("STDG_NM") or row.get("BJDONG_NM") or "")
        if "여의도" in dong:
            kept.append(row)
    return kept


def collect_seoul_trade(key: str, secrets: list[str]) -> dict[str, Any]:
    if not key:
        return {"ok": False, "error": "missing SEOUL_OPEN_API_KEY", "items": []}
    year = now_kst().year
    rows = yeouido_only(seoul_rows(key, "tbLnOpendataRtmsV", year, secrets))
    if year == date.today().year:
        try:
            rows.extend(yeouido_only(seoul_rows(key, "tbLnOpendataRtmsV", year - 1, secrets)))
        except Exception:  # noqa: BLE001
            pass
    items = []
    for row in rows:
        items.append(
            {
                "dong": row.get("STDG_NM") or row.get("BJDONG_NM"),
                "name": row.get("BLDG_NM"),
                "use": row.get("BLDG_USG") or row.get("HOUSE_TYPE"),
                "area": row.get("ARCH_AREA") or row.get("BLDG_AREA"),
                "floor": row.get("FLR") or row.get("FLOOR"),
                "year_built": row.get("ARCH_YR") or row.get("BUILD_YEAR"),
                "amount_manwon": row.get("THING_AMT") or row.get("OBJ_AMT"),
                "contract_day": row.get("CTRT_DAY") or row.get("DEAL_YMD"),
                "gu": row.get("CGG_NM") or row.get("SGG_NM"),
            }
        )
    items.sort(key=lambda x: str(x.get("contract_day") or ""), reverse=True)
    return {"ok": True, "error": None, "items": items[:300]}


def collect_seoul_rent(key: str, secrets: list[str]) -> dict[str, Any]:
    if not key:
        return {"ok": False, "error": "missing SEOUL_OPEN_API_KEY", "items": []}
    year = now_kst().year
    try:
        rows = yeouido_only(seoul_rows(key, "tbLnOpendataRentV", year, secrets, max_pages=12))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:240], "items": []}
    items = []
    for row in rows:
        items.append(
            {
                "dong": row.get("STDG_NM") or row.get("BJDONG_NM"),
                "name": row.get("BLDG_NM"),
                "use": row.get("BLDG_USG") or row.get("HOUSE_TYPE") or row.get("RENT_SE"),
                "area": row.get("RENT_AREA") or row.get("ARCH_AREA") or row.get("BLDG_AREA"),
                "floor": row.get("FLR") or row.get("FLOOR"),
                "year_built": row.get("ARCH_YR") or row.get("BUILD_YEAR"),
                "deposit_manwon": row.get("GRFE") or row.get("GRNTY_AMT") or row.get("RENT_GTN"),
                "monthly_manwon": row.get("RTFE") or row.get("MTH_RENT_AMT") or row.get("RENT_FEE"),
                "contract_day": row.get("CTRT_DAY") or row.get("DEAL_YMD"),
                "gu": row.get("CGG_NM") or row.get("SGG_NM"),
            }
        )
    items.sort(key=lambda x: str(x.get("contract_day") or ""), reverse=True)
    return {"ok": True, "error": None, "items": items[:300]}


def source_meta(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": bool(result.get("ok")),
        "count": len(result.get("items") or []),
        "error": result.get("error"),
    }


def main() -> int:
    env = keys()
    secrets = [value for value in env.values() if value]
    snapshot = {
        "generated_at": now_kst().isoformat(timespec="seconds"),
        "focus": "서울 영등포구 여의도동",
        "sources": {},
        "cheongyak": [],
        "lh": [],
        "trades": [],
        "rents": [],
    }
    failures = 0

    jobs = [
        ("cheongyak", lambda: collect_cheongyak(env["cheongyak"], secrets)),
        ("lh", lambda: collect_lh(env["lh"], secrets)),
        ("seoul_trade", lambda: collect_seoul_trade(env["seoul"], secrets)),
        ("seoul_rent", lambda: collect_seoul_rent(env["seoul"], secrets)),
    ]
    mapping = {
        "cheongyak": "cheongyak",
        "lh": "lh",
        "seoul_trade": "trades",
        "seoul_rent": "rents",
    }
    for name, fn in jobs:
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "error": redact(str(exc), secrets)[:400], "items": []}
        snapshot["sources"][name] = source_meta(result)
        snapshot[mapping[name]] = result.get("items") or []
        status = "ok" if result.get("ok") else "FAIL"
        print(f"{name}: {status} n={len(result.get('items') or [])} err={result.get('error')}")
        if not result.get("ok"):
            failures += 1

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out = DATA_DIR / "snapshot.json"
    out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    return 0 if failures < 4 else 1


if __name__ == "__main__":
    sys.exit(main())
