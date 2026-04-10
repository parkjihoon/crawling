"""
크롤링 결과 웹 대시보드 뷰어

브라우저에서 크롤링 결과를 실시간 확인할 수 있는 로컬 웹서버.

사용법:
    python viewer.py                    # data/ 폴더의 최신 파일
    python viewer.py --file data/rocketpunch_20260410.json
    python viewer.py --port 8080

기능:
    - 수집된 공고 목록 테이블 (정렬/필터)
    - 허위 공고 의심 하이라이트
    - 통계 요약 카드
    - JSON/CSV 데이터 자동 로드
"""

import argparse
import json
import csv
import os
import sys
import http.server
import socketserver
import webbrowser
from typing import Optional
from pathlib import Path
from datetime import datetime

# ─────────────────────────────────────────────
# HTML 대시보드 템플릿
# ─────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Crawling Dashboard</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; }

.header { background: #1e293b; padding: 20px 32px; border-bottom: 1px solid #334155; display: flex; justify-content: space-between; align-items: center; }
.header h1 { font-size: 20px; font-weight: 600; color: #f8fafc; }
.header .meta { font-size: 13px; color: #94a3b8; }

.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; padding: 24px 32px; }
.stat-card { background: #1e293b; border-radius: 12px; padding: 20px; border: 1px solid #334155; }
.stat-card .label { font-size: 13px; color: #94a3b8; margin-bottom: 4px; }
.stat-card .value { font-size: 28px; font-weight: 700; color: #f8fafc; }
.stat-card .sub { font-size: 12px; color: #64748b; margin-top: 4px; }
.stat-card.alert .value { color: #f87171; }
.stat-card.success .value { color: #4ade80; }
.stat-card.info .value { color: #60a5fa; }

.controls { padding: 16px 32px; display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.search-box { background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 10px 16px; color: #e2e8f0; font-size: 14px; width: 300px; outline: none; }
.search-box:focus { border-color: #60a5fa; }
.filter-btn { background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 8px 16px; color: #94a3b8; font-size: 13px; cursor: pointer; transition: all 0.15s; }
.filter-btn:hover { border-color: #60a5fa; color: #e2e8f0; }
.filter-btn.active { background: #1e40af; border-color: #3b82f6; color: #fff; }

.table-container { padding: 0 32px 32px; overflow-x: auto; }
table { width: 100%; border-collapse: collapse; background: #1e293b; border-radius: 12px; overflow: hidden; }
thead th { background: #0f172a; padding: 12px 16px; text-align: left; font-size: 12px; font-weight: 600; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; cursor: pointer; user-select: none; white-space: nowrap; border-bottom: 1px solid #334155; }
thead th:hover { color: #e2e8f0; }
thead th.sorted { color: #60a5fa; }
tbody tr { border-bottom: 1px solid #1e293b; transition: background 0.1s; }
tbody tr:hover { background: #334155; }
tbody td { padding: 12px 16px; font-size: 14px; vertical-align: top; }
tbody tr.suspicious { background: rgba(248, 113, 113, 0.08); }
tbody tr.suspicious td:first-child { border-left: 3px solid #f87171; }
tbody tr.scam { background: rgba(248, 113, 113, 0.15); }

.badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
.badge-normal { background: #065f46; color: #6ee7b7; }
.badge-suspicious { background: #7c2d12; color: #fdba74; }
.badge-scam { background: #7f1d1d; color: #fca5a5; }
.badge-unclear { background: #374151; color: #9ca3af; }

.company-cell { display: flex; align-items: center; gap: 10px; }
.company-logo { width: 28px; height: 28px; border-radius: 6px; object-fit: cover; background: #334155; }
.company-name { font-weight: 500; }
.category-tag { font-size: 12px; color: #94a3b8; margin-top: 2px; }

.empty-state { text-align: center; padding: 60px; color: #64748b; }
.empty-state h2 { font-size: 18px; margin-bottom: 8px; color: #94a3b8; }

.match-dots { display: flex; gap: 4px; }
.match-dot { width: 8px; height: 8px; border-radius: 50%; }
.match-dot.yes { background: #4ade80; }
.match-dot.no { background: #f87171; }

.footer { text-align: center; padding: 20px; color: #475569; font-size: 12px; }
</style>
</head>
<body>

<div class="header">
    <h1>Crawling Dashboard</h1>
    <div class="meta" id="meta-info">Loading...</div>
</div>

<div class="stats" id="stats-container"></div>

<div class="controls">
    <input type="text" class="search-box" id="search" placeholder="Search company, title, category...">
    <button class="filter-btn active" onclick="setFilter('all')">All</button>
    <button class="filter-btn" onclick="setFilter('suspicious')">Suspicious</button>
    <button class="filter-btn" onclick="setFilter('normal')">Normal</button>
</div>

<div class="table-container">
    <table>
        <thead>
            <tr>
                <th onclick="sortBy('data_index')">#</th>
                <th onclick="sortBy('company_name')">Company</th>
                <th onclick="sortBy('title')">Title</th>
                <th onclick="sortBy('category')">Category</th>
                <th onclick="sortBy('match_score')">Match</th>
                <th onclick="sortBy('classification')">Status</th>
            </tr>
        </thead>
        <tbody id="table-body"></tbody>
    </table>
</div>

<div class="footer">Crawling Project Dashboard &mdash; Generated by viewer.py</div>

<script>
// ─── Data ───
const DATA = __DATA_PLACEHOLDER__;

let currentFilter = 'all';
let currentSort = { key: 'data_index', asc: true };
let searchQuery = '';

// ─── Init ───
function init() {
    document.getElementById('meta-info').textContent =
        `${DATA.length} postings | ${DATA._meta?.source || 'local'} | ${DATA._meta?.timestamp || new Date().toLocaleString()}`;

    renderStats();
    renderTable();

    document.getElementById('search').addEventListener('input', (e) => {
        searchQuery = e.target.value.toLowerCase();
        renderTable();
    });
}

function renderStats() {
    const container = document.getElementById('stats-container');
    const total = DATA.length;
    const suspicious = DATA.filter(d => d.classification === 'suspicious' || d.classification === 'scam').length;
    const normal = total - suspicious;
    const companies = new Set(DATA.map(d => d.company_name)).size;

    container.innerHTML = `
        <div class="stat-card info">
            <div class="label">Total Postings</div>
            <div class="value">${total}</div>
            <div class="sub">from ${companies} companies</div>
        </div>
        <div class="stat-card success">
            <div class="label">Normal</div>
            <div class="value">${normal}</div>
            <div class="sub">${total ? ((normal/total)*100).toFixed(0) : 0}% of total</div>
        </div>
        <div class="stat-card alert">
            <div class="label">Suspicious</div>
            <div class="value">${suspicious}</div>
            <div class="sub">${total ? ((suspicious/total)*100).toFixed(0) : 0}% flagged</div>
        </div>
        <div class="stat-card">
            <div class="label">Companies</div>
            <div class="value">${companies}</div>
            <div class="sub">unique companies</div>
        </div>
    `;
}

function renderTable() {
    const tbody = document.getElementById('table-body');
    let filtered = DATA.filter(d => {
        if (currentFilter === 'suspicious') return d.classification === 'suspicious' || d.classification === 'scam';
        if (currentFilter === 'normal') return d.classification === 'normal' || !d.classification;
        return true;
    });

    if (searchQuery) {
        filtered = filtered.filter(d =>
            (d.company_name || '').toLowerCase().includes(searchQuery) ||
            (d.title || '').toLowerCase().includes(searchQuery) ||
            (d.category || '').toLowerCase().includes(searchQuery)
        );
    }

    filtered.sort((a, b) => {
        let va = a[currentSort.key] ?? '';
        let vb = b[currentSort.key] ?? '';
        if (typeof va === 'number' && typeof vb === 'number') return currentSort.asc ? va - vb : vb - va;
        return currentSort.asc ? String(va).localeCompare(String(vb)) : String(vb).localeCompare(String(va));
    });

    if (filtered.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="empty-state"><h2>No results</h2>Try adjusting filters</td></tr>';
        return;
    }

    tbody.innerHTML = filtered.map(d => {
        const cls = d.classification || 'normal';
        const rowClass = cls === 'scam' ? 'scam' : cls === 'suspicious' ? 'suspicious' : '';
        const badge = `<span class="badge badge-${cls}">${cls}</span>`;

        const matchInfo = d.match_info || {};
        const matchDots = Object.entries(matchInfo).map(([k, v]) =>
            `<span class="match-dot ${v ? 'yes' : 'no'}" title="${k}: ${v ? 'O' : 'X'}"></span>`
        ).join('');

        const logoUrl = d.company_logo_url || '';
        const logoImg = logoUrl
            ? `<img class="company-logo" src="${logoUrl}" onerror="this.style.display='none'">`
            : `<div class="company-logo"></div>`;

        return `<tr class="${rowClass}">
            <td>${d.data_index ?? d.posting_id ?? ''}</td>
            <td><div class="company-cell">${logoImg}<div><div class="company-name">${d.company_name || '-'}</div>${d.company_id ? `<div class="category-tag">ID: ${d.company_id}</div>` : ''}</div></div></td>
            <td>${d.title || '-'}</td>
            <td><span class="category-tag">${d.category || '-'}</span></td>
            <td><div class="match-dots">${matchDots || '-'}</div></td>
            <td>${badge}</td>
        </tr>`;
    }).join('');
}

function setFilter(f) {
    currentFilter = f;
    document.querySelectorAll('.filter-btn').forEach(btn => {
        btn.classList.toggle('active', btn.textContent.toLowerCase().includes(f));
    });
    renderTable();
}

function sortBy(key) {
    if (currentSort.key === key) {
        currentSort.asc = !currentSort.asc;
    } else {
        currentSort = { key, asc: true };
    }
    document.querySelectorAll('thead th').forEach(th => th.classList.remove('sorted'));
    renderTable();
}

init();
</script>
</body>
</html>"""


def load_data(filepath: str) -> list[dict]:
    """JSON 또는 CSV 파일에서 데이터를 로드한다."""
    path = Path(filepath)

    if path.suffix == ".json":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else [data]

    elif path.suffix == ".csv":
        with open(path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            return list(reader)

    else:
        raise ValueError(f"Unsupported file format: {path.suffix}")


def find_latest_data(data_dir: str = "data") -> Optional[str]:
    """data/ 폴더에서 가장 최근 파일을 찾는다."""
    data_path = Path(data_dir)
    if not data_path.exists():
        return None

    files = list(data_path.glob("*.json")) + list(data_path.glob("*.csv"))
    if not files:
        return None

    return str(max(files, key=lambda f: f.stat().st_mtime))


def generate_dashboard(data: list[dict], source: str = "local") -> str:
    """데이터로 대시보드 HTML을 생성한다."""
    # 메타 정보 추가
    for item in data:
        if "classification" not in item:
            item["classification"] = "normal"
        if "match_score" not in item:
            match_info = item.get("match_info", {})
            if isinstance(match_info, dict):
                total = len(match_info)
                matched = sum(1 for v in match_info.values() if v)
                item["match_score"] = matched / total if total > 0 else 0

    # JSON 직렬화
    meta = {
        "source": source,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(data),
    }

    # _meta를 배열에 속성으로 추가 (JS에서 DATA._meta로 접근)
    data_json = json.dumps(data, ensure_ascii=False)
    # DATA._meta 는 JS에서 별도 설정
    meta_json = json.dumps(meta, ensure_ascii=False)

    html = DASHBOARD_HTML.replace(
        "__DATA_PLACEHOLDER__",
        f"Object.assign({data_json}, {{_meta: {meta_json}}})"
    )

    return html


def main():
    parser = argparse.ArgumentParser(description="Crawling result dashboard viewer")
    parser.add_argument("--file", "-f", help="Data file path (JSON or CSV)")
    parser.add_argument("--port", "-p", type=int, default=8000, help="Server port (default: 8000)")
    parser.add_argument("--no-browser", action="store_true", help="Don't open browser automatically")
    parser.add_argument("--save", "-s", help="Save dashboard HTML to file instead of serving")

    args = parser.parse_args()

    # 데이터 로드
    data_file = args.file or find_latest_data()

    if data_file:
        print(f"Loading data from: {data_file}")
        data = load_data(data_file)
    else:
        print("No data file found. Using sample data from test parse...")
        # test_parse_local.py의 출력 파일 시도
        for candidate in ["rocketpunch_sample_parsed.json", "debug_page_parsed.json"]:
            if Path(candidate).exists():
                data = load_data(candidate)
                data_file = candidate
                print(f"Found: {candidate}")
                break
        else:
            print("No data available. Run the crawler first or provide --file")
            sys.exit(1)

    print(f"Loaded {len(data)} postings")

    # 대시보드 생성
    html = generate_dashboard(data, source=data_file or "unknown")

    if args.save:
        Path(args.save).write_text(html, encoding="utf-8")
        print(f"Dashboard saved to: {args.save}")
        return

    # 임시 HTML 파일 저장 후 서빙
    dashboard_path = Path("dashboard.html")
    dashboard_path.write_text(html, encoding="utf-8")

    # HTTP 서버 시작
    os.chdir(str(Path.cwd()))

    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/" or self.path == "/index.html":
                self.path = "/dashboard.html"
            return super().do_GET()

        def log_message(self, format, *args):
            pass  # 로그 숨김

    print(f"\nDashboard server running at http://localhost:{args.port}")
    print("Press Ctrl+C to stop\n")

    if not args.no_browser:
        webbrowser.open(f"http://localhost:{args.port}")

    with socketserver.TCPServer(("", args.port), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nServer stopped.")


if __name__ == "__main__":
    main()
