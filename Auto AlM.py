#!/usr/bin/env python3
"""
Anaplan ALM automation (parallel sync) - tailored to user's JSON structure.
Features:
 - backup previous run logs to "logs/Log-files backup"
 - masked password input with '*' echo
 - auto-detect workspace id from model id
 - show model name + model id when prompting user
 - three ALM options per Dev->Prod mapping:
    1) Select from list of syncable tags
    2) Load absolute (latest) tag
    3) Create new tag in Dev (then sync)
 - collect user choices for all mappings, then execute syncs in parallel
 - logs saved to logs/run_<timestamp>.log
Requirements: requests, tqdm
"""
import os, sys, json, time, shutil
from pathlib import Path
from datetime import datetime
import requests
from requests.auth import HTTPBasicAuth
import getpass
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

# ---------- CONFIG ----------
CONFIG_PATH = "config.json"    # your provided JSON
LOG_DIR = Path("logs")
LOG_BACKUP_DIR = LOG_DIR / "Log-files backup"
API_BASE = "https://api.anaplan.com/2/0"
AUTH_URL = "https://auth.anaplan.com/token/authenticate"
MAX_WORKERS = 8                # concurrency for parallel syncs (adjust as needed)
POLL_INTERVAL = 3              # seconds between polling sync task statuses
# -----------------------------

def ts(fmt="%Y%m%d_%H%M%S"):
    return datetime.now().strftime(fmt)

def backup_logs():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LOG_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    moved = []
    for f in LOG_DIR.glob("run_*.log"):
        if f.is_file():
            newname = LOG_BACKUP_DIR / f"{f.stem}_{ts()}{f.suffix}"
            shutil.move(str(f), str(newname))
            moved.append(newname.name)
    if moved:
        print(f"[INFO] Moved {len(moved)} previous log file(s) to backup:")
        for n in moved:
            print("  -", n)
    else:
        print("[INFO] No previous run log files found to move.")
    return moved

class Logger:
    def __init__(self):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.path = LOG_DIR / f"run_{ts()}.log"
        self.f = open(self.path, "a", encoding="utf-8")
    def write(self, *parts):
        line = " ".join(str(p) for p in parts)
        timestamped = f"{datetime.now().isoformat()} {line}"
        self.f.write(timestamped + "\n")
        self.f.flush()
        print(timestamped)
    def close(self):
        self.f.close()

# masked password with asterisk: fallback to getpass if environment doesn't support
def get_password_masked(prompt="Password: "):
    try:
        # try to use getpass with a visible asterisk echo (best-effort)
        # many terminals won't allow printing '*' while input is hidden; fallback to getpass
        import sys, tty, termios
        sys.stdout.write(prompt); sys.stdout.flush()
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        passwd = []
        try:
            tty.setraw(fd)
            while True:
                ch = sys.stdin.read(1)
                if ch in ("\r", "\n"):
                    sys.stdout.write("\n")
                    break
                if ch == "\x7f":  # backspace
                    if passwd:
                        passwd.pop()
                        sys.stdout.write("\b \b")
                        sys.stdout.flush()
                    continue
                if ch == "\x03":
                    raise KeyboardInterrupt
                passwd.append(ch)
                sys.stdout.write("*"); sys.stdout.flush()
            return "".join(passwd)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    except Exception:
        return getpass.getpass(prompt)

# Authentication
def get_auth_token(username, password):
    resp = requests.post(AUTH_URL, auth=HTTPBasicAuth(username, password), timeout=30)
    if resp.status_code not in (200,201):
        raise RuntimeError(f"Authentication failed: {resp.status_code} - {resp.text}")
    data = resp.json()
    token = None
    # common shapes
    token = data.get("tokenInfo", {}).get("tokenValue") or data.get("value") or data.get("token")
    if not token:
        raise RuntimeError(f"Auth token not found in response: {data}")
    return token

def headers_for(token):
    return {"Authorization": f"AnaplanAuthToken {token}", "Content-Type": "application/json"}

# Get model metadata (including model name and workspace id)
def get_model_meta(model_id, token):
    url = f"{API_BASE}/models/{model_id}"
    r = requests.get(url, headers=headers_for(token), timeout=30)
    if r.status_code == 200:
        return r.json()
    # fallback try workspace-specific model listing is not possible without workspace id
    raise RuntimeError(f"Failed to fetch model metadata for {model_id}: {r.status_code} {r.text}")

def get_workspace_id_for_model(model_meta):
    # model metadata commonly includes 'workspaceId' or nested 'workspace'. Try common keys
    wid = model_meta.get("workspaceId") or model_meta.get("workspace", {}).get("id") or model_meta.get("workspaceId")
    # some responses embed workspace in 'workspace' object
    if not wid:
        # try a few other fields
        wid = model_meta.get("workspaceId") or model_meta.get("workspaceId")
    return wid

def get_model_name(model_meta):
    return model_meta.get("name") or model_meta.get("modelName") or "(unknown-name)"

# List syncable revisions (target=prod model, source=dev model)
def list_syncable_revisions(target_model_id, source_model_id, token):
    url = f"{API_BASE}/models/{target_model_id}/alm/SyncableRevisions?sourceModelId={source_model_id}"
    r = requests.get(url, headers=headers_for(token), timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Failed to list syncable revisions: {r.status_code} {r.text}")
    # vary shape: expect list in 'revisions' or top-level list
    jd = r.json()
    return jd.get("revisions") or jd.get("items") or jd or []

# Get latest revision id on source model
def get_latest_revision_id(source_model_id, token):
    url = f"{API_BASE}/models/{source_model_id}/alm/latestRevision"
    r = requests.get(url, headers=headers_for(token), timeout=30)
    if r.status_code != 200:
        # fallback: list revisions and pick latest by date if available
        list_url = f"{API_BASE}/models/{source_model_id}/alm/revisions"
        lr = requests.get(list_url, headers=headers_for(token), timeout=30)
        if lr.status_code == 200:
            items = lr.json().get("revisions") or lr.json().get("items") or []
            if not items:
                raise RuntimeError("No revisions found for source model")
            # try to sort by created date if available, else take last
            try:
                items_sorted = sorted(items, key=lambda x: x.get("createdDate") or x.get("created") or "")
                return items_sorted[-1].get("id")
            except Exception:
                return items[-1].get("id")
        raise RuntimeError(f"Failed to get latest revision: {r.status_code} {r.text}")
    jd = r.json()
    # common keys
    return jd.get("revisionId") or jd.get("id") or (jd.get("revision") or {}).get("id")

# Create new revision tag on source model
def create_revision_tag(source_model_id, token, name, description="Created by script"):
    url = f"{API_BASE}/models/{source_model_id}/alm/revisions"
    body = {"name": name, "description": description}
    r = requests.post(url, headers=headers_for(token), json=body, timeout=30)
    if r.status_code not in (200,201):
        raise RuntimeError(f"Failed to create revision: {r.status_code} {r.text}")
    return r.json()

# Create sync task on target model (sync from source revision)
def create_run_sync_task(target_model_id, token, sync_body):
    url = f"{API_BASE}/models/{target_model_id}/alm/syncTasks"
    r = requests.post(url, headers=headers_for(token), json=sync_body, timeout=30)
    if r.status_code not in (200,201):
        raise RuntimeError(f"Failed to create sync task: {r.status_code} {r.text}")
    return r.json()

# Poll sync task to completion
def poll_sync_task(target_model_id, token, sync_task_id, logger, timeout_seconds=1800):
    url = f"{API_BASE}/models/{target_model_id}/alm/syncTasks/{sync_task_id}"
    start = time.time()
    while True:
        r = requests.get(url, headers=headers_for(token), timeout=30)
        if r.status_code != 200:
            logger.write(f"[WARN] Polling sync task returned {r.status_code}: {r.text}")
        else:
            j = r.json()
            # possible shapes: j['task']['status'] or j['status']
            status = (j.get("task", {}) or {}).get("status") or j.get("status")
            logger.write(f"[POLL] SyncTask {sync_task_id} status: {status}")
            if status and str(status).lower() in ("completed","success","finished"):
                return {"status": "completed", "detail": j}
            if status and str(status).lower() in ("failed","error"):
                return {"status": "failed", "detail": j}
        if time.time() - start > timeout_seconds:
            return {"status": "timeout", "detail": None}
        time.sleep(POLL_INTERVAL)

# worker to perform the chosen job (will be run in parallel)
def perform_job(job, token, logger):
    """
    job = {
      'dev_model_id', 'dev_model_name', 'dev_workspace',
      'prod_model_id', 'prod_model_name', 'prod_workspace',
      'action': 'SELECT'|'LATEST'|'CREATE',
      'selected_revision_id' (only for SELECT),
      'new_revision_name' (only for CREATE)
    }
    """
    dev = job['dev_model_id']
    prod = job['prod_model_id']
    logger.write(f"[JOB START] Dev:{dev} -> Prod:{prod} Action:{job['action']}")
    try:
        if job['action'] == 'SELECT':
            rev_id = job.get('selected_revision_id')
            if not rev_id:
                raise RuntimeError("No revision id selected for SELECT action")
        elif job['action'] == 'LATEST':
            rev_id = get_latest_revision_id(dev, token)
            logger.write(f"[INFO] Latest revision on Dev {dev} is {rev_id}")
        elif job['action'] == 'CREATE':
            name = job.get('new_revision_name') or f"AutoTag_{ts()}"
            r = create_revision_tag(dev, token, name, description="Created by ALM automation script")
            # try to extract created revision id
            rev_id = None
            if isinstance(r, dict):
                rev_id = (r.get("revision", {}) or {}).get("id") or r.get("id") or r.get("revisionId")
            if not rev_id:
                # fallback: list revisions and find by name
                list_url = f"{API_BASE}/models/{dev}/alm/revisions"
                lr = requests.get(list_url, headers=headers_for(token), timeout=30)
                if lr.status_code == 200:
                    for it in lr.json().get("revisions", []):
                        if it.get("name") == name:
                            rev_id = it.get("id"); break
            if not rev_id:
                raise RuntimeError("Unable to determine created revision id")
            logger.write(f"[INFO] Created revision {rev_id} with name {name} on Dev {dev}")
        else:
            raise RuntimeError(f"Unsupported job action: {job['action']}")

        # create sync task to target
        sync_body = {
            "sourceModelId": dev,
            "revisionId": rev_id,
            "preserveOverrides": True
        }
        logger.write(f"[INFO] Creating sync task to target {prod} using revision {rev_id}")
        resp = create_run_sync_task(prod, token, sync_body)
        # extract task id
        task_id = (resp.get("task", {}) or {}).get("id") or resp.get("id") or resp.get("taskId")
        if not task_id:
            # sometimes response contains nested info, search keys
            for k in resp:
                if isinstance(resp[k], dict) and resp[k].get("id"):
                    task_id = resp[k].get("id"); break
        if not task_id:
            raise RuntimeError(f"Could not find sync task id in response: {resp}")
        logger.write(f"[INFO] Sync task created: {task_id}. Polling until completion...")
        poll_result = poll_sync_task(prod, token, task_id, logger)
        logger.write(f"[JOB END] Dev:{dev} -> Prod:{prod} Result:{poll_result.get('status')}")
        return {"job": job, "result": poll_result}
    except Exception as e:
        logger.write(f"[JOB ERROR] Dev:{dev} -> Prod:{prod} Error: {e}")
        return {"job": job, "error": str(e)}

# --- Main flow ---
def main():
    moved = backup_logs()
    logger = Logger()
    logger.write("Run started")
    if moved:
        logger.write("Previous log files moved:", ", ".join(moved))

    # load config
    if not Path(CONFIG_PATH).exists():
        logger.write("Config file not found:", CONFIG_PATH)
        print("Place your config.json in the same folder and re-run.")
        logger.close(); return

    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)

    # The JSON you provided uses 'profiles' as dict of profiles
    profiles = cfg.get("profiles") or {}
    if not profiles:
        logger.write("No profiles found in config")
        print("Config missing 'profiles' object. Exiting.")
        logger.close(); return

    # Auth
    username = input("Anaplan username (email): ").strip()
    password = get_password_masked("Anaplan password: ")
    try:
        token = get_auth_token(username, password)
    except Exception as e:
        logger.write("Authentication failed:", e)
        print("Auth failed:", e)
        logger.close(); return
    logger.write("Authenticated successfully")

    # Build list of mappings (dev->prod pairs) from config
    mappings = []  # each: {'dev_model_id', 'prod_model_id', 'profile_name'}
    for prof_name, prof in profiles.items():
        for entry in prof.get("model_ids", []) or []:
            dev = entry.get("dev_model_id")
            prods = entry.get("prod_model_ids") or []
            for p in prods:
                mappings.append({"profile": prof_name, "dev_model_id": dev, "prod_model_id": p, "export_action_names": prof.get("export_action_names")})

    if not mappings:
        logger.write("No dev-prod mappings found in config")
        print("No mappings to process. Exiting.")
        logger.close(); return

    # Pre-fetch model metadata (name + workspace) for all unique model ids
    unique_model_ids = set([m['dev_model_id'] for m in mappings] + [m['prod_model_id'] for m in mappings])
    model_meta_map = {}
    for mid in unique_model_ids:
        try:
            meta = get_model_meta(mid, token)
            name = get_model_name(meta)
            wsid = get_workspace_id_for_model(meta)
            model_meta_map[mid] = {"meta": meta, "name": name, "workspace": wsid}
            logger.write(f"Fetched metadata: {mid} -> name:{name} workspace:{wsid}")
        except Exception as e:
            logger.write(f"[WARN] Could not fetch metadata for model {mid}: {e}")
            # still include with unknowns
            model_meta_map[mid] = {"meta": None, "name": "(unknown)", "workspace": None}

    # Input collection phase: for each mapping show model names and ask user's choice
    pending_jobs = []
    print("\n=== INPUT COLLECTION PHASE ===")
    for idx, m in enumerate(mappings, start=1):
        dev = m['dev_model_id']; prod = m['prod_model_id']
        dev_name = model_meta_map.get(dev, {}).get("name", "(unknown)")
        prod_name = model_meta_map.get(prod, {}).get("name", "(unknown)")
        print(f"\n[{idx}] Dev: {dev_name} ({dev})  ->  Prod: {prod_name} ({prod})")
        print("Choose action for this pair:")
        print("  1 - Select from list of syncable revision tags (you will be shown list)")
        print("  2 - Load absolute (latest) revision tag")
        print("  3 - Create new revision tag in Dev (and sync it)")
        choice = input("Enter 1 / 2 / 3 (or press Enter to skip this pair): ").strip()
        if choice not in ("1","2","3"):
            logger.write(f"Skipping mapping Dev:{dev} Prod:{prod} per user input")
            continue

        job = {
            "profile": m["profile"],
            "dev_model_id": dev,
            "dev_model_name": dev_name,
            "dev_workspace": model_meta_map.get(dev, {}).get("workspace"),
            "prod_model_id": prod,
            "prod_model_name": prod_name,
            "prod_workspace": model_meta_map.get(prod, {}).get("workspace"),
        }

        if choice == "1":
            # fetch syncable revisions and let user choose (collect selection now)
            try:
                revs = list_syncable_revisions(prod, dev, token)
                if not revs:
                    print("No syncable revisions found for this pair. You may choose another action or skip.")
                    logger.write(f"No syncable revisions for Dev:{dev} -> Prod:{prod}")
                    sel = input("Type 's' to skip, or Enter to treat as 'Load latest' instead: ").strip().lower()
                    if sel == 's':
                        logger.write("User skipped after no syncable revisions.")
                        continue
                    else:
                        # treat as latest
                        job['action'] = 'LATEST'
                else:
                    print("Available syncable revisions:")
                    for i, r in enumerate(revs, start=1):
                        rn = r.get("name") or r.get("revisionName") or "(no-name)"
                        rid = r.get("id") or r.get("revisionId") or "(no-id)"
                        print(f"  {i}. {rn}  id={rid}")
                    sel = input("Enter number to select revision (or Enter to skip this pair): ").strip()
                    if not sel:
                        logger.write("User skipped after seeing list.")
                        continue
                    try:
                        idx_sel = int(sel) - 1
                        sel_rev = revs[idx_sel]
                        rev_id = sel_rev.get("id") or sel_rev.get("revisionId")
                        job['action'] = 'SELECT'
                        job['selected_revision_id'] = rev_id
                    except Exception as e:
                        logger.write("Invalid selection:", e)
                        print("Invalid selection; skipping this pair.")
                        continue
            except Exception as e:
                logger.write(f"[WARN] Error fetching syncable revisions for Dev:{dev} Prod:{prod}: {e}")
                print("Could not fetch syncable revisions; treating as 'Load latest' instead.")
                job['action'] = 'LATEST'

        elif choice == "2":
            job['action'] = 'LATEST'

        elif choice == "3":
            name_input = input("Enter name for new revision tag (or press Enter to auto-generate): ").strip()
            if not name_input:
                name_input = f"AutoTag_{ts()}"
            job['action'] = 'CREATE'
            job['new_revision_name'] = name_input

        pending_jobs.append(job)
        logger.write("Queued job:", job['dev_model_id'], "->", job['prod_model_id'], "action:", job['action'])

    if not pending_jobs:
        logger.write("No jobs queued. Exiting.")
        print("No jobs to run. Exiting.")
        logger.close(); return

    # Execution phase: run all queued jobs in parallel
    print("\n=== EXECUTION PHASE: Triggering jobs in parallel ===")
    logger.write(f"Starting parallel execution of {len(pending_jobs)} job(s) with max_workers={MAX_WORKERS}")
    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        future_to_job = {ex.submit(perform_job, job, token, logger): job for job in pending_jobs}
        for fut in tqdm(as_completed(future_to_job), total=len(future_to_job), desc="Jobs"):
            job = future_to_job[fut]
            try:
                res = fut.result()
                results.append(res)
            except Exception as e:
                logger.write(f"[ERROR] Job raised unhandled exception: {e}")
                results.append({"job": job, "error": str(e)})

    logger.write("All jobs finished. Summary:")
    for r in results:
        if 'error' in r:
            logger.write(" - Job ERROR:", r.get('job',{}).get('dev_model_id'), "->", r.get('job',{}).get('prod_model_id'), r.get('error'))
        else:
            st = r.get('result', {}).get('status') if r.get('result') else r.get('status')
            logger.write(" - Job result:", r.get('job',{}).get('dev_model_id'), "->", r.get('job',{}).get('prod_model_id'), "status:", st)

    logger.write("Run finished.")
    logger.close()
    print(f"\nDone. Logs saved to: {logger.path}")

if __name__ == "__main__":
    main()
