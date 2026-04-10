"""
크롤링 대시보드 웹 서버

Flask 기반 웹 서버로 브라우저에서:
- 크롤링 작업 실행/중지
- 실시간 진행 상황 모니터링
- 로그 확인
- 수집 결과 조회/검색/필터
- LLM 분류 실행

사용법:
    python server.py                  # http://localhost:5000
    python server.py --port 8080      # 포트 지정
    python server.py --debug          # 디버그 모드

API 엔드포인트:
    GET  /                  - 대시보드 메인
    GET  /api/status        - 크롤링 상태
    POST /api/crawl/start   - 크롤링 시작
    POST /api/crawl/stop    - 크롤링 중지
    GET  /api/results       - 수집 결과
    GET  /api/logs          - 로그 스트림
    POST /api/classify      - LLM 분류 실행
    POST /api/schedule/start - 증분 수집 실행 (1회)
    GET  /api/schedule/history - 스케줄러 실행 이력
    GET  /api/health        - 사이트 건강 상태
    GET  /api/faults        - 장애 이력
"""

import argparse
import json
import logging
import os
import sys
import time
import threading
import queue
from datetime import datetime
from pathlib import Path
from typing import Optional

# Flask 임포트
try:
    from flask import Flask, jsonify, request, Response, send_from_directory
except ImportError:
    print("Flask not installed. Run: pip install flask")
    print("  or: venv\\Scripts\\pip.exe install flask")
    sys.exit(1)

# 프로젝트 모듈
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src.models.job_posting import JobPosting, save_to_json, save_to_csv

# ────────────────────────────────────────────
# 로깅 설정: 콘솔 + 큐 (SSE 스트리밍용)
# ────────────────────────────────────────────
log_queue: queue.Queue = queue.Queue(maxsize=5000)


class QueueHandler(logging.Handler):
    """로그를 큐에 넣어 SSE 스트리밍으로 전달한다."""
    def emit(self, record):
        try:
            msg = self.format(record)
            log_queue.put_nowait(msg)
        except queue.Full:
            pass


# 루트 로거에 큐 핸들러 추가
queue_handler = QueueHandler()
queue_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
)
logging.getLogger().addHandler(queue_handler)
logging.getLogger().setLevel(logging.INFO)

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────
# 전역 상태
# ────────────────────────────────────────────

class CrawlState:
    """크롤링 전역 상태 관리."""
    def __init__(self):
        self.is_running = False
        self.should_stop = False
        self.current_task: Optional[str] = None
        self.progress = {
            "current_page": 0,
            "total_pages": 0,
            "items_found": 0,
            "items_detailed": 0,
            "errors": 0,
            "started_at": None,
            "elapsed_sec": 0,
        }
        self.results: list[dict] = []
        self.thread: Optional[threading.Thread] = None
        self.log_history: list[str] = []

    def reset(self):
        self.is_running = False
        self.should_stop = False
        self.current_task = None
        self.progress = {
            "current_page": 0,
            "total_pages": 0,
            "items_found": 0,
            "items_detailed": 0,
            "errors": 0,
            "started_at": None,
            "elapsed_sec": 0,
        }

    def to_dict(self):
        if self.progress["started_at"]:
            self.progress["elapsed_sec"] = int(
                time.time() - self.progress["started_at"]
            )
        return {
            "is_running": self.is_running,
            "current_task": self.current_task,
            "progress": self.progress,
            "results_count": len(self.results),
        }


state = CrawlState()

# ────────────────────────────────────────────
# Flask 앱
# ────────────────────────────────────────────

app = Flask(__name__, static_folder=None)


@app.route("/")
def index():
    """대시보드 메인 페이지."""
    return DASHBOARD_HTML


@app.route("/api/status")
def api_status():
    """크롤링 상태 반환."""
    return jsonify(state.to_dict())


@app.route("/api/crawl/start", methods=["POST"])
def api_crawl_start():
    """크롤링 시작."""
    if state.is_running:
        return jsonify({"error": "Already running"}), 409

    data = request.get_json(silent=True) or {}
    site = data.get("site", "rocketpunch")
    start_page = int(data.get("start_page", 1))
    end_page = int(data.get("end_page", 3))
    delay = float(data.get("delay", 5.0))
    keywords = data.get("keywords", "")
    fetch_details = data.get("fetch_details", False)
    headless = data.get("headless", True)
    discover_api = data.get("discover_api", False)

    def crawl_worker():
        state.reset()
        state.is_running = True
        state.progress["total_pages"] = end_page - start_page + 1
        state.progress["started_at"] = time.time()
        state.current_task = f"Crawling {site} (pages {start_page}-{end_page})"

        logger.info(f"Crawl started: {site} pages={start_page}-{end_page} delay={delay}")

        try:
            if site == "rocketpunch":
                from src.crawlers.rocketpunch import RocketPunchCrawler
                crawler = RocketPunchCrawler(
                    crawl_delay=delay,
                    keywords=keywords,
                    headless=headless,
                )

                all_postings = []

                for page_num in range(start_page, end_page + 1):
                    if state.should_stop:
                        logger.info("Crawl stopped by user")
                        break

                    state.progress["current_page"] = page_num
                    state.current_task = f"Page {page_num}/{end_page}"

                    # Phase 1: 목록 수집
                    crawler.rate_limiter.wait()
                    response = crawler.fetch_list(page_num)

                    if response is None:
                        state.progress["errors"] += 1
                        crawler.rate_limiter.on_error()
                        continue

                    crawler.rate_limiter.on_success()
                    items = crawler.parse_list(response)
                    state.progress["items_found"] += len(items)

                    logger.info(f"Page {page_num}: {len(items)} postings found")

                    # Phase 2: 상세 URL 캡처
                    if fetch_details and items:
                        state.current_task = f"Page {page_num}: capturing URLs..."
                        crawler.rate_limiter.wait()
                        url_map = crawler.fetch_list_with_urls(page_num)

                        if url_map:
                            idx_to_url = {e["data_index"]: e["detail_url"] for e in url_map}
                            for item in items:
                                idx = item.get("data_index", "")
                                if idx in idx_to_url:
                                    item["url"] = idx_to_url[idx]

                        # Phase 3: 상세 수집
                        for i, item in enumerate(items):
                            if state.should_stop:
                                break
                            detail_url = item.get("url", "")
                            if not detail_url:
                                continue

                            state.current_task = (
                                f"Page {page_num}: detail {i+1}/{len(items)}"
                            )
                            crawler.rate_limiter.wait()
                            detail_resp = crawler.fetch_detail(detail_url)

                            if detail_resp:
                                crawler.rate_limiter.on_success()
                                posting = crawler.parse_detail(detail_resp, detail_url)
                                if posting:
                                    all_postings.append(posting)
                                    state.progress["items_detailed"] += 1
                            else:
                                crawler.rate_limiter.on_error()
                                state.progress["errors"] += 1

                    # items를 결과에 추가 (상세 없어도)
                    for item in items:
                        result = {**item}
                        result["classification"] = "normal"
                        state.results.append(result)

                # 결과 저장
                if state.results:
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    data_dir = Path("data")
                    data_dir.mkdir(exist_ok=True)

                    json_path = data_dir / f"{site}_{ts}.json"
                    with open(json_path, "w", encoding="utf-8") as f:
                        json.dump(state.results, f, ensure_ascii=False, indent=2)
                    logger.info(f"Results saved: {json_path}")

                logger.info(
                    f"Crawl complete: {len(state.results)} total, "
                    f"{state.progress['errors']} errors"
                )

            else:
                logger.error(f"Unknown site: {site}")

        except Exception as e:
            logger.error(f"Crawl error: {e}")
            state.progress["errors"] += 1

        finally:
            state.is_running = False
            state.current_task = "Done"

    state.thread = threading.Thread(target=crawl_worker, daemon=True)
    state.thread.start()

    return jsonify({"status": "started"})


@app.route("/api/crawl/stop", methods=["POST"])
def api_crawl_stop():
    """크롤링 중지 요청."""
    if not state.is_running:
        return jsonify({"error": "Not running"}), 400

    state.should_stop = True
    logger.info("Stop requested")
    return jsonify({"status": "stopping"})


@app.route("/api/results")
def api_results():
    """수집 결과 반환."""
    q = request.args.get("q", "").lower()
    filter_type = request.args.get("filter", "all")

    results = state.results

    if q:
        results = [
            r for r in results
            if q in (r.get("title", "") + r.get("company_name", "") + r.get("category", "")).lower()
        ]

    if filter_type == "suspicious":
        results = [r for r in results if r.get("classification") in ("suspicious", "scam")]
    elif filter_type == "normal":
        results = [r for r in results if r.get("classification", "normal") == "normal"]

    return jsonify({
        "total": len(state.results),
        "filtered": len(results),
        "items": results,
    })


@app.route("/api/results/load", methods=["POST"])
def api_results_load():
    """파일에서 결과 로드."""
    data = request.get_json(silent=True) or {}
    filepath = data.get("file", "")

    if not filepath:
        # 최신 파일 자동 탐색
        data_dir = Path("data")
        if data_dir.exists():
            files = sorted(data_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
            if files:
                filepath = str(files[0])

    if not filepath or not Path(filepath).exists():
        return jsonify({"error": "File not found"}), 404

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            state.results = json.load(f)
        logger.info(f"Loaded {len(state.results)} results from {filepath}")
        return jsonify({"status": "loaded", "count": len(state.results)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/logs/stream")
def api_logs_stream():
    """SSE (Server-Sent Events)로 로그 실시간 스트리밍."""
    def generate():
        while True:
            try:
                msg = log_queue.get(timeout=1)
                yield f"data: {json.dumps({'log': msg})}\n\n"
            except queue.Empty:
                # heartbeat
                yield f"data: {json.dumps({'heartbeat': True})}\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/api/logs/history")
def api_logs_history():
    """최근 로그 히스토리."""
    limit = int(request.args.get("limit", 100))
    return jsonify({"logs": state.log_history[-limit:]})


@app.route("/api/classify", methods=["POST"])
def api_classify():
    """LLM으로 허위 공고 분류."""
    if not state.results:
        return jsonify({"error": "No results to classify"}), 400

    data = request.get_json(silent=True) or {}
    provider = data.get("provider", "openai")
    model = data.get("model", "")

    try:
        from src.utils.llm_refiner import LLMRefiner
        refiner = LLMRefiner(provider=provider, model=model or None)

        classified = 0
        for item in state.results:
            if state.should_stop:
                break
            result = refiner.classify_posting(item)
            item["classification"] = result.category
            item["confidence"] = result.confidence
            item["reasons"] = result.reasons
            classified += 1
            logger.info(
                f"Classified [{classified}/{len(state.results)}]: "
                f"{item.get('title', '')[:30]} → {result.category}"
            )

        return jsonify({"status": "done", "classified": classified})

    except Exception as e:
        logger.error(f"Classify error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/files")
def api_files():
    """data/ 폴더의 파일 목록."""
    data_dir = Path("data")
    if not data_dir.exists():
        return jsonify({"files": []})

    files = []
    for f in sorted(data_dir.glob("*.*"), key=lambda x: x.stat().st_mtime, reverse=True):
        files.append({
            "name": f.name,
            "path": str(f),
            "size": f.stat().st_size,
            "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
        })

    return jsonify({"files": files})


# ────────────────────────────────────────────
# 증분 수집 (스케줄러) API
# ────────────────────────────────────────────

schedule_state = {
    "is_running": False,
    "last_result": None,
    "thread": None,
}


@app.route("/api/schedule/start", methods=["POST"])
def api_schedule_start():
    """증분 수집 1회 실행."""
    if schedule_state["is_running"]:
        return jsonify({"error": "Incremental crawl already running"}), 409

    data = request.get_json(silent=True) or {}
    site = data.get("site", "rocketpunch")
    pages = data.get("pages", "1-10")
    delay = float(data.get("delay", 5.0))
    keywords = data.get("keywords", "")
    headless = data.get("headless", True)

    def schedule_worker():
        schedule_state["is_running"] = True
        logger.info(f"[schedule] Incremental crawl started: {site} pages={pages}")
        try:
            from src.scheduler import run_incremental
            result = run_incremental(
                site=site, pages=pages, delay=delay,
                keywords=keywords, headless=headless,
            )
            schedule_state["last_result"] = result
            logger.info(
                f"[schedule] Done: {result.get('new_items', 0)} new, "
                f"{result.get('duplicates', 0)} duplicates"
            )
        except Exception as e:
            logger.error(f"[schedule] Error: {e}")
            schedule_state["last_result"] = {"error": str(e)}
        finally:
            schedule_state["is_running"] = False

    t = threading.Thread(target=schedule_worker, daemon=True)
    schedule_state["thread"] = t
    t.start()

    return jsonify({"status": "started", "site": site, "pages": pages})


@app.route("/api/schedule/status")
def api_schedule_status():
    """증분 수집 상태."""
    return jsonify({
        "is_running": schedule_state["is_running"],
        "last_result": schedule_state["last_result"],
    })


@app.route("/api/schedule/history")
def api_schedule_history():
    """증분 수집 실행 이력."""
    site = request.args.get("site", "rocketpunch")
    limit = int(request.args.get("limit", 30))
    try:
        from src.scheduler import get_run_history
        history = get_run_history(site, limit=limit)
        return jsonify({"site": site, "runs": history})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ────────────────────────────────────────────
# 장애 탐지 API
# ────────────────────────────────────────────

@app.route("/api/health")
def api_health():
    """사이트 건강 상태 요약."""
    site = request.args.get("site", "rocketpunch")
    try:
        from src.utils.fault_detector import FaultDetector
        summary = FaultDetector.get_health_summary(site)
        return jsonify(summary)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/faults")
def api_faults():
    """장애 이력 조회."""
    site = request.args.get("site", "rocketpunch")
    limit = int(request.args.get("limit", 50))
    try:
        from src.utils.fault_detector import FaultDetector
        faults = FaultDetector.load_fault_history(site, limit=limit)
        return jsonify({"site": site, "faults": faults, "total": len(faults)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ────────────────────────────────────────────
# 대시보드 HTML (인라인)
# ────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Crawling Dashboard</title>
<style>
:root {
  --bg-primary: #0f172a;
  --bg-card: #1e293b;
  --bg-hover: #334155;
  --border: #334155;
  --text-primary: #f8fafc;
  --text-secondary: #94a3b8;
  --text-muted: #64748b;
  --accent: #3b82f6;
  --accent-hover: #2563eb;
  --success: #22c55e;
  --warning: #f59e0b;
  --danger: #ef4444;
  --danger-hover: #dc2626;
}

* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Pretendard', sans-serif; background: var(--bg-primary); color: var(--text-primary); min-height: 100vh; }

/* ── Layout ── */
.app { display: flex; height: 100vh; }
.sidebar { width: 240px; background: var(--bg-card); border-right: 1px solid var(--border); display: flex; flex-direction: column; flex-shrink: 0; }
.main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }

/* ── Sidebar ── */
.sidebar-header { padding: 20px; border-bottom: 1px solid var(--border); }
.sidebar-header h1 { font-size: 16px; font-weight: 700; }
.sidebar-header .sub { font-size: 11px; color: var(--text-muted); margin-top: 4px; }

.nav-items { flex: 1; padding: 12px; }
.nav-item { display: flex; align-items: center; gap: 10px; padding: 10px 12px; border-radius: 8px; cursor: pointer; color: var(--text-secondary); font-size: 14px; transition: all 0.15s; margin-bottom: 2px; }
.nav-item:hover { background: var(--bg-hover); color: var(--text-primary); }
.nav-item.active { background: var(--accent); color: white; }
.nav-item .icon { width: 18px; text-align: center; }
.nav-item .badge { margin-left: auto; background: var(--accent); color: white; font-size: 11px; padding: 1px 6px; border-radius: 10px; }

.sidebar-footer { padding: 12px; border-top: 1px solid var(--border); }
.status-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
.status-dot.running { background: var(--success); animation: pulse 1.5s infinite; }
.status-dot.idle { background: var(--text-muted); }
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }

/* ── Page Content ── */
.page { display: none; flex-direction: column; flex: 1; overflow: hidden; }
.page.active { display: flex; }
.page-header { padding: 20px 28px; border-bottom: 1px solid var(--border); }
.page-header h2 { font-size: 18px; font-weight: 600; }
.page-body { flex: 1; overflow-y: auto; padding: 24px 28px; }

/* ── Cards Grid ── */
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; margin-bottom: 24px; }
.card { background: var(--bg-card); border: 1px solid var(--border); border-radius: 10px; padding: 16px; }
.card .label { font-size: 12px; color: var(--text-muted); margin-bottom: 6px; }
.card .value { font-size: 24px; font-weight: 700; }
.card .sub { font-size: 11px; color: var(--text-muted); margin-top: 4px; }
.card.accent .value { color: var(--accent); }
.card.success .value { color: var(--success); }
.card.danger .value { color: var(--danger); }
.card.warning .value { color: var(--warning); }

/* ── Form Controls ── */
.form-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 14px; margin-bottom: 20px; }
.form-group { display: flex; flex-direction: column; gap: 4px; }
.form-group label { font-size: 12px; color: var(--text-muted); font-weight: 500; }
.form-group input, .form-group select {
  background: var(--bg-primary); border: 1px solid var(--border); border-radius: 6px;
  padding: 8px 12px; color: var(--text-primary); font-size: 13px; outline: none;
}
.form-group input:focus, .form-group select:focus { border-color: var(--accent); }

.btn { display: inline-flex; align-items: center; gap: 6px; padding: 8px 18px; border-radius: 8px; font-size: 13px; font-weight: 600; cursor: pointer; border: none; transition: all 0.15s; }
.btn-primary { background: var(--accent); color: white; }
.btn-primary:hover { background: var(--accent-hover); }
.btn-danger { background: var(--danger); color: white; }
.btn-danger:hover { background: var(--danger-hover); }
.btn-secondary { background: var(--bg-hover); color: var(--text-primary); border: 1px solid var(--border); }
.btn-secondary:hover { border-color: var(--accent); }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-group { display: flex; gap: 8px; flex-wrap: wrap; }

/* ── Progress Bar ── */
.progress-bar { height: 6px; background: var(--bg-primary); border-radius: 3px; overflow: hidden; margin: 12px 0; }
.progress-bar .fill { height: 100%; background: var(--accent); border-radius: 3px; transition: width 0.3s; }

/* ── Logs ── */
.log-container { background: #0c0c14; border: 1px solid var(--border); border-radius: 8px; padding: 12px; font-family: 'Cascadia Code', 'JetBrains Mono', 'Fira Code', monospace; font-size: 12px; line-height: 1.6; overflow-y: auto; flex: 1; min-height: 200px; max-height: calc(100vh - 300px); }
.log-line { color: var(--text-secondary); white-space: pre-wrap; word-break: break-all; }
.log-line.error { color: var(--danger); }
.log-line.warning { color: var(--warning); }
.log-line.info { color: var(--success); }

/* ── Table ── */
.table-controls { display: flex; gap: 10px; margin-bottom: 14px; flex-wrap: wrap; align-items: center; }
.search-input { background: var(--bg-primary); border: 1px solid var(--border); border-radius: 6px; padding: 8px 14px; color: var(--text-primary); font-size: 13px; width: 280px; outline: none; }
.search-input:focus { border-color: var(--accent); }
.chip { padding: 4px 12px; border-radius: 20px; font-size: 12px; cursor: pointer; background: var(--bg-primary); border: 1px solid var(--border); color: var(--text-secondary); transition: all 0.15s; }
.chip:hover { border-color: var(--accent); color: var(--text-primary); }
.chip.active { background: var(--accent); border-color: var(--accent); color: white; }

table { width: 100%; border-collapse: collapse; }
thead th { background: var(--bg-primary); padding: 10px 14px; text-align: left; font-size: 11px; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.04em; border-bottom: 1px solid var(--border); cursor: pointer; white-space: nowrap; position: sticky; top: 0; z-index: 1; }
thead th:hover { color: var(--text-primary); }
tbody tr { border-bottom: 1px solid rgba(51,65,85,0.3); transition: background 0.1s; }
tbody tr:hover { background: var(--bg-hover); }
tbody td { padding: 10px 14px; font-size: 13px; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
.badge-normal { background: #065f46; color: #6ee7b7; }
.badge-suspicious { background: #7c2d12; color: #fdba74; }
.badge-scam { background: #7f1d1d; color: #fca5a5; }
tr.suspicious { background: rgba(248,113,113,0.05); }

/* ── File list ── */
.file-item { display: flex; align-items: center; justify-content: space-between; padding: 10px 14px; background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; margin-bottom: 6px; }
.file-item .name { font-size: 13px; font-weight: 500; }
.file-item .meta { font-size: 11px; color: var(--text-muted); }
</style>
</head>
<body>

<div class="app">
  <!-- Sidebar -->
  <div class="sidebar">
    <div class="sidebar-header">
      <h1>Crawler</h1>
      <div class="sub">Job Posting Collector</div>
    </div>
    <div class="nav-items">
      <div class="nav-item active" data-page="dashboard"><span class="icon">&#9632;</span> Dashboard</div>
      <div class="nav-item" data-page="crawl"><span class="icon">&#9654;</span> Crawl</div>
      <div class="nav-item" data-page="results"><span class="icon">&#9776;</span> Results <span class="badge" id="results-badge">0</span></div>
      <div class="nav-item" data-page="logs"><span class="icon">&#9999;</span> Logs</div>
      <div class="nav-item" data-page="schedule"><span class="icon">&#128337;</span> Scheduler</div>
      <div class="nav-item" data-page="health"><span class="icon">&#128154;</span> Health</div>
      <div class="nav-item" data-page="files"><span class="icon">&#128193;</span> Files</div>
    </div>
    <div class="sidebar-footer">
      <span class="status-dot idle" id="status-dot"></span>
      <span style="font-size:12px;color:var(--text-muted)" id="status-text">Idle</span>
    </div>
  </div>

  <!-- Main Content -->
  <div class="main">

    <!-- Dashboard Page -->
    <div class="page active" id="page-dashboard">
      <div class="page-header"><h2>Dashboard</h2></div>
      <div class="page-body">
        <div class="cards">
          <div class="card accent"><div class="label">Total Collected</div><div class="value" id="d-total">0</div></div>
          <div class="card success"><div class="label">Normal</div><div class="value" id="d-normal">0</div></div>
          <div class="card danger"><div class="label">Suspicious</div><div class="value" id="d-suspicious">0</div></div>
          <div class="card warning"><div class="label">Companies</div><div class="value" id="d-companies">0</div></div>
        </div>
        <div class="card" style="margin-bottom:20px">
          <div class="label">Current Task</div>
          <div style="font-size:14px;margin-top:6px" id="d-task">No active task</div>
          <div class="progress-bar"><div class="fill" id="d-progress" style="width:0%"></div></div>
          <div style="font-size:11px;color:var(--text-muted)" id="d-progress-text">-</div>
        </div>
        <div class="card">
          <div class="label">Recent Logs</div>
          <div class="log-container" style="max-height:300px;margin-top:8px" id="d-logs"></div>
        </div>
      </div>
    </div>

    <!-- Crawl Page -->
    <div class="page" id="page-crawl">
      <div class="page-header"><h2>Run Crawler</h2></div>
      <div class="page-body">
        <div class="form-grid">
          <div class="form-group"><label>Site</label><select id="c-site"><option value="rocketpunch">Rocketpunch</option></select></div>
          <div class="form-group"><label>Start Page</label><input type="number" id="c-start" value="1" min="1"></div>
          <div class="form-group"><label>End Page</label><input type="number" id="c-end" value="3" min="1"></div>
          <div class="form-group"><label>Delay (sec)</label><input type="number" id="c-delay" value="5" min="1" step="0.5"></div>
          <div class="form-group"><label>Keywords</label><input type="text" id="c-keywords" placeholder="e.g. backend"></div>
          <div class="form-group"><label>Fetch Details</label><select id="c-details"><option value="false">No (list only)</option><option value="true">Yes (page_action click)</option></select></div>
          <div class="form-group"><label>Headless</label><select id="c-headless"><option value="true">Yes</option><option value="false">No (show browser)</option></select></div>
          <div class="form-group"><label>Discover API</label><select id="c-api"><option value="false">No</option><option value="true">Yes (capture_xhr)</option></select></div>
        </div>
        <div class="btn-group">
          <button class="btn btn-primary" id="btn-start" onclick="startCrawl()">&#9654; Start Crawl</button>
          <button class="btn btn-danger" id="btn-stop" onclick="stopCrawl()" disabled>&#9724; Stop</button>
        </div>
        <div style="margin-top:20px">
          <div class="card">
            <div class="label">Progress</div>
            <div class="progress-bar"><div class="fill" id="c-progress" style="width:0%"></div></div>
            <div style="font-size:12px;color:var(--text-muted)" id="c-status">Ready</div>
          </div>
        </div>
      </div>
    </div>

    <!-- Results Page -->
    <div class="page" id="page-results">
      <div class="page-header"><h2>Results</h2></div>
      <div class="page-body">
        <div class="table-controls">
          <input type="text" class="search-input" id="r-search" placeholder="Search company, title, category..." oninput="renderResults()">
          <div class="chip active" onclick="setResultFilter('all', this)">All</div>
          <div class="chip" onclick="setResultFilter('suspicious', this)">Suspicious</div>
          <div class="chip" onclick="setResultFilter('normal', this)">Normal</div>
          <button class="btn btn-secondary" onclick="loadResults()">Load from file</button>
        </div>
        <div style="overflow-x:auto">
          <table>
            <thead><tr>
              <th>#</th><th>Company</th><th>Title</th><th>Category</th><th>Status</th><th>URL</th>
            </tr></thead>
            <tbody id="r-tbody"></tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- Logs Page -->
    <div class="page" id="page-logs">
      <div class="page-header">
        <h2>Live Logs</h2>
      </div>
      <div class="page-body" style="display:flex;flex-direction:column">
        <div class="btn-group" style="margin-bottom:12px">
          <button class="btn btn-secondary" onclick="clearLogs()">Clear</button>
          <label style="font-size:12px;color:var(--text-muted);display:flex;align-items:center;gap:4px">
            <input type="checkbox" id="l-autoscroll" checked> Auto-scroll
          </label>
        </div>
        <div class="log-container" id="l-container"></div>
      </div>
    </div>

    <!-- Scheduler Page -->
    <div class="page" id="page-schedule">
      <div class="page-header"><h2>Incremental Scheduler</h2></div>
      <div class="page-body">
        <div class="cards" style="margin-bottom:20px">
          <div class="card accent"><div class="label">Last Run</div><div class="value" style="font-size:16px" id="s-last-time">-</div></div>
          <div class="card success"><div class="label">New Items</div><div class="value" id="s-new">-</div></div>
          <div class="card"><div class="label">Duplicates</div><div class="value" id="s-dupes">-</div></div>
          <div class="card warning"><div class="label">Errors</div><div class="value" id="s-errors">-</div></div>
        </div>

        <div class="card" style="margin-bottom:20px">
          <div class="label" style="margin-bottom:12px">Run Incremental Collection</div>
          <div class="form-grid">
            <div class="form-group"><label>Site</label><select id="s-site"><option value="rocketpunch">Rocketpunch</option></select></div>
            <div class="form-group"><label>Pages</label><input type="text" id="s-pages" value="1-10" placeholder="1-10"></div>
            <div class="form-group"><label>Delay (sec)</label><input type="number" id="s-delay" value="5" min="1" step="0.5"></div>
            <div class="form-group"><label>Keywords</label><input type="text" id="s-keywords" placeholder="optional"></div>
          </div>
          <div class="btn-group" style="margin-top:8px">
            <button class="btn btn-primary" id="btn-schedule-run" onclick="runIncremental()">&#9654; Run Now</button>
            <span style="font-size:12px;color:var(--text-muted);display:flex;align-items:center" id="s-run-status"></span>
          </div>
        </div>

        <div class="card">
          <div class="label" style="margin-bottom:12px">Run History</div>
          <div style="overflow-x:auto">
            <table>
              <thead><tr>
                <th>Timestamp</th><th>Total</th><th>New</th><th>Dupes</th><th>Errors</th><th>Health</th><th>File</th>
              </tr></thead>
              <tbody id="s-history-tbody"></tbody>
            </table>
          </div>
        </div>
      </div>
    </div>

    <!-- Health Page -->
    <div class="page" id="page-health">
      <div class="page-header"><h2>System Health</h2></div>
      <div class="page-body">
        <div class="cards" style="margin-bottom:20px">
          <div class="card" id="h-score-card"><div class="label">Health Score</div><div class="value" id="h-score">-</div><div class="sub" id="h-status">-</div></div>
          <div class="card danger"><div class="label">Recent Faults</div><div class="value" id="h-fault-count">0</div></div>
          <div class="card warning"><div class="label">Criticals</div><div class="value" id="h-critical-count">0</div></div>
          <div class="card"><div class="label">Last Fault</div><div class="value" style="font-size:14px" id="h-last-fault">None</div></div>
        </div>

        <div class="card" style="margin-bottom:20px">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
            <div class="label">Health Score Meter</div>
            <button class="btn btn-secondary" onclick="refreshHealth()">Refresh</button>
          </div>
          <div style="background:var(--bg-primary);border-radius:8px;height:32px;overflow:hidden;position:relative">
            <div id="h-meter" style="height:100%;border-radius:8px;transition:width 0.5s;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;color:white;min-width:40px" ></div>
          </div>
        </div>

        <div class="card">
          <div class="label" style="margin-bottom:12px">Fault History</div>
          <div style="overflow-x:auto">
            <table>
              <thead><tr>
                <th>Time</th><th>Type</th><th>Severity</th><th>Message</th><th>Recovered</th>
              </tr></thead>
              <tbody id="h-fault-tbody"></tbody>
            </table>
          </div>
        </div>
      </div>
    </div>

    <!-- Files Page -->
    <div class="page" id="page-files">
      <div class="page-header"><h2>Saved Files</h2></div>
      <div class="page-body">
        <button class="btn btn-secondary" onclick="refreshFiles()" style="margin-bottom:16px">Refresh</button>
        <div id="f-list"></div>
      </div>
    </div>

  </div>
</div>

<script>
// ─── State ───
let currentFilter = 'all';
let results = [];
let logLines = [];

// ─── Navigation ───
document.querySelectorAll('.nav-item').forEach(item => {
  item.addEventListener('click', () => {
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    item.classList.add('active');
    document.getElementById('page-' + item.dataset.page).classList.add('active');
  });
});

// ─── SSE Log Stream ───
const evtSource = new EventSource('/api/logs/stream');
evtSource.onmessage = function(e) {
  const data = JSON.parse(e.data);
  if (data.log) {
    addLog(data.log);
  }
};

function addLog(msg) {
  logLines.push(msg);
  if (logLines.length > 2000) logLines = logLines.slice(-1500);

  const cls = msg.includes('ERROR') ? 'error' : msg.includes('WARNING') ? 'warning' : msg.includes('INFO') ? 'info' : '';

  // Live logs page
  const lc = document.getElementById('l-container');
  const div = document.createElement('div');
  div.className = 'log-line ' + cls;
  div.textContent = msg;
  lc.appendChild(div);
  if (lc.children.length > 2000) lc.removeChild(lc.firstChild);
  if (document.getElementById('l-autoscroll').checked) lc.scrollTop = lc.scrollHeight;

  // Dashboard logs
  const dl = document.getElementById('d-logs');
  const d2 = div.cloneNode(true);
  dl.appendChild(d2);
  if (dl.children.length > 200) dl.removeChild(dl.firstChild);
  dl.scrollTop = dl.scrollHeight;
}

// ─── Polling Status ───
setInterval(async () => {
  try {
    const res = await fetch('/api/status');
    const s = res.ok ? await res.json() : null;
    if (!s) return;

    const dot = document.getElementById('status-dot');
    const txt = document.getElementById('status-text');
    dot.className = 'status-dot ' + (s.is_running ? 'running' : 'idle');
    txt.textContent = s.is_running ? 'Running' : 'Idle';

    document.getElementById('btn-start').disabled = s.is_running;
    document.getElementById('btn-stop').disabled = !s.is_running;

    // Dashboard cards
    document.getElementById('d-task').textContent = s.current_task || 'No active task';

    const p = s.progress;
    const pct = p.total_pages ? Math.round((p.current_page / p.total_pages) * 100) : 0;
    document.getElementById('d-progress').style.width = pct + '%';
    document.getElementById('c-progress').style.width = pct + '%';
    document.getElementById('d-progress-text').textContent =
      `Page ${p.current_page}/${p.total_pages} | Found: ${p.items_found} | Detailed: ${p.items_detailed} | Errors: ${p.errors} | ${p.elapsed_sec}s`;
    document.getElementById('c-status').textContent =
      s.is_running ? `Running... ${p.current_page}/${p.total_pages}` : 'Ready';

    document.getElementById('results-badge').textContent = s.results_count;

    // Fetch results for dashboard
    if (s.results_count > 0) {
      const rr = await fetch('/api/results');
      if (rr.ok) {
        const rd = await rr.json();
        results = rd.items;
        const sus = results.filter(r => r.classification === 'suspicious' || r.classification === 'scam').length;
        const companies = new Set(results.map(r => r.company_name)).size;
        document.getElementById('d-total').textContent = results.length;
        document.getElementById('d-normal').textContent = results.length - sus;
        document.getElementById('d-suspicious').textContent = sus;
        document.getElementById('d-companies').textContent = companies;
        renderResults();
      }
    }
  } catch (e) {}
}, 2000);

// ─── Crawl Controls ───
async function startCrawl() {
  const body = {
    site: document.getElementById('c-site').value,
    start_page: parseInt(document.getElementById('c-start').value),
    end_page: parseInt(document.getElementById('c-end').value),
    delay: parseFloat(document.getElementById('c-delay').value),
    keywords: document.getElementById('c-keywords').value,
    fetch_details: document.getElementById('c-details').value === 'true',
    headless: document.getElementById('c-headless').value === 'true',
    discover_api: document.getElementById('c-api').value === 'true',
  };
  await fetch('/api/crawl/start', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) });
}

async function stopCrawl() {
  await fetch('/api/crawl/stop', { method: 'POST' });
}

// ─── Results ───
function renderResults() {
  const q = (document.getElementById('r-search')?.value || '').toLowerCase();
  let filtered = results;
  if (currentFilter === 'suspicious') filtered = filtered.filter(r => r.classification === 'suspicious' || r.classification === 'scam');
  if (currentFilter === 'normal') filtered = filtered.filter(r => !r.classification || r.classification === 'normal');
  if (q) filtered = filtered.filter(r => ((r.title||'')+(r.company_name||'')+(r.category||'')).toLowerCase().includes(q));

  const tbody = document.getElementById('r-tbody');
  if (!tbody) return;
  tbody.innerHTML = filtered.map((r, i) => {
    const cls = r.classification === 'suspicious' || r.classification === 'scam' ? 'suspicious' : '';
    const badge = `<span class="badge badge-${r.classification || 'normal'}">${r.classification || 'normal'}</span>`;
    const urlLink = r.url ? `<a href="${r.url}" target="_blank" style="color:var(--accent);font-size:12px">Open</a>` : '-';
    return `<tr class="${cls}"><td>${r.data_index ?? i}</td><td>${r.company_name || '-'}</td><td>${r.title || '-'}</td><td style="font-size:12px;color:var(--text-muted)">${r.category || '-'}</td><td>${badge}</td><td>${urlLink}</td></tr>`;
  }).join('');
}

function setResultFilter(f, el) {
  currentFilter = f;
  document.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
  if (el) el.classList.add('active');
  renderResults();
}

async function loadResults() {
  await fetch('/api/results/load', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}' });
}

// ─── Logs ───
function clearLogs() {
  document.getElementById('l-container').innerHTML = '';
  document.getElementById('d-logs').innerHTML = '';
}

// ─── Files ───
async function refreshFiles() {
  const res = await fetch('/api/files');
  if (!res.ok) return;
  const data = await res.json();
  const list = document.getElementById('f-list');
  if (!data.files.length) { list.innerHTML = '<div style="color:var(--text-muted);padding:20px">No files in data/ folder</div>'; return; }
  list.innerHTML = data.files.map(f => `
    <div class="file-item">
      <div><div class="name">${f.name}</div><div class="meta">${(f.size/1024).toFixed(1)} KB | ${f.modified}</div></div>
      <button class="btn btn-secondary" onclick="loadFile('${f.path}')">Load</button>
    </div>
  `).join('');
}

async function loadFile(path) {
  await fetch('/api/results/load', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({file: path}) });
}

// ─── Scheduler ───
async function runIncremental() {
  const btn = document.getElementById('btn-schedule-run');
  const statusEl = document.getElementById('s-run-status');
  btn.disabled = true;
  statusEl.textContent = 'Running...';

  const body = {
    site: document.getElementById('s-site').value,
    pages: document.getElementById('s-pages').value,
    delay: parseFloat(document.getElementById('s-delay').value),
    keywords: document.getElementById('s-keywords').value,
    headless: true,
  };

  try {
    const res = await fetch('/api/schedule/start', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) });
    if (!res.ok) { const d = await res.json(); statusEl.textContent = d.error || 'Error'; btn.disabled = false; return; }
    // Poll for completion
    pollScheduleStatus();
  } catch (e) { statusEl.textContent = 'Error: ' + e.message; btn.disabled = false; }
}

async function pollScheduleStatus() {
  const btn = document.getElementById('btn-schedule-run');
  const statusEl = document.getElementById('s-run-status');
  const check = async () => {
    try {
      const res = await fetch('/api/schedule/status');
      const data = await res.json();
      if (data.is_running) {
        statusEl.textContent = 'Running...';
        setTimeout(check, 2000);
      } else {
        btn.disabled = false;
        if (data.last_result) {
          const r = data.last_result;
          if (r.error) { statusEl.textContent = 'Error: ' + r.error; }
          else {
            statusEl.textContent = `Done: ${r.new_items} new, ${r.duplicates} dupes`;
            document.getElementById('s-new').textContent = r.new_items;
            document.getElementById('s-dupes').textContent = r.duplicates;
            document.getElementById('s-errors').textContent = r.errors;
            document.getElementById('s-last-time').textContent = r.timestamp ? r.timestamp.slice(0, 19) : '-';
          }
        }
        refreshScheduleHistory();
      }
    } catch (e) { btn.disabled = false; statusEl.textContent = 'Poll error'; }
  };
  setTimeout(check, 2000);
}

async function refreshScheduleHistory() {
  const site = document.getElementById('s-site').value;
  try {
    const res = await fetch(`/api/schedule/history?site=${site}&limit=30`);
    if (!res.ok) return;
    const data = await res.json();
    const tbody = document.getElementById('s-history-tbody');
    if (!data.runs || !data.runs.length) { tbody.innerHTML = '<tr><td colspan="7" style="color:var(--text-muted)">No history yet</td></tr>'; return; }

    tbody.innerHTML = data.runs.reverse().map(r => {
      const ts = (r.timestamp || '').slice(0, 19);
      const h = r.health || {};
      const healthBadge = h.criticals > 0
        ? `<span class="badge badge-scam">${h.criticals}C ${h.warnings || 0}W</span>`
        : h.warnings > 0
          ? `<span class="badge badge-suspicious">${h.warnings}W</span>`
          : '<span class="badge badge-normal">OK</span>';
      const file = r.output_file ? r.output_file.split('/').pop() : '-';
      return `<tr><td style="font-size:12px">${ts}</td><td>${r.total_found || 0}</td><td style="color:var(--success)">${r.new_items || 0}</td><td>${r.duplicates || 0}</td><td style="color:${r.errors > 0 ? 'var(--danger)' : 'inherit'}">${r.errors || 0}</td><td>${healthBadge}</td><td style="font-size:11px;color:var(--text-muted)">${file}</td></tr>`;
    }).join('');

    // Update last-run cards from history
    if (data.runs.length > 0) {
      const last = data.runs[0];
      document.getElementById('s-last-time').textContent = (last.timestamp || '-').slice(0, 19);
      document.getElementById('s-new').textContent = last.new_items ?? '-';
      document.getElementById('s-dupes').textContent = last.duplicates ?? '-';
      document.getElementById('s-errors').textContent = last.errors ?? '-';
    }
  } catch (e) {}
}

// ─── Health ───
async function refreshHealth() {
  const site = 'rocketpunch';
  try {
    // Health summary
    const hRes = await fetch(`/api/health?site=${site}`);
    if (hRes.ok) {
      const h = await hRes.json();
      const score = h.health_score ?? 0;
      document.getElementById('h-score').textContent = score;
      document.getElementById('h-status').textContent = h.status || '-';

      // Color the score card
      const scoreCard = document.getElementById('h-score-card');
      scoreCard.className = 'card ' + (score >= 80 ? 'success' : score >= 50 ? 'warning' : 'danger');

      // Meter bar
      const meter = document.getElementById('h-meter');
      meter.style.width = score + '%';
      meter.style.background = score >= 80 ? 'var(--success)' : score >= 50 ? 'var(--warning)' : 'var(--danger)';
      meter.textContent = score + '%';

      document.getElementById('h-fault-count').textContent = h.recent_faults ?? 0;
    }

    // Fault history
    const fRes = await fetch(`/api/faults?site=${site}&limit=50`);
    if (fRes.ok) {
      const f = await fRes.json();
      const faults = f.faults || [];
      const criticals = faults.filter(x => x.severity === 'critical').length;
      document.getElementById('h-critical-count').textContent = criticals;

      if (faults.length > 0) {
        const last = faults[faults.length - 1];
        document.getElementById('h-last-fault').textContent = last.fault_type + ' (' + last.severity + ')';
      } else {
        document.getElementById('h-last-fault').textContent = 'None';
      }

      const tbody = document.getElementById('h-fault-tbody');
      if (!faults.length) { tbody.innerHTML = '<tr><td colspan="5" style="color:var(--text-muted)">No faults recorded</td></tr>'; return; }

      tbody.innerHTML = faults.reverse().map(ev => {
        const ts = (ev.timestamp || '').slice(0, 19);
        const sevBadge = ev.severity === 'critical'
          ? '<span class="badge badge-scam">CRITICAL</span>'
          : ev.severity === 'warning'
            ? '<span class="badge badge-suspicious">WARNING</span>'
            : '<span class="badge badge-normal">INFO</span>';
        const recovered = ev.auto_recovered ? '<span style="color:var(--success)">Yes</span>' : '<span style="color:var(--text-muted)">No</span>';
        const msg = (ev.message || '').length > 80 ? ev.message.slice(0, 80) + '...' : (ev.message || '-');
        return `<tr><td style="font-size:12px">${ts}</td><td><span style="font-size:12px;color:var(--accent)">${ev.fault_type}</span></td><td>${sevBadge}</td><td style="font-size:12px">${msg}</td><td>${recovered}</td></tr>`;
      }).join('');
    }
  } catch (e) { console.error('Health refresh error:', e); }
}

// Init
refreshFiles();
refreshScheduleHistory();
refreshHealth();
</script>
</body>
</html>"""


# ────────────────────────────────────────────
# Main
# ────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Crawling Dashboard Server")
    parser.add_argument("--port", "-p", type=int, default=5000)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    print(f"\n  Crawling Dashboard")
    print(f"  http://localhost:{args.port}")
    print(f"  Press Ctrl+C to stop\n")

    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == "__main__":
    main()
