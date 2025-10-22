#!/usr/bin/env python3
import os, json, requests, hashlib, time, difflib
from typing import Dict, Any, List, Optional

def restore_from_maps(abstracted_code: Dict[str, str], secret_maps: Dict, abstraction_maps: Dict) -> Dict[str, str]:
    restored_files = {}
    for path, content in abstracted_code.items():
        reverse_map = {}
        if path in abstraction_maps:
            for original, placeholder in abstraction_maps[path].items():
                reverse_map[placeholder] = original
        if path in secret_maps:
            reverse_map.update(secret_maps[path])
        sorted_placeholders = sorted(reverse_map.keys(), key=len, reverse=True)
        for placeholder in sorted_placeholders:
            content = content.replace(placeholder, reverse_map[placeholder])
        restored_files[path] = content
    return restored_files

def generate_diff(original_content: str, new_content: str) -> str:
    diff = difflib.unified_diff(original_content.splitlines(keepends=True), new_content.splitlines(keepends=True), fromfile='original', tofile='updated')
    return ''.join(diff)

def get_env(var_name: str, required: bool = True, default: Any = None) -> str:
    env_var_name = f"INPUT_{var_name.upper()}" # No more hyphen replacement
    value = os.environ.get(env_var_name)
    if required and not value:
        print(f"::error::Missing required input: {var_name}")
        raise ValueError(f"Input '{var_name}' is required.")
    return value or default

def estimate_tokens(text: str) -> int:
    return 0 if not text else len(text) // 4

try:
    GITHUB_TOKEN = get_env("repo-token")
    ACTION_TOKEN = get_env("opirate-action-token")
    API_KEY_NAME = get_env("opirate-api-key-name")
    PROVIDER = get_env("opirate-provider")
    MODEL = get_env("opirate-model")
    API_URL = get_env("opirate-api-url").rstrip('/')
    TOKEN_BUDGET = get_env("token-budget", required=False)
    ALLOW_LIST = get_env("allow-list", required=False)
    GITHUB_EVENT_PATH = os.environ.get("GITHUB_EVENT_PATH")
    if not GITHUB_EVENT_PATH:
        raise ValueError("GITHUB_EVENT_PATH not found.")
except ValueError:
    raise SystemExit(1)

def post_comment(pull_request_url: str, body: str):
    comments_url = pull_request_url.replace('/pulls/', '/issues/') + "/comments"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    response = requests.post(comments_url, headers=headers, json={"body": body})
    if response.status_code not in [200, 201]:
        print(f"::error::Error posting comment: {response.status_code} {response.text}")
    print("Successfully posted comment to PR.")

def get_pr_files() -> Dict[str, str]:
    with open(GITHUB_EVENT_PATH) as f: event = json.load(f)
    pr_url = event["pull_request"]["url"]
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3.diff"}
    print(f"Fetching diff from: {pr_url}")
    response = requests.get(pr_url, headers=headers); response.raise_for_status()
    diff_text, files, current_file, file_content = response.text, {}, None, []
    for line in diff_text.split('\n'):
        if line.startswith('+++ b/'):
            if current_file and file_content: files[current_file] = "\n".join(file_content)
            current_file, file_content = line[6:], []
        elif line.startswith('+') and not line.startswith('+++'):
            file_content.append(line[1:])
    if current_file and file_content: files[current_file] = "\n".join(file_content)
    print(f"Found {len(files)} changed files to analyze.")
    return files

def main():
    start_time = time.time()
    with open(GITHUB_EVENT_PATH) as f: event = json.load(f)
    pr_url = event["pull_request"]["_links"]["self"]["href"]
    try:
        print("Step 1: Getting changed files from PR...")
        pr_files = get_pr_files()
        if not pr_files: print("No new code to analyze. Exiting."); return
        if TOKEN_BUDGET:
            try:
                budget, estimated_tokens = int(TOKEN_BUDGET), estimate_tokens("".join(pr_files.values()))
                print(f"Token budget: {budget}, Estimated: ~{estimated_tokens}")
                if estimated_tokens > budget: raise SystemExit(f"Analysis aborted. Estimated tokens (~{estimated_tokens}) exceeds budget ({budget}).")
            except ValueError: print(f"::warning::Invalid 'token-budget': '{TOKEN_BUDGET}'. Skipping check.")
        
        print("Step 3: Sending files for secure redaction...")
        redaction_files = [('files', (name, content, 'text/plain')) for name, content in pr_files.items()]
        redaction_form_data = {}
        if ALLOW_LIST: redaction_form_data['allow_list_json'] = json.dumps([item.strip() for item in ALLOW_LIST.split(',')])
        redact_res = requests.post(f"{API_URL}/api/redact", files=redaction_files, data=redaction_form_data)
        if not redact_res.ok: raise Exception(f"Redaction API failed: {redact_res.status_code} {redact_res.text}")
        redaction_data, abstracted_files = redact_res.json(), redaction_data["abstracted_files"]
        
        print("Step 4: Submitting abstracted code for analysis...")
        main_form_data = {"task": "code_review", "provider": PROVIDER, "model": MODEL, "api_key_name": API_KEY_NAME, "token_saver_enabled": "True"}
        abstracted_content_string = "".join(sorted([v for v in abstracted_files.values() if v]))
        main_form_data["tamper_evident_hash"] = hashlib.sha256(abstracted_content_string.encode()).hexdigest()
        main_files_data = [('files', (name, content, 'text/plain')) for name, content in abstracted_files.items()]
        opirate_headers = {"X-0Pirate-Action-Token": ACTION_TOKEN}
        main_res = requests.post(f"{API_URL}/api/process_code", data=main_form_data, files=main_files_data, headers=opirate_headers)
        if not main_res.ok: raise Exception(f"Job submission failed: {main_res.status_code} {main_res.text}")
        job_id = main_res.json()['job_id']; print(f"Job submitted successfully. Job ID: {job_id}")
        
        print("Step 5: Polling for analysis results...")
        final_result = None
        for i in range(30):
            print(f"Polling attempt {i+1}/30...")
            time.sleep(10)
            status_res = requests.get(f"{API_URL}/api/status/{job_id}", headers=opirate_headers)
            status_data = status_res.json()
            if status_data.get("status") == "completed": final_result = status_data; break
            if status_data.get("status") == "failed": raise Exception(f"Analysis failed: {status_data.get('notice')}")
        if not final_result: raise Exception("Timed out waiting for analysis results.")
            
        print("Step 6: Formatting and posting results...")
        analysis, modified_abstracted_files = final_result.get("analysis", "No analysis provided."), final_result.get("result", {})
        secret_maps, abstraction_maps = redaction_data.get("secret_maps", {}), redaction_data.get("abstraction_maps", {})
        restored_files = restore_from_maps(modified_abstracted_files, secret_maps, abstraction_maps)
        comment_body = f"### 🏴‍☠️ 0pirate Security & Code Review\n\n**AI Analysis:**\n\n> {analysis}\n\n---"
        changes_found = False
        if restored_files and any(restored_files.values()):
            diffs_md = ""
            for filename, restored_content in restored_files.items():
                original_content = pr_files.get(filename, "")
                if restored_content.strip() != original_content.strip():
                    human_readable_diff = generate_diff(original_content, restored_content)
                    if human_readable_diff:
                        changes_found = True
                        diffs_md += f"\n<details><summary><code>{filename}</code></summary>\n\n```diff\n{human_readable_diff}\n```\n\n</details>\n"
            if changes_found: comment_body += "\n\n**Suggested Changes:**\n" + diffs_md
        if not changes_found: comment_body += "\n\n**✅ No code changes were suggested.**\n"
        post_comment(pr_url, comment_body)
    except Exception as e:
        error_message = f"An error occurred during the 0pirate action: {e}"
        print(f"::error::{error_message}")
        try: post_comment(pr_url, f"### 🏴‍☠️ 0pirate Action Failed\n\nAn unexpected error occurred: `{e}`")
        except: pass
        raise SystemExit(1)
    finally:
        print(f"Action finished in {time.time() - start_time:.2f} seconds.")

if __name__ == "__main__":
    main()
