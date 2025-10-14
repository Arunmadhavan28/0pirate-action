0pirate Security Review GitHub Action
The official GitHub Action for 0pirate, the zero-knowledge AI gateway for enterprise code.

This action automatically analyzes code changes in your pull requests to find bugs, fix security vulnerabilities, and improve code quality—all while guaranteeing your source code never leaves your ecosystem. It acts as an automated senior developer and security expert on every PR.

The 0pirate Advantage: Zero-Knowledge Security
Unlike other AI tools that require you to send your proprietary source code to a third-party server, 0pirate operates on a strict zero-knowledge principle. Our security model is designed so that we never see your original code.

How It Works: A Secure, Two-Step Flow

Client-Side Redaction: The action runs our open-source redactor inside your CI environment. It intelligently replaces sensitive information (secrets, PII) and abstracts your proprietary logic (variable names, function names) into meaningless placeholders. The sensitive mapping data required to restore the code never leaves your runner.

Analysis on Abstracted Code: Only the sanitized, abstracted code is sent to the 0pirate backend for AI analysis. Our servers have no way to reverse-engineer your original logic.

In-Browser Restoration: The corrected, still-abstracted code is returned to your frontend. The final restoration happens securely in your browser, using the mapping data that was kept local.

This process provides the power of advanced AI analysis with the security of a completely offline tool.

Getting Started: A 3-Step Guide
Integrate 0pirate into your repository in minutes.

Step 1: Set Up Your 0pirate Account & API Key

First, you need an account on 0pirate.com to manage your AI provider keys.

Go to 0pirate.com and sign up.

Navigate to your Account -> API Keys dashboard.

Add the API key for your preferred AI provider (e.g., your company's Gemini or OpenAI key) and give it a memorable, unique name (e.g., acme-corp-gemini-main).

Step 2: Configure Your GitHub Repository Secret

Next, securely tell your GitHub repository which key to use from your 0pirate account.

In your GitHub repository, go to Settings > Secrets and variables > Actions.

Click New repository secret.

Create a secret with the exact name OPIRATE_API_KEY_NAME.

For the value, enter the exact name you chose on 0pirate.com (e.g., acme-corp-gemini-main).

Step 3: Create the GitHub Workflow File

Finally, create a workflow file in your repository to trigger the action on every pull request.

In your repository, create a new file at .github/workflows/0pirate_review.yml:

# .github/workflows/0pirate_review.yml
name: 0pirate Security Review

# This triggers the action on every new pull request or when new commits are pushed.
on:
  pull_request:
    types: [opened, synchronize]

# This permission is required for the action to post comments on your PR.
permissions:
  pull-requests: write

jobs:
  code-review:
    runs-on: ubuntu-latest
    steps:
      # Step 1: Check out the repository's code
      - name: Checkout code
        uses: actions/checkout@v4
        with:
          # Fetch all history for a more accurate diff analysis
          fetch-depth: 0

      # Step 2: Run the 0pirate Action
      - name: 0pirate Security Review
        uses: Arunmadhavan28/0pirate-action@v1
        with:
          # This token is used to post comments back to the PR
          github-token: ${{ secrets.GITHUB_TOKEN }}
          
          # This tells the action which key to use from your 0pirate account
          opirate-api-key-name: ${{ secrets.OPIRATE_API_KEY_NAME }}
          
          # --- Optional: You have full control to customize the provider and model ---
          # opirate-provider: 'openai'
          # opirate-model: 'gpt-4o'

Commit this file to your main branch. You're all set! The 0pirate action will now automatically review every new pull request.

Action Inputs
All inputs are configured under the with: key in your workflow file.

Input

Description

Required

Default

github-token

The GitHub token for API access, used to post comments back to the PR. Always use ${{ secrets.GITHUB_TOKEN }}.

true

N/A

opirate-api-key-name

The name of the API key you saved in your 0pirate.com account. This is passed via a GitHub secret.

true

N/A

opirate-provider

The AI provider you want to use (e.g., gemini, openai, anthropic). Must match your saved key.

false

gemini

opirate-model

The specific model for the analysis (e.g., gemini-1.5-pro, gpt-4o).

false

gemini-1.5-flash

opirate-api-url

The base URL for the 0pirate API. Only change this for self-hosted or enterprise deployments.

false

https://api.0pirate.com

token-budget

(Coming Soon) The maximum token budget for an analysis. If exceeded, the action will fail.

false

N/A

Happy coding, and may your code be secure! 

