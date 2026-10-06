"""End-to-end processing-time benchmark — cold start, one match at a time.

Produces the numbers in report chapter 4, "端到端結果". See README.md in this folder.

Each benchmark match gets a fresh project dir under ``matches/_e2e/<name>/`` that holds
only ``input/<video>`` (a hard link to the original, so no extra disk and the original
match's caches are never touched). Nothing in ``cache/`` or ``stages/`` exists before the
run, so every stage — GPU phases included — really runs.

Usage (from the repo root; or double-click run_e2e.bat, which forwards its arguments):

    uv run python experiments/end_to_end/run_e2e.py --dry-run     # prechecks + prepare, no run
    uv run python experiments/end_to_end/run_e2e.py               # the real thing (~6 h)
    uv run python experiments/end_to_end/run_e2e.py --only test1  # subset

The scratch match dirs stay under ``matches/_e2e/`` (git-ignored, like every match dir).
Outputs go to ``experiments/end_to_end/results/``:
    env_*.json       machine / versions / git commit, one file per invocation
    runs.jsonl       one JSON record per finished match (appended as it goes)
    e2e_timing.csv   one row per match, one column per stage (rebuilt after every match)
    stages_long.csv  match x stage long table, for plotting
    summary.md       human-readable table
    driver.log       this script's own log (the runner's output stays on the console)

Re-running skips matches already recorded as ok in runs.jsonl. A match whose dir already
has cache/ or stages/ content (e.g. an interrupted run) is refused, because a warm cache
would make its timing meaningless — delete or rename that dir to redo it.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

E2E_ROOT = REPO / "matches" / "_e2e"
RESULTS = REPO / "experiments" / "end_to_end" / "results"

#: Short -> long, so the 8-minute test1 surfaces any setup problem first.
DEFAULT_MATCHES = ["test1", "test5", "test4", "TTY_vs_ASY_2023", "test2"]

#: Fixed column order for the CSV; unknown stages are appended after these.
STAGE_ORDER = [
    "match_segmentation", "audio_highlight", "highlight_ranking", "score_recognition",
    "court_detection", "shuttle_tracking", "pose", "event_detection",
    "stroke_classification", "player_identity", "commentary",
]
#: Stages whose time is mostly waiting on an external API, not local compute.
API_BOUND = {"score_recognition", "commentary"}

MODEL_FILES = [
    "models/TrackNet_best.pt",
    "models/InpaintNet_best.pt",
    "models/bst_CG_JnB_bone_between_2_hits_with_max_limits_seq_100_merged.pt",
]
MIN_FREE_GB = 20


# --------------------------------------------------------------------------- helpers

def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def log(msg: str) -> None:
    line = f"[e2e {dt.datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    with open(RESULTS / "driver.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run_text(cmd: list[str]) -> str | None:
    try:
        out = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=60)
        return out.stdout.strip() if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def keep_awake() -> None:
    """Stop Windows from sleeping while this process runs (display may still turn off)."""
    if os.name != "nt":
        return
    import ctypes
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    log("sleep inhibited for the duration of the run")


def ffprobe(video: Path) -> dict:
    raw = run_text([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate:format=duration",
        "-of", "json", str(video),
    ])
    if not raw:
        return {}
    d = json.loads(raw)
    st = (d.get("streams") or [{}])[0]
    num, _, den = (st.get("r_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else None
    return {
        "video_sec": round(float(d.get("format", {}).get("duration", 0)), 2),
        "fps": round(fps, 3) if fps else None,
        "resolution": f"{st.get('width')}x{st.get('height')}",
    }


# --------------------------------------------------------------------------- prechecks

def probe_runtime() -> dict:
    """Ask a separate interpreter (same venv) what torch / onnxruntime can see."""
    code = r"""
import json
out = {}
try:
    import torch
    out["torch"] = torch.__version__
    out["torch_cuda"] = torch.version.cuda
    out["cuda_available"] = torch.cuda.is_available()
    if out["cuda_available"]:
        out["gpu"] = torch.cuda.get_device_name(0)
except Exception as e:
    out["torch_error"] = repr(e)
try:
    import onnxruntime as ort
    out["onnxruntime"] = ort.__version__
    out["ort_providers"] = ort.get_available_providers()
except Exception as e:
    out["ort_error"] = repr(e)
print(json.dumps(out))
"""
    raw = run_text([sys.executable, "-c", code])
    try:
        return json.loads(raw.splitlines()[-1]) if raw else {"error": "probe failed"}
    except json.JSONDecodeError:
        return {"error": raw}


def check_gemini() -> tuple[bool, str]:
    """Key present and the configured model reachable (metadata GET, no generation quota)."""
    import urllib.error
    import urllib.request

    from modules.common.config import get_gemini_api_key
    from modules.score_recognition.recognizer import DEFAULT_MODEL

    key = get_gemini_api_key()
    if not key:
        return False, "no Gemini API key (GEMINI_API_KEY or config.yaml)"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{DEFAULT_MODEL}?key={key}"
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            return resp.status == 200, f"model {DEFAULT_MODEL} reachable"
    except urllib.error.HTTPError as e:
        return False, f"Gemini HTTP {e.code} for model {DEFAULT_MODEL} (bad key or model name?)"
    except OSError as e:
        return False, f"Gemini unreachable: {e}"


def prechecks(args) -> dict:
    problems: list[str] = []
    env: dict = {"started_at": now_iso(), "python": sys.version.split()[0],
                 "platform": platform.platform(), "cpu": platform.processor()}

    env["git_commit"] = run_text(["git", "rev-parse", "HEAD"])
    dirty = run_text(["git", "status", "--porcelain"])
    env["git_dirty_files"] = len(dirty.splitlines()) if dirty else 0
    env["nvidia_smi"] = run_text(["nvidia-smi", "--query-gpu=name,driver_version,memory.total",
                                  "--format=csv,noheader"])

    rt = probe_runtime()
    env["runtime"] = rt
    if not rt.get("cuda_available"):
        msg = f"torch does not see a CUDA GPU ({rt})"
        (log if args.allow_cpu else problems.append)(("WARNING: " if args.allow_cpu else "") + msg)
    if "CUDAExecutionProvider" not in (rt.get("ort_providers") or []):
        msg = "onnxruntime has no CUDAExecutionProvider -> pose would run on CPU"
        (log if args.allow_cpu else problems.append)(("WARNING: " if args.allow_cpu else "") + msg)

    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            problems.append(f"{tool} not on PATH")
    for f in MODEL_FILES:
        if not (REPO / f).is_file():
            problems.append(f"missing model weights: {f}")

    free_gb = shutil.disk_usage(REPO).free / 1e9
    env["disk_free_gb"] = round(free_gb, 1)
    if free_gb < MIN_FREE_GB:
        problems.append(f"only {free_gb:.0f} GB free (want >= {MIN_FREE_GB})")

    if args.skip_gemini_check:
        log("Gemini check skipped")
    else:
        ok, msg = check_gemini()
        env["gemini_check"] = msg
        (log if ok else problems.append)(msg)

    env["matches"] = args.only or DEFAULT_MATCHES
    env["commentary"] = "off (runner default)"
    for p in problems:
        log(f"PRECHECK FAIL: {p}")
    env["precheck_problems"] = problems
    return env


# --------------------------------------------------------------------------- prepare

def has_content(d: Path) -> bool:
    return d.is_dir() and any(d.iterdir())


def prepare(name: str) -> tuple[Path | None, str]:
    """Create matches/_e2e/<name>/input/<video> as a hard link. Returns (dir, note)."""
    from modules.contracts import resolve_input_video

    src_match = REPO / "matches" / name
    try:
        src = resolve_input_video(src_match)
    except FileNotFoundError as e:
        return None, f"source video missing: {e}"

    dst = E2E_ROOT / name
    if has_content(dst / "stages") or has_content(dst / "cache"):
        return None, (f"{dst.relative_to(REPO)} already has stages/ or cache/ — not a cold "
                      "start. Delete or rename that dir to re-run it.")
    inp = dst / "input"
    inp.mkdir(parents=True, exist_ok=True)
    others = [p for p in inp.iterdir() if p.name != src.name]
    if others:
        return None, f"unexpected files in {inp.relative_to(REPO)}: {[p.name for p in others]}"
    target = inp / src.name
    if target.exists():
        return dst, "input already prepared"
    try:
        os.link(src, target)
        return dst, "hard-linked input"
    except OSError:
        shutil.copy2(src, target)
        return dst, "copied input (hard link not possible)"


# --------------------------------------------------------------------------- collect

def parse_ts(s: str | None) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(s) if s else None
    except ValueError:
        return None


def collect(dst: Path) -> dict:
    stages: dict[str, dict] = {}
    for st in sorted((dst / "stages").glob("*/status.json")):
        d = json.loads(st.read_text(encoding="utf-8"))
        a, b = parse_ts(d.get("started_at")), parse_ts(d.get("finished_at"))
        stages[d.get("name", st.parent.name)] = {
            "status": d.get("status"),
            "sec": round((b - a).total_seconds(), 3) if a and b else None,
            "error": (d.get("error") or "")[:500] or None,
        }

    info: dict = {"stages": stages}
    seg = dst / "stages" / "match_segmentation" / "segments.json"
    if seg.is_file():
        s = json.loads(seg.read_text(encoding="utf-8")).get("segments", [])
        info["n_rallies"] = len(s)
        info["rally_sec"] = round(sum(x.get("duration_sec", 0) for x in s), 1)
    ev = dst / "stages" / "event_detection" / "events.json"
    if ev.is_file():
        info["n_events"] = len(json.loads(ev.read_text(encoding="utf-8")).get("events", []))
    return info


# --------------------------------------------------------------------------- report

def load_runs() -> list[dict]:
    p = RESULTS / "runs.jsonl"
    if not p.is_file():
        return []
    latest: dict[str, dict] = {}  # a re-run replaces that match's earlier (e.g. failed) record
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            latest.pop(r["match"], None)
            latest[r["match"]] = r
    return list(latest.values())


def write_reports(runs: list[dict]) -> None:
    stage_cols = list(STAGE_ORDER)
    for r in runs:
        stage_cols += [s for s in r["stages"] if s not in stage_cols]
    stage_cols = [s for s in stage_cols if any(s in r["stages"] for r in runs)]

    base = ["match", "ok", "exit_code", "video_sec", "fps", "resolution", "n_rallies",
            "rally_sec", "n_events", "e2e_sec", "stage_sum_sec", "overhead_sec",
            "local_sec", "rtf", "rtf_local", "started_at", "finished_at"]
    with open(RESULTS / "e2e_timing.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(base + [f"{s}_sec" for s in stage_cols])
        for r in runs:
            w.writerow([r.get(k) for k in base] +
                       [r["stages"].get(s, {}).get("sec") for s in stage_cols])

    with open(RESULTS / "stages_long.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["match", "stage", "status", "sec", "share_of_e2e"])
        for r in runs:
            for s in stage_cols:
                v = r["stages"].get(s)
                if v:
                    share = round(v["sec"] / r["e2e_sec"], 4) if v["sec"] and r["e2e_sec"] else None
                    w.writerow([r["match"], s, v["status"], v["sec"], share])

    def mmss(x):
        return "-" if x is None else f"{int(x // 60)}:{int(x % 60):02d}"

    lines = ["# E2E processing time (cold start)", "",
             "RTF = E2E / video length; RTF local = local (E2E minus score_recognition) / video length. "
             "Below 1 means faster than real time.", "",
             "| match | video | rallies | rally time | E2E | local (no API) | RTF | RTF local "
             "| E2E / rally-sec | local / rally-sec | ok |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]

    def per_rally(x, r):
        return round(x / r["rally_sec"], 2) if x and r.get("rally_sec") else "-"

    for r in runs:
        lines.append(f"| {r['match']} | {mmss(r.get('video_sec'))} | {r.get('n_rallies', '-')} | "
                     f"{mmss(r.get('rally_sec'))} | {mmss(r['e2e_sec'])} | "
                     f"{mmss(r.get('local_sec'))} | {r.get('rtf')} | {r.get('rtf_local')} | "
                     f"{per_rally(r['e2e_sec'], r)} | {per_rally(r.get('local_sec'), r)} | "
                     f"{'yes' if r['ok'] else 'NO'} |")
    ok_runs = [r for r in runs if r["ok"]]  # a failed run's partial stages would skew shares
    tot = {s: sum((r["stages"].get(s, {}).get("sec") or 0) for r in ok_runs) for s in stage_cols}
    grand = sum(tot.values()) or 1
    lines += ["", f"## Share of total time by stage ({len(ok_runs)} successful matches)", "",
              "| stage | total | share |", "|---|---|---|"]
    for s, v in sorted(tot.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {s} | {mmss(v)} | {v / grand:.1%} |")
    (RESULTS / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- run

def run_one(name: str, dst: Path) -> dict:
    rel = dst.relative_to(REPO).as_posix()
    from modules.contracts import resolve_input_video
    probe = ffprobe(resolve_input_video(dst))

    log(f"=== {name}: runner start ({probe.get('video_sec')} s video)")
    started = now_iso()
    t0 = time.perf_counter()
    # Console is inherited (not piped): the runner draws progress differently when
    # stdout is not a TTY, and the benchmark should measure the normal code path.
    proc = subprocess.run([sys.executable, "-m", "modules.runner", rel], cwd=REPO)
    e2e = round(time.perf_counter() - t0, 2)
    finished = now_iso()

    info = collect(dst)
    stages = info["stages"]
    stage_sum = round(sum(v["sec"] or 0 for v in stages.values()), 2)
    api = sum((stages.get(s, {}).get("sec") or 0) for s in API_BOUND)
    local = round(e2e - api, 2)
    vsec = probe.get("video_sec") or 0
    ok = proc.returncode == 0 and all(v["status"] == "completed" for v in stages.values())
    rec = {
        "match": name, "ok": ok, "exit_code": proc.returncode, **probe,
        "n_rallies": info.get("n_rallies"), "rally_sec": info.get("rally_sec"),
        "n_events": info.get("n_events"),
        "e2e_sec": e2e, "stage_sum_sec": stage_sum, "overhead_sec": round(e2e - stage_sum, 2),
        "local_sec": local,
        "rtf": round(e2e / vsec, 3) if vsec else None,
        "rtf_local": round(local / vsec, 3) if vsec else None,
        "started_at": started, "finished_at": finished, "stages": stages,
    }
    with open(RESULTS / "runs.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    failed = [s for s, v in stages.items() if v["status"] != "completed"]
    log(f"=== {name}: {'OK' if ok else 'FAILED'} in {e2e / 60:.1f} min"
        + (f" (not completed: {failed})" if failed else ""))
    return rec


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="+", help=f"subset of matches (default: {DEFAULT_MATCHES})")
    ap.add_argument("--dry-run", action="store_true", help="prechecks + prepare dirs, then stop")
    ap.add_argument("--allow-cpu", action="store_true", help="don't abort when the GPU is missing")
    ap.add_argument("--skip-gemini-check", action="store_true")
    args = ap.parse_args()
    os.chdir(REPO)
    RESULTS.mkdir(parents=True, exist_ok=True)

    log(f"precheck ({'dry run' if args.dry_run else 'full run'})")
    env = prechecks(args)
    if env["precheck_problems"]:
        log("aborting: fix the precheck failures above (nothing was run)")
        raise SystemExit(2)
    # One file per invocation: a re-run of a single match must not erase the main run's record.
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    env_path = RESULTS / f"env_{stamp}{'_dryrun' if args.dry_run else ''}.json"
    env_path.write_text(json.dumps(env, indent=2, ensure_ascii=False), encoding="utf-8")
    rt = env["runtime"]
    log(f"GPU: {rt.get('gpu')} | torch {rt.get('torch')} (CUDA {rt.get('torch_cuda')}) | "
        f"ort {rt.get('onnxruntime')} | git {str(env['git_commit'])[:7]} "
        f"(+{env['git_dirty_files']} uncommitted files)")

    done = {r["match"] for r in load_runs() if r.get("ok")}
    plan: list[tuple[str, Path]] = []
    for name in env["matches"]:
        if name in done:
            log(f"{name}: already done in runs.jsonl, skipping")
            continue
        dst, note = prepare(name)
        log(f"{name}: {note}")
        if dst is not None:
            plan.append((name, dst))

    if args.dry_run:
        log(f"dry run OK — ready to run: {[n for n, _ in plan]}")
        return
    if not plan:
        log("nothing to run")
        return

    keep_awake()
    t0 = time.perf_counter()
    for name, dst in plan:
        try:
            run_one(name, dst)
        except Exception as e:  # keep going: one broken match must not cost the night
            log(f"=== {name}: driver error {e!r}")
        write_reports(load_runs())
    log(f"all done in {(time.perf_counter() - t0) / 3600:.2f} h — see "
        f"{(RESULTS / 'summary.md').relative_to(REPO)}")


if __name__ == "__main__":
    main()
