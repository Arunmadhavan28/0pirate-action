#!/usr/bin/env python3
"""
This is the main script for the 0pirate GitHub Action.

Orchestration Flow:
1.  Retrieves all required inputs and environment variables.
2.  Fetches the code changes from the triggering pull request.
3.  Checks the token budget.
4.  Calls the /api/redact endpoint to get abstracted code and maps.
5.  Submits ONLY the abstracted code to the /api/process_code endpoint.
6.  Polls the job status endpoint.
7.  Restores the AI's suggestions to be human-readable using the maps.
8.  Formats a readable diff and posts it as a PR comment.
"""
import os
import json
import requests
import hashlib
import time
import difflib
from typing import Dict, Any, List

# --- Helper Functions ---

def restore_from_maps(abstracted_code: Dict[str, str], secret_maps: Dict, abstraction_maps: Dict) -> Dict[str, str]:
    """Reverses the abstraction process to make the code readable again."""
    restored_files = {}
    for path, content in abstracted_code.items():
        reverse_map = {}
        # Abstraction maps are original -> placeholder
        if path in abstraction_maps:
            for original, placeholder in abstraction_maps[path].items():
                reverse_map[placeholder] = original
        # Secret maps are placeholder -> original
        if path in secret_maps:
            reverse_map.update(secret_maps[path])
        
        sorted_placeholders = sorted(reverse_map.keys(), key=len, reverse=True)
        for placeholder in sorted_placeholders:
            content = content.replace(placeholder, reverse_map[placeholder])
        restored_files[path] = content
    return restored_files

def generate_diff(original_content: str, new_content: str) -> str:
    """Generates a unified diff string between two versions of a file."""
    diff = difflib.unified_diff(
        original_content.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile='original',
        tofile='updated'
    )
    return ''.join(diff)

# --- Configuration & Secure Environment Handling ---

def get_env(var_name: str, required: bool = True, default: Any = None) -> str:
    """
    Securely retrieves an environment variable set by the GitHub Actions runner.
    """
    # FIX: Do not replace hyphens.
    env_var_name = f"INPUT_{var_name.upper()}"
    value = os.environ.get(env_var_name)
    
    if required and not value:
        print(f"::error::Missing required input: {var_name}")
        raise ValueError(f"Input '{var_name}' is required.")
    return value or default

def estimate_tokens(text: str) -> int:
    """Provides a rough, safe estimate of the token count."""
    if not text:
        return 0
    return len(text) // 4

try:
    # --- Action Inputs ---
    GITHUB_TOKEN = get_env("repo-token")
    ACTION_TOKEN = get_env("opirate-action-token")
    API_KEY_NAME = get_env("opirate-api-key-name")
    PROVIDER = get_env("opirate-provider")
    MODEL = get_env("opirate-model")
    API_URL = get_env("opirate-api-url").rstrip('/')
    TOKEN_BUDGET = get_env("token-budget", required=False)
    ALLOW_LIST = get_env("allow-list", required=False)

    # --- GitHub Actions Context ---
    GITHUB_EVENT_PATH = os.environ.get("GITHUB_EVENT_PATH")
    if not GITHUB_EVENT_PATH:
        raise ValueError("GITHUB_EVENT_PATH not found in environment.")

except ValueError:
    raise SystemExit(1)

# --- API & GitHub Interaction ---

def post_comment(pull_request_url: str, body: str):
    """Posts a formatted comment to the given pull request."""
    comments_url = pull_request_url.replace('/pulls/', '/issues/') + "/comments"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}", # Uses the correct token variable
        "Accept": "application/vnd.github.v3+json"
    }
    response = requests.post(comments_url, headers=headers, json={"body": body})
    if response.status_code not in [200, 201]:
        print(f"::error::Error posting comment: {response.status_code} {response.text}")
        raise Exception("Failed to post comment to PR.")
    print("Successfully posted comment to PR.")

def get_pr_files() -> Dict[str, str]:
    """
    Gets the raw diff of the pull request and extracts the full content
    of only the added or modified files.
    """
    with open(GITHUB_EVENT_PATH) as f:
        event = json.load(f)
    
    pr_url = event["pull_request"]["url"]
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}", # Uses the correct token variable
        "Accept": "application/vnd.github.v3.diff"
    }
    
    print(f"Fetching diff from: {pr_url}")
    response = requests.get(pr_url, headers=headers)
    response.raise_for_status()
    
    diff_text = response.text
    files = {}
    current_file = None
    file_content = []
    
    for line in diff_text.split('\n'):
        if line.startswith('+++ b/'):
            if current_file and file_content:
                files[current_file] = "\n".join(file_content)
            current_file = line[6:]
            file_content = []
        elif line.startswith('+') and not line.startswith('+++'):
            file_content.append(line[1:])
            
    if current_file and file_content:
        files[current_file] = "\n".join(file_content)

    if not files:
        print("No added or modified lines found in the PR diff.")
    else:
        print(f"Found {len(files)} changed files to analyze.")
    return files

# --- Main Action Logic ---

def main():
    start_time = time.time()
    
    with open(GITHUB_EVENT_PATH) as f:
        event = json.load(f)
    pr_url = event["pull_request"]["_links"]["self"]["href"]
    
    try:
        # 1. Get changed files
        print("Step 1: Getting changed files from PR...")
        pr_files = get_pr_files()
        if not pr_files:
            print("No new code to analyze. Exiting successfully.")
            return

        # 2. Check Token Budget
        if TOKEN_BUDGET:
            try:
                budget = int(TOKEN_BUDGET)
                combined_code = "".join(pr_files.values())
                estimated_tokens = estimate_tokens(combined_code)
                print(f"Token budget: {budget}, Estimated tokens: ~{estimated_tokens}")
                if estimated_tokens > budget:
                    error_message = f"Analysis aborted. Estimated token count (~{estimated_tokens}) exceeds budget of {budget}."
                    print(f"::error::{error_message}")
                    post_comment(pr_url, f"### 🏴‍☠️ 0pirate Action Aborted\n\n**Cost Control**: {error_message}")
                    raise SystemExit(1)
            except ValueError:
                print(f"::warning::Invalid 'token-budget': '{TOKEN_BUDGET}'. Skipping check.")

        # 3. Call /api/redact
        print("Step 3: Sending files for secure redaction...")
        redaction_files = [('files', (name, content, 'text/plain')) for name, content in pr_files.items()]
        redaction_form_data = {}
        if ALLOW_LIST:
            redaction_form_data['allow_list_json'] = json.dumps([item.strip() for item in ALLOW_LIST.split(',')])

        redact_res = requests.post(f"{API_URL}/api/redact", files=redaction_files, data=redaction_form_data)
        if not redact_res.ok:
            print(f"::error::Redaction API failed: {redact_res.status_code} {redact_res.text}")
            raise Exception("Redaction step failed.")
        redaction_data = redact_res.json()
        abstracted_files = redaction_data["abstracted_files"]
        
        # 4. Call /api/process_code
        print("Step 4: Submitting abstracted code for analysis...")
        main_form_data = {
            "task": "code_review",
            "provider": PROVIDER,
            "model": MODEL,
            "api_key_name": API_KEY_NAME,
            "token_saver_enabled": "True",
        }
        
        abstracted_content_string = "".join(sorted([v for v in abstracted_files.values() if v]))
        hash_hex = hashlib.sha256(abstracted_content_string.encode()).hexdigest()
        main_form_data["tamper_evident_hash"] = hash_hex

        main_files_data = [('files', (name, content, 'text/plain')) for name, content in abstracted_files.items()]
        
        # CORRECT: Authenticate with the 0pirate backend using the dedicated action token
        opirate_headers = {"X-0Pirate-Action-Token": ACTION_TOKEN}

        main_res = requests.post(f"{API_URL}/api/process_code", data=main_form_data, files=main_files_data, headers=opirate_headers)
        if not main_res.ok:
            print(f"::error::Job submission failed: {main_res.status_code} {main_res.text}")
            raise Exception("Job submission failed.")
        job_id = main_res.json()['job_id']
        print(f"Job submitted successfully. Job ID: {job_id}")

        # 5. Poll for job completion
        print("Step 5: Polling for analysis results...")
        final_result = None
        for i in range(30): # Poll for up to 5 minutes
            print(f"Polling attempt {i+1}/30...")
            time.sleep(10)
            status_res = requests.get(f"{API_URL}/api/status/{job_id}", headers=opirate_headers)
            status_data = status_res.json()
            if status_data.get("status") == "completed":
                final_result = status_data
                break
            elif status_data.get("status") == "failed":
                raise Exception(f"0pirate analysis failed: {status_data.get('notice')}")
        
        if not final_result:
            raise Exception("Timed out waiting for analysis results.")
            
        # 6. Format and post the comment
        print("Step 6: Formatting and posting results...")
        analysis = final_result.get("analysis", "No analysis provided by the AI.")
        modified_abstracted_files = final_result.get("result", {})

        # CORRECT: Restore the code to be human-readable
        secret_maps = redaction_data.get("secret_maps", {})
        abstraction_maps = redaction_data.get("abstraction_maps", {})
        restored_files = restore_from_maps(modified_abstracted_files, secret_maps, abstraction_maps)

        comment_body = (
            f"### 🏴‍☠️ 0pirate Security & Code Review\n\n"
            f"**AI Analysis:**\n\n> {analysis}\n\n---"
        )
        
        if restored_files and any(restored_files.values()):
            comment_body += "\n\n**Suggested Changes:**\n"
            for filename, restored_content in restored_files.items():
                original_content = pr_files.get(filename, "")
                if restored_content.strip() != original_content.strip():
                    # Generate a human-readable diff
                    human_readable_diff = generate_diff(original_content, restored_content)
                    if human_readable_diff:
                        comment_body += (
                            f"\n<details><summary><code>{filename}</code></summary>\n\n"
                            f"```diff\n{human_readable_diff}\n```\n\n</details>\n"
                        )
        else:
            comment_body += "\n\n**✅ No code changes were suggested.**\n"
            
        post_comment(pr_url, comment_body)

    except Exception as e:
        error_message = f"An error occurred during the 0pirate action: {e}"
        print(f"::error::{error_message}")
        try:
            post_comment(pr_url, f"### 🏴‍☠️ 0pirate Action Failed\n\nAn unexpected error occurred: `{e}`")
        except Exception as comment_exc:
            print(f"::error::Failed to post error comment to PR: {comment_exc}")
        raise SystemExit(1)
    finally:
        print(f"Action finished in {time.time() - start_time:.2f} seconds.")

if __name__ == "__main__":
    main()
