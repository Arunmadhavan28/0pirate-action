#!/usr/bin/env python3
import os
import json
import requests
import hashlib
import time
from typing import Dict, Any, List

# --- Configuration & Environment ---
def get_env(var_name: str, required: bool = True, default: Any = None) -> str:
    """Helper to get environment variables and handle errors."""
    value = os.environ.get(var_name)
    if required and not value:
        print(f"::error::Missing required input: {var_name}")
        raise ValueError(f"Input '{var_name}' is required.")
    return value or default

try:
    # --- Action Inputs ---
    GITHUB_TOKEN = get_env("github-token")
    API_KEY_NAME = get_env("0pirate-api-key-name")
    PROVIDER = get_env("0pirate-provider")
    MODEL = get_env("0pirate-model")
    API_URL = get_env("0pirate-api-url").rstrip('/')
    TOKEN_BUDGET = get_env("token-budget", required=False) # For future use

    # --- GitHub Actions Context ---
    # These are read directly, not as inputs
    GITHUB_EVENT_PATH = os.environ.get("GITHUB_EVENT_PATH")
    if not GITHUB_EVENT_PATH:
        raise ValueError("GITHUB_EVENT_PATH not found in environment.")

except ValueError:
    # Exit with a non-zero code to fail the action step
    raise SystemExit(1)


# --- API & GitHub Interaction ---
def post_comment(pull_request_url: str, body: str):
    """Posts a comment to the given pull request."""
    headers = {
        "Authorization": f"token {TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    comments_url = f"{pull_request_url}/comments"
    response = requests.post(comments_url, headers=headers, json={"body": body})
    if response.status_code not in [200, 201]:
        print(f"::error::Error posting comment: {response.status_code} {response.text}")
        raise Exception("Failed to post comment to PR.")
    print("Successfully posted comment to PR.")

def get_pr_files() -> Dict[str, str]:
    """Gets the raw diff of the pull request and extracts added/modified file contents."""
    with open(GITHUB_EVENT_PATH) as f:
        event = json.load(f)
    
    pr_url = event["pull_request"]["url"]
    headers = {"Authorization": f"token {TOKEN}", "Accept": "application/vnd.github.v3.diff"}
    
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
        # We only care about added lines for analysis
        elif line.startswith('+') and not line.startswith('+++'):
            file_content.append(line[1:])
            
    if current_file and file_content:
        files[current_file] = "\n".join(file_content)

    if not files:
        print("No added or modified lines found in the PR diff.")
    else:
        print(f"Found {len(files)} changed files in the PR.")
    return files


# --- Main Action Logic ---
def main():
    start_time = time.time()
    
    with open(GITHUB_EVENT_PATH) as f:
        event = json.load(f)
    pr_url = event["pull_request"]["_links"]["self"]["href"]
    
    try:
        # 1. Get changed files from the Pull Request
        print("Step 1: Getting changed files from PR...")
        pr_files = get_pr_files()
        if not pr_files:
            print("No new code to analyze. Exiting successfully.")
            return

        # 2. STEP 1 of 0pirate API: Call /api/redact
        print("Step 2: Sending files to 0pirate for secure redaction...")
        redaction_files = [('files', (name, content, 'text/plain')) for name, content in pr_files.items()]
        
        redact_res = requests.post(f"{API_URL}/api/redact", files=redaction_files)
        if not redact_res.ok:
            print(f"::error::Redaction API failed with status {redact_res.status_code}: {redact_res.text}")
            raise Exception("Redaction step failed.")
        redaction_data = redact_res.json()
        
        abstracted_files = redaction_data["abstracted_files"]
        
        # 3. STEP 2 of 0pirate API: Call /api/process_code with abstracted code
        print("Step 3: Submitting abstracted code for analysis...")
        main_form_data = {
            "task": "code_review",
            "provider": PROVIDER,
            "model": MODEL,
            "api_key_name": API_KEY_NAME,
            "token_saver_enabled": "True", # Always use diff mode for PR comments
        }
        
        abstracted_content_string = "".join(sorted([v for v in abstracted_files.values() if v]))
        hash_hex = hashlib.sha256(abstracted_content_string.encode()).hexdigest()
        main_form_data["tamper_evident_hash"] = hash_hex

        main_files_data = [('files', (name, content, 'text/plain')) for name, content in abstracted_files.items()]
        
        # Note: The 0pirate API token is NOT sent here. The backend uses the `api_key_name`
        # to look up the key associated with the authenticated user, which is more secure.
        # For a public action, you'd typically have a dedicated 0pirate API token.
        # We will assume for now the GitHub token provides user context.
        opirate_headers = {"Authorization": f"Bearer {TOKEN}"}

        main_res = requests.post(f"{API_URL}/api/process_code", data=main_form_data, files=main_files_data, headers=opirate_headers)
        if not main_res.ok:
            print(f"::error::Job submission failed with status {main_res.status_code}: {main_res.text}")
            raise Exception("Job submission failed.")
        job_id = main_res.json()['job_id']
        print(f"Job submitted successfully. Job ID: {job_id}")

        # 4. Poll for job completion
        print("Step 4: Polling for analysis results...")
        final_result = None
        for i in range(30): # Poll for up to 5 minutes (30 * 10s)
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
            
        # 5. Format and post the comment to the PR
        print("Step 5: Formatting and posting results to PR...")
        analysis = final_result.get("analysis", "No analysis provided by the AI.")
        diff_result = final_result.get("result", {})
        
        comment_body = (
            f"### 🏴‍☠️ 0pirate Security & Code Review\n\n"
            f"**AI Analysis:**\n\n> {analysis}\n\n---"
        )
        
        if diff_result and isinstance(diff_result, dict) and any(diff_result.values()):
            comment_body += "\n\n**Suggested Changes:**\n"
            for filename, diff in diff_result.items():
                if diff.strip():
                    comment_body += (
                        f"\n<details><summary><code>{filename}</code></summary>\n\n"
                        f"```diff\n{diff}\n```\n\n</details>\n"
                    )
        else:
            comment_body += "\n\n**✅ No code changes were suggested.**\n"
            
        post_comment(pr_url, comment_body)

    except Exception as e:
        print(f"::error::An error occurred during the 0pirate action: {e}")
        # Optionally post an error comment to the PR
        try:
            post_comment(pr_url, f"### 🏴‍☠️ 0pirate Action Failed\n\nAn unexpected error occurred: `{e}`")
        except:
            pass # Avoid failing the action if comment posting fails
        raise SystemExit(1)
    finally:
        print(f"Action finished in {time.time() - start_time:.2f} seconds.")

if __name__ == "__main__":
    main()

